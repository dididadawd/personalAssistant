# app/api/pages_routes.py
"""
API Routes for Page endpoints (non-API routes).
These serve HTML pages and static files.
"""

import os
from flask import Blueprint, request, jsonify, send_from_directory
import os
from datetime import date, timedelta

# Import database functions
from ..db.json_manager import find_records

# Import utilities
from ..utils.logger import log_and_print

# Import agent
from ..core.agent import active_agent as agent_class

# API Logging Configuration
YAHLI_API_LOGGING = os.environ.get('YAHLI_API_LOGGING', 'true').lower() == 'true'

def api_log(message, level="SYSTEM"):
    """Conditional logging based on YAHLI_API_LOGGING flag"""
    if YAHLI_API_LOGGING:
        log_and_print(f"[PAGES] {message}", level)

# Create blueprint
pages_bp = Blueprint('pages', __name__)

# Global reference to the active agent instance
_active_agent = None

def set_active_agent(agent):
    global _active_agent
    _active_agent = agent

def get_agent():
    if _active_agent:
        return _active_agent
    from flask import current_app
    return current_app.config.get('ACTIVE_AGENT')


# =========================================================================================
#  Page Routes
# =========================================================================================

@pages_bp.route("/projects", methods=["GET"])
def get_projects_page():
    """Get projects page - returns active projects list."""
    api_log(">>> GET /projects - Fetching active projects", "SYSTEM")
    agent = get_agent()
    if not agent:
        api_log("Agent not loaded", "ERROR")
        return jsonify({"projects": []}), 500
    
    try:
        query = {'status': 'active'}
        projects = find_records(agent, 'projects', query)

        if not projects or isinstance(projects, str):
            api_log("No projects found", "SYSTEM")
            return jsonify({"projects": []})
        
        api_log(f"Returning {len(projects)} projects", "SYSTEM")
        return jsonify({"projects": projects})
    except Exception as e:
        api_log(f"Error in get_projects: {e}", "ERROR")
        return jsonify({"projects": []})


@pages_bp.route("/tasks/today", methods=["GET"])
def get_today_tasks_page():
    """Get today's tasks page - returns tasks due today + overdue."""
    api_log(">>> GET /tasks/today - Fetching today's tasks", "SYSTEM")
    agent = get_agent()
    if not agent:
        api_log("Agent not loaded", "ERROR")
        return jsonify({"tasks": []}), 500
    
    try:
        today_iso_format = date.today().isoformat()
        
        query = {
            'status': 'pending',
            'due_date': {'$lte': today_iso_format}
        }
        
        tasks = find_records(agent, 'tasks', query)

        if not tasks or isinstance(tasks, str):
            api_log("No tasks found for today", "SYSTEM")
            return jsonify({"tasks": []})
        
        tasks.sort(key=lambda x: x.get('due_date', ''))
        api_log(f"Returning {len(tasks)} tasks for today", "SYSTEM")

        return jsonify({"tasks": tasks})
    except Exception as e:
        api_log(f"Error in get_today_tasks: {e}", "ERROR")
        return jsonify({"tasks": []})


@pages_bp.route("/tasks/tomorrow", methods=["GET"])
def get_tomorrow_tasks_page():
    """Get tomorrow's tasks page - returns tasks due tomorrow + today."""
    api_log(">>> GET /tasks/tomorrow - Fetching tomorrow's tasks", "SYSTEM")
    from ..utils.date_helpers import get_logical_date
    
    agent = get_agent()
    if not agent:
        api_log("Agent not loaded", "ERROR")
        return jsonify({"tasks": []}), 500
    
    try:
        logical_today = get_logical_date()
        today_iso = logical_today.isoformat()
        tomorrow_iso = (logical_today + timedelta(days=1)).isoformat()
        
        query = {
            'status': 'pending',
            'due_date': {'$in': [today_iso, tomorrow_iso]}
        }
        
        tasks = find_records(agent, 'tasks', query)

        if not tasks or isinstance(tasks, str):
            api_log("No tasks found for tomorrow", "SYSTEM")
            return jsonify({"tasks": []})
        
        tasks.sort(key=lambda x: x.get('due_date', ''))
        api_log(f"Returning {len(tasks)} tasks for tomorrow", "SYSTEM")

        return jsonify({"tasks": tasks})

    except Exception as e:
        api_log(f"Error in get_tomorrow_tasks: {e}", "ERROR")
        return jsonify({"tasks": []})


