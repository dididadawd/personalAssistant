# app/core/agent.py
# --- 1. Standard & Third-Party Imports ---
import os
import json
import uuid
import time
import inspect
import threading
import asyncio
import re
import ast
from collections import deque
from json_repair import repair_json
from datetime import datetime, timedelta, date
from collections import defaultdict
from telegram.error import BadRequest # <-- Add the specific error import
from telegram import Bot 
from telegram.constants import ParseMode
# Import prompts from centralized system
from ..prompts.prompt_loader import (
    initialize_bot_persona_promt,
    execute_task_main_promt,
    autonomously_advance_mission_planning_prompt,
    _operate_on_tic_promt,
    consolidate_mission_log_if_needed_promt,
    set_goal_promt,
)
# --- 2. Imports from other modules in our project ---
from ..db.json_manager import add_record, find_records, update_record, delete_record, update_records_by_query
from ..utils.date_helpers import get_next_scheduled_date
from ..scheduler.jobs import schedule_runner, process_routines, check_and_execute_freeze_schedule, postpone_overdue_tasks
from ..services.main_service import schedule_one_time_task
# Import prompts safely - may not exist yet
try:
    from ..prompts.system_prompts import YAHLI_ROOM_UNIVERSAL_RULES
except ImportError:
    YAHLI_ROOM_UNIVERSAL_RULES = ""
from ..utils.logger import log_and_print
# API Logging Configuration
YAHLI_API_LOGGING = os.environ.get('YAHLI_API_LOGGING', 'true').lower() == 'true'
ENABLE_LONG_TERM_MEMORY = os.environ.get('ENABLE_LONG_TERM_MEMORY', 'false').lower() == 'true'
def agent_log(message, level="SYSTEM"):
    """Conditional logging based on YAHLI_API_LOGGING flag"""
    if YAHLI_API_LOGGING:
        log_and_print(f"[AGENT] {message}", level)
# Import system_state functions safely
try:
    from ..utils.system_state import _split_message_intelligently
except ImportError:
    _split_message_intelligently = None
from memory_manager import VectorMemory
# --- 3. Circular Dependency Fix: Import TOOL_MAP and ApiKeyRotator from where they now live ---
from .llm_provider import api_key_manager, GEMINI_MODEL_NAME, GEMINI_MODEL_FOR_COMPLEX_NAME, is_successful_response
from .tools_registry import TOOL_MAP
from ..config import Config
# --- Constants & Global Variables for this module ---
AI_THINKING_LOCK = threading.RLock()
PERSONAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'personas')
DATA_FILE_LOCKS = defaultdict(threading.Lock)
DEBUG = False
ASYNC_LOOP = None
TELEGRAM_BOT_TOKEN = Config.TELEGRAM_BOT_TOKEN
script_dir = os.path.dirname(os.path.abspath(__file__))
TRANSCRIPT_FILE_PATH = os.path.join(script_dir, "conversation_transcript.txt")
# =========================================================================================
#  The Agent Class - The "Brain"
# =========================================================================================
def prompt_user(prompt_text):
    """Prompts the user with a question, and logs both the question and the answer."""
    log_and_print(prompt_text, "SYSTEM")
    user_input = input("> ")
    with open(TRANSCRIPT_FILE_PATH, 'a', encoding='utf-8') as f:
        f.write(f"[USER] > {user_input}\n")
    return user_input
async def send_proactive_message(chat_id: int, message: str):
    """
    Sends a proactive message. If the message is too long, automatically splits it into multiple messages.
    """
    if not TELEGRAM_BOT_TOKEN:
        #logger.error("Cannot send proactive message: TELEGRAM_BOT_TOKEN is not set.")
        return
    if not message or not message.strip():
        log_and_print("--- WARNING: Attempted to send an empty message to Telegram. Suppressed. ---", "SYSTEM_ERROR")
        return
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    # --- Start of the upgrade ---
    # Step 1: Split the message into valid parts
    message_parts = _split_message_intelligently(message)
    # Step 2: Send each part separately in a loop
    for i, part in enumerate(message_parts):
        try:
            # First attempt: Send with HTML formatting
            await bot.send_message(
                chat_id=chat_id, 
                text=part, 
                parse_mode=ParseMode.HTML
            )
        except BadRequest as e:
            if 'parse' in str(e):
                print(f"--- WARNING: Could not parse message part {i+1} as HTML. Falling back to plain text. ---", "SYSTEM_ERROR")
                try:
                    # Second attempt: If failed, send as plain text
                    await bot.send_message(
                        chat_id=chat_id, 
                        text=part
                    )
                except Exception as final_e:
                    print(f"--- CRITICAL ERROR: Failed to send message part {i+1} even as plain text. Error: {final_e} ---", "SYSTEM_ERROR")
            else:
                # Other BadRequest error (like 'Message is too long' - although shouldn't happen now)
                print(f"--- CRITICAL ERROR: An unexpected BadRequest occurred on part {i+1}. Error: {e} ---", "SYSTEM_ERROR")
        except Exception as e:
            print(f"--- CRITICAL ERROR: An unexpected error occurred while sending part {i+1}. Error: {e} ---", "SYSTEM_ERROR")
        # Add a short pause between messages to ensure they arrive in the correct order
        if len(message_parts) > 1 and i < len(message_parts) - 1:
            await asyncio.sleep(0.5) # Half a second
