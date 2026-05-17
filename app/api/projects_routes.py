# app/api/projects_routes.py
"""
API Routes for Projects functionality.
These routes call functions from the project_manager service.
"""

from flask import Blueprint, request, jsonify

# Import service functions
from ..services.project_manager import (
    create_project_timeboxed_action_plan,
    set_current_step_task_project,
    complete_current_step,
    define_project_step,
    get_project_step_history,
    revert_project_step
)

# Import database functions
from ..db.json_manager import find_records, update_record, add_record, delete_record

# Import utilities
from ..utils.logger import log_and_print

# Create blueprint
projects_bp = Blueprint('projects', __name__)

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
    # Fallback: try to get from app context
    from flask import current_app
    return current_app.config.get('ACTIVE_AGENT')


# =========================================================================================
#  Project Routes
# =========================================================================================

@projects_bp.route('/create', methods=['POST'])
def create_project():
    """Create a new time-boxed project."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    result = create_project_timeboxed_action_plan(
        agent=agent,
        goal=data.get('goal'),
        title=data.get('title'),
        scope_description=data.get('scope_description'),
        total_duration_days=data.get('total_duration_days', 30),
        mission_id=data.get('mission_id'),
        first_due_date=data.get('first_due_date')
    )
    return jsonify(result)


@projects_bp.route('/update_step', methods=['POST'])
def update_step():
    """Update the current step of a project."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    result = set_current_step_task_project(
        agent=agent,
        project_id=data.get('project_id'),
        description=data.get('description')
    )
    return jsonify(result)


@projects_bp.route('/advance', methods=['POST'])
def advance_project():
    """Advance project to the next step."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    result = complete_current_step(
        agent=agent,
        project_id=data.get('project_id')
    )
    return jsonify(result)


@projects_bp.route('/define_step', methods=['POST'])
def define_step():
    """Define/update steps for a project."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    result = define_project_step(
        agent=agent,
        project_id=data.get('project_id'),
        steps_updates=data.get('steps_updates', [])
    )
    return jsonify(result)


@projects_bp.route('/add', methods=['POST'])
def add_new_project():
    """Add a new project (simple version) - uses create_project_timeboxed_action_plan."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    # Use the main create function with minimal parameters
    result = create_project_timeboxed_action_plan(
        agent=agent,
        title=data.get('title', ''),
        scope_description=data.get('description', ''),
        total_duration_days=data.get('total_duration_days', 30),
        mission_id=data.get('mission_id')
    )
    return jsonify(result)


@projects_bp.route('/history', methods=['GET'])
def get_history():
    """Get project step history."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    project_id = request.args.get('project_id')
    if not project_id:
        return jsonify({"error": "Missing project_id"}), 400
    
    result = get_project_step_history(agent=agent, project_id=project_id)
    return jsonify(result)


@projects_bp.route('/revert', methods=['POST'])
def revert_step():
    """Revert project to a previous version."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    result = revert_project_step(
        agent=agent,
        project_id=data.get('project_id'),
        history_versions_back=data.get('history_versions_back', 1)
    )
    return jsonify(result)


@projects_bp.route('/list', methods=['GET'])
def list_projects():
    """List all projects."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    status = request.args.get('status', 'active')
    projects = find_records(agent, 'projects', {'status': status})
    return jsonify({"projects": projects})


@projects_bp.route('/update_step_date', methods=['POST'])
def update_step_date():
    """Update the due date of a project step."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    project_id = data.get('project_id')
    step_index = data.get('step_index', 0)
    new_date = data.get('due_date')
    
    if not project_id or not new_date:
        return jsonify({"error": "Missing required fields"}), 400
    
    # Get current project
    projects = find_records(agent, 'projects', {'project_id': project_id})
    if not projects or isinstance(projects, str):
        return jsonify({"error": "Project not found"}), 404
    
    project = projects[0]
    steps = project.get('steps', [])
    
    if step_index < len(steps):
        steps[step_index]['due_date'] = new_date
        update_record(agent, 'projects', {'project_id': project_id}, {'steps': steps})
        return jsonify({"status": "Success", "message": "Step date updated"})
    
    return jsonify({"error": "Invalid step index"}), 400


# Export the blueprint
__all__ = ['projects_bp', 'set_active_agent']