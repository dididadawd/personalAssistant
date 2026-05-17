# app/api/missions_routes.py
"""
API Routes for Mission Management endpoints.
Handles mission file uploads, side chats, and mission interactions.
"""

from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from datetime import datetime
import os
import uuid
import json
import re
import textwrap
import threading

# Import database functions
from ..db.json_manager import find_records, add_record, update_record

# Import utilities
from ..utils.logger import log_and_print
from ..services.file_handler import extract_text_from_file, analyze_document_and_update_tic, inspect_file_content

# Import agent and tools
from ..core.agent import active_agent as agent_class
from ..core.agent import _operate_on_tic
from ..core.llm_provider import api_key_manager
from ..core.tools_registry import TOOL_MAP

from ..services.main_service import add_user_response_to_mission_log
from ..core.agent import autonomously_advance_mission

from ..config import Config

GEMINI_MODEL_NAME = Config.GEMINI_MODEL_NAME


def generate_semantic_filename(content_snippet: str) -> str:
    """
    Uses AI to generate an English filename describing the content.
    """
    prompt = textwrap.dedent(f"""
        Analyze the following document content snippet.
        Generate a short, descriptive, and safe filename in English (using snake_case) that accurately describes this content.
        
        **Rules:**
        1. Use ONLY English letters, numbers, and underscores.
        2. Keep it under 5 words.
        3. Do NOT include the file extension.
        4. Output ONLY the filename string. No markdown, no explanations.

        **Content Snippet:**
        "{content_snippet[:2000]}"
        
        **Filename:**
    """)

    try:
        response = api_key_manager.generate_content(prompt, model_name=GEMINI_MODEL_NAME)
        clean_name = response.strip().replace('"', '').replace("'", "").replace("`", "").strip()
        clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', clean_name)
        clean_name = re.sub(r'_+', '_', clean_name)
        return clean_name if clean_name else "document"
    except Exception as e:
        print(f"Error generating filename: {e}")
        return "uploaded_file"

# Create blueprint
missions_bp = Blueprint('missions', __name__)

# Global reference to the active agent instance
_active_agent = None

# Configuration
UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx'}

def set_active_agent(agent):
    global _active_agent
    _active_agent = agent

def get_agent():
    if _active_agent:
        return _active_agent
    from flask import current_app
    return current_app.config.get('ACTIVE_AGENT')

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# =========================================================================================
#  Mission Routes
# =========================================================================================

@missions_bp.route("/upload_file", methods=["POST"])
def upload_mission_file():
    active_agent = get_agent()
    if not active_agent:
        return jsonify({"error": "Agent not loaded"}), 500

    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
        
    file = request.files['file']
    mission_id = request.form.get('mission_id')
    
    if file.filename == '' or not mission_id:
        return jsonify({"error": "No selected file or missing mission_id"}), 400
        
    if file and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        _, ext = os.path.splitext(original_filename)
        
        temp_uuid = uuid.uuid4().hex
        temp_filename = f"temp_{temp_uuid}{ext.lower()}"
        
        mission_dir = os.path.join(UPLOAD_FOLDER, mission_id)
        os.makedirs(mission_dir, exist_ok=True)
        
        temp_file_path = os.path.join(mission_dir, temp_filename)
        file.save(temp_file_path)
        
        try:
            content_text = extract_text_from_file(temp_file_path)
            
            if not content_text or len(content_text.strip()) < 5:
                final_filename = f"unprocessed_{original_filename}"
                final_file_path = os.path.join(mission_dir, final_filename)
                os.rename(temp_file_path, final_file_path)
                log_and_print("⚠️ Text extraction failed. Original file saved unprocessed.", "WARNING")
            else:
                log_and_print("--- 🏷️ Generating filename... ---", "SYSTEM")
                semantic_name_base = generate_semantic_filename(content_text)
                
                final_filename = f"{semantic_name_base}.txt"
                final_file_path = os.path.join(mission_dir, final_filename)
                
                if os.path.exists(final_file_path):
                    final_filename = f"{semantic_name_base}_{uuid.uuid4().hex}.txt"
                    final_file_path = os.path.join(mission_dir, final_filename)

                with open(final_file_path, 'w', encoding='utf-8') as f:
                    f.write(content_text)
                
                try:
                    os.remove(temp_file_path)
                    log_and_print(f"--- 🗑️ Original file ({ext}) deleted and replaced with full text version. ---", "SYSTEM")
                except Exception as e:
                    print(f"Failed to delete temp file: {e}")

            log_and_print(f"--- 📁 Final save: '{final_filename}' ---", "SYSTEM")
            
            result = analyze_document_and_update_tic(active_agent, mission_id, final_file_path, final_filename)
            return jsonify(result)

        except Exception as e:
            log_and_print(f"Error processing file upload: {e}", "ERROR")
            return jsonify({"error": str(e)}), 500
    
    return jsonify({"error": "File type not allowed"}), 400


