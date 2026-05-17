# app/services/analytics.py

import os
import json
import io
import base64
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta

# --- 1. Imports from our modules ---
from ..db.json_manager import find_records
from ..utils.logger import log_and_print
from ..core.llm_provider import api_key_manager, GEMINI_MODEL_NAME
from ..prompts.system_prompts import calculate_strategic_price_pricing_promt
# Import from the journal for workload calculation in pricing
from .google_workspace import list_calendar_events 

# Configure matplotlib for headless operation (required for servers)
plt.switch_backend('Agg')

# =========================================================================================
#  1. Client Analysis and Communication
# =========================================================================================

def find_clients_to_contact(agent, days_since_last_contact: int = 90):
    """
    Analytics tool: Finds clients who haven't had documented communication
    in the specified number of days. Accepts the number of days as a parameter.
    """
    log_and_print(f"--- Starting analysis to locate 'dormant' clients (no contact in the last {days_since_last_contact} days)... ---", "SYSTEM")

    all_clients = find_records(agent, 'clients', {})
    all_comms = find_records(agent, 'communications', {})

    if isinstance(all_clients, str) or not all_clients:
        return {"error": "Could not retrieve client list or it is empty."}
    
    last_contact_dates = {}
    if not isinstance(all_comms, str):
        for comm in all_comms:
            client_name = comm.get('client_name')
            date_str = comm.get('date')
            if not client_name or not date_str:
                continue

            try:
                current_date = datetime.strptime(date_str, "%Y-%m-%d")
                if client_name not in last_contact_dates or current_date > last_contact_dates[client_name]:
                    last_contact_dates[client_name] = current_date
            except ValueError:
                #logger.warning(f"Could not parse date '{date_str}' for client '{client_name}'. Skipping.")
                continue

    dormant_clients = []
    today = datetime.now()
    threshold_date = today - timedelta(days=days_since_last_contact)

    for client in all_clients:
        client_name = find_field_by_substring(client, 'name')
        if not client_name:
            continue

        if client_name in last_contact_dates:
            if last_contact_dates[client_name] < threshold_date:
                days_ago = (today - last_contact_dates[client_name]).days
                dormant_clients.append({
                    "name": client_name,
                    "last_contact_days_ago": days_ago
                })
        else:
            dormant_clients.append({
                "name": client_name,
                "last_contact_days_ago": "No record"
            })
            
    if not dormant_clients:
        return {"status": f"Great! You have been in touch with all clients within the last {days_since_last_contact} days."}

    return {"status": "Found clients needing contact", "clients": dormant_clients}

def find_field_by_substring(record: dict, substring: str):
    """
    Flexible helper function.
    Searches the dictionary for the first key containing a specific substring (case-insensitive)
    and returns its value.
    
    For example, searching for 'email' will match both 'contact_email' and 'Email_Address'.
    
    :param record: The dictionary (client record) to search in.
    :param substring: The substring to search for within key names.
    :return: The value of the found field, or None if no matching key is found.
    """
    for key, value in record.items():
        if substring.lower() in key.lower():
            return value
    return None



def find_and_sort_communications(agent, table_name: str, client_name: str):
    """
    Finds all communications for a specific client and sorts them by date, from oldest to newest.
    
    Args:
        agent: The agent object.
        table_name (str): The table name to search (will be automatically passed as "communications").
        client_name (str): The client name to search for.

    Returns:
        list: A sorted list of communication records, or an empty list if none found.
    """
    # Step 1: Use the existing function to find all records for the client
    query = {"client_name": client_name}
    records = find_records(agent, table_name, query)

    # Step 2: Verify we received a valid list and there's something to sort
    if not isinstance(records, list) or not records:
        return records  # Return empty list or error message if exists

    # Step 3: Perform the sorting
    # Sorting is done based on the 'date' field.
    # Added .get('date', '') to prevent errors if a record lacks a date field.
    try:
        sorted_records = sorted(records, key=lambda r: r.get('date', ''))
        return sorted_records
    except Exception as e:
        # In case of sorting error (e.g., invalid date format in a record)
        # Return unsorted records with a warning message.
        #logger.error(f"Could not sort records for client '{client_name}': {e}")
        return records # Fallback to unsorted list

def get_last_communication(agent, table_name: str, client_name: str):
    """
    Finds the most recent (latest) communication with a specific client.
    
    Args:
        agent: The agent object.
        table_name (str): The table name ("communications").
        client_name (str): The client name to search for.

    Returns:
        dict or None: The latest communication object, or None if no communications found.
    """
    # Step 1: Use the function we already built to get all communications sorted from oldest to newest
    sorted_communications = find_and_sort_communications(agent, table_name, client_name)

    # Step 2: If the list is not empty, return the last item (which is the most recent)
    if sorted_communications and isinstance(sorted_communications, list):
        return sorted_communications[-1]  # [-1] returns the last item in the list

    # Step 3: If no records found, return None
    return None


# =========================================================================================
#  2. Strategic Pricing (AI Driven)
# =========================================================================================

def calculate_strategic_price(agent, client_name: str):
    """
    Analytics tool: Calculates a recommended strategic price for a performance.
    Based on recent performance averages, calendar workload, and client details.
    """
    log_and_print(f"--- Starting strategic pricing analysis for '{client_name}'... ---", "SYSTEM")

    # 1. Collect relevant data
    all_shows = find_records(agent, 'shows', {})
    if isinstance(all_shows, str): all_shows = []

    # Take only the 5 most recent shows to maintain relevance
    recent_shows = sorted(all_shows, key=lambda x: x.get('date', ''), reverse=True)[:5]
    
    # Check calendar workload for the upcoming month
    upcoming_events_raw = list_calendar_events(agent, days_ahead=30)
    upcoming_events = upcoming_events_raw.get('events', [])
    
    # 2. Dedicated prompt for the financial analyst AI
    pricing_prompt = calculate_strategic_price_pricing_promt.format(
        client_name=client_name,
        recent_shows=json.dumps(recent_shows, ensure_ascii=False, indent=2),
        upcoming_events=len(upcoming_events),
    )

    try:
        # 3. Get decision from AI and return it
        pricing_response = api_key_manager.generate_content(pricing_prompt, model_name=GEMINI_MODEL_NAME)
        cleaned_response = pricing_response.strip().replace("```json", "").replace("```", "")
        pricing_decision = json.loads(cleaned_response)
        
        log_and_print(f"   - AI Decision: Recommend {pricing_decision.get('recommended_price')} NIS. Reasoning: {pricing_decision.get('reasoning')}", "SYSTEM")
        return pricing_decision

    except (json.JSONDecodeError, ValueError) as e:
        #logger.error(f"Failed to parse AI response for price calculation: {e}")
        return {"error": "The AI failed to generate a valid pricing structure."}

