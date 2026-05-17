# app/api/chat_routes.py
"""
API Routes for Chat functionality.
These routes handle chat-related operations.
"""

from flask import Blueprint, request, jsonify
import json
import os

# Import database functions
from ..db.json_manager import find_records, update_record, add_record, delete_record

# Import utilities
from ..utils.logger import log_and_print

# Create blueprint
chat_bp = Blueprint('chat', __name__)

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
#  Chat Routes
# =========================================================================================

@chat_bp.route('/send', methods=['POST'])
def send_chat_message():
    """Send a chat message to the agent."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    message = data.get('message')
    
    if not message:
        return jsonify({"error": "Missing message"}), 400
    
    try:
        # Use the agent's chat functionality
        response = agent.process_message(message)
        return jsonify({"response": response})
    except Exception as e:
        log_and_print(f"Error processing chat message: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@chat_bp.route('/history', methods=['GET'])
def get_chat_history():
    """Get chat history."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    # Get from conversation history
    history = list(agent.conversation_history)
    return jsonify({"history": history})


@chat_bp.route('/clear', methods=['POST'])
def clear_chat():
    """Clear chat history."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    agent.conversation_history.clear()
    return jsonify({"status": "Success", "message": "Chat history cleared"})


@chat_bp.route('/save', methods=['POST'])
def save_chat():
    """Save current chat to file."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    chat_name = data.get('name', 'default_chat')
    
    # Save conversation history to file
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    chat_file = os.path.join(BASE_DIR, 'chats', f'{chat_name}.json')
    
    os.makedirs(os.path.dirname(chat_file), exist_ok=True)
    
    with open(chat_file, 'w', encoding='utf-8') as f:
        json.dump(list(agent.conversation_history), f, indent=2, ensure_ascii=False)
    
    return jsonify({"status": "Success", "message": f"Chat saved as {chat_name}"})


@chat_bp.route('/load', methods=['POST'])
def load_chat():
    """Load a saved chat."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500
    
    data = request.json or {}
    chat_name = data.get('name', 'default_chat')
    
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    chat_file = os.path.join(BASE_DIR, 'chats', f'{chat_name}.json')
    
    if not os.path.exists(chat_file):
        return jsonify({"error": "Chat not found"}), 404
    
    with open(chat_file, 'r', encoding='utf-8') as f:
        history = json.load(f)
    
    agent.conversation_history.clear()
    agent.conversation_history.extend(history)
    
    return jsonify({"status": "Success", "message": f"Chat {chat_name} loaded", "history": history})


@chat_bp.route('/list', methods=['GET'])
def list_chats():
    """List all saved chats."""
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    chats_dir = os.path.join(BASE_DIR, 'chats')
    
    if not os.path.exists(chats_dir):
        return jsonify({"chats": []})
    
    chats = [f.replace('.json', '') for f in os.listdir(chats_dir) if f.endswith('.json')]
    return jsonify({"chats": chats})


# Export the blueprint
__all__ = ['chat_bp', 'set_active_agent']