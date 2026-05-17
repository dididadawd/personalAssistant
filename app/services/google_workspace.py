# app/services/google_workspace.py

import os
import json
import textwrap
from datetime import datetime, timedelta, timezone, date
from datetime import time as dt_time
from email.utils import parsedate_to_datetime
from typing import List, Dict, Any, Optional
import uuid
import re

# Google API Imports
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import gspread

# --- 1. Imports from our modules ---
from ..db.json_manager import find_records, update_record
from ..utils.logger import log_and_print
from ..core.llm_provider import api_key_manager, GEMINI_MODEL_NAME
from ..utils.date_helpers import is_valid_iso_date
# --- Constants & Paths ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CREDENTIALS_FILE = os.path.join(BASE_DIR, 'credentials.json')
TOKEN_FILE = os.path.join(BASE_DIR, 'token.json')
PERSONAS_DIR = os.path.join(BASE_DIR, "personas")

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/tasks',
    'https://www.googleapis.com/auth/spreadsheets.readonly'
]

# =========================================================================================
#  1. AUTHENTICATION HELPERS
# =========================================================================================

def _get_google_creds():
    """מנהל את הטוקן והתחברות לגוגל."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=8080)
            if creds is None:
                log_and_print("ERROR: OAuth authentication failed. Please ensure http://localhost:8080 is registered in Google Cloud Console redirect URIs.", "ERROR")
                return None
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return creds


# =========================================================================================
#  2. CALENDAR SERVICES
# =========================================================================================
import socket  
def get_calendar_service():
    """פונקציה חסינה לחלוטין לסורקי פורטים של אנטיוירוס."""
    creds = None
    
    # 1. Load existing token safely
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            log_and_print(f"Warning: Failed to load existing token: {e}", "WARNING")
            creds = None 
            
    if not creds or not creds.valid:
        try:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as refresh_error:
                    log_and_print(f"Warning: Token refresh failed ({refresh_error}), starting new flow.", "WARNING")
                    creds = None
            
            # 2. Trigger new Login Flow
            if not creds or not creds.valid:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                
                # --- CREATE A CUSTOM SCANNER-PROOF SERVER ---
                server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                
                port = 8080
                try:
                    server_socket.bind(('127.0.0.1', port))
                except OSError:
                    # If 8080 is blocked by another app, pick a dynamic port
                    server_socket.bind(('127.0.0.1', 0))
                    port = server_socket.getsockname()[1]
                    
                server_socket.listen(5)
                server_socket.settimeout(300) # Give you 5 minutes to log in
                
                # Set redirect URI and get the login URL
                flow.redirect_uri = f'http://127.0.0.1:{port}/'
                auth_url, _ = flow.authorization_url(prompt='consent')
                
                log_and_print("===================================================", "SYSTEM")
                log_and_print("ACTION REQUIRED: A Google Login link is ready.", "SYSTEM")
                log_and_print(f"PLEASE CLICK THIS LINK: {auth_url}", "SYSTEM")
                log_and_print("===================================================", "SYSTEM")
                
                auth_response = None
                log_and_print("Waiting for you to log in... (Ignoring background noise)", "SYSTEM")
                
                # --- The Loop That Defeats The Antivirus ---
                while True:
                    try:
                        client, addr = server_socket.accept()
                        request = client.recv(4096).decode('utf-8', 'ignore')
                        
                        # Check if this is the REAL response from Google (contains 'state=' and 'code=')
                        if request.startswith("GET /?") and "state=" in request and "code=" in request:
                            first_line = request.split("\r\n")[0]
                            path = first_line.split(" ")[1]
                            auth_response = f"http://127.0.0.1:{port}{path}"
                            
                            # Send success message back to the browser
                            resp_body = "Authentication successful! You can safely close this tab."
                            response = (
                                "HTTP/1.1 200 OK\r\n"
                                "Content-Type: text/plain; charset=utf-8\r\n"
                                f"Content-Length: {len(resp_body)}\r\n"
                                "Connection: close\r\n\r\n"
                                f"{resp_body}"
                            )
                            client.sendall(response.encode('utf-8'))
                            client.close()
                            break # We got the real code! Exit the listener loop.
                            
                        else:
                            # It's a fake background scanner/antivirus. Send empty response and KEEP LISTENING.
                            client.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n")
                            client.close()
                            
                    except socket.timeout:
                        server_socket.close()
                        raise Exception("Timed out waiting for authorization (5 minutes passed).")
                        
                server_socket.close()
                
                # Finish the OAuth flow (requires https protocol string)
                auth_response = auth_response.replace('http://', 'https://')
                flow.fetch_token(authorization_response=auth_response)
                creds = flow.credentials
                
            if creds is None:
                log_and_print("ERROR: OAuth authentication failed.", "ERROR")
                raise Exception("Calendar authentication failed")
                
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
                
        except Exception as oauth_error:
            log_and_print(f"ERROR: OAuth flow failed: {oauth_error}", "ERROR")
            raise

    return build('calendar', 'v3', credentials=creds)

def list_calendar_events(agent, query: str = None, start_date_iso: str = None, end_date_iso: str = None, days_ahead: int = 30):
    """
    מציג אירועים מהיומן.
    *** גרסה משופרת: מבצעת חיפוש מדויק תחילה, ורק אם נכשל, עוברת לחיפוש סמנטי בעזרת AI. ***
    """
    log_and_print(f"--- Fetching events from calendar (range: {start_date_iso} to {end_date_iso})... ---", "SYSTEM")
    try:
        service = get_calendar_service()
        
        # --- חלק בניית התאריכים (נשאר זהה) ---
        if days_ahead == 0 and not start_date_iso and not end_date_iso:
            today = date.today()
            time_min_dt = datetime.combine(today, dt_time.min, tzinfo=timezone.utc)
            time_max_dt = datetime.combine(today, dt_time.max, tzinfo=timezone.utc)
        else:
            now_utc = datetime.now(timezone.utc)
            if start_date_iso:
                start_dt = datetime.fromisoformat(start_date_iso)
                time_min_dt = datetime.combine(start_dt.date(), dt_time.min, tzinfo=timezone.utc)
            else:
                time_min_dt = now_utc
            if end_date_iso:
                end_dt = datetime.fromisoformat(end_date_iso)
                time_max_dt = datetime.combine(end_dt.date(), dt_time.max, tzinfo=timezone.utc)
            else:
                time_max_dt = now_utc + timedelta(days=days_ahead)
            
        time_min_rfc = time_min_dt.isoformat().replace('+00:00', 'Z')
        time_max_rfc = time_max_dt.isoformat().replace('+00:00', 'Z')
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min_rfc,
            timeMax=time_max_rfc,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        all_events_in_range = events_result.get('items', [])
        
        # --- התחלת הלוגיקה הדו-שלבית החדשה ---
        
        filtered_events = []
        if query:
            # שלב 1: נסה למצוא התאמה מדויקת (case-insensitive)
            log_and_print(f"   - Step 1: Attempting precise search for '{query}'...", "SYSTEM")
            query_lower = query.lower().strip()
            precise_matches = [
                event for event in all_events_in_range
                if event.get('summary', '').lower().strip() == query_lower
            ]

            if precise_matches:
                log_and_print(f"   - Precise search succeeded! Found {len(precise_matches)} events.", "SYSTEM")
                filtered_events = precise_matches
            else:
                # שלב 2: אם החיפוש המדויק נכשל, הפעל את מנגנון הגיבוי הסמנטי
                log_and_print(f"   - Precise search failed. Activating step 2: Broad semantic search using AI...", "SYSTEM")
                try:
                    semantic_prompt = textwrap.dedent(f"""
                        You are a Hebrew language expert. Your task is to expand a search query with related concepts.
                        User's search term: "{query}"
                        Generate a comma-separated list of related Hebrew words or short phrases. Include synonyms and related activities. Keep the original term in the list.
                        Example for "טיסה": טיסה,חופשה,חו"ל,טיול,נסיעה לחו"ל
                        Example for "חזרה": חזרה,חזרות,אימון
                        Your response MUST be only the comma-separated list.
                    """)
                    response = api_key_manager.generate_content(semantic_prompt, model_name=GEMINI_MODEL_NAME)
                    
                    original_terms = [term.strip().lower() for term in response.strip().split(',')]
                    log_and_print(f"   - AI expanded search to terms: {', '.join(original_terms)}", "SYSTEM")
                    
                    final_search_terms_set = set()
                    for term in original_terms:
                        final_search_terms_set.add(term)
                        sanitized_term = re.sub(r'[^א-תa-zA-Z0-9\s]', '', term)
                        if sanitized_term and sanitized_term != term:
                            final_search_terms_set.add(sanitized_term)

                    search_terms = list(final_search_terms_set)
                    
                    # בצע את הסינון הרחב על בסיס המונחים מה-AI
                    filtered_events = [
                        event for event in all_events_in_range
                        if any(term in event.get('summary', '').lower() for term in search_terms)
                    ]

                except Exception as e:
                    log_and_print(f"   - WARNING: Semantic search using AI failed ({e}), falling back to basic search.", "SYSTEM")
                    # מנגנון גיבוי למקרה שה-AI נכשל
                    base_query = query_lower
                    if base_query.endswith('ות'): base_query = base_query[:-2]
                    elif base_query.endswith('ים'): base_query = base_query[:-2]
                    
                    filtered_events = [
                        event for event in all_events_in_range
                        if base_query in event.get('summary', '').lower()
                    ]
        else:
            # אם לא סופקה שאילתה, החזר את כל האירועים בטווח
            filtered_events = all_events_in_range

        # --- סוף הלוגיקה הדו-שלבית ---

        if not filtered_events:
            return {"status": "No upcoming events found matching the criteria.", "events": []}

        # --- הכנת הפלט (נשאר זהה) ---
        event_list = []
        for event in filtered_events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            event_list.append({
                "id": event.get('id'), # הוספת ID
                "summary": event.get('summary'),
                "start_time": start,
                "end_time": end,
                "colorId": event.get('colorId') # <-- הוספת מזהה הצבע
            })
            
        return {"status": "Success", "events": event_list}

    except HttpError as error:
        #logger.error(f"Google Calendar API error: {error}")
        return {"error": f"A Google Calendar API error occurred: {error.reason}"}
    except Exception as e:
        #logger.error(f"Error fetching calendar events: {e}", exc_info=True)
        return {"error": str(e)}



def create_calendar_event(agent, events: List[Dict[str, str]]):
    """
    יוצר אירוע חדש אחד או יותר ביומן על בסיס רשימת אירועים, עם תמיכה בצבעים.
    *** משודרג: מקבל רשימה של אובייקטי אירועים, כולל color_id אופציונלי. ***
    """
    if not isinstance(events, list) or not events:
        return {"error": "Invalid input. The 'events' parameter must be a non-empty list of event objects."}

    log_and_print(f"--- Starting process to create {len(events)} calendar events (with color support)... ---", "SYSTEM")
    
    service = get_calendar_service()
    created_events = []
    failed_events = []

    for i, event_data in enumerate(events):
        summary = event_data.get('summary')
        start_time = event_data.get('start_time')
        end_time = event_data.get('end_time')
        description = event_data.get('description') # אופציונלי
        color_id = event_data.get('color_id')       # <-- **התוספת החדשה**

        if not summary or not start_time or not end_time:
            error_msg = f"Event {i+1} ('{summary or 'No Summary'}') is missing required fields (summary, start_time, end_time)."
            #logger.error(error_msg)
            failed_events.append({"event_data": event_data, "error": error_msg})
            continue

        try:
            datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            error_msg = f"Event {i+1} ('{summary}') has an invalid timestamp format. Use full ISO 8601 format."
            #logger.error(error_msg)
            failed_events.append({"event_data": event_data, "error": error_msg})
            continue
            
        # --- **ולידציה חדשה עבור הצבע** ---
        if color_id and not (isinstance(color_id, str) and color_id.isdigit() and 1 <= int(color_id) <= 11):
            log_and_print(f"   - Invalid color_id '{color_id}' for event '{summary}'. It must be a string from '1' to '11'. Ignoring color.", "WARNING")
            color_id = None # אפס את הצבע כדי למנוע שגיאת API

        try:
            event_body = {
                'summary': summary,
                'description': description,
                'start': {'dateTime': start_time, 'timeZone': 'Asia/Jerusalem'},
                'end': {'dateTime': end_time, 'timeZone': 'Asia/Jerusalem'},
            }
            
            # --- **הוספת הצבע לגוף הבקשה אם הוא תקין** ---
            if color_id:
                event_body['colorId'] = color_id
            
            created_event_result = service.events().insert(calendarId='primary', body=event_body).execute()
            created_events.append({
                "summary": created_event_result['summary'], 
                "link": created_event_result.get('htmlLink')
            })

        except Exception as e:
            error_msg = f"API error while creating event '{summary}': {e}"
            #logger.error(error_msg, exc_info=True)
            failed_events.append({"event_data": event_data, "error": str(e)})

    status = "Partial success"
    if not failed_events:
        status = "All events created successfully."
    elif not created_events:
        status = "All events failed to create."

    return {
        "status": status,
        "created_count": len(created_events),
        "failed_count": len(failed_events),
        "created_events": created_events,
        "failed_events": failed_events
    }


def find_conflicting_events(agent, primary_event_query: str, secondary_event_query: str):
    """
    כלי-על מומחה עם יכולת תיקון עצמי. מוצא אירועים מסוג אחד שמתרחשים בתוך טווח הזמן של אירוע אחר.
    שלב 1: מנסה חיפוש מילולי.
    שלב 2 (גיבוי): אם החיפוש נכשל, מבצע חיפוש סמנטי בעזרת AI כדי למצוא את האירוע הסביר ביותר.
    """
    log_and_print(f"--- Starting conflict detection: Searching for '{secondary_event_query}' within '{primary_event_query}'... ---", "SYSTEM")

    # --- שלב א': ניסיון #1 - חיפוש מילולי ומדויק ---
    log_and_print(f"   - Step 1 (Attempt 1): Locating primary event with literal search ('{primary_event_query}')...", "SYSTEM")
    primary_events_result = list_calendar_events(agent, query=primary_event_query, days_ahead=365)
    
    if primary_events_result.get("error"):
        return {"error": f"Failed to search for the primary event: {primary_events_result['error']}"}

    found_primary_events = primary_events_result.get("events", [])
    primary_event = None

    if len(found_primary_events) == 1:
        primary_event = found_primary_events[0]
    elif len(found_primary_events) > 1:
        # אם יש יותר מדי תוצאות מדויקות, זה עדיין מצב שדורש הבהרה
        event_names = [f"'{e.get('summary')}'" for e in found_primary_events]
        return {"error": f"I found multiple events matching '{primary_event_query}': {', '.join(event_names)}. Please be more specific."}

    # --- שלב ב': ניסיון #2 - מנגנון גיבוי עם חיפוש סמנטי חכם ---
    if not primary_event:
        log_and_print(f"   - Literal search failed. Activating step 2 (Attempt 2): Smart semantic search...", "SYSTEM")
        
        # 1. אחזר את כל האירועים הקרובים כדי לתת ל-AI הקשר מלא
        all_upcoming_events_raw = list_calendar_events(agent, days_ahead=180) # חפש בחצי השנה הקרובה
        all_upcoming_events = all_upcoming_events_raw.get("events", [])
        
        if not all_upcoming_events:
            return {"error": "I couldn't find any upcoming events in your calendar at all to search through."}

        # 2. פרומפט ממוקד שמנחה את ה-AI לבחור את האירוע הנכון
        selection_prompt = textwrap.dedent(f"""
            You are an intelligent calendar assistant. The user is trying to find a specific event based on a general description.
            Your task is to analyze the user's query and select the single most likely event from the list of actual calendar events.

            **User's search query:** "{primary_event_query}"

            **List of actual upcoming events from the user's calendar:**
            ```json
            {json.dumps(all_upcoming_events, ensure_ascii=False, indent=2)}
            ```

            **Your decision:**
            Based on the user's query, which event from the list is the most probable match?
            Your response MUST be ONLY the JSON object of the single best matching event, copied exactly from the list above.
            If no event seems like a reasonable match, return an empty JSON object {{}}.
        """)
        
        try:
            selection_response = api_key_manager.generate_content(selection_prompt, model_name=GEMINI_MODEL_NAME)
            cleaned_response = selection_response.strip().replace("```json", "").replace("```", "")
            primary_event = json.loads(cleaned_response)
            
            if not primary_event or 'summary' not in primary_event:
                return {"error": f"I couldn't find any upcoming event that seems related to '{primary_event_query}' in your calendar."}

            log_and_print(f"   - Smart search succeeded! AI identified the relevant event: '{primary_event.get('summary')}'", "SYSTEM")

        except (json.JSONDecodeError, ValueError) as e:
            return {"error": f"AI failed to select a matching event: {e}"}

    # --- שלב ג': המשך התהליך עם האירוע שנבחר (בין אם בחיפוש מילולי או חכם) ---
    start_time = primary_event.get("start_time")
    end_time = primary_event.get("end_time")

    start_date_iso = start_time.split('T')[0] if 'T' in start_time else start_time
    end_date_iso = end_time.split('T')[0] if 'T' in end_time else end_time
    
    log_and_print(f"   - Step 3: Detected date range is {start_date_iso} to {end_date_iso}.", "SYSTEM")
    log_and_print(f"   - Step 4: Searching for secondary events ('{secondary_event_query}') within the determined range...", "SYSTEM")

    conflicting_events_result = list_calendar_events(
        agent,
        query=secondary_event_query,
        start_date_iso=start_date_iso,
        end_date_iso=end_date_iso
    )

    # ... (שאר הקוד של הפונקציה נשאר זהה) ...
    if conflicting_events_result.get("error"):
        return {"error": f"Failed to search for conflicting events: {conflicting_events_result['error']}"}

    found_conflicts = conflicting_events_result.get("events", [])

    if not found_conflicts:
        return {
            "status": "Success",
            "message": f"Great news! I found the event '{primary_event.get('summary')}' from {start_date_iso} to {end_date_iso}, and there are no '{secondary_event_query}' scheduled during that time.",
            "conflicts": []
        }
        
    return {
        "status": "Success",
        "primary_event": primary_event,
        "conflicts": found_conflicts
    }


# =========================================================================================
#  3. GMAIL SERVICES
# =========================================================================================

def get_gmail_service():
    """פונקציה סטנדרטית להתחברות ל-Gmail API."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=8080)
            if creds is None:
                log_and_print("ERROR: OAuth authentication failed for Gmail. Please ensure http://localhost:8080 is registered in Google Cloud Console.", "ERROR")
                raise Exception("Gmail authentication failed")
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)


