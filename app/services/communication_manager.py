# app/services/whatsapp_bot.py

import os
import json
import time
import textwrap
import urllib.parse
import webbrowser
import pyautogui
import pygetwindow as gw
import pyperclip
import keyboard
from datetime import datetime, timedelta, date
from typing import Any, Dict, Tuple, List
import re

# --- 1. Imports from our modules ---
from app.db.json_manager import find_records, update_record, delete_record
from app.utils.logger import log_and_print
from app.core.llm_provider import api_key_manager, GEMINI_MODEL_NAME
from app.services.google_workspace import list_calendar_events
from app.utils.system_state import get_system_state
from app.utils.date_helpers import get_current_datetime
from app.utils.llm_helpers import safe_extract_text
from app.db.query_parser import get_user_profile, _process_end_of_day_goals
from app.config import Config
# Import prompts from centralized system
from app.prompts.prompt_loader import (
    handle_communication_request_find_person,
    handle_communication_request_promt_personal,
    handle_communication_request_promt_group,
    generate_daily_briefing_promt,
    generate_end_of_day_report_promt
)
AGENT_NAME = "show_manager"
# --- Constants & Paths ---
script_dir = os.path.dirname(os.path.abspath(__file__))
PERSONAS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "personas")
kb_path = os.path.join(PERSONAS_DIR, AGENT_NAME, "knowledge_base")
STATE_FILE_PATH = os.path.join(kb_path, "system_state.json")

ADMIN_CHAT_ID = Config.ADMIN_CHAT_ID
ASYNC_LOOP = None
# =========================================================================================
#  1. Desktop Automation (WhatsApp)
# =========================================================================================
PROCRASTINATION_MARKER = "⏳"
POSTPONEMENT_COUNTER_PATTERN = re.compile(r"\(Postponed already (\d+) days\)")
POSTPONEMENT_COUNTER_TEMPLATE = "(Postponed already {} days)"
HEBREW_DAYS = {
    'Sunday': 'Sunday', 'Monday': 'Monday', 'Tuesday': 'Tuesday',
    'Wednesday': 'Wednesday', 'Thursday': 'Thursday', 'Friday': 'Friday', 'Saturday': 'Saturday'
}

def send_whatsapp_message(phone_number: str, message: str, name: str):
    try:
        encoded_message = urllib.parse.quote(message)
        url = f"whatsapp://send?phone={phone_number}&text={encoded_message}"
        webbrowser.open(url)
        
        print("Waiting for WhatsApp window to open and become active...")
        
        max_wait_time = 30
        start_time = time.time()
        is_whatsapp_active = False
        
        while not is_whatsapp_active:
            active_window = pyautogui.getActiveWindowTitle()
            
            # First verify that the WhatsApp window is in focus
            if active_window and "WhatsApp" in active_window:
                
                # Now look for the image that proves the chat has loaded
                try:
                    # Looking for the image you cropped on screen (confidence=0.8 allows slight pixel deviation)
                    button_location = pyautogui.locateOnScreen('chat_ready.png', confidence=0.8)
                    if button_location is not None:
                        is_whatsapp_active = True
                        break # Everything is ready, can exit the loop!
                except pyautogui.ImageNotFoundException:
                    pass # Image not yet on screen, continue waiting
                
            if time.time() - start_time > max_wait_time:
                return {"error": "Time elapsed - chat did not load."}
                
            time.sleep(1) # Check every second
            
        print("Chat loaded completely! Pressing enter.")
        time.sleep(2) # Check every second
        # Press enter to send (no need for long wait now since we're sure the screen is open)
        pyautogui.press('enter')
        time.sleep(1)
        
        return {"status": "Success", "message": f"Message dispatched to {name}"}
        
    except Exception as e:
        return {"error": f"Automation failed: {e}"}