class active_agent:
    def __init__(self):
        self.name = None
        self.config = None
        # self.model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        self.conversation_history = deque(maxlen=20)
        self.memory = None
        self.AI_THINKING_LOCK = AI_THINKING_LOCK # <-- Add this line
    def launch_task_in_thread(self, job: dict):
        """
        Executes a task in a separate Thread to prevent blocking.
        This is the new entry point for scheduled tasks.
        """
        prompt = job.get("prompt", "N/A")
        log_and_print(f"--- Launching task in new Thread: '{prompt}' ---", "SYSTEM")
        # Create a new Thread whose purpose is to run the existing task logic
        # daemon=True ensures the Thread closes automatically when the main program closes
        task_thread = threading.Thread(
            target=self.run_single_scheduled_task, 
            args=(job,), 
            daemon=True
        )
        task_thread.start()
    def run_single_scheduled_task(self, job: dict):
        """
        Running a scheduled task - optimal integration version:
        - Restart-proof Gatekeeper
        - Safe file writing (LOCK)
        - Smart success logic
        """
        job_id = job.get("job_id")
        prompt = job.get("prompt", "No prompt")
        weeks_interval = job.get("weeks_interval", 1)
        last_run_str = job.get("last_run_date")
        # --- Part A: Gatekeeper (time interval check) ---
        if weeks_interval > 1 and last_run_str:
            last_run = datetime.strptime(last_run_str, "%Y-%m-%d").date()
            today = date.today()
            days_passed = (today - last_run).days
            days_required = (weeks_interval * 7) - 1  # Small safety margin
            if days_passed < days_required:
                log_and_print(f"Delaying '{prompt}'. {days_passed} days passed (required {weeks_interval} weeks).", "SYSTEM")
                return
        # --- Part B: Running the task ---
        log_and_print(f"\n--- Running scheduled task: '{prompt}' ---", "SYSTEM")
        response = self.execute_task(prompt, execution_mode="silent", is_isolated_run=True)
        # --- Part C: Update last_run_date in file ---
        kb_path = os.path.join(PERSONAS_DIR, self.name, "knowledge_base")
        file_path = os.path.join(kb_path, "schedule.json")
        lock = DATA_FILE_LOCKS.get("schedule", threading.Lock())
        with lock:
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    all_jobs = json.load(f)
                for j in all_jobs:
                    if j.get("job_id") == job_id:
                        j["last_run_date"] = date.today().isoformat()
                        break
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(all_jobs, f, indent=2, ensure_ascii=False)
        # --- Part D: Decision on sending notification ---
        notification_policy = job.get("send_notification", False)
        should_send = False
        if notification_policy is True:
            should_send = True
        elif notification_policy == "on_success":
            should_send = is_successful_response(response)
        if not should_send:
            return
        # --- Part E: Sending the notification ---
        target_chat_id = self.config.get("telegram_chat_id")
        if not target_chat_id:
            log_and_print("Notification required but telegram_chat_id not defined.", "SYSTEM_ERROR")
            return
        if isinstance(response, dict):
            for key in ["briefing", "report", "reply", "message"]:
                if key in response:
                    final_message = response[key]
                    break
            else:
                final_message = json.dumps(response, ensure_ascii=False, indent=2)
        else:
            final_message = str(response)
        if ASYNC_LOOP and ASYNC_LOOP.is_running():
            asyncio.run_coroutine_threadsafe(
                send_proactive_message(target_chat_id, final_message),
                ASYNC_LOOP
            )
            log_and_print("--- Scheduled task completed (notification sent) ---", "SYSTEM")
    def _execute_tool_with_context(self, tool_name: str, parameters: dict, context_mission_id: str = None, chat_context: str = None):
        """
        Internal function that invokes tools.
        Includes logic for automatic injection of mission_id if the operation is performed within a mission context.
        """
        if tool_name not in TOOL_MAP:
            return json.dumps({"error": f"Tool '{tool_name}' is not a valid tool."})
        mapping = TOOL_MAP[tool_name]
        target_function = mapping['target_function']
        final_args = mapping.get('static_params', {}).copy()
        final_args['agent'] = self
        # "Flatten" the parameters to one level
        flat_params = {}
        if 'parameters' in parameters and isinstance(parameters['parameters'], dict):
            flat_params.update(parameters['parameters'])
        else:
            flat_params.update(parameters)
        # Remove duplicates
        flat_params.update({k: v for k, v in parameters.items() if k != 'parameters'})
        # --- Auto-injection logic ---
        if context_mission_id and tool_name in ['add_project', 'create_project_timeboxed_action_plan']:
            final_args['mission_id'] = context_mission_id
            log_and_print(f"--- Auto-linking Project to Mission ID: {context_mission_id} ---", "SYSTEM")
        # --- Fix add_record logic ---
        if target_function == add_record:
            # 1. Extract table name
            if 'table_name' in flat_params:
                final_args['table_name'] = flat_params.pop('table_name')
            # 2. Extract the data - the critical fix to prevent duplication (Nesting)
            if 'data' in flat_params and isinstance(flat_params['data'], dict):
                # If the AI sent a 'data' object inside the parameters, we'll use its content directly
                final_args['data'] = flat_params['data']
            else:
                # Otherwise, assume all other parameters are the fields themselves
                final_args['data'] = flat_params
        else:
            # Regular logic for the rest of the tools
            sanitized_params = {}
            try:
                sig = inspect.signature(target_function)
                for param_name in sig.parameters:
                    if param_name in flat_params:
                        sanitized_params[param_name] = flat_params[param_name]
                if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                    sanitized_params.update(flat_params)
            except ValueError:
                sanitized_params.update(flat_params)
            final_args.update(sanitized_params)
        try:
            result = target_function(**final_args)
            return json.dumps(result, ensure_ascii=False, default=str)
        except TypeError as e:
            return json.dumps({"error": f"Function Argument Error: {e}"})
        except Exception as e:
            return json.dumps({"error": f"An internal error occurred while running the tool: {e}"})

    def load_persona(self, name: str):
        self.name = name
        config_path = os.path.join(PERSONAS_DIR, f"{self.name}.json")
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            #logger.info(f"Persona '{self.name}' loaded successfully.")
            # --- New addition: Initialize memory after loading persona ---
            try:
                self.memory = VectorMemory(
                    agent_name=self.name, 
                    base_path=PERSONAS_DIR,
                    api_key_manager=api_key_manager # <-- The new parameter!
                )
            except Exception as e:
                #logger.error(f"Failed to initialize VectorMemory for {self.name}: {e}")
                self.memory = None
            # --------------------------------------------------------
            # Bind the super tool to the model after knowing who the persona is
            #self.model.tools = [self._create_dynamic_tool()]
            return True
        except FileNotFoundError:
            #logger.error(f"Config file for persona '{self.name}' not found.")
            return False
    def save_persona(self):
        """Saves the bot's current configuration to a file"""
        if not self.name or not self.config:
            #logger.error("Cannot save. Name or configuration missing.")
            return
        config_path = os.path.join(PERSONAS_DIR, f"{self.name}.json")
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
        #logger.info(f"Persona '{self.name}' saved successfully.")
    def initialize_bot_persona(self, user_raw_request: str):
        print("\n1. Analyzing your request to define identity, data structure, and schedule...")
        try:
            meta_prompt = initialize_bot_persona_promt.format(
                user_raw_request=user_raw_request
            )
            try:
                result = api_key_manager.safe_generate_content(meta_prompt, model_name=GEMINI_MODEL_NAME)
                if not result["success"]:
                    print(f"   Error in request analysis: {result['error']}")
                    self.config = None
                    return False
                cleaned_response = json.dumps(result["decision"], ensure_ascii=False)
                self.config = json.loads(cleaned_response)
                print("   Request analysis completed:")
                print(json.dumps(self.config, indent=2, ensure_ascii=False))
                return True
            except Exception as e:
                print(f"   Error in request analysis: {e}")
                self.config = None
                return False
        except Exception as e:
            log_and_print(f"   Error in request analysis: {e}", "ERROR")
            #logger.error(f"Error during persona initialization: {e}")
            self.config = None
            return False
    def setup_knowledge_base(self):
        """
        Checks the data schema the AI created. If tables exist, it verifies their files exist.
        If a file is empty, it guides the user through populating the first list.
        All interaction is documented in the conversation file.
        """
        
        if not self.config or 'data_schema' not in self.config or not self.config['data_schema']:
            # The user shouldn't see this, this is a technical log
            #logger.info("No data_schema found in config, skipping knowledge base setup.")
            return
        log_and_print("\n2. Setting up the Knowledge Base...", "SYSTEM")
        # Path to the specific knowledge base folder of this persona
        kb_path = os.path.join(PERSONAS_DIR, self.name, "knowledge_base")
        os.makedirs(kb_path, exist_ok=True) # Ensure the folder exists
        for name, schema in self.config['data_schema'].items():
            # Protection layer in case the AI returned a list instead of a dict
            actual_schema = schema
            if isinstance(schema, list) and schema:
                #logger.warning(f"AI returned a list for schema '{name}'. Correcting to first element.")
                actual_schema = schema[0]
            elif not isinstance(actual_schema, dict):
                #logger.error(f"Invalid schema for '{name}' (not a dict). Skipping.")
                continue
            file_path = os.path.join(kb_path, f"{name}.json")
            # Check if the table needs to be populated
            if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
                log_and_print(f"   - Knowledge base '{name}' is empty. Starting with a first list.", "SYSTEM")
                new_entry = {}
                for column, col_type in actual_schema.items():
                    # If the column is ID, create it automatically
                    if column.endswith("_id"):
                        value = str(uuid.uuid4().hex)
                        new_entry[column] = value
                        log_and_print(f"     - Unique identifier '{column}' automatically generated for you: {value}", "SYSTEM")
                    else:
                        # Otherwise, request input from the user through the logging function
                        user_value = prompt_user(f"     > Please enter a value for '{column}' (type: {col_type}): ")
                        new_entry[column] = user_value
                # Save the new list to file
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump([new_entry], f, indent=2, ensure_ascii=False)
                log_and_print(f"   First list saved at {file_path}", "SYSTEM")
            else:
                continue
                log_and_print(f"   - Knowledge base '{name}' already exists.", "SYSTEM")
    def _create_dynamic_tool(self):
        """Creates a dynamic genai tool to perform AI-coordinated operations"""
        def execute_mapped_tool(tool_name: str, parameters: dict, chat_context: str = None) -> str:
            return self._execute_tool_with_context(tool_name, parameters, chat_context=chat_context)
        return execute_mapped_tool
    def get_available_tables(self) -> list[str]:
        """
        Scans the knowledge base folder and returns a list of available table names.
        """
        if not self.name:
            return [] # If no active persona, no tables
        kb_path = os.path.join(PERSONAS_DIR, self.name, "knowledge_base")
        if not os.path.exists(kb_path):
            return []
        try:
            # Returns a list of file names, without the '.json' extension
            # For example, ['clients', 'shows', 'communications']
            tables = [f.replace(".json", "") for f in os.listdir(kb_path) if f.endswith(".json")]
            return tables
        except Exception as e:
            #logger.error(f"Error getting available tables: {e}")
            return []
    def execute_task(self, user_prompt: str, execution_mode: str = "interactive", is_isolated_run: bool = False, chat_context: str = None):
        """Manages tasks using a heuristic ReAct approach with systematic thinking."""
        with self.AI_THINKING_LOCK:
            agent_log(f">>> execute_task START - User prompt: {user_prompt[:100]}...", "SYSTEM")
            log_and_print(f"\n--- Bot thinking (new clip from user: {user_prompt}) ---", "SYSTEM")
            temp_download_link = None 
            # <-- Change: History selection logic ---
            if is_isolated_run:
                # In isolated mode, use a temporary empty history
                current_history = deque(maxlen=20)
                log_and_print(f"\n--- Bot thinking (isolated run): {user_prompt} ---", "SYSTEM")
            else:
                # In normal mode, use the shared history and add user input
                current_history = self.conversation_history
            current_history.append(f"User: {user_prompt}")

            # Filter tools based on chat context - remove generate_end_of_day_report from yahli_room
            if chat_context == "yahli_room":
                filtered_tool_map = {name: info for name, info in TOOL_MAP.items() if name != "generate_end_of_day_report"}
            else:
                filtered_tool_map = TOOL_MAP

            tools_description = "\n".join([
                f"- `{name}`: {info.get('description', '')}"
                for name, info in filtered_tool_map.items()
            ])
            available_tables = self.get_available_tables()
            # Convert the list to a string that's easy for the AI to read
            tables_list_str = ", ".join([f"`{table}`" for table in available_tables]) if available_tables else "No tables found."
            # --- Long-term memory retrieval ---
            memory_context = "No relevant long-term memories found."
            if ENABLE_LONG_TERM_MEMORY and self.memory:
                retrieved_memories = self.memory.search_memory(user_prompt, n_results=3, strategy="vector")
                if retrieved_memories:
                    formatted_memories = "\n- ".join(retrieved_memories)
                    memory_context = f"{formatted_memories}"
                    log_and_print(f"--- Bot remembered: {formatted_memories} ---", "SYSTEM")
            # ----------------------------------------------
            max_turns = 7
            turn = -1
            while turn < max_turns:
                turn += 1
                log_and_print(f"\n--- Thinking round #{turn + 1} (with full conversation history) ---", "SYSTEM")
                # Build the prompt with all the history
                data_schema_for_prompt = json.dumps(self.config['data_schema'], indent=2, ensure_ascii=False)
                reasoning_prompt = execute_task_main_promt.format(
                    data_schema_for_prompt=data_schema_for_prompt,
                    tools_description=tools_description,
                    tables_list_str=tables_list_str,
                    current_history_joined="\n".join(current_history),
                    memory_context=memory_context,
                    turn=turn,
                    newline="\n"
                )
                
                # Print the full prompt to log for debugging (optional but useful)
                # #logger.info(f"REASONING PROMPT:\n{reasoning_prompt}")
                #log_and_print(reasoning_prompt)
                # --- Centralized failsafe via safe_generate_content ---
                result = api_key_manager.safe_generate_content(reasoning_prompt, model_name=GEMINI_MODEL_NAME)
                if not result["success"]:
                    log_and_print(f"--- ERROR: {result['error']} ---", "SYSTEM_ERROR")
                    current_history.append(f"System Note: {result['error']}. Retrying the turn.")
                    turn -= 1
                    continue

                decision = result["decision"]
                # --- Process the decision ---
                if "final_answer" in decision:
                    final_answer = decision["final_answer"]
                    # --- Save interaction to long-term memory ---
                    if ENABLE_LONG_TERM_MEMORY and self.memory:
                        self.memory.add_memory(
                            user_prompt=user_prompt,
                            final_answer=final_answer
                        )
                        log_and_print(f"--- Interaction saved to long-term memory ---", "SYSTEM")
                    log_and_print(f"--- Task completed. Clearing memory for next task. ---", "SYSTEM")
                    # --- The central fix: Contextual memory management ---
                    # Instead of deleting everything, we keep only the last context.
                    final_user_prompt_for_history = f"User: {user_prompt}"
                    final_bot_answer_for_history = f"AI: {final_answer}"
                    current_history.clear() # First clear everything
                    current_history.append(final_user_prompt_for_history) # Add the user's question
                    current_history.append(final_bot_answer_for_history) # Add the bot's answer
                    response_payload = {"reply": final_answer}
                    if temp_download_link:
                        # If a file was created along the way, attach it to the final answer
                        response_payload["download_link"] = temp_download_link
                        response_payload["message"] = final_answer 
                        agent_log(f">>> execute_task END - Returning with download_link", "SYSTEM")
                        return response_payload # Return the dictionary so Flask handles it
                    agent_log(f">>> execute_task END - Returning final answer", "SYSTEM")
                    return final_answer
                if "confirmation_request" in decision:
                    log_and_print(f"confirmation_request: : {json.dumps(decision['confirmation_request'], ensure_ascii=False)}")
                    agent_log(">>> execute_task END - Returning confirmation_request", "SYSTEM")
                    if execution_mode == 'silent':
                        # We're in silent mode, cannot ask! Return an error.
                        error_message = "SILENT MODE ERROR: AI attempted to ask a clarifying question. The task is not fully autonomous."
                        log_and_print(error_message, "SYSTEM_ERROR")
                        return {"user_facing_error": error_message}
                    # The AI is requesting approval for a dangerous action
                    request_data = decision["confirmation_request"]
                    # Add the information to history so the AI remembers what it asked
                    current_history.append(f"AI seeking confirmation: {request_data}")
                    # Return the approval request to the Frontend
                    return decision
                if "clarification_question" in decision:
                    log_and_print(f"--- AI is asking for more information: {decision['clarification_question']} ---", "SYSTEM")
                    agent_log(">>> execute_task END - Returning clarification_question", "SYSTEM")
                    if execution_mode == 'silent':
                        # We're in silent mode, cannot ask! Return an error.
                        error_message = "SILENT MODE ERROR: AI attempted to ask a clarifying question. The task is not fully autonomous."
                        log_and_print(error_message, "SYSTEM_ERROR")
                        return {"user_facing_error": error_message}
                    question = decision["clarification_question"]
                    current_history.append(f"AI: {question}") # Add the AI's question to history
                    return question
                elif "tool_name" in decision and "parameters" in decision:
                    tool_name = decision["tool_name"]
                    parameters = decision["parameters"]
                    log_and_print(f"--- ACTION: Bot invokes tool `{tool_name}` with parameters: {parameters} ---", "SYSTEM")
                    current_history.append(f"Tool Call: {json.dumps(decision, ensure_ascii=False)}")
                    tool_result_str = self._execute_tool_with_context(tool_name, parameters, chat_context=chat_context)
                    log_and_print(f"--- OBSERVATION: Tool results: {tool_result_str} ---", "SYSTEM")
                    # --- The critical fix: Check if the tool is requesting user input ---
                    try:
                        tool_result_obj = json.loads(tool_result_str)
                        if "download_link" in tool_result_obj:
                            temp_download_link = tool_result_obj["download_link"]
                            log_and_print(f"--- Download link captured: {temp_download_link} ---", "SYSTEM")
                        if tool_name == "generate_daily_briefing":
                            log_and_print("--- Morning report created. Returning it directly to the user. ---", "SYSTEM")
                            current_history.clear()
                            return tool_result_obj["briefing"]
                        # Check if this is an end-of-day report
                        elif tool_name == "generate_end_of_day_report":
                            log_and_print("--- End of day report created. Returning it directly to the user. ---", "SYSTEM")
                            current_history.clear()
                            return tool_result_obj["report"]
                        # Check if this is a message draft
                        elif tool_name == "draft_message_universal":
                            log_and_print("--- Message draft created. Returning it directly to the user. ---", "SYSTEM")
                            current_history.clear()
                            return tool_result_obj["drafted_message"]
                        elif tool_name == "visualize_data":
                            # Check if the graph was created successfully and has image data
                            if tool_result_obj.get("status") == "Success":
                                log_and_print("--- Graph created. Returning it directly to the user. ---", "SYSTEM")
                                current_history.clear()  # Clear memory for the next task
                                # Return the entire result object, the frontend knows how to handle it
                                return tool_result_obj
                        elif tool_name == "send_simple_notification":
                            # Check if the graph was created successfully and has image data
                            log_and_print("--- Reminder created. Returning directly to the user. ---", "SYSTEM")
                            current_history.clear()  # Clear memory for the next task
                            # Return the entire result object, the frontend knows how to handle it
                            return tool_result_obj
                        elif tool_name == "add_scheduled_job":
                            # Check if the graph was created successfully and has image data
                            log_and_print("--- Reminder created. Returning directly to the user. ---", "SYSTEM")
                            current_history.clear()  # Clear memory for the next task
                            # Return the entire result object, the frontend knows how to handle it
                            if ("message" in tool_result_obj):
                                return tool_result_obj["message"]
                            else:
                                return tool_result_obj["user_facing_error"]
                        elif tool_name == "perform_advanced_research":
                            # Check if the graph was created successfully and has image data
                            log_and_print("--- Reminder created. Returning directly to the user. ---", "SYSTEM")
                            current_history.clear()  # Clear memory for the next task
                            # Return the entire result object, the frontend knows how to handle it
                            if ("report" in tool_result_obj):
                                return tool_result_obj["report"]
                            else:
                                return tool_result_obj["error"]
                        elif tool_name == "add_user_response_to_mission_log" or tool_name == "autonomously_advance_mission" or tool_name == "create_mission" or tool_name == "update_mission_log":
                            # Check if the graph was created successfully and has image data
                            log_and_print("--- Reminder created. Returning directly to the user. ---", "SYSTEM")
                            current_history.clear()  # Clear memory for the next task
                            # Return the entire result object, the frontend knows how to handle it
                            if ("status" in tool_result_obj):
                                return tool_result_obj["status"]
                            else:
                                return tool_result_obj["error"]
                        if isinstance(tool_result_obj, dict) and tool_result_obj.get("status") == "Awaiting Input":
                            # We found a question for the user! Exit the thinking loop and return the question immediately.
                            log_and_print("--- ACTION: Returning question to user and waiting for answer ---", "SYSTEM")
                            return tool_result_obj
                        if isinstance(tool_result_obj, dict) and "error" in tool_result_obj:
                            log_and_print("--- WARNING: Tool returned an error. Retrying the step. ---", "SYSTEM")
                            turn -= 1
                            current_history.append(f"Tool Output: {tool_result_str}")
                            continue # Skip the rest of the loop and start the next iteration
                    except (json.JSONDecodeError, TypeError):
                        # The result is not JSON or not in expected format, continue as if it's a regular text result
                        pass
                    # If it's not a question, add the result to history and continue thinking normally
                    current_history.append(f"Tool Output: {tool_result_str}")
                else:
                    log_and_print(f"ERROR: Invalid decision format from AI: {json.dumps(decision, ensure_ascii=False, default=str)}", "ERROR")
                    current_history.append("System Note: Your decision format was invalid.")
                    continue
            # If the loop ends without a solution
            log_and_print("--- No solution reached after several rounds, clearing memory. ---", "SYSTEM")
            current_history.clear()
            return "I couldn't reach a final answer after several attempts. Would you like to try rephrasing the request?"
    def run_scheduled_task(self):
        """
        This method is invoked by the scheduler. It reads the task from the configuration
        and invokes the main task engine.
        """
        if not self.config.get('schedule_info') or not self.config['schedule_info'].get('task'):
            #logger.warning("Scheduled task ran, but no task description was found in the config.")
            return
        task_description = self.config['schedule_info']['task']
        log_and_print(f"\n--- Running scheduled task: '{task_description}' ---", "SYSTEM")
        # Use the main engine to execute the saved task
        response = self.execute_task(task_description)
        log_and_print(response, "BOT (Scheduled Task)")
        log_and_print(f"--- Scheduled task completed ---", "SYSTEM")
