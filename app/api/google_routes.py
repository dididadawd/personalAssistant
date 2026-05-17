# app/api/google_routes.py
"""
API Routes for Google Tasks integration.
"""

from flask import Blueprint, request, jsonify

# Import Google Workspace functions
from ..services.google_workspace import get_tasks_service, create_google_task, get_primary_task_list_id

# Import database functions
from ..db.json_manager import find_records

# Import utilities
from ..utils.logger import log_and_print
from ..utils.date_helpers import get_logical_date

# Import agent
from ..core.agent import active_agent as agent_class

# Create blueprint
google_bp = Blueprint('google', __name__)

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
#  Google Routes
# =========================================================================================

@google_bp.route("/tasks", methods=["GET"])
def get_google_tasks_by_date():
    """Get Google Tasks - all overdue + specific date."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        target_date_str = request.args.get('date')
        if not target_date_str:
            target_date_str = get_logical_date().isoformat()
        
        tasks_service = get_tasks_service()
        if not tasks_service:
            return jsonify({"error": "Google Tasks not available"}), 500
        
        task_list_id = get_primary_task_list_id(agent)
        if not task_list_id:
            return jsonify({"tasks": []})
        
        results = tasks_service.tasks().list(tasklist=task_list_id, showCompleted=False).execute()
        items = results.get('items', [])
        
        filtered_tasks = []
        for item in items:
            due = item.get('due')
            if due:
                task_date_str = due.split('T')[0]
                
                if task_date_str <= target_date_str:
                    is_overdue = task_date_str < target_date_str
                    
                    filtered_tasks.append({
                        "id": item['id'],
                        "title": item['title'],
                        "status": item['status'],
                        "due": item['due'],
                        "is_overdue": is_overdue,
                        "original_date": task_date_str
                    })
        
        filtered_tasks.sort(key=lambda x: x['original_date'])
        
        return jsonify({"tasks": filtered_tasks})
        
    except Exception as e:
        log_and_print(f"Error getting Google Tasks: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@google_bp.route("/tasks/complete", methods=["POST"])
def complete_google_task():
    """Mark a Google Task as completed."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json or {}
        task_id = data.get("task_id")
        
        if not task_id:
            return jsonify({"error": "Missing task_id"}), 400
        
        tasks_service = get_tasks_service()
        if not tasks_service:
            return jsonify({"error": "Google Tasks not available"}), 500
        
        task_list_id = get_primary_task_list_id(agent)
        if not task_list_id:
            return jsonify({"error": "No task list found"}), 404
        
        # Mark task as completed
        tasks_service.tasks().update(
            tasklist=task_list_id,
            task=task_id,
            body={'status': 'completed'}
        ).execute()
        
        return jsonify({"status": "Success", "message": "Task completed"})
        
    except Exception as e:
        log_and_print(f"Error completing Google Task: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


# Export the blueprint
__all__ = ['google_bp', 'set_active_agent']