@pages_bp.route("/tasks/general", methods=["GET"])
def get_general_tasks_page():
    """Get general tasks page - returns tasks without due date."""
    api_log(">>> GET /tasks/general - Fetching general tasks", "SYSTEM")
    agent = get_agent()
    if not agent:
        api_log("Agent not loaded", "ERROR")
        return jsonify({"tasks": []}), 500
    
    try:
        query = {
            'status': 'pending',
            'category': {'$in': ['general', 'project']} 
        }
        
        tasks = find_records(agent, 'tasks', query)

        if not tasks or isinstance(tasks, str):
            api_log("No general tasks found", "SYSTEM")
            return jsonify({"tasks": []})
        
        tasks.sort(key=lambda x: x.get('due_date', '9999-99-99'))
        api_log(f"Returning {len(tasks)} general tasks", "SYSTEM")

        return jsonify({"tasks": tasks})
    except Exception as e:
        api_log(f"Error in get_general_tasks: {e}", "ERROR")
        return jsonify({"tasks": []})


@pages_bp.route("/outputs/<path:filename>", methods=["GET"])
def download_presentation_file(filename):
    """Serve output files for download."""
    api_log(f">>> GET /outputs/{filename} - Downloading file", "SYSTEM")
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir = os.path.join(script_dir, "outputs")
    return send_from_directory(output_dir, filename, as_attachment=True)