# =========================================================================================
#  Core Autonomous Logic (Tightly coupled with the agent)
# =========================================================================================
def update_mission_log(agent, mission_id: str, log_entry: dict):
    """
    Adds a new log entry (thought, action, result) to an existing super-mission.
    """
    missions = find_records(agent, 'missions', {'mission_id': mission_id})
    if not missions or isinstance(missions, str):
        return {"error": "Mission not found."}
    mission = missions[0]
    mission.get('log', []).append(log_entry)
    return update_record(agent, 'missions', {'mission_id': mission_id}, {'log': mission['log']})
def autonomously_advance_mission(agent, mission_id: str):
    """
    The "brain" of the AI. Checks a super-mission, decides the next step, executes it,
    and schedules itself to run again in the future.
    """
    with agent.AI_THINKING_LOCK: # Use the agent's lock here too if possible, or rely on the lock in execute_task
        consolidate_mission_log_if_needed(agent, mission_id)
        log_and_print(f"--- AI starts autonomous thinking round on mission {mission_id} ---", "SYSTEM")
        missions = find_records(agent, 'missions', {'mission_id': mission_id})
        if not missions or isinstance(missions, str): 
            return {"error": "Mission not found."}
        mission = missions[0]
        log = mission.get('log', [])
        tic = mission.get('task_information_center', {})
        scheduling_prefs = tic.get('scheduling_preferences', {})
        work_days = scheduling_prefs.get('work_days', [])    
        last_log_entry = log[-1] if log else {} # Accidental protection if the log is empty
        decision_text = "No decision generated yet" 
        should_wait = False # Flag to check if we stopped for waiting
        next_check_time = None
        now = datetime.now()
        # --- Start: New tracking logic ---
        # We'll check if the function was triggered by a reminder and the user still hasn't responded.
        if log and DEBUG == False:
            now = datetime.now()
            # Scenario 1: Waiting for response from the main user
            if last_log_entry.get('type') == 'user_question':
                should_wait = True
                log_and_print(f"--- Internal wait: User hasn't yet answered the question in mission {mission_id}. ---", "SYSTEM")
                # Send a reminder to the main user via Telegram
                reminder_message = f"Hey, just reminding you that in mission '{mission.get('title')}', I'm still waiting for your answer on: {last_log_entry.get('content')}"
                reminder_time = (now + timedelta(minutes=1)).isoformat()
                schedule_one_time_task(agent, f"Your goal is only to send a reminder, it doesn't matter what's written in MESSAGE, all you need to do is send it using the send_simple_notification function send_simple_notification(message='{reminder_message}')", reminder_time, send_notification=True)
                log_and_print(f"   - Internal reminder scheduled to be sent to the user.", "SYSTEM")
                last_question_time = datetime.fromisoformat(last_log_entry.get('timestamp'))
                days_waiting = (now - last_question_time).days
            # Scenario 2: Waiting for response from an external party
            elif last_log_entry.get('type') == 'waiting_for_external_response':
                should_wait = True
                content = last_log_entry.get('content', {})
                recipient_name = content.get('recipient_name')
                message_intent = content.get('message_intent')
                log_and_print(f"--- External wait: No update received from '{recipient_name}' in mission {mission_id}. ---", "SYSTEM")
                # Send a reminder to the external party via WhatsApp
                reminder_intent = f"A friendly reminder regarding: {message_intent}"
                reminder_time = (now + timedelta(minutes=1)).isoformat()
                reminder_time_me = (now + timedelta(minutes=3)).isoformat()
                # Here we schedule the *send action* itself
                schedule_one_time_task(agent, f"Your goal is only to send a reminder to that same person, it doesn't matter what's written in message_intent, all you need to do is send it using the handle_communication_request function handle_communication_request(recipient_name='{recipient_name}', message_intent='{reminder_intent}')", reminder_time, send_notification=True)
                #schedule_one_time_task(agent, f"Your goal is only to send a reminder, it doesn't matter what's written in MESSAGE, all you need to do is send it using send_simple_notification send_simple_notification(message='{reminder_intent}')", reminder_time_me, send_notification=True)
                log_and_print(f"   - External reminder scheduled to be sent to '{recipient_name}'.", "SYSTEM")
                # Schedule the next check for tomorrow (to send another reminder if still no answer)
                last_question_time = datetime.fromisoformat(last_log_entry.get('timestamp'))
                days_waiting = (now - last_question_time).days
            if should_wait:
                # Smart time calculation
                if days_waiting > 5:
                    # Emergency - checking tomorrow
                    base_time = now + timedelta(days=1)
                elif work_days:
                    # According to work days
                    next_check_time  = get_next_scheduled_date(work_days)
                else:
                    # Default
                    if now.hour < 8: # If middle of the night - check tomorrow morning at 10
                        base_time = now.replace(hour=10, minute=0, second=0)
                    elif now.hour >= 23: # If end of night - check tomorrow at 10
                        base_time = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0)
                    else: # Just middle of the day - check tomorrow at the same time
                        base_time = now + timedelta(days=1)
                if not next_check_time:
                    next_check_time = base_time.isoformat()
                log_and_print(f"--- Mission on hold. Next check at {next_check_time} ---", "SYSTEM")
                follow_up_prompt = f"autonomously_advance_mission(mission_id='{mission_id}')"
                schedule_result = schedule_one_time_task(agent, follow_up_prompt, next_check_time, send_notification=False)
                if schedule_result and schedule_result.get("status") == "Success":
                    message = schedule_result.get("message", "")
                    task_id_match = re.search(r"task_id: (one-off-[a-f0-9]+)", message)
                    if task_id_match:
                        new_task_id = task_id_match.group(1)
                        update_record(agent, 'missions', {'mission_id': mission_id}, {'active_reminder_task_id': new_task_id})
                        log_and_print(f"   - Next follow-up check scheduled for tomorrow. ID: {new_task_id}.", "SYSTEM")
                return {"status": "all the user tasks were complited you dont need to do anything else just return final_answer with some context"}
        # --- End: New tracking logic ---
        internal_tools_to_hide = ['create_mission', 'update_mission_log', 'autonomously_advance_mission', 
                                'add_user_response_to_mission_log', 'perform_advanced_research', "find_quick_answer_online", "execute_search_multi_task_prompt",
                                "handle_communication_request", "set_work_schedule_template", "confirm_and_schedule_work_day"]
        tools_for_prompt = "\n".join([
            f"- `{name}`: {info.get('description', '')}"
            for name, info in TOOL_MAP.items() 
            if name not in internal_tools_to_hide
        ])
        planning_prompt = autonomously_advance_mission_planning_prompt.format(
            goal_description=mission.get('goal_description'),
            log_json=json.dumps(mission.get('log', []), ensure_ascii=False, indent=2),
            tools_for_prompt=tools_for_prompt,
            mission_state_json=json.dumps(tic.get('mission_state'), ensure_ascii=False, indent=2)
        )
        result = api_key_manager.safe_generate_content(planning_prompt, model_name=GEMINI_MODEL_NAME)
        if not result["success"]:
            log_and_print(f"AI returned an empty response. Scheduling a retry.", "WARNING")
            update_mission_log(agent, mission_id, {"timestamp": datetime.now().isoformat(), "type": "system", "content": f"AI response was empty. The mission will be re-evaluated later. Error: {result['error']}"})
            next_check_time = (datetime.now() + timedelta(minutes=5)).isoformat()
            schedule_one_time_task(agent, f"autonomously_advance_mission(mission_id='{mission_id}')", next_check_time, send_notification=False)
            return {"status": "all the user tasks were complited you dont need to do anything else just return final_answer with some context"}

        decision = result["decision"]
        decision_text = result["raw_text"]
        # Save the AI's raw decision (recommended to keep)
        update_mission_log(agent, mission_id, {"timestamp": datetime.now().isoformat(), "type": "ai_thought", "content": json.dumps(decision, ensure_ascii=False)})
        action = decision.get("action")
        if action == "tool_call":
            tool_name = decision.get("tool_name")
            parameters = decision.get("parameters", {})
            result = agent._execute_tool_with_context(tool_name, parameters, context_mission_id=mission_id)
            update_mission_log(agent, mission_id, {"timestamp": datetime.now().isoformat(), "type": "tool_result", "tool": tool_name, "content": json.dumps(json.loads(result), ensure_ascii=False)})
            return autonomously_advance_mission(agent, mission_id)
        elif action == "ask_user":
            question_text = decision.get("question")
            update_mission_log(agent, mission_id, {"timestamp": datetime.now().isoformat(), "type": "user_question", "content": question_text})
            last_user_input = ""
            for entry in reversed(log):
                if entry.get('type') == 'user_response':
                    last_user_input = entry.get('content', "")
                    break
            _operate_on_tic(agent, mission_id, user_input=last_user_input, ai_response=decision)
            # --- Start: Adding automatic (smart) reminder ---
            # 1. Extract scheduling preferences from TIC (the tic variable should be defined at the start of autonomously_advance_mission)
            scheduling_prefs = tic.get('scheduling_preferences', {})
            work_days = scheduling_prefs.get('work_days', [])
            # 2. Calculate the next check time
            if work_days and len(work_days) < 7:
                # Use the helper function to calculate the next date based on preferred days
                # If you want a reminder on the same day in the morning, leave remind_day_before=False
                check_time = get_next_scheduled_date(work_days, remind_day_before=False)
                log_and_print(f"   - AI asked a question. Scheduling smart follow-up based on prefs {work_days} for {check_time}.", "SYSTEM")
            else:
                # "Every day" or default
                base_time = now + timedelta(days=1)
                # Night check: If it's now after 23:00 or before 08:00 -> set to 10:00 AM
                if now.hour >= 23 or now.hour < 8:
                    base_time = base_time.replace(hour=10, minute=0, second=0)
                check_time = base_time.isoformat()
                log_and_print(f"   - AI asked a question. Scheduling standard follow-up for tomorrow.", "SYSTEM")
            follow_up_prompt = f"autonomously_advance_mission(mission_id='{mission_id}')"
            if DEBUG == False:
                # Schedule the check
                schedule_result = schedule_one_time_task(agent, follow_up_prompt, check_time, send_notification=False)
                # Save the reminder ID in the mission so we can delete it later
                if schedule_result and schedule_result.get("status") == "Success" and "message" in schedule_result:
                    # 1. Get the full confirmation message
                    message = schedule_result.get("message")
                    # 2. Extract the task_id from the message. It's found after the string "task_id: "
                    task_id_parts = message.split("task_id: ")
                    if len(task_id_parts) > 1:
                        task_id = task_id_parts[-1].strip()
                        if task_id:
                            # 3. If extraction succeeded, perform the update normally
                            update_record(agent, 'missions', {'mission_id': mission_id}, {'active_reminder_task_id': task_id})
                            log_and_print(f"   - Follow-up task created with ID: {task_id}", "SYSTEM")
        elif action == "waiting_for_external_response":
            # Usually when users use this they tell it about an opinion you sent, so there's no point in it also sending a message
            recipient = decision.get("recipient_name")
            intent = decision.get("message_intent")
            context = decision.get("context", "")
            log_and_print(f"--- AI entering wait mode for response from '{recipient}' regarding: '{intent}' ---", "SYSTEM")
            last_user_input = ""
            for entry in reversed(log):
                if entry.get('type') == 'user_response':
                    last_user_input = entry.get('content', "")
                    break
            _operate_on_tic(agent, mission_id, user_input=last_user_input, ai_response=decision)
            # Step 1: Create a log entry indicating the start of the wait
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "type": "waiting_for_external_response",
                "content": {
                    "recipient_name": recipient,
                    "message_intent": intent,
                    "context": context
                }
            }
            update_mission_log(agent, mission_id, log_entry)
            # Step 2: Schedule the first follow-up check for tomorrow (logic is identical to ask_user)
            scheduling_prefs = tic.get('scheduling_preferences', {})
            work_days = scheduling_prefs.get('work_days', [])
            # Usually when users use this they tell it about an opinion you sent, so there's no point in it also sending a message
            #reminder_intent = f"A friendly reminder regarding: {intent}"
            #reminder_time = (now + timedelta(minutes=1)).isoformat()
            # Here we schedule the *send action* itself
            #schedule_one_time_task(agent, f"Your goal is only to send a reminder, it doesn't matter what's written in MESSAGE, all you need to do is send it using send_simple_notification send_simple_notification(message='{reminder_intent}')", reminder_time_me, send_notification=True)
            # If there's a days list, but it contains fewer than 7 days -> those are specific days.
            # If the list is empty, or contains 7 days (or more) -> it's considered "every day", and then we go to else.
            # 2. Calculate the next check time
            if work_days and len(work_days) < 7:
                # Use the helper function to calculate the next date based on preferred days
                # If you want a reminder on the same day in the morning, leave remind_day_before=False
                check_time = get_next_scheduled_date(work_days, remind_day_before=False)
                log_and_print(f"   - AI asked a question. Scheduling smart follow-up based on prefs {work_days} for {check_time}.", "SYSTEM")
            else:
                # "Every day" or default
                base_time = now + timedelta(days=1)
                # Night check: If it's now after 23:00 or before 08:00 -> set to 10:00 AM
                if now.hour >= 23 or now.hour < 8:
                    base_time = base_time.replace(hour=10, minute=0, second=0)
                check_time = base_time.isoformat()
                log_and_print(f"   - AI asked a question. Scheduling standard follow-up for tomorrow.", "SYSTEM")
            if DEBUG == False:
                follow_up_prompt = f"autonomously_advance_mission(mission_id='{mission_id}')"
                schedule_result = schedule_one_time_task(agent, follow_up_prompt, check_time, send_notification=False)
                # Step 3: Save the reminder ID so we can cancel it
                if schedule_result and schedule_result.get("status") == "Success" and "message" in schedule_result:
                    message = schedule_result.get("message")
                    task_id = message.split("task_id: ")[-1].strip()
                    if task_id:
                        update_record(agent, 'missions', {'mission_id': mission_id}, {'active_reminder_task_id': task_id})
                        log_and_print(f"   - Daily follow-up check scheduled. ID: {task_id}", "SYSTEM")
            # --- End: Adding automatic reminder ---
            # Stop and wait for user response
        elif action == "complete_mission":
            update_record(agent, 'missions', {'mission_id': mission_id}, {'status': 'completed'})
            update_records_by_query(
                agent,
                'projects',
                {'mission_id': mission_id}, # Finds the mission's projects
                {'mission_id': None}        # Detaches them from the mission
            )
            log_and_print(f"   - Related projects became regular projects and will remain active.", "SYSTEM")
            update_mission_log(agent, mission_id, {"timestamp": datetime.now().isoformat(), "type": "system", "content": f"Mission completed. Reason: {decision.get('reason')}"})
            delete_record(agent, 'missions', {'mission_id': mission_id})
        else:
            log_and_print(f"--- AI returned an unknown action: '{action}'. Scheduling a retry for tomorrow. ---", "SYSTEM_ERROR")
            next_check_time = (datetime.now() + timedelta(days=1)).isoformat()
            schedule_one_time_task(agent, f"autonomously_advance_mission(mission_id='{mission_id}')", next_check_time, send_notification=False)
            update_mission_log(agent, mission_id, {"timestamp": datetime.now().isoformat(), "type": "system", "content": f"AI did not perform a recognized action. Next automatic check scheduled for {next_check_time}"})

        return {"status": "all the user tasks were complited you dont need to do anything else just return final_answer with some context"}
