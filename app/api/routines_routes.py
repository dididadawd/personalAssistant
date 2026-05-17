# app/api/routines_routes.py
"""
API Routes for Routines functionality.
"""

from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
import os
import json
import threading

# Import database functions
from ..db.json_manager import find_records, update_record, add_record, delete_record

# Import utilities
from ..utils.logger import log_and_print
from ..utils.date_helpers import get_logical_date

# Create blueprint
routines_bp = Blueprint('routines', __name__)

# Global reference to the active agent instance
_active_agent = None
_DATA_FILE_LOCKS = {}

def set_active_agent(agent):
    global _active_agent
    _active_agent = agent

def get_agent():
    if _active_agent:
        return _active_agent
    from flask import current_app
    return current_app.config.get('ACTIVE_AGENT')


def _update_routine_stats_logic(agent, routine_id: str, completion_date_iso: str):
    """
    Updates routine statistics safely with type safety guards.
    """
    table_name = "routines"
    
    kb_path = f"personas/{agent.name}/knowledge_base"
    file_path = f"{kb_path}/{table_name}.json"
    
    import os
    if not os.path.exists(file_path): 
        return None

    import json
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                routines = []
            else:
                routines = json.loads(content)
    except (json.JSONDecodeError, Exception) as e:
        log_and_print(f"CRITICAL ERROR reading routines.json: {e}", "ERROR")
        return None
    
    updated_routine = None
    routine_found = False

    for r in routines:
        if r.get("routine_id") == routine_id:
            routine_found = True
            
            if r.get("last_completed_date") == completion_date_iso:
                return r 

            try:
                current_total = int(r.get("total_completions", 0))
                current_streak_val = int(r.get("current_streak", 0))
                longest_streak_val = int(r.get("longest_streak", 0))
            except (ValueError, TypeError):
                current_total = 0
                current_streak_val = 0
                longest_streak_val = 0

            r["total_completions"] = current_total + 1
            r["current_streak"] = current_streak_val + 1

            if r["current_streak"] > longest_streak_val:
                r["longest_streak"] = r["current_streak"]

            r["last_completed_date"] = completion_date_iso
            updated_routine = r
            break
    
    if routine_found and updated_routine:
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(routines, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log_and_print(f"Error saving routines.json: {e}", "ERROR")
            return None
    
    return updated_routine


# =========================================================================================
#  Routines Routes
# =========================================================================================

@routines_bp.route("", methods=["GET"])
def get_routines():
    """
    Returns all routines from the data file.
    """
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    try:
        routines = find_records(agent, 'routines', {})
        if not routines or isinstance(routines, str):
            return jsonify({"routines": []})
        return jsonify({"routines": routines})
    except Exception as e:
        log_and_print(f"Error in get_routines: {e}", "ERROR")
        return jsonify({"routines": []}), 500


@routines_bp.route("/toggle", methods=["POST"])
def toggle_routine_status():
    """Toggle a routine's active status."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json or {}
        routine_id = data.get("routine_id")
        
        if not routine_id:
            return jsonify({"error": "Missing routine_id"}), 400
        
        table_name = "routines"
        
        if table_name not in _DATA_FILE_LOCKS:
            _DATA_FILE_LOCKS[table_name] = threading.Lock()
        
        lock = _DATA_FILE_LOCKS.get(table_name)
        kb_path = f"personas/{agent.name}/knowledge_base"
        file_path = f"{kb_path}/{table_name}.json"

        with lock:
            if not os.path.exists(file_path):
                return jsonify({"error": "Routines file not found"}), 404
                
            with open(file_path, 'r', encoding='utf-8') as f:
                routines = json.load(f)

            for r in routines:
                if r.get('routine_id') == routine_id:
                    current_status = r.get('is_active', True)
                    r['is_active'] = not current_status
                    break

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(routines, f, indent=2, ensure_ascii=False)

        return jsonify({"status": "Success", "message": "Routine status toggled"})

    except Exception as e:
        log_and_print(f"Error toggling routine: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@routines_bp.route("/complete", methods=["POST"])
def mark_routine_completion():
    """Mark a routine as completed for today."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json or {}
        routine_id = data.get("routine_id")
        if not routine_id:
            return jsonify({"error": "Missing routine_id"}), 400
        
        today_iso = get_logical_date().isoformat()
        updated_routine = _update_routine_stats_logic(agent, routine_id, today_iso)
        
        if updated_routine:
            return jsonify({"status": "Success", "routine": updated_routine})
        else:
            return jsonify({"error": "Routine not found or update failed"}), 404

    except Exception as e:
        log_and_print(f"Error in mark_routine_completion: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@routines_bp.route("/add", methods=["POST"])
def add_routine_endpoint():
    """Add a new routine."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json or {}
        title = data.get("title")
        frequency = data.get("frequency", "daily")
        days_of_week = data.get("days_of_week", ".sun,mon,tue,wed,thu,fri,sat")
        icon = data.get("icon", "fas fa-sync-alt")
        description = data.get("description", "")
        
        if not title:
            return jsonify({"error": "Missing title"}), 400
        
        import uuid
        new_routine = {
            "routine_id": f"routine_{uuid.uuid4().hex[:8]}",
            "title": title,
            "frequency": frequency,
            "days_of_week": days_of_week,
            "icon": icon,
            "description": description,
            "status": "active",
            "total_completions": 0,
            "current_streak": 0,
            "longest_streak": 0,
            "last_completed_date": None,
            "created_at": datetime.now().isoformat()
        }
        
        result = add_record(agent, "routines", new_routine)
        return jsonify({"status": "Success", "routine": new_routine})
        
    except Exception as e:
        log_and_print(f"Error adding routine: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@routines_bp.route("/delete", methods=["POST"])
def delete_routine_endpoint():
    """Delete a routine."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json or {}
        routine_id = data.get("routine_id")
        
        if not routine_id:
            return jsonify({"error": "Missing routine_id"}), 400
        
        result = delete_record(agent, 'routines', {'routine_id': routine_id})
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Export the blueprint
__all__ = ['routines_bp', 'set_active_agent']