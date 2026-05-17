# app/api/calendar_routes.py
"""
API Routes for Calendar Events endpoints.
"""

import os
from flask import Blueprint, request, jsonify
from googleapiclient.discovery import build

# Import Google Workspace functions
from ..services.google_workspace import list_calendar_events, create_calendar_event, TOKEN_FILE, SCOPES

# Import database functions
from ..db.json_manager import find_records, delete_record

# Import utilities
from ..utils.logger import log_and_print

# Import agent directly from main (to get the actual instance)
from ..scheduler.jobs import _active_agent as scheduler_agent

# Create blueprint
calendar_bp = Blueprint('calendar', __name__)

# Global reference to the active agent instance
_active_agent = None

def set_active_agent(agent):
    global _active_agent
    _active_agent = agent

def get_agent():
    """Get the active agent from multiple sources."""
    # 1. First check local _active_agent (set via set_active_agent in main.py)
    global _active_agent
    if _active_agent:
        return _active_agent
    
    # 2. Fallback: try to get from scheduler's global agent
    try:
        from ..scheduler import jobs as sched_module
        if hasattr(sched_module, '_active_agent') and sched_module._active_agent:
            return sched_module._active_agent
    except:
        pass
    
    # 3. Final fallback: try from current_app config
    try:
        from flask import current_app
        return current_app.config.get('ACTIVE_AGENT')
    except:
        pass
    
    return None


# =========================================================================================
#  Calendar Routes
# =========================================================================================

@calendar_bp.route('', methods=['GET'])
def get_calendar_events_for_fc():
    """Get calendar events for frontend calendar."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    start_date_iso = request.args.get('start')
    end_date_iso = request.args.get('end')

    try:
        events_result = list_calendar_events(
            agent=agent, 
            start_date_iso=start_date_iso, 
            end_date_iso=end_date_iso
        )
        
        if isinstance(events_result, dict) and "error" in events_result:
            log_and_print(f"Error fetching from Google Calendar: {events_result['error']}", "ERROR")
            return jsonify([]), 500
            
        raw_events = events_result.get("events", []) if isinstance(events_result, dict) else []
        
        # --- Start of critical fix: Conversion to FullCalendar format ---
        formatted_events = []
        import uuid # Ensure uuid import exists at top of file
        
        for event in raw_events:
            start = event.get('start_time')
            end = event.get('end_time')
            is_all_day = 'T' not in start if start else False

            formatted_events.append({
                'id': event.get('id', str(uuid.uuid4())),
                'title': event.get('summary', 'Untitled Event'),
                'start': start,
                'end': end,
                'allDay': is_all_day,
                'extendedProps': {
                    'colorId': event.get('colorId')
                }
            })
            
        return jsonify(formatted_events)
        # --- End of critical fix ---

    except Exception as e:
        log_and_print(f"Error in get_calendar_events_for_fc: {e}", "ERROR")
        return jsonify([]), 500


@calendar_bp.route('/create', methods=['POST'])
def create_calendar_event_endpoint():
    """Create a new calendar event."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json or {}
        events = data.get('events', [])
        
        if not events:
            return jsonify({"error": "Missing events array"}), 400
        
        result = create_calendar_event(agent, events)
        return jsonify(result)
    except Exception as e:
        log_and_print(f"Error creating calendar event: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@calendar_bp.route('/delete', methods=['POST'])
def delete_calendar_event_endpoint():
    """
    Delete a Google Calendar event by ID
    """
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json or {}
        event_id = data.get("event_id")

        if not event_id:
            return jsonify({"error": "Missing event_id"}), 400

        creds = None
        if os.path.exists(TOKEN_FILE):
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        
        if not creds or not creds.valid:
            return jsonify({"error": "Google Credentials invalid"}), 401

        service = build('calendar', 'v3', credentials=creds)

        service.events().delete(calendarId='primary', eventId=event_id).execute()
        
        return jsonify({"status": "Success", "message": "Event deleted successfully"})

    except Exception as e:
        log_and_print(f"Error deleting calendar event: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


# =========================================================================================
#  Alias Routes for frontend compatibility
# =========================================================================================

@calendar_bp.route('/events', methods=['GET'])
def get_calendar_events_alias():
    """Get events for frontend calendar."""
    return get_calendar_events_for_fc()


@calendar_bp.route('/events/create', methods=['POST'])
def create_calendar_event_alias():
    """Alias for /api/calendar/events/create (frontend compatibility)."""
    return create_calendar_event_endpoint()


@calendar_bp.route('/events/delete', methods=['POST'])
def delete_calendar_event_alias():
    """Alias for /api/calendar/events/delete (frontend compatibility)."""
    return delete_calendar_event_endpoint()


# Export the blueprint
__all__ = ['calendar_bp', 'set_active_agent']