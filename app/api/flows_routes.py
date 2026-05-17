# app/api/flows_routes.py
"""
API Routes for Flow Automation endpoints.
Handles flow automation, webhooks, and paused flow resumption.
"""

from flask import Blueprint, request, jsonify
import uuid
import threading
from datetime import datetime

# Import database functions
from ..db.json_manager import find_records, add_record, update_record

# Import utilities
from ..utils.logger import log_and_print

# Import agent
from ..core.agent import active_agent as agent_class, execute_flow_run

# Create blueprint
flows_bp = Blueprint('flows', __name__)

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
#  Flow Routes
# =========================================================================================

@flows_bp.route('/pending_inputs', methods=['GET'])
def get_pending_flow_inputs():
    """Check if there are paused flows waiting for user input."""
    agent = get_agent()
    if not agent:
        return jsonify({"pending": []})
    
    paused_runs = find_records(agent, "flow_runs", {"status": "paused_for_user"})
    if not paused_runs or isinstance(paused_runs, str):
        return jsonify({"pending": []})

    pending_list = []
    for run in paused_runs:
        flow_id = run.get("flow_id")
        blueprints = find_records(agent, "flow_blueprints", {"flow_id": flow_id})
        
        flow_name = blueprints[0].get("name", "Automated Process") if blueprints and not isinstance(blueprints, str) else "Automated Process"
        question = run.get("pending_question", "User intervention required")

        pending_list.append({
            "run_id": run.get("run_id"),
            "flow_name": flow_name,
            "question": question
        })

    return jsonify({"pending": pending_list})


@flows_bp.route('/webhook/<flow_id>', methods=['POST'])
def trigger_flow_webhook(flow_id):
    """Trigger for external systems (ESP32, Make, Zapier) to start a flow."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    blueprints = find_records(agent, "flow_blueprints", {"flow_id": flow_id})
    if not blueprints or isinstance(blueprints, str):
        return jsonify({"error": f"Flow ID '{flow_id}' not found."}), 404
    
    initial_payload = request.json or {}
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    
    run_record = {
        "run_id": run_id,
        "flow_id": flow_id,
        "status": "pending",
        "current_step_index": 0,
        "payload": initial_payload,
        "history": [f"Triggered via Webhook at {datetime.now().isoformat()}"]
    }
    
    add_record(agent, "flow_runs", run_record)
    
    # Run in thread to return quickly
    threading.Thread(target=execute_flow_run, args=(agent, run_id), daemon=True).start()
    
    return jsonify({"status": "Success", "message": "Flow triggered", "run_id": run_id})


@flows_bp.route('/resume', methods=['POST'])
def resume_paused_flow():
    """Resume a paused flow after user answered a question."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    run_id = data.get("run_id")
    user_answer = data.get("user_answer")
    
    if not run_id or user_answer is None:
        return jsonify({"error": "Missing run_id or user_answer"}), 400
    
    runs = find_records(agent, "flow_runs", {"run_id": run_id, "status": "paused_for_user"})
    if not runs or isinstance(runs, str):
        return jsonify({"error": "Valid paused run not found."}), 404
    
    run = runs[0]
    run["status"] = "running"
    run["user_response"] = user_answer
    
    # Add to history
    history = run.get("history", [])
    history.append(f"User responded at {datetime.now().isoformat()}: {user_answer}")
    run["history"] = history
    
    update_record(agent, "flow_runs", {"run_id": run_id}, run)
    
    # Resume execution
    threading.Thread(target=execute_flow_run, args=(agent, run_id), daemon=True).start()
    
    return jsonify({"status": "Success", "message": "Flow resumed", "run_id": run_id})


# Export the blueprint
__all__ = ['flows_bp', 'set_active_agent']