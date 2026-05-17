# app/api/tasks_routes.py
"""
API Routes for Tasks functionality.
These routes call functions from the task_manager service.
"""

from flask import Blueprint, request, jsonify

# Import service functions
from ..services.task_manager import (
    add_task,
    complete_task
)

# Import project functions
from ..services.project_manager import complete_current_step

# Import database functions
from ..db.json_manager import find_records, update_record, add_record, delete_record

# Import utilities
from ..utils.logger import log_and_print

# Create blueprint
tasks_bp = Blueprint('tasks', __name__)

# Global reference to the active agent instance (set in main.py)
_active_agent = None

def set_active_agent(agent):
    """Set the active agent for this blueprint module."""
    global _active_agent
    _active_agent = agent


def get_agent():
    """Get the active agent instance."""
    if _active_agent:
        return _active_agent
    from flask import current_app
    return current_app.config.get('ACTIVE_AGENT')


# =========================================================================================
#  Task Routes
# =========================================================================================

@tasks_bp.route('/add', methods=['POST'])
def add_new_task():
    """Add a new task."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    result = add_task(
        agent=agent,
        title=data.get('title'),
        description=data.get('description', ''),
        due_date=data.get('due_date'),
        category=data.get('category', 'general'),
        priority=data.get('priority'),
        icon=data.get('icon', 'fas fa-tasks'),
        project_id=data.get('project_id'),
        routine_id=data.get('routine_id'),
        deadline_limit=data.get('deadline_limit'),
        allow_duplicates=data.get('allow_duplicates', False)
    )
    return jsonify(result)


@tasks_bp.route('/complete', methods=['POST'])
def complete_task_route():
    """Complete a task."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    result = complete_task(
        agent=agent,
        task_id=data.get('task_id'),
        task_description=data.get('task_description'),
        task_date=data.get('task_date'),
        task_ids_list=data.get('task_ids_list')
    )
    return jsonify(result)


@tasks_bp.route("/complete_direct", methods=["POST"])
def complete_task_direct_endpoint():
    active_agent = get_agent()
    if not active_agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json
        # Support both single ID and list
        task_id = data.get("task_id") 
        task_ids = data.get("task_ids") 
        
        if task_id and not task_ids:
            task_ids = [task_id]

        if not task_ids:
            return jsonify({"error": "Missing task_ids"}), 400

        task_title = data.get("task_title", "Unknown Task")
        
        # 1. Find the project_id before deletion/completion (so we can check if the project is done)
        related_project_id = None
        if len(task_ids) > 0:
            # Check the first task to see if it belongs to a project
            existing_task = find_records(active_agent, 'tasks', {'task_id': task_ids[0]})
            if existing_task and isinstance(existing_task, list) and len(existing_task) > 0:
                related_project_id = existing_task[0].get('project_id')

        log_and_print(f"--- ⚡ Quick execution: Completing {len(task_ids)} tasks ('{task_title}') ---", "SYSTEM")

        # 2. Perform the completion itself
        result = complete_task(active_agent, task_ids_list=task_ids)
        
        # 3. Automation check: Should we advance the project?
        auto_advance_msg = ""
        if related_project_id:
            # Check how many *open* tasks remain for this project
            remaining_tasks = find_records(active_agent, 'tasks', {'project_id': related_project_id, 'status': 'pending'})
            
            # Note: complete_task may have already updated the current task to completed,
            # so if the list is empty (or contains only irrelevant items), we advance.
            
            is_empty = False
            if not remaining_tasks or (isinstance(remaining_tasks, list) and len(remaining_tasks) == 0):
                is_empty = True
            
            if is_empty:
                log_and_print(f"--- 🎯 All tasks in step completed! Auto-advancing project... ---", "SYSTEM")
                # Call our fixed function
                advance_result = complete_current_step(active_agent, related_project_id)
                if "message" in advance_result:
                    auto_advance_msg = f" (Project auto-advanced: {advance_result['message']})"

        # Return response to client
        response = result if isinstance(result, dict) else {"status": "Success"}
        if auto_advance_msg:
            response['message'] = str(response.get('message', '')) + auto_advance_msg
            
        return jsonify(response)

    except Exception as e:
        log_and_print(f"Error in direct task completion: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500



@tasks_bp.route('/today', methods=['GET'])
def get_today_tasks():
    """Get tasks for today."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    from ..utils.date_helpers import get_logical_date
    today = get_logical_date().isoformat()
    
    tasks = find_records(agent, 'tasks', {
        'due_date': today,
        'status': 'pending'
    })
    return jsonify({"tasks": tasks})


@tasks_bp.route('/tomorrow', methods=['GET'])
def get_tomorrow_tasks():
    """Get tasks for tomorrow."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    from datetime import timedelta
    from ..utils.date_helpers import get_logical_date
    tomorrow = (get_logical_date() + timedelta(days=1)).isoformat()
    
    tasks = find_records(agent, 'tasks', {
        'due_date': tomorrow,
        'status': 'pending'
    })
    return jsonify({"tasks": tasks})


@tasks_bp.route('/general', methods=['GET'])
def get_general_tasks():
    """Get general (no date) tasks."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    tasks = find_records(agent, 'tasks', {
        'status': 'pending'
    })
    # Filter out tasks with due dates for general category
    general_tasks = [t for t in tasks if not t.get('due_date')]
    return jsonify({"tasks": general_tasks})


@tasks_bp.route('/by_date', methods=['GET'])
def get_tasks_by_date_route():
    """Get tasks for a specific date."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({"error": "Missing date parameter"}), 400
    
    tasks = find_records(agent, 'tasks', {
        'due_date': date_str,
        'status': 'pending'
    })
    return jsonify({"tasks": tasks})


@tasks_bp.route('/list', methods=['GET'])
def list_tasks():
    """List all tasks with optional filtering."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    status = request.args.get('status', 'pending')
    tasks = find_records(agent, 'tasks', {'status': status})
    return jsonify({"tasks": tasks})


@tasks_bp.route('/update_date', methods=['POST'])
def update_task_date():
    """Update task due date."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    task_id = data.get('task_id')
    new_date = data.get('due_date')
    
    if not task_id or not new_date:
        return jsonify({"error": "Missing required fields"}), 400
    
    result = update_record(agent, 'tasks', {'task_id': task_id}, {'due_date': new_date})
    return jsonify(result)


# Export the blueprint
__all__ = ['tasks_bp', 'set_active_agent']