def execute_flow_run(agent, run_id: str):
    """The automation engine for running flow blueprints."""
    # ... (The Flows engine code remains here) ...
    pass # Placeholder
# =========================================================================================
#  Helper functions for the core logic
# =========================================================================================
def _operate_on_tic(agent, mission_id: str, user_input, ai_response):
    """
    "TIC Operator": Analyzes the last message and produces the updated version of the entire TIC.
    Includes a Retry mechanism in case of JSON error.
    """
    mission_records = find_records(agent, 'missions', {'mission_id': mission_id})
    if not mission_records or isinstance(mission_records, str):
        log_and_print(f"--- TIC Operator: Mission with ID {mission_id} not found ---", "ERROR")
        return
    mission = mission_records[0]
    current_tic = mission.get('task_information_center')
    goal = mission.get('goal_description')
    operator_prompt = _operate_on_tic_promt.format(
        goal=goal,
        current_tic_json=json.dumps(current_tic, ensure_ascii=False, indent=2),
        user_input=user_input,
        ai_response=ai_response
    )
    # --- Centralized failsafe via safe_generate_content ---
    result = api_key_manager.safe_generate_content(operator_prompt, model_name=GEMINI_MODEL_NAME)
    if not result["success"]:
        log_and_print(f"--- TIC Operator: Failed to get valid response. Error: {result['error']} ---", "ERROR")
        return
    new_tic = result["decision"]
    # --- Continue original logic (only if new_tic exists) ---
    if new_tic:
        # Verify the structure is valid before saving
        if 'mission_state' in new_tic and 'next_actionable_task' in new_tic:
            # If the received mission_state is an empty object, update only the next task
            if not new_tic['mission_state']: # In Python, empty object {} evaluates to False
                # Build an update object that will preserve the existing mission_state
                tic_for_update = {
                    "mission_state": current_tic.get('mission_state', {}),  # Use the existing mission_state
                    "next_actionable_task": new_tic['next_actionable_task']   # Update the next task
                }
                # Update the record with the new object we built
                update_record(agent, 'missions', {'mission_id': mission_id}, {'task_information_center': tic_for_update})
                log_and_print(f"--- TIC updated (task only). Next action: {tic_for_update.get('next_actionable_task')} ---", "SYSTEM")
            else:
                # If mission_state is not empty, perform a full update normally
                update_record(agent, 'missions', {'mission_id': mission_id}, {'task_information_center': new_tic})
                log_and_print(f"--- TIC updated. Next action: {new_tic.get('next_actionable_task')} ---", "SYSTEM")