def check_gmail_for_updates(agent, days_back: int = 7, client_name_filter: str = None):
    """
    סורק את Gmail, מסכם מיילים חדשים, ומתעד אותם אוטומטית עם תאריך השליחה המקורי.
    *** גרסה מתוקנת: שומרת את תאריך שליחת המייל, לא את תאריך הסריקה. ***
    """
    persona_name = agent.name
    
    kb_path = os.path.join(PERSONAS_DIR, persona_name, "knowledge_base")
    communications_file_path = os.path.join(kb_path, "communications.json")
    existing_message_ids = set()
    #try:
    if os.path.exists(communications_file_path) and os.path.getsize(communications_file_path) > 0:
        with open(communications_file_path, 'r', encoding='utf-8') as f:
            all_comms = json.load(f)
            for comm in all_comms:
                if comm.get('email_message_id'):
                    existing_message_ids.add(comm['email_message_id'])
    #except Exception as e:
        
        #logger.warning(f"Could not load existing communications, starting fresh. Error: {e}")

    try:
        service = get_gmail_service()
        clients_to_scan = []
        if client_name_filter:
            found_clients = find_records(agent, 'clients', {'search_term': client_name_filter})
            if found_clients and not isinstance(found_clients, str):
                clients_to_scan = found_clients
            else:
                return {"status": "Client not found", "client_name": client_name_filter}
        else:
            all_clients = find_records(agent, 'clients', {})
            if not (isinstance(all_clients, str) and "Error" in all_clients):
                clients_to_scan = all_clients

        if not clients_to_scan:
            return {"status": "No clients found to scan."}

        all_updates = []
        new_emails_processed_count = 0
        for client in clients_to_scan:
            client_name = find_field_by_substring(client, 'name')
            client_email = find_field_by_substring(client, 'email')
            
            if not client_email or not client_name:
                continue
            
            query = f'from:{client_email.strip()} newer_than:{days_back}d'
            results = service.users().messages().list(userId='me', q=query).execute()
            messages = results.get('messages', [])

            if messages:
                for msg in messages:
                    message_id = msg['id']
                    
                    if message_id in existing_message_ids:
                        continue

                    new_emails_processed_count += 1
                    
                    msg_data = service.users().messages().get(userId='me', id=message_id).execute()
                    payload = msg_data.get('payload', {})
                    headers = payload.get('headers', [])
                    subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
                    
                    # ===> התחלת התיקון: חילוץ ועיבוד תאריך השליחה המקורי <===
                    # קובעים תאריך נוכחי כברירת מחדל למקרה שהחילוץ ייכשל
                    email_sent_date = date.today().strftime("%Y-%m-%d") 
                    #try:
                    # 1. חפש את ה-'Date' בכותרות (headers) של המייל
                    date_header = next((h['value'] for h in headers if h['name'].lower() == 'date'), None)
                    if date_header:
                        # 2. המר את טקסט התאריך לאובייקט datetime (יעבוד בזכות ה-import החדש)
                        dt_object = parsedate_to_datetime(date_header)
                        if dt_object:
                            # 3. פרמט את האובייקט למבנה YYYY-MM-DD
                            email_sent_date = dt_object.strftime("%Y-%m-%d")
                    #except Exception as e:
                        #logger.warning(f"Could not parse date from email header for message {message_id}. Falling back to current date. Error: {e}")
                    # ===> סוף התיקון <===
                    
                    summarization_prompt = textwrap.dedent(f"""
                        Summarize the following email's main point in one short, clear sentence in Hebrew.
                        Subject: "{subject}"
                        Snippet: "{msg_data.get('snippet', '')}"
                        One-sentence summary:
                    """)
                    
                    try:
                        summary_response = api_key_manager.generate_content(summarization_prompt, model_name=GEMINI_MODEL_NAME)
                        ai_summary = summary_response.strip().replace("*", "")
                    except Exception as e:
                        #logger.error(f"AI summarization failed: {e}")
                        ai_summary = f"Subject: {subject}"
                    
                    update_text = f"מייל חדש מלקוח '{client_name}'. נושא: {subject}. סיכום: {ai_summary}"
                    all_updates.append({"client_name": client_name, "update": update_text})
                    
                    communication_data = {
                        "communication_id": str(uuid.uuid4().hex),
                        "email_message_id": message_id,
                        "client_name": client_name,
                        "communication_type": "Email (Auto-detected)",
                        "summary": f"Subject: {subject} | Summary: {ai_summary}",
                        # ===> שימוש בתאריך המקורי שחולץ מהמייל <===
                        "date": email_sent_date
                    }
                    
                    records = []
                    if os.path.exists(communications_file_path) and os.path.getsize(communications_file_path) > 0:
                        with open(communications_file_path, 'r', encoding='utf-8') as f:
                            records = json.load(f)
                    records.append(communication_data)
                    with open(communications_file_path, 'w', encoding='utf-8') as f:
                        json.dump(records, f, indent=2, ensure_ascii=False)

        if new_emails_processed_count == 0:
            return {"status": "No NEW emails found. All recent emails from specified clients are already logged."}

        return {"status": "Success", "updates": all_updates}

    except HttpError as error:
        return {"error": f"Gmail API error. Details: {error}"}
    except Exception as e:
        #logger.error(f"An unexpected error occurred in check_gmail_for_updates: {e}", exc_info=True)
        return {"error": f"An unexpected error occurred: {e}"}