@pages_bp.route("/", methods=["GET"])
def serve_frontend():
    """Serve the main frontend HTML file."""
    api_log(">>> GET / - Serving frontend", "SYSTEM")
    
    # Added another os.path.dirname to go up from app to the root directory (agent - Copy)
    script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    client_dir = os.path.join(script_dir, "client")
    
    response = send_from_directory(client_dir, "frontend.html")
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@pages_bp.route("/chat", methods=["POST"])
def handle_chat():
    """Handle main chat functionality - Yahli Room."""

    api_log(">>> POST /chat - New chat message received", "SYSTEM")
    from flask import request
    from datetime import date
    
    agent = get_agent()
    if not agent:
        api_log("Agent not loaded - critical error", "ERROR")
        return jsonify({"reply": "Critical error: Agent not loaded."}), 500

    try:
        request_data = request.json
        user_message = request_data.get("message")
        state = request_data.get("state")
        chat_context = request_data.get("context")
    except Exception as e:
        api_log(f"Error parsing request JSON: {e}", "ERROR")
        return jsonify({"error": "Invalid request format"}), 400

    if not user_message:
        api_log("No message provided", "ERROR")
        return jsonify({"error": "No message provided"}), 400

    api_log(f"Message received, context: {chat_context}, message length: {len(user_message)}", "SYSTEM")

    if chat_context == "yahli_room":
        from ..services.main_service import get_user_profile
        from ..services.google_workspace import list_calendar_events
        from ..db.json_manager import find_records
        from ..prompts.system_prompts import YAHLI_ROOM_UNIVERSAL_RULES
        
        room_mode = request_data.get("mode")
        sub_mode = request_data.get("subMode")
        api_log(f"Yahli Room mode: {room_mode}, sub_mode: {sub_mode}", "SYSTEM")

        system_prompt = ""
        if room_mode == 'brainstorm':
            persona = sub_mode.upper() if sub_mode else 'BRAINSTORMER'
            system_prompt = f"[System Prompt: 'Yahli Room' - Brainstorming. Persona: '{persona}'.\n{YAHLI_ROOM_UNIVERSAL_RULES}]"
        
        elif room_mode == 'personal_growth':
            loc = request_data.get("location", "unknown")
            nrg = request_data.get("energy", "unknown")
            time = request_data.get("time_available", "unknown")
            system_prompt = f"[System Prompt: Personal Growth. Context: Loc={loc}, Energy={nrg}, Time={time}.\n{YAHLI_ROOM_UNIVERSAL_RULES}]"
        
        profile = get_user_profile(agent)
        today_str = date.today().isoformat()
        
        pending_tasks = find_records(agent, 'tasks', {'status': 'pending', 'due_date': {'$lte': today_str}})
        tasks_list_str = "\n".join([f"- {t.get('title')} (ID: {t.get('task_id')})" for t in (pending_tasks or [])]) or "No pending tasks."
        
        try:
            one_time_tasks_raw = find_records(agent, 'one_time_tasks', {})
            if isinstance(one_time_tasks_raw, str): 
                one_time_tasks_raw = [] # Protection against crash
            active_one_time = [t for t in (one_time_tasks_raw or []) if t.get('status') != 'completed']
            one_time_tasks_str = "\n".join([
                f"- {t.get('title') or t.get('content') or 'Untitled'} (ID: {t.get('id') or t.get('task_id') or t.get('_id')})"
                for t in active_one_time
            ]) or "No one-time tasks."
        except Exception as e:
            api_log(f"Error fetching one time tasks: {e}", "WARNING")
            one_time_tasks_str = "Could not fetch one time tasks."

        try:
            cal_data = list_calendar_events(agent, days_ahead=1)
            todays_events = [e for e in cal_data.get('events', []) if e.get('start_time', '').startswith(today_str)]
            events_list_str = "\n".join([f"- {e.get('summary', 'Untitled Event')} at {e.get('start_time').split('T')[1][:5]}" for e in todays_events]) or "No calendar events."
        except:
            events_list_str = "Could not fetch calendar."

        try:
            all_ideas_raw = find_records(agent, 'IDEA', {'type': 'brainstorm_idea'})
            ideas_list_str = "\n".join([f"- {idea.get('content')}" for idea in (all_ideas_raw or [])]) or "No saved ideas."
        except:
            ideas_list_str = "Could not fetch ideas."
            
        context_string = (
            f"\n\n[SYSTEM DIRECTIVE: YAHLI ROOM CONTEXT]\n"
            f"--- PERSONAL PROFILE ---\n"
            f"Likes: {profile.get('preferences')}\n"
            f"Dislikes: {profile.get('anti_preferences')}\n"
            f"General Info: {profile.get('general_info')}\n"
            f"Mood: {profile.get('current_mood')} | Notes: {profile.get('stress_notes')}\n\n"
            f"--- BRAINSTORM VAULT (Saved Ideas - ATTENTION: THESE ARE NOT TASKS) ---\n" 
            f"{ideas_list_str}\n\n"
            f"--- TODAY'S CONTEXT ---\n"
            f"Pending Tasks (Actionable items to be completed):\n{tasks_list_str}\n\n"
            f"One-Time Tasks (Quick/single tasks):\n{one_time_tasks_str}\n\n"
            f"Calendar Events:\n{events_list_str}\n"
            f"[END OF CONTEXT]\n"
        )
        
        user_message = f"{system_prompt}{context_string}\nUser says: {user_message}"

    try:
        api_log("Processing message through agent...", "SYSTEM")
        if state and state.get("next_action"):
            from ..services.main_service import handle_follow_up
            bot_response = handle_follow_up(state, user_message)
        else:
            bot_response = agent.execute_task(user_message, chat_context=chat_context)

        if isinstance(bot_response, dict) and "confirmation_request" in bot_response:
            api_log("Confirmation request detected - auto confirming", "SYSTEM")
            is_gui_delete_routine = "Delete the routine with ID" in user_message
            is_gui_complete_task = "Mark the task" in user_message and "as completed" in user_message
            is_gui_advance_project = "Advance the project" in user_message and "to the next step" in user_message

            if is_gui_delete_routine or is_gui_complete_task or is_gui_advance_project:
                api_log("--- AUTO-CONFIRMATION: Pre-approved action detected. Simulating 'yes'. ---", "SYSTEM")
                bot_response = agent.execute_task("yes", chat_context=chat_context)

        api_log("Response generated successfully", "SYSTEM")
        if isinstance(bot_response, dict):
            return jsonify(bot_response)
        elif isinstance(bot_response, str):
            return jsonify({"reply": bot_response})
        else:
            api_log("Unexpected response type", "ERROR")
            return jsonify({"reply": "An unexpected error occurred in response type."})

    except Exception as e:
        api_log(f"CRITICAL ERROR in chat processing: {e}", "ERROR")
        return jsonify({"error": f"An internal error occurred: {e}"}), 500


# Export the blueprint
__all__ = ['pages_bp', 'set_active_agent']