def consolidate_mission_log_if_needed(agent, mission_id: str):
    """
    Checks the mission log length. If there are more than 7 user responses, it compresses
    the entire history into one executive summary and saves it in place of the old log.
    Keeps the last message to maintain conversation continuity.
    """
    missions = find_records(agent, 'missions', {'mission_id': mission_id})
    if not missions: return
    mission = missions[0]
    log = mission.get('log', [])
    # Count user interactions
    user_interaction_count = sum(1 for entry in log if entry.get('type') == 'user_response')
    # Compression threshold (per your request: above 7)
    if user_interaction_count > 7:
        log_and_print(f"--- Performing memory compression for mission {mission_id} (found {user_interaction_count} user messages) ---", "SYSTEM")
        # We want to summarize everything except the last message (if it's from the user),
        # so the AI treats it as the current "trigger" for action.
        last_entry = log[-1]
        history_to_summarize = log[:-1] if log else []
        # If there's no history to summarize (short case), exit
        if not history_to_summarize:
            return
        summarization_prompt = consolidate_mission_log_if_needed_promt.format(
            history_json=json.dumps(history_to_summarize, ensure_ascii=False, indent=2)
        )
        # Send to LLM for summarization
        response = api_key_manager.generate_content(summarization_prompt, model_name=GEMINI_MODEL_NAME)
        summary_text = response if isinstance(response, str) else (response.text if hasattr(response, 'text') else str(response))
        summary_text = summary_text.strip()
        # Create a new log
        new_log = [
            {
                "timestamp": datetime.now().isoformat(),
                "type": "system_summary",
                "content": f"--- Previous history summary (memory compression) ---\n{summary_text}"
            },
            last_entry # Keep the last one so the AI can respond to it
        ]
        # Update the database
        update_record(agent, 'missions', {'mission_id': mission_id}, {'log': new_log})
        log_and_print(f"   - Log compressed successfully. New length: {len(new_log)} entries.", "SYSTEM")