# =========================================================================================
#  4. TASKS SERVICES
# =========================================================================================

def get_tasks_service():
    """מתחברת באופן מאובטח ל-Google Tasks API ומחזירה אובייקט service."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=8080)
            if creds is None:
                log_and_print("ERROR: OAuth authentication failed for Tasks. Please ensure http://localhost:8080 is registered in Google Cloud Console.", "ERROR")
                raise Exception("Tasks authentication failed")
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    # השינוי המרכזי: בניית שירות 'tasks' בגרסה 'v1'
    return build('tasks', 'v1', credentials=creds)


def create_google_task(agent, task_list_id: str, tasks: list):
    """
    יוצרת משימה אחת או יותר ב-Google Tasks.
    *** גרסה מותאמת לסביבת הקוד הנוכחית, קוראת ל-get_tasks_service() באופן ישיר. ***
    """
    if not isinstance(tasks, list) or not tasks:
        return {"error": "Input 'tasks' must be a non-empty list."}

    log_and_print(f"--- Starting creation of {len(tasks)} tasks in Google Tasks... ---", "SYSTEM")

    created_tasks_details = []
    errors = []

    try:
        # השינוי המרכזי: קבלת אובייקט ה-service ישירות מפונקציית העזר
        service = get_tasks_service()
    except Exception as e:
        error_msg = f"Failed to get Google Tasks service. Error: {e}"
        #logger.error(error_msg, exc_info=True)
        return {"error": error_msg}

    for task_data in tasks:
        if 'title' not in task_data or 'due' not in task_data:
            errors.append({
                "task_data": task_data,
                "error": "Task data is missing required fields 'title' or 'due'."
            })
            continue

        task_body = {
            'title': task_data.get('title'),
            'notes': task_data.get('notes'),
            'due': task_data.get('due')
        }

        try:
            created_task = service.tasks().insert(
                tasklist=task_list_id,
                body=task_body
            ).execute()
            
            created_tasks_details.append(created_task)
            log_and_print(f"  (+) Task created: '{created_task.get('title')}'", "SUCCESS")

        except HttpError as e:
            error_details = f"Failed to create task '{task_data.get('title')}'. Reason: {e}"
            #logger.error(error_details)
            errors.append({
                "task_data": task_data,
                "error": error_details
            })

    result = {
        "created_count": len(created_tasks_details),
        "created_tasks": created_tasks_details,
        "errors": errors
    }

    return result


def get_primary_task_list_id(agent):
    """
    מתחברת ל-Google Tasks ומחזירה את המזהה (ID) של רשימת המשימות הראשית.
    """
    try:
        service = get_tasks_service()
        task_lists = service.tasklists().list().execute()
        items = task_lists.get('items')
        if items:
            # בדרך כלל, הרשימה הראשונה היא רשימת ברירת המחדל
            return items[0].get('id')
        else:
            log_and_print("No task lists found in account.", "ERROR")
            return None
    except HttpError as e:
        log_and_print(f"API error while retrieving task lists: {e}", "ERROR")
        return None
# =========================================================================================
#  5. SHEETS / GSPREAD
# =========================================================================================


def get_gspread_client():
    """
    מתחבר ל-API של גוגל. 
    כולל תיקון לשגיאת insecure_transport.
    """    
    # --- התיקון לשגיאה שקיבלת ---
    # שורה זו מאפשרת שימוש ב-http עבור localhost בלי לצעוק על אבטחה
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    


    creds = None
    
    # 1. ניסיון טעינת טוקן קיים
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            print("Token file corrupted, deleting...")
            os.remove(TOKEN_FILE)
            creds = None

    # 2. אם הטוקן לא תקין או לא קיים
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds:
            # אתחול ה-Flow
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            
            # הגדרת כתובת "דמה" מקומית
            flow.redirect_uri = 'http://localhost:8080/'

            # יצירת הקישור לאישור
            auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')

            print("\n" + "="*60)
            print(" שלב 1: לחץ על הקישור הבא (או העתק אותו לדפדפן):")
            print(auth_url)
            print("="*60)
            print(" שלב 2: אשר את הגישה בחשבון הגוגל שלך.")
            print(" שלב 3: כשהדפדפן מציג שגיאה ב-localhost, העתק את הכתובת המלאה למעלה.")
            print("="*60)
            
            # קבלת הכתובת מהמשתמש
            code_response = input("הדבק כאן את הכתובת המלאה שהועתקה: ").strip()

            try:
                # כעת זה יעבוד גם עם http בזכות השורה שהוספנו למעלה
                flow.fetch_token(authorization_response=code_response)
                creds = flow.credentials
            except Exception as e:
                print(f"שגיאה בפענוח הכתובת: {e}")
                # אם עדיין יש בעיה, נדפיס אותה אבל לא נתרסק מיד כדי שתוכל לראות
                return None

        # שמירת הטוקן
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    
    return gspread.authorize(creds)



def import_google_sheet(agent, sheet_url: str, table_name: str = None):
    """
    מייבא נתונים מגיליון Google Sheet ישירות לטבלת JSON חדשה במערכת.
    """
    # 1. בדיקת תקינות קלט (Validation) - מונע את השגיאה שקיבלת
    if not sheet_url or not isinstance(sheet_url, str):
        return {"status": "Error", "message": "Invalid or missing 'sheet_url'. Please provide a valid Google Sheets URL."}
    
    if not table_name or not isinstance(table_name, str):
        return {"status": "Error", "message": "Invalid or missing 'table_name'."}

    # --- בדיקת חוסר שדות ואינטראקציה עם המשתמש ---
    if not table_name:
        return {
            "status": "Awaiting Input",
            "message": "תן שם לטבלה החדשה שתיקלוט את נתוני הגיליון (למשל: 'imported_leads'):",
            "state": {
                "next_action": "import_google_sheet",
                "sheet_url": sheet_url,
                "missing_field": "table_name"
            }
        }
    
    log_and_print(f"--- 📥 Starting import from Google Sheet to table '{table_name}' ---", "SYSTEM")

    try:
        # 2. התחברות ופתיחת הגיליון
        gc = get_gspread_client()
        if not gc:
            return {"status": "Error", "message": "Failed to initialize Google Sheets client (Auth failed)."}

        spreadsheet = gc.open_by_url(sheet_url)
        
        # בדיקה שהגיליון קיים (לפעמים sheet1 לא קיים אם שינו שם)
        try:
            worksheet = spreadsheet.sheet1
        except:
            worksheet = spreadsheet.get_worksheet(0) # לוקח את הראשון לפי אינדקס אם השם אינו 'Sheet1'

        # 3. קריאת כל הנתונים
        # שימוש ב-expected_headers=[] מונע שגיאות אם יש תאים ריקים בכותרת, אבל get_all_records הכי נח
        records = worksheet.get_all_records()

        if not records:
            return {"status": "Info", "message": "The Google Sheet is empty or headers are missing."}
        
        log_and_print(f"   - Found {len(records)} rows in spreadsheet.", "SYSTEM")

        # 4. יצירת סכמה (Schema) וניקוי שם הטבלה
        clean_table_name = re.sub(r'[^a-zA-Z0-9_]', '', table_name).lower()
        if not clean_table_name:
            clean_table_name = "imported_table"

        headers = records[0].keys()
        id_field = f"{clean_table_name.rstrip('s')}_id"
        
        columns_schema = {id_field: "string"}
        for h in headers:
            # המרת כותרות ריקות למשהו תקין אם יש
            safe_header = h if h else f"col_{uuid.uuid4().hex}"
            columns_schema[safe_header] = "string"

        # 5. עדכון הקונפיגורציה של הסוכן
        if 'data_schema' not in agent.config: 
            agent.config['data_schema'] = {}
            
        agent.config['data_schema'][clean_table_name] = columns_schema
        agent.save_persona()

        # 6. הוספת ID ייחודי ושמירה
        final_records = []
        for rec in records:
            new_rec = rec.copy()
            # מוודא שכל המפתחות הם מחרוזות (למקרה של כותרות מספריות)
            new_rec = {str(k): v for k, v in new_rec.items()}
            new_rec[id_field] = uuid.uuid4().hex
            final_records.append(new_rec)

        kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
        if not os.path.exists(kb_path):
            os.makedirs(kb_path) # יצירת התיקייה אם לא קיימת

        file_path = os.path.join(kb_path, f"{clean_table_name}.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(final_records, f, indent=2, ensure_ascii=False)

        return {
            "status": "Success",
            "message": f"Successfully imported {len(final_records)} records from Google Sheet into table '{clean_table_name}'."
        }

    except gspread.exceptions.SpreadsheetNotFound:
        return {"error": "Spreadsheet not found. Please check the URL and make sure you have permission to view it (Share with the service account email)."}
    except gspread.exceptions.APIError as e:
        return {"error": f"Google API Error: {e}"}
    except Exception as e:
        import traceback
        traceback.print_exc() # ידפיס את השגיאה המלאה לטרמינל לדיבוג
        return {"error": f"An error occurred: {str(e)}"}


# =========================================================================================
#  PRIVATE HELPERS
# =========================================================================================

def find_field_by_substring(record: dict, substring: str):
    """
    פונקציית עזר גמישה.
    מחפשת במילון את המפתח הראשון שמכיל מחרוזת מסוימת (ללא תלות באותיות גדולות/קטנות)
    ומחזירה את הערך שלו.
    
    לדוגמה, חיפוש 'email' יתאים גם ל-'contact_email' וגם ל-'Email_Address'.
    
    :param record: המילון (רשומת הלקוח) שבו יש לחפש.
    :param substring: המחרוזת שיש לחפש בתוך שמות המפתחות.
    :return: הערך של השדה שנמצא, או None אם לא נמצא מפתח תואם.
    """
    for key, value in record.items():
        if substring.lower() in key.lower():
            return value
    return None

