# app/api/yahli_routes.py
"""
API Routes for Yahli Chat functionality.
Handles chat save, load, delete, and list operations.
"""

import os
from flask import Blueprint, request, jsonify
from datetime import datetime

# Import database functions
from ..db.json_manager import find_records, add_record, update_record, delete_record

# Import utilities
from ..utils.logger import log_and_print

# API Logging Configuration
YAHLI_API_LOGGING = os.environ.get('YAHLI_API_LOGGING', 'true').lower() == 'true'

def api_log(message, level="SYSTEM"):
    """Conditional logging based on YAHLI_API_LOGGING flag"""
    if YAHLI_API_LOGGING:
        log_and_print(f"[YAHLI] {message}", level)

# Create blueprint
yahli_bp = Blueprint('yahli', __name__)

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

# --- Helper Function for IDs ---
def build_id_query(chat_id):
    """Helper to handle generic ID queries (supports MongoDB ObjectId if available)"""
    try:
        from bson import ObjectId
        if isinstance(chat_id, str) and len(chat_id) == 24:
            return {"_id": ObjectId(chat_id)}
    except ImportError:
        pass
    return {"id": chat_id} # Fallback


# =========================================================================================
#  Yahli Routes
# =========================================================================================

@yahli_bp.route("/get_chats_list", methods=["GET"])
def get_saved_chats_list():
    """Get list of saved chats."""
    api_log(">>> GET /get_chats_list - Fetching saved chats list", "SYSTEM")
    agent = get_agent()
    if not agent:
        api_log("Agent not loaded", "ERROR")
        return jsonify({"chats": []}), 500
    
    try:
        # Fix: Fetch only relevant records instead of the entire table
        chat_records = find_records(agent, 'IDEA', {'type': 'yahli_saved_chat'})
        saved_chats = []
        
        if isinstance(chat_records, list):
            for record in chat_records:
                raw_id = record.get('_id') or record.get('id') or record.get('record_id') or record.get('IDEA_id')
                str_id = str(raw_id) if raw_id else None
                
                if str_id:
                    saved_chats.append({
                        "id": str_id,
                        "title": record.get('content', 'Untitled Chat'),
                        "created_at": record.get('created_at')
                    })
            
            saved_chats.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        api_log(f"Found {len(saved_chats)} saved chats", "SYSTEM")
        return jsonify({"chats": saved_chats})
    except Exception as e:
        api_log(f"Error getting list: {e}", "ERROR")
        return jsonify({"chats": []})


@yahli_bp.route("/save_chat", methods=["POST"])
def save_yahli_chat_history():
    """Save or update a chat history."""
    api_log(">>> POST /save_chat - Saving chat history", "SYSTEM")
    agent = get_agent()
    if not agent:
        api_log("Agent not loaded", "ERROR")
        return jsonify({"error": "Agent not loaded"}), 500
    
    try:
        data = request.json or {}
        history = data.get("history", [])
        chat_id = data.get("chat_id")
        
        api_log(f"Chat ID: {chat_id}, History length: {len(history)}", "SYSTEM")
        
        if chat_id:
            query = build_id_query(chat_id)
            
            update_data = {
                "chat": history,
                "updated_at": datetime.now().isoformat()
            }
            
            try:
                update_record(agent, 'IDEA', query, update_data)
                api_log(f"Chat updated successfully: {chat_id}", "SYSTEM")
            except Exception as update_err:
                api_log(f"Update failed: {update_err}", "ERROR")
            
            return jsonify({"status": "Success", "message": "Chat updated automatically", "type": "update"})
        
        else:
            title = data.get("title", "New Chat")
            api_log(f"Creating new chat: {title}", "SYSTEM")
            
            new_chat_record = {
                "type": "yahli_saved_chat",
                "content": title,
                "chat": history,
                "created_at": datetime.now().isoformat()
            }
            
            new_id = add_record(agent, 'IDEA', new_chat_record)
            new_id_str = str(new_id) if new_id else None
            
            api_log(f"New chat created with ID: {new_id_str}", "SYSTEM")
            return jsonify({
                "status": "Success", 
                "message": "Chat created", 
                "type": "create",
                "new_chat_id": new_id_str 
            })
    
    except Exception as e:
        api_log(f"Error saving chat: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@yahli_bp.route("/load_chat", methods=["POST"])
def load_yahli_chat_history():
    """Load a saved chat history."""
    api_log(">>> POST /load_chat - Loading chat history", "SYSTEM")
    agent = get_agent()
    if not agent:
        api_log("Agent not loaded", "ERROR")
        return jsonify({"error": "No agent"}), 500
    
    try:
        data = request.json or {}
        chat_id = data.get("chat_id")
        
        if not chat_id:
            api_log("No chat ID provided", "ERROR")
            return jsonify({"error": "No chat ID provided"}), 400

        api_log(f"Loading chat ID: {chat_id}", "SYSTEM")
        
        # Fix: Instead of fetching the entire DB, fetch only chats, drastically reducing search time
        chat_records = find_records(agent, 'IDEA', {'type': 'yahli_saved_chat'})
        target_record = None
        
        if isinstance(chat_records, list):
            for rec in chat_records:
                current_id = rec.get('_id') or rec.get('id') or rec.get('record_id') or rec.get('IDEA_id')
                if str(current_id) == str(chat_id):
                    target_record = rec
                    break
        
        history = []
        if target_record:
            history = target_record.get('chat', [])
            if isinstance(history, str):
                try:
                    import json
                    history = json.loads(history)
                except:
                    pass
            api_log(f"Chat loaded, history length: {len(history)}", "SYSTEM")
        else:
            api_log(f"Chat not found: {chat_id}", "WARNING")

        return jsonify({"history": history})
    except Exception as e:
        api_log(f"Error loading chat: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@yahli_bp.route("/delete_chat", methods=["POST"])
def delete_yahli_chat_endpoint():
    """Delete a saved chat."""
    api_log(">>> POST /delete_chat - Deleting chat", "SYSTEM")
    agent = get_agent()
    if not agent:
        api_log("Agent not loaded", "ERROR")
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json or {}
        chat_id = data.get("chat_id")

        if not chat_id:
            api_log("Missing chat_id", "ERROR")
            return jsonify({"error": "Missing chat_id"}), 400

        api_log(f"Deleting chat ID: {chat_id}", "SYSTEM")
        
        query = build_id_query(chat_id)
        result = delete_record(agent, 'IDEA', query)
        
        # Fallback if first delete attempt fails
        if result.get("error") or (isinstance(result, dict) and result.get("deleted_count") == 0):
            result = delete_record(agent, 'IDEA', {'record_id': chat_id})

        api_log(f"Chat deleted: {chat_id}", "SYSTEM")
        return jsonify({"status": "Success", "message": "Chat deleted", "details": result})

    except Exception as e:
        api_log(f"Error deleting chat: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


# Export the blueprint
__all__ = ['yahli_bp', 'set_active_agent']