def send_whatsapp_message_group(group_id: str, message: str, name: str):
    """
    Sends a WhatsApp message to a group using WhatsApp Desktop.
    Uses the 'keyboard' library for a bulletproof paste command that bypasses language issues.
    Uses image recognition to wait precisely for the chat to load.
    """
    try:
        url = f"whatsapp://chat?code={group_id}"
        webbrowser.open(url)

        log_and_print(f"Opening WhatsApp group {name}...")

        # 1. Wait for WhatsApp window to open and focus on it
        max_wait = 30
        start = time.time()
        window_found = False
        
        while not window_found:
            all_windows = gw.getWindowsWithTitle('WhatsApp')
            if all_windows:
                window = all_windows[0]
                try:
                    window.activate() # Strong command that brings the window to the front and focuses it
                except:
                    pass # Sometimes activate throws an error if the window is already focused, ignore
                window_found = True
                break

            if time.time() - start > max_wait:
                return {"error": "WhatsApp window did not open in time."}

            time.sleep(0.5)

        log_and_print("WhatsApp window is active. Waiting for chat to load (looking for image)...")
        
        # 2. Smart wait for chat to load using image recognition
        chat_loaded = False
        chat_load_start = time.time()
        chat_load_timeout = 20 # Wait up to 20 seconds for the chat itself to load
        
        while not chat_loaded:
            try:
                # Look for the image you prepared on screen. 
                # confidence=0.8 allows flexibility in recognition (requires opencv-python installed)
                location = pyautogui.locateOnScreen('chat_ready.png', confidence=0.8)
                if location is not None:
                    chat_loaded = True
                    break
            except pyautogui.ImageNotFoundException:
                # Image not yet on screen (loading), continue waiting
                pass
            except Exception as e:
                # Handle other screen recognition errors
                log_and_print(f"Image recognition error: {e}")
                pass
                
            if time.time() - chat_load_start > chat_load_timeout:
                return {"error": "WhatsApp window opened, but the chat interface failed to load in time."}
                
            time.sleep(0.5) # Check screen twice per second

        log_and_print("Chat loaded completely! Pasting message...")
        
        # Tiny half-second pause to ensure screen loading animation finished
        time.sleep(2)

        # 3. Copy message to clipboard - works perfectly with Hebrew
        pyperclip.copy(message)
        
        log_and_print("Pasting message using the 'keyboard' library...")

        # 🔥 The magic here: Use 'keyboard' library for pasting 🔥
        keyboard.press_and_release('ctrl+v')
        
        # Tiny pause to ensure paste completed before pressing enter
        time.sleep(1)

        # Send with pyautogui (this command is not affected by language)
        pyautogui.press('enter')

        time.sleep(1)

        return {
            "status": "Success",
            "message": f"WhatsApp message to group '{name}' was successfully dispatched."
        }

    except Exception as e:
        return {"error": f"Failed to send message: {e}"}



