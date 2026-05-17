# app/api/system_routes.py
"""
API Routes for System functionality.
These routes handle system-level operations.
"""

import os
from flask import Blueprint, request, jsonify

# Import system utilities
from ..utils.system_state import get_system_state, save_system_state, is_system_frozen
from ..utils.logger import log_and_print

# API Logging Configuration
YAHLI_API_LOGGING = os.environ.get('YAHLI_API_LOGGING', 'true').lower() == 'true'

def api_log(message, level="SYSTEM"):
    """Conditional logging based on YAHLI_API_LOGGING flag"""
    if YAHLI_API_LOGGING:
        log_and_print(f"[SYSTEM] {message}", level)

# Create blueprint
system_bp = Blueprint('system', __name__)

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
#  System Routes
# =========================================================================================

@system_bp.route('/status', methods=['GET'])
def get_status():
    """Get system status."""
    api_log(">>> GET /status - Getting system status", "SYSTEM")
    status = get_system_state()
    return jsonify(status)


@system_bp.route('/freeze', methods=['POST'])
def freeze_system():
    """Freeze the system."""
    api_log(">>> POST /freeze - Freezing system", "SYSTEM")
    data = request.json or {}
    reason = data.get('reason', 'Manual freeze')
    
    save_system_state({'frozen': True, 'freeze_reason': reason})
    api_log(f"System frozen: {reason}", "SYSTEM")
    
    return jsonify({"status": "Success", "message": "System frozen"})


@system_bp.route('/unfreeze', methods=['POST'])
def unfreeze_system():
    """Unfreeze the system."""
    api_log(">>> POST /unfreeze - Unfreezing system", "SYSTEM")
    save_system_state({'frozen': False, 'freeze_reason': None})
    api_log("System unfrozen", "SYSTEM")
    
    return jsonify({"status": "Success", "message": "System unfrozen"})


@system_bp.route('/is_frozen', methods=['GET'])
def check_frozen():
    """Check if system is frozen."""
    api_log(">>> GET /is_frozen - Checking if system is frozen", "SYSTEM")
    frozen = is_system_frozen()
    return jsonify({"frozen": frozen})


@system_bp.route('/info', methods=['GET'])
def get_system_info():
    """Get system information."""
    api_log(">>> GET /info - Getting system info", "SYSTEM")
    agent = get_agent()
    
    info = {
        "agent_loaded": agent is not None,
        "agent_name": agent.name if agent else None,
    }
    
    return jsonify(info)


@system_bp.route('/tools/execute', methods=['POST'])
def execute_tool_direct():
    """Execute a tool directly via API."""
    api_log(">>> POST /tools/execute - Executing tool", "SYSTEM")
    from ..utils.system_state import manage_freeze_mode
    
    agent = get_agent()
    if not agent:
        api_log("Agent not loaded", "ERROR")
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    tool_name = data.get('tool_name')
    params = data.get('params', {})
    
    api_log(f"Tool: {tool_name}, Params: {params}", "SYSTEM")
    
    if tool_name == "manage_freeze_mode":
        result = manage_freeze_mode(agent, **params)
        api_log(f"Tool executed successfully: {tool_name}", "SYSTEM")
        return jsonify({"result": result})
    
    api_log(f"Tool not found: {tool_name}", "ERROR")
    return jsonify({"error": "Tool not found"}), 404


# Export the blueprint
__all__ = ['system_bp', 'set_active_agent']