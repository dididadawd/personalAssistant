# app/api/ideas_routes.py
"""
API Routes for Ideas/Notes endpoints.
"""

from flask import Blueprint, request, jsonify
import os
from datetime import datetime

# Import database functions
from ..db.json_manager import find_records, add_record, delete_record

# Import utilities
from ..utils.logger import log_and_print

# Import agent
from ..core.agent import active_agent as agent_class

# Create blueprint
ideas_bp = Blueprint('ideas', __name__)

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
#  Ideas Routes
# =========================================================================================

@ideas_bp.route('', methods=['GET'])
def get_ideas():
    """Get all ideas/notes - only brainstorm_idea type, sorted by date."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        ideas = find_records(agent, 'IDEA', {'type': 'brainstorm_idea'})
        if isinstance(ideas, str):
            return jsonify({"ideas": []})
        
        if ideas:
            ideas.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return jsonify({"ideas": ideas or []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ideas_bp.route('/add', methods=['POST'])
def add_idea():
    """Add a new idea/note."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json or {}
        title = data.get("title")
        content = data.get("content", "")
        
        if not title:
            return jsonify({"error": "Missing title"}), 400

        idea_data = {
            "title": title,
            "content": content,
            "created_at": str(datetime.now().isoformat())
        }
        
        result = add_record(agent, 'ideas', idea_data)
        return jsonify(result)
    except Exception as e:
        log_and_print(f"Error adding idea: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@ideas_bp.route('/delete', methods=['POST'])
def delete_idea_endpoint():
    """Delete an idea/note."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json or {}
        query = data.get("query", {})

        if not query:
            return jsonify({"error": "Missing query parameter"}), 400

        result = delete_record(agent, 'ideas', query)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Export the blueprint
__all__ = ['ideas_bp', 'set_active_agent']