def handle_communication_request(agent, recipient_name: str, message_intent: str):
    """
    Advanced smart tool for managing all outgoing WhatsApp communication.
    The tool uses AI to select the most appropriate recipient ID,
    and the code retrieves full details from the DB and sends the message.
    """
    log_and_print(f"--- Starting smart communication request handling with semantic identification for '{recipient_name}' ---", "SYSTEM")
    log_and_print(f"   - Message intent: '{message_intent}'", "SYSTEM")

    # --- Step 1: Collect all possible recipients from the unified DB ---
    all_possible_recipients = find_records(agent, 'recipients', {})
    
    if not isinstance(all_possible_recipients, list) or not all_possible_recipients:
        return {"error": "The recipients database is empty. I have no one to message."}

    # --- Step 2: Run AI to select the correct recipient ID ---
    selection_prompt = handle_communication_request_find_person.format(
        recipient_name=recipient_name,
        recipients_json=json.dumps(all_possible_recipients, ensure_ascii=False, indent=2)
    )

    try:
        response = api_key_manager.generate_content(selection_prompt, model_name=GEMINI_MODEL_NAME)
        # *** Upgrade #2: We expect to receive a simple string, not JSON ***
        selected_recipient_id = safe_extract_text(response).strip().replace('"', '')

        # *** Upgrade #4: Check if AI determined the user doesn't exist in the database ***
        if selected_recipient_id == "NOT_FOUND":
            log_and_print(f"   - User not found: AI determined the recipient '{recipient_name}' does not exist in the database.", "SYSTEM")
            return {"error": f"The recipient you requested ('{recipient_name}') was not found in the contacts database. Please verify the name and try again."}

        if not selected_recipient_id:
            return {"error": f"The AI could not confidently select a recipient for '{recipient_name}'."}

        # *** Upgrade #3: Retrieve full data from the list based on the received ID ***
        selected_recipient_details = next((recipient for recipient in all_possible_recipients if recipient.get('recipient_id') == selected_recipient_id), None)

        if not selected_recipient_details:
            # Edge case: AI returned an ID that doesn't exist in the list
            return {"error": f"Internal error: The AI returned an invalid recipient ID ('{selected_recipient_id}')."}

        log_and_print(f"   - Semantic identification successful. Selected recipient: '{selected_recipient_details.get('name')}' (ID: {selected_recipient_id})", "SYSTEM")

    except Exception as e:
        return {"error": f"An error occurred during the AI recipient selection phase: {e}"}

    recipient_type = selected_recipient_details.get('type')
    if recipient_type == 'contact':
        drafting_prompt = handle_communication_request_promt_personal.format(
            recipient_name=selected_recipient_details.get('name'),
            message_intent=message_intent
        )
    else:
        drafting_prompt = handle_communication_request_promt_group.format(
            recipient_name=selected_recipient_details.get('name'),
            message_intent=message_intent
        )
    try:
        response = api_key_manager.generate_content(drafting_prompt, model_name=GEMINI_MODEL_NAME)
        drafted_message = safe_extract_text(response).strip()
        log_and_print(f"   - AI drafted the message: '{drafted_message}'", "SYSTEM")

        # --- Step 4: Send based on recipient type from full details ---
        recipient_type = selected_recipient_details.get('type')
        if recipient_type == 'contact':
            phone_number = selected_recipient_details.get('phone_number')
            if not phone_number: return {"error": f"Contact '{selected_recipient_details.get('name')}' is missing a phone number."}
            return send_whatsapp_message(phone_number=phone_number, message=drafted_message, name=selected_recipient_details.get('name'))
        
        elif recipient_type == 'group':
            group_id = selected_recipient_details.get('group_id')
            if not group_id: return {"error": f"Group '{selected_recipient_details.get('name')}' is missing a group_id."}
            return send_whatsapp_message_group(group_id, drafted_message, selected_recipient_details.get('name'))
        
        else:
            return {"error": "An unknown recipient type was found in the database."}

    except Exception as e:
        error_message = f"An error occurred after recipient selection: {e}"
        log_and_print(f"--- ❌ ERROR: {error_message} ---", "ERROR")
        return {"error": error_message}




def send_simple_notification(agent, message: str):
    """
    Sends a simple and direct text message to the user registered on Telegram.
    """
    if not agent or not agent.config:
        return {"error": "Agent not properly loaded."}

    target_chat_id = agent.config.get('telegram_chat_id')
    if not target_chat_id:
        return {"error": "No Telegram chat ID is registered for this agent."}

    if not message:
        return {"error": "Cannot send an empty message."}
        
    try:
        if ASYNC_LOOP and ASYNC_LOOP.is_running():
            log_and_print(f"--- ✅ Simple message sent to user: '{message}' ---", "SYSTEM")
            return {'message': message}
        else:
            return {"error": "Async event loop is not running. Cannot send notification."}
    except Exception as e:
        return {"error": f"Failed to send notification: {e}"}