@missions_bp.route("/update_tic_from_side_chat", methods=["POST"])
def update_tic_from_side_chat_endpoint():
    active_agent = get_agent()
    if not active_agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json
        mission_id = data.get("mission_id")
        content_to_add = data.get("content_to_add")

        if not mission_id or not content_to_add:
            return jsonify({"error": "Missing mission_id or content_to_add"}), 400
        
        missions = find_records(active_agent, 'missions', {'mission_id': mission_id})
        if not missions:
            return jsonify({"error": "Mission not found"}), 404
        
        mission = missions[0]
        side_history = mission.get('side_chat_history', [])

        context_prompt = textwrap.dedent(f"""
            You are analyzing a side-conversation history to understand the User's Intent.
            The user decided to save a specific piece of content (The Output) to the project memory.
            
            **The Output to Save:**
            "{content_to_add}"

            **Full Side-Chat History:**
            {json.dumps(side_history, ensure_ascii=False)}

            **Your Task:**
            Summarize exactly **WHAT the user asked for** or **WHAT the user wanted to achieve** that resulted in this output.
            Collect all the user's requirements/instructions from the chat into one clear, concise sentence in Hebrew.
            
            Example:
            If user asked "Draft an email", then "Change tone", then "Add details" -> 
            Output: "The user requested drafting an email with specific details and a certain tone."

            **Response (Hebrew text ONLY):**
        """)

        try:
            response = api_key_manager.generate_content(context_prompt, model_name=GEMINI_MODEL_NAME)
            user_intent_summary = response.strip()
        except Exception:
            user_intent_summary = "The user requested saving this information from a side consultation."

        log_and_print(f"--- 🧠 TIC Update Intent: {user_intent_summary} ---", "SYSTEM")

        _operate_on_tic(active_agent, mission_id, user_intent_summary, content_to_add)
        
        updated_missions = find_records(active_agent, 'missions', {'mission_id': mission_id})
        new_tic = {}
        if updated_missions:
            new_tic = updated_missions[0].get('task_information_center', {})

        return jsonify({
            "status": "Success", 
            "message": "TIC updated successfully.",
            "updated_tic": new_tic 
        })

    except Exception as e:
        log_and_print(f"Error updating TIC directly from side chat: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@missions_bp.route("/side_chat", methods=["POST"])
def handle_mission_side_chat():
    active_agent = get_agent()
    if not active_agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json
        mission_id = data.get("mission_id")
        user_query = data.get("query")

        if not mission_id or not user_query:
            return jsonify({"error": "Missing mission_id or query"}), 400

        missions = find_records(active_agent, 'missions', {'mission_id': mission_id})
        if not missions:
            return jsonify({"error": "Mission not found"}), 404
        
        mission = missions[0]
        side_history = mission.get('side_chat_history', [])
        
        timestamp = datetime.now().isoformat()
        side_history.append({
            "role": "user",
            "content": user_query,
            "timestamp": timestamp
        })

        tic = mission.get('task_information_center', {})
        goal = mission.get('goal_description', '')
        recent_logs = mission.get('log', [])[-5:]
        
        tools_to_hide = ['create_mission', 'update_mission_log', 'autonomously_advance_mission', 
                            'add_user_response_to_mission_log', 'perform_advanced_research', "find_quick_answer_online", "execute_search_multi_task_prompt",
                            "set_work_schedule_template", "confirm_and_schedule_work_day"]
        base_tools_desc = "\n".join([
            f"- `{name}`: {info.get('description', '')}"
            for name, info in TOOL_MAP.items() 
            if name not in tools_to_hide
        ])

        special_tools_desc = """
        there are 3 more special tools:
        "execute_search_multi_task_prompt": {{
                    "description": (
                        "This function is used to request direct information from the internet — for example, contact details of a place or any other fact-based data that has a clear, simple answer. It can also handle multiple small questions within a single tool call; the ‘prompt’ parameter may include several tasks (e.g., asking for both the contact details of a hospital and information about a specific house). The function then retrieves and returns the relevant information accordingly."
                    )
                }},
        "find_quick_answer_online": {{
            "description": (
                "Use this when you need to gather detailed information about a task that isn’t clearly defined."
                "it gets query as paramet with the quation that we want to ask"
            )
        }}
        "inspect_file_content": {{
            "target_function": inspect_file_content,
            "static_params": {{}}, # mission_id is injected automatically by your system
            "description": (
                "Use this tool when you need to answer a specific question based on a file stored in the mission's 'files_index'. "
                "Instead of guessing, this tool opens the full file content from the disk and extracts the exact answer. "
                "Requires 'filename' (copy exactly from files_index) and 'specific_question'."
            )
        }}
        """
        
        full_tools_desc = base_tools_desc + special_tools_desc
        
        available_tables = active_agent.get_available_tables()
        tables_list_str = ", ".join(available_tables)

        current_run_history = [] 
        max_turns = 5
        turn = 0
        final_answer = None
        
        while turn < max_turns:
            turn += 1
            
            prompt = textwrap.dedent(f"""
                You are a smart internal consultant for a specific mission.
                Your goal is to answer the User's Question accurately or perform the requested update.
                after you did your task you can return final_answer.
                
                **Mission Context:**
                - Goal: "{goal}"
                - State (TIC): {json.dumps(tic, ensure_ascii=False)}
                
                **Side-Chat History:**
                {json.dumps(side_history, ensure_ascii=False)}
                
                **Available Tools:**
                {full_tools_desc}
                
                **Available Database Tables:**
                {tables_list_str}
                
                **Current Thought Trace:**
                {json.dumps(current_run_history, ensure_ascii=False)}
                
                **INSTRUCTIONS:**
                1. Use the Current Thought Trace to determine the next required action.
                2. Answer in Hebrew.

                **OUTPUT FORMAT (JSON ONLY):**
                Option A (Call Tool):
                {{ "action": "tool_call", "tool_name": "name", "parameters": {{ ... }} }}
                
                Option B (Final Answer):
                {{ "action": "final_answer", "content": "Your text here" }}
            """)
            
            try:
                response = api_key_manager.generate_content(prompt, model_name=GEMINI_MODEL_NAME)
                raw_text = response.strip()
                
                decision_text = raw_text
                start_index = raw_text.find('{')
                end_index = raw_text.rfind('}')
                if start_index != -1 and end_index != -1:
                    decision_text = raw_text[start_index : end_index + 1]
                
                decision = json.loads(decision_text)
                action = decision.get("action")
                
                if action == "final_answer":
                    final_answer = decision.get("content")
                    break 
                
                elif action == "tool_call":
                    tool_name = decision.get("tool_name")
                    params = decision.get("parameters", {})
                    
                    tool_result = active_agent._execute_tool_with_context(tool_name, params, context_mission_id=mission_id)
                    
                    current_run_history.append({
                        "step": f"Turn {turn}",
                        "thought": f"Calling tool {tool_name}",
                        "tool_output": tool_result
                    })
                    
                else:
                    final_answer = raw_text
                    break

            except Exception as e:
                log_and_print(f"Side chat thinking error: {e}", "ERROR")
                current_run_history.append({"error": str(e)})
        
        if not final_answer:
            final_answer = "I could not complete the action."

        side_history.append({
            "role": "bot",
            "content": final_answer,
            "timestamp": datetime.now().isoformat()
        })

        update_record(active_agent, 'missions', {'mission_id': mission_id}, {'side_chat_history': side_history})
        
        return jsonify({"status": "Success", "reply": final_answer, "updated_history": side_history})

    except Exception as e:
        log_and_print(f"Critical error in side chat: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@missions_bp.route('/clear_side_chat', methods=['POST'])
def clear_mission_side_chat():
    active_agent = get_agent()
    if not active_agent:
        return jsonify({"error": "Agent not loaded"}), 500
    try:
        data = request.json
        mission_id = data.get("mission_id")
        update_record(active_agent, 'missions', {'mission_id': mission_id}, {'side_chat_history': []})
        return jsonify({"status": "Success", "message": "Chat history cleared"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@missions_bp.route('/update_tic', methods=['POST'])
def update_mission_tic_endpoint():
    """
    Endpoint for manual update of the TIC (Task Information Center) from the interface.
    """
    active_agent = get_agent()
    if not active_agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json
        mission_id = data.get("mission_id")
        new_tic_data = data.get("tic_data")

        if not mission_id or new_tic_data is None:
            return jsonify({"error": "Missing mission_id or tic_data"}), 400

        result = update_record(active_agent, 'missions', {'mission_id': mission_id}, {'task_information_center': new_tic_data})
        
        if "error" in result:
            return jsonify(result), 500
            
        return jsonify({"status": "Success", "message": "TIC updated successfully"})

    except Exception as e:
        log_and_print(f"Error updating TIC: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@missions_bp.route('/response', methods=['POST'])
def handle_mission_user_response():
    """
    Dedicated endpoint for receiving user responses within autonomous missions.
    """
    active_agent = get_agent()
    if not active_agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json
        mission_id = data.get("mission_id")
        response_text = data.get("response_text")

        if not mission_id or not response_text:
            return jsonify({"error": "Missing mission_id or response_text"}), 400

        # Attempt to use the original function if imported successfully
        if 'add_user_response_to_mission_log' in globals():
            result = add_user_response_to_mission_log(active_agent, mission_id, response_text)
            return jsonify(result)
        else:
            return jsonify({"error": "Function add_user_response_to_mission_log not found in agent core"}), 500

    except Exception as e:
        log_and_print(f"Error in mission response handler: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@missions_bp.route('/retry_error', methods=['POST'])
def retry_last_mission_error():
    """
    Deletes the last error entry from the log and restarts the mission.
    """
    active_agent = get_agent()
    if not active_agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json
        mission_id = data.get("mission_id")

        if not mission_id:
            return jsonify({"error": "Missing mission_id"}), 400

        missions = find_records(active_agent, 'missions', {'mission_id': mission_id})
        if not missions or isinstance(missions, str):
            return jsonify({"error": "Mission not found"}), 404
        
        mission = missions[0]
        log = mission.get('log', [])

        if log and log[-1].get('type') == 'error':
            log.pop() 
            update_record(active_agent, 'missions', {'mission_id': mission_id}, {'log': log})
            
            log_and_print(f"--- 🔄 Error manually removed from mission {mission_id}. Retrying... ---", "SYSTEM")
            
            if 'autonomously_advance_mission' in globals():
                threading.Thread(target=autonomously_advance_mission, args=(active_agent, mission_id)).start()
                return jsonify({"status": "Success", "message": "Error removed and retrying..."})
            else:
                return jsonify({"error": "autonomously_advance_mission function not found"}), 500
        else:
            return jsonify({"error": "The last log entry is not an error, nothing to retry."}), 400

    except Exception as e:
        log_and_print(f"Error in retry handler: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@missions_bp.route('', methods=['GET'])
def get_missions():
    """
    Returns all active missions, sorted by creation date.
    """
    active_agent = get_agent()
    if not active_agent:
        return jsonify({"missions": []}), 500
    try:
        query = {
            'status': 'active',
            '$sort': {'created_at': -1} 
        }
        missions = find_records(active_agent, 'missions', query)
        
        if not missions or isinstance(missions, str):
            return jsonify({"missions": []})
            
        return jsonify({"missions": missions})
    except Exception as e:
        print(f"Error in get_missions: {e}")
        return jsonify({"missions": []}), 500


# Export the blueprint
__all__ = ['missions_bp', 'set_active_agent']