def draft_message_universal(agent, purpose: str, context_data: dict, user_request_for_context: str):
    """
    Adaptive drafting tool. Treats the template as inspiration and adapts the content, structure, and tone
    to perfectly answer the user's new request.
    """
    log_and_print(f"--- Starting adaptive drafting for purpose '{purpose}' ---", "SYSTEM")

    # ... (the code for finding the appropriate template remains completely identical) ...
    all_templates = find_records(agent, 'message_templates', {})
    if not isinstance(all_templates, list) or not all_templates:
        return {"error": "Could not load any message templates."}
    matching_template = next((t for t in all_templates if t.get('purpose') == purpose), None)
    if not matching_template:
        # Here is the semantic backup mechanism we already wrote
        log_and_print(f"   - No exact template found, activating semantic search...", "SYSTEM")
        
        available_purposes = [t.get('purpose') for t in all_templates]
        
        # --- Start of critical fix in prompt ---
        matching_prompt = textwrap.dedent(f"""
            You are a selection expert. Your only job is to choose the single best option from a list.
            Analyze the "User's Goal" and select the most relevant "Template Name" from the provided list.

            **User's Goal:** "{purpose}"

            **Available Template Names:**
            {json.dumps(available_purposes, ensure_ascii=False)}

            **INSTRUCTIONS:**
            - Analyze the meaning of the user's goal.
            - Compare it to the meaning of each available template name.
            - Your response MUST be ONLY the name of the best matching template, as a plain string.
            - DO NOT add quotes, JSON formatting, or any extra text.

        """)
        # --- End of critical fix in prompt ---
        
        try:
            match_response = api_key_manager.generate_content(matching_prompt, model_name=GEMINI_MODEL_NAME)
            # Made the cleanup more aggressive to ensure we get only the text
            best_match_purpose = safe_extract_text(match_response).strip().replace('"', '').replace("`", "").replace("json", "").strip()
            
            matching_template = next((t for t in all_templates if t.get('purpose') == best_match_purpose), None)
            
            if not matching_template:
                # Added more detailed error message for debugging
                log_and_print(f"   - ERROR: Semantic match failed. AI returned '{best_match_purpose}', which is not in the template list.", "ERROR")
                return {"error": f"AI failed to find a suitable template. It suggested '{best_match_purpose}' which is invalid."}
            
            log_and_print(f"   - AI selected template '{best_match_purpose}' as a replacement.", "SYSTEM")
        except Exception as e:
            return {"error": f"AI error during template matching: {e}"}

    template_text = matching_template.get('content')
    final_purpose = matching_template.get('purpose')

    # --- The new and upgraded PROMPT ---
    drafting_prompt = textwrap.dedent(f"""
        You are an expert copywriter and strategic communicator. Your mission is to craft a new, personalized message in English that perfectly achieves the user's goal. You will use an existing template as a creative starting point, but you are empowered to adapt it significantly.

        **This is your creative brief:**

        **1. The Creative Inspiration (The Old Template):**
        This is your style guide and structural inspiration for a '{final_purpose}'. 
        **CRITICAL: You are NOT required to follow it word-for-word.** Adapt, rewrite, add, or remove sections as needed to best fit the 'New Mission'.
        ```
        {template_text}
        ```

        **2. The Key Facts (Context Data):**
        These are the non-negotiable facts that MUST be accurately included in the final message.
        ```json
        {json.dumps(context_data, ensure_ascii=False, indent=2)}
        ```

        **3. The New Mission (The User's Actual Goal):**
        This is the user's ultimate goal. Your final text must fulfill this request. Analyze it for any changes in tone, audience, or offer compared to the original template.
        ```
        {user_request_for_context}
        ```

        **Your Thought Process (Before Writing):**
        1.  Analyze the 'New Mission'. Is the situation different from the template's original purpose? (e.g., an existing client vs. a new one? A request vs. a statement?).
        2.  Based on the differences, how should you adapt the tone and content of the 'Creative Inspiration'?
        3.  Integrate all the 'Key Facts' naturally into the new text.
        4. my name is יהלי

        **Final Output:** Your response must be ONLY the final, complete, and perfectly adapted message text.
    """)

    try:
        final_response = api_key_manager.generate_content(drafting_prompt, model_name=GEMINI_MODEL_NAME)
        return {"status": "Success", "drafted_message": safe_extract_text(final_response)}
    except Exception as e:
        return {"error": f"An error occurred during adaptive message drafting: {e}"}


# get_system_state is imported from system_state_manager

def _get_daily_data(agent: Any, today_str: str) -> Tuple[List[Dict], List[Dict]]:
    """Collects events and tasks for today."""
    calendar_events_raw = list_calendar_events(agent, days_ahead=1)
    all_events = calendar_events_raw.get('events', [])
    today_events = [event for event in all_events if event.get('start_time', '').startswith(today_str)]
    
    today_tasks_raw = find_records(agent, 'tasks', {'due_date': today_str, 'status': 'pending'})
    today_tasks = today_tasks_raw if isinstance(today_tasks_raw, list) else []
    
    return today_events, today_tasks



def get_actionable_goal_insights(agent):
    """
    Internal helper function (not a tool). Scans all active goals and returns
    a detailed list of objects, each containing all knowledge needed for the AI
    to generate a strategic recommendation.
    """
    active_goals = find_records(agent, 'goals', {'status': 'active'})
    if not isinstance(active_goals, list) or not active_goals:
        return []

    insights = []
    for goal in active_goals:
        # Package all relevant information about the goal in one clean object
        insights.append({
            "title": goal.get('title'),
            "description": goal.get('description'),
            "metrics": goal.get('metrics', {}),
            "suggested_actions": goal.get('suggested_actions', [])
        })
        
    return insights


def generate_daily_briefing(agent: Any) -> Dict[str, Any]:
    """
    Generates a smart morning briefing, combining tasks, full project overview, and meetings.
    **Improved version**: Shows a general overview of all active projects, not just those relevant to today.
    """
    #logger.info("--- Starting strategic morning briefing generation (including project overview)... ---")
    state = get_system_state()
    if state.get("freeze_mode", {}).get("is_active"):
        # If the mode is active - return a short and adapted response
        reason = state["freeze_mode"].get("reason", "unknown")
        return {
            "status": "Success", 
            "briefing": f"Good morning! The system is currently in freeze mode due to {reason}. No tasks to perform. Take care of yourself and come back safely! ❄️"
        }
    try:
        # --- Step 1: Data collection (with extended separation) ---
        now = datetime.now()
        today = now.date()
        today_iso = today.isoformat()
        current_time_str = get_current_datetime(agent)
        day_name_en = now.strftime('%A')
        day_name_hebrew = HEBREW_DAYS.get(day_name_en, day_name_en)

        # 1a. Collect tasks and meetings for today
        today_events, today_tasks_raw = _get_daily_data(agent, today_iso)
        today_tasks = list(today_tasks_raw)

        # 1b. Collect comprehensive information on all active projects (for the general overview)
        active_projects_overview = []
        today_project_steps = [] # Separate list only for steps that need to be done today
        
        active_projects = find_records(agent, 'projects', {'status': 'active'})
        if isinstance(active_projects, list):
            for proj in active_projects:
                steps = proj.get("steps", [])
                current_index = proj.get("current_step_index", 0)

                # Calculate progress percentage
                progress = int((current_index / len(steps)) * 100) if steps else 0
                
                # Find the current task
                current_step_desc = "The project is completed or no steps were defined."
                if 0 <= current_index < len(steps):
                    current_step_desc = steps[current_index].get('description') or steps[current_index].get('title', 'No description')

                # Calculate time remaining until project completion
                time_remaining = "No end date set"
                if steps and steps[-1].get("due_date"):
                    try:
                        final_due_date = date.fromisoformat(steps[-1]["due_date"])
                        delta = final_due_date - today
                        if delta.days > 0:
                            time_remaining = f"{delta.days} days remaining"
                        elif delta.days == 0:
                            time_remaining = "Today is the deadline!"
                            # Add to general overview
                            active_projects_overview.append({
                                "project_title": proj.get('title'),
                                "progress_percentage": progress,
                                "current_step_description": current_step_desc,
                                "time_remaining": time_remaining
                            })
                        else:
                            time_remaining = f"Late by {abs(delta.days)} days"
                    except (ValueError, TypeError):
                        time_remaining = "Invalid end date"


                # Separate check: Is the current step for today? If so, add it to the daily task list
                if 0 <= current_index < len(steps) and steps[current_index].get("due_date") == today_iso:
                    today_project_steps.append({
                        "project_title": proj.get('title'),
                        "step_description": current_step_desc,
                    })

        # 1c. Identify postponed items
        procrastinated_items = []
        # Combined today's tasks and steps for procrastination check
        items_for_today_check = today_tasks + [{"title": f"Project: {p['project_title']}", "description": p['step_description']} for p in today_project_steps]
        for item in items_for_today_check:
            text_to_check = item.get('description', '') or item.get('title', '')
            if PROCRASTINATION_MARKER in text_to_check or POSTPONEMENT_COUNTER_PATTERN.search(text_to_check):
                    procrastinated_items.append(item)
        
        # --- Step 2: Get proactive suggestions regarding goals ---
        actionable_goal_insights = get_actionable_goal_insights(agent)
        
        # --- New prompt with strategic project overview ---
        briefing_prompt = generate_daily_briefing_promt.format(
            day_name_hebrew=day_name_hebrew,
            current_time=current_time_str,
            today_events_json=json.dumps(today_events, ensure_ascii=False, indent=2),
            today_tasks_json=json.dumps(today_tasks, ensure_ascii=False, indent=2),
            active_projects_overview_json=json.dumps(active_projects_overview, ensure_ascii=False, indent=2),
            today_project_steps_json=json.dumps(today_project_steps, ensure_ascii=False, indent=2),
            procrastinated_tasks_json=json.dumps(procrastinated_items, ensure_ascii=False, indent=2),
            actionable_goal_insights_json=json.dumps(actionable_goal_insights, ensure_ascii=False, indent=2)
        )
        
        # --- Step 3: Generate response from AI ---
        #logger.info(f"Sending request to language model (request time: {current_time_str})...")
        response = api_key_manager.generate_content(briefing_prompt, model_name=GEMINI_MODEL_NAME)
        
        #logger.info("--- Morning briefing generation completed successfully ---")
        return {"status": "Success", "briefing": safe_extract_text(response)}
        
    except Exception as e:
        #logger.error(f"Error generating daily briefing: {e}", exc_info=True)
        return {"error": "An unexpected error occurred while generating the briefing."}

def generate_end_of_day_report(agent):
    """
    Generates an end-of-day report that is a personal coaching conversation.
    The function identifies what was completed and what remains open (tasks and projects), cleans the system,
    and guides the AI to create a challenging, encouraging, funny, and personality-filled summary.
    *** Upgraded version: Handles tasks, projects, and provides feedback on routines. ***
    """
    # log_and_print("--- Starting end-of-day process (including project and routine management)... ---", "SYSTEM")
    state = get_system_state()
    if state.get("freeze_mode", {}).get("is_active"):
        # If the mode is active - return a short and adapted response
        reason = state["freeze_mode"].get("reason", "unknown")
        # --- Cleanup and reset (preparation for next day) ---
        update_record(agent, 'USER_PROFILE', {"type": "user_profile"}, {
            "unplanned_wins": [],
            "current_mood": "neutral",
            "stress_notes": ""
        })
        return {
            "status": "Success", 
            "report": f"Good evening! The system is currently in freeze mode due to {reason}. No tasks to perform. Take care of yourself and come back safely! ❄️ Just so you know, we reset your non-task successes but that doesn't mean you're not amazing, stay as you are because there's no one like you in the world!!!"
        }

    try:
        # --- Define Dates (FIXED: Added tomorrow_str) ---
        today_str = date.today().isoformat()
        yesterday_str = (date.today() - timedelta(days=1)).isoformat() 
        tomorrow_str = (date.today() + timedelta(days=1)).isoformat() # <-- FIX: This was missing!

        # --- Initialize Report Lists ---
        completed_items_for_prompt = [] # General name for achievements (tasks and projects)
        pending_items_for_prompt = []   # General name for postponed items
        streak_routines_for_prompt = [] # New list for high-streak routines

        # --- 1. Retrieve profile (mood and invisible wins) ---
        user_profile = get_user_profile(agent)
        current_mood = user_profile.get("current_mood", "neutral")
        stress_notes = user_profile.get("stress_notes", "")
        unplanned_wins_for_prompt = user_profile.get("unplanned_wins", [])
        
        # --- Step 1: Handle completed tasks (cleanup and reporting) ---
        all_completed_tasks = find_records(agent, 'tasks', {'status': 'completed'})
        if isinstance(all_completed_tasks, list) and all_completed_tasks:
            # Add to report only those completed today
            todays_completed = [t for t in all_completed_tasks if t.get('due_date') == today_str or t.get('completion_date') == today_str]
            
            for task in todays_completed:
                # Extract postponement days from completed task
                description = task.get('description', '')
                days_delayed = 0
                match = re.search(POSTPONEMENT_COUNTER_PATTERN, description)
                if match:
                    days_delayed = int(match.group(1))
                
                completed_items_for_prompt.append({
                    "type": "Task", 
                    "title": task.get('title'),
                    "days_delayed_before_completion": days_delayed
                })

            # Delete *all* completed tasks from the system (including old ones)
            delete_record(agent, 'tasks', {'status': 'completed'})
        
        # --- Step 2: Handle completed projects (cleanup and reporting) ---
        all_completed_projects = find_records(agent, 'projects', {'status': 'completed'})
        if isinstance(all_completed_projects, list) and all_completed_projects:
            todays_completed = [p for p in all_completed_projects if p.get('due_date') == today_str]
            for project in todays_completed:
                completed_items_for_prompt.append({"type": "Completed Project", "title": project.get('title')})
            # Delete all completed projects from the system
            delete_record(agent, 'projects', {'status': 'completed'})

        # --- Step 3: Handle remaining open tasks (postponement and marking) ---
        tasks_to_postpone = find_records(agent, 'tasks', {'due_date': today_str, 'status': 'pending'})
        if isinstance(tasks_to_postpone, list):
            for task in tasks_to_postpone:
                task_id = task.get('task_id')
                current_desc = task.get('description', '')
                days_postponed = 1
                
                # Search if a postponement counter already exists
                match = re.search(POSTPONEMENT_COUNTER_PATTERN, current_desc)
                if match:
                    days_postponed = int(match.group(1)) + 1
                    
                # Clear the description completely from all old postponement marks
                base_desc = re.sub(POSTPONEMENT_COUNTER_PATTERN, "", current_desc)
                base_desc = base_desc.replace(PROCRASTINATION_MARKER, "").strip()
                
                # Build the new counter string
                new_counter_str = POSTPONEMENT_COUNTER_TEMPLATE.format(days_postponed)
                
                # Compose the new and clean description
                new_desc = f"{base_desc} {PROCRASTINATION_MARKER} {new_counter_str}".strip()
                
                # If you ever uncomment this, it will now work because 'tomorrow_str' exists!
                # update_record(agent, 'tasks', {'task_id': task_id}, {"due_date": tomorrow_str, "description": new_desc})
                
                pending_items_for_prompt.append({"type": "Task", "title": task.get('title'), "days_pending": days_postponed})
                
        # --- Step 4: Handle delayed projects (postponing current step) ---
        active_projects = find_records(agent, 'projects', {'status': 'active'})
        if isinstance(active_projects, list):
            for project in active_projects:
                current_index = project.get("current_step_index", 0)
                steps = project.get("steps", [])
                if current_index < len(steps):
                    current_step = steps[current_index]
                    step_due_date = current_step.get("due_date")
                    
                    if step_due_date and (step_due_date <= today_str):
                        # Postponement logic and counter update (like in tasks)
                        step_title = project["steps"][current_index].get("title", "")
                        days_postponed = 1
                        match = re.search(POSTPONEMENT_COUNTER_PATTERN, step_title)
                        if match:
                            days_postponed = int(match.group(1)) + 1
                            step_title = re.sub(POSTPONEMENT_COUNTER_PATTERN, "", step_title).strip()
                        
                        # Add to report
                        step_description = current_step.get('description') or current_step.get('title')
                        pending_items_for_prompt.append({
                            "type": "Project Step",
                            "title": f"'{project.get('title')}': {step_description}",
                            "days_pending": days_postponed
                        })

        # --- Step 5: Process goals and receive overdue goals ---
        expired_goals = _process_end_of_day_goals(agent)
        if isinstance(expired_goals, list):
            for goal in expired_goals:
                pending_items_for_prompt.append({
                    "type": "Overdue Goal", 
                    "title": goal.get('title'),
                    "days_pending": 1
                })
            
        # --- Step 6: Handle routines with high streak ---
        # We look for routines performed today with a streak of more than 3 days
        all_routines = find_records(agent, 'routines', {})
        if isinstance(all_routines, list):
            for r in all_routines:
                # Assumption: The routine was updated today so last_completed_date is today
                streak = int(r.get('current_streak', 0))
                if streak > 3:
                    streak_routines_for_prompt.append({
                        "title": r.get('title'),
                        "streak": streak
                    })

        # --- Step 7: Collect information for tomorrow ---
        tomorrows_tasks_raw = find_records(agent, 'tasks', {'due_date': tomorrow_str})
        tomorrows_tasks = tomorrows_tasks_raw if isinstance(tomorrows_tasks_raw, list) else []
        
        # Add project steps for tomorrow
        if isinstance(active_projects, list):
            for proj in active_projects:
                idx = proj.get("current_step_index", 0)
                steps = proj.get("steps", [])
                if idx < len(steps) and steps[idx].get("due_date") == tomorrow_str:
                    step = steps[idx]
                    step_description = step.get('description') or step.get('title')
                    tomorrows_tasks.append({"title": f"Project '{proj.get('title')}': {step_description}"})

        calendar_events_raw = list_calendar_events(agent, days_ahead=2)
        tomorrows_events = [e for e in calendar_events_raw.get('events', []) if e.get('start_time','').startswith(tomorrow_str)]

        # --- Step 8: The extended prompt (including the new section for routines) ---
        report_prompt = generate_end_of_day_report_promt.format(
            current_mood=current_mood,
            stress_notes=stress_notes,
            completed_items_for_prompt=json.dumps(completed_items_for_prompt, ensure_ascii=False),
            unplanned_wins_for_prompt=json.dumps(unplanned_wins_for_prompt, ensure_ascii=False),
            streak_routines_for_prompt=json.dumps(streak_routines_for_prompt, ensure_ascii=False),
            pending_items_for_prompt=json.dumps(pending_items_for_prompt, ensure_ascii=False),
            tomorrows_tasks=json.dumps(tomorrows_tasks, ensure_ascii=False),
            tomorrows_events=json.dumps(tomorrows_events, ensure_ascii=False),
            tomorrow_str=tomorrow_str
        )
        
        # --- Step 9: Generate response from AI ---
        response = api_key_manager.generate_content(report_prompt, model_name=GEMINI_MODEL_NAME)
        report_text = safe_extract_text(response)

        # ==========================================================
        # --- 3. Cleanup and reset (preparation for next day) ---
        # ==========================================================
        update_record(agent, 'USER_PROFILE', {"type": "user_profile"}, {
            "unplanned_wins": [],
            "current_mood": "neutral",
            "stress_notes": ""
        })
        return {"status": "Success", "report": report_text}
        
    except Exception as e:
        error_msg = f"An unexpected error occurred while generating the report: {str(e)}"
        return {
            "status": "Error", 
            "error": str(e),
            "report": error_msg 
        }