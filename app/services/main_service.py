# app/services/main_service.py
# Core service functions imported by tools_registry

import ast
import os
import json
import uuid
import textwrap
import re
import threading
import asyncio
import io
import base64
import socket
import csv
from datetime import datetime, timedelta, timezone, date
from datetime import time as dt_time
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict
from functools import cmp_to_key
import copy

import pandas as pd
import matplotlib.pyplot as plt
import google.generativeai as genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..db.json_manager import find_records, update_record, add_record, delete_record, update_records_by_query
from ..utils.logger import log_and_print
from ..core.llm_provider import api_key_manager, GEMINI_MODEL_NAME
from ..config import Config
from ..utils.date_helpers import is_valid_iso_date, get_logical_date, get_current_datetime, calculate_relative_date, get_next_scheduled_date
from ..prompts.system_prompts import (
    autonomously_advance_mission_planning_prompt,
    set_goal_promt,
    generate_and_build_flow_promt,
    _operate_on_tic_promt,
    consolidate_mission_log_if_needed_promt,
)
from .google_workspace import (
    create_calendar_event,
    list_calendar_events,
    create_google_task,
    get_primary_task_list_id,
)

# Constants
GEMINI_MODEL_FOR_COMPLEX_NAME = Config.GEMINI_MODEL_FOR_COMPLEX_NAME
DEBUG = False

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PERSONAS_DIR = os.path.join(BASE_DIR, "personas")
TELEGRAM_BOT_TOKEN = Config.TELEGRAM_BOT_TOKEN
ADMIN_CHAT_ID = Config.ADMIN_CHAT_ID

DATA_FILE_LOCKS = defaultdict(threading.Lock)


def add_payment(agent, name: str, amount: float, payment_day: int, currency: str = "shekel"):
    """
    Dedicated function for adding a payment.
    It builds the object in an organized way and sends it to add_record with the correct table name.
    """
    log_and_print(f"--- Creating new payment: '{name}' for amount {amount} on day {payment_day} ---", "SYSTEM")

    # Create unique identifier (like in routine)
    payment_id = f"pay_{uuid.uuid4().hex}"
    
    new_payment = {
        "payment_id": payment_id,
        "name": name,
        "amount": amount,
        "payment_day": payment_day,
        "currency": currency,
        "created_at": datetime.now().isoformat()
    }
    
    # Internal call to add_record with a fixed table name ("payments")
    return add_record(agent, 'payments', new_payment)


def set_work_schedule_template(agent, schedule_data: dict):
    """
    Tool for defining or updating the weekly work hours template.
    *** Fixed version: Merges new changes with the existing template instead of overwriting it. ***
    :param schedule_data: Dictionary containing one or more days to update.
    For example: {"Sunday": {"start": "08:00", "end": "16:00"}}
    """
    log_and_print("--- Updating weekly work hours template (in merge mode)... ---", "SYSTEM")
    if not agent or not agent.config:
        return {"error": "Agent not loaded."}

    # Step 1: Load the existing template, or create an empty dict if it doesn't exist yet
    # agent.config.get('work_schedule_template', {}) will return the template, or {} if the key is not found.
    existing_schedule = agent.config.get('work_schedule_template', {})
    
    # Step 2: Merge the new data into the existing template
    # .update() is a command that adds new keys and overwrites values of existing keys.
    existing_schedule.update(schedule_data)
    
    # Step 3: Save the full merged template back to the configuration
    agent.config['work_schedule_template'] = existing_schedule
    
    # Save the change to the persona's JSON file
    agent.save_persona()
    
    log_and_print(f"   - Template updated. Current state: {json.dumps(existing_schedule, ensure_ascii=False)}", "SYSTEM")
    return {"status": "Success", "message": "Work schedule template updated successfully."}


def confirm_and_schedule_work_day(agent, start_time: str = None, target_date_iso: str = None):
    """
    Confirms work hours for a specific day (default: today),
    and initiates automatic scheduling of tasks for that day.
    """
    # --- Part 1: Pre-processing of time and date ---
    
    # If no date provided, default is today
    if not target_date_iso:
        target_date_iso = date.today().isoformat()

    # Handle start time and convert to datetime object
    if start_time is None:
        # Take current time without seconds
        now = datetime.now()
        actual_start_time = now.replace(second=0, microsecond=0)
    else:
        try:
            # Try to parse the provided time (supports both HH:MM and HH:MM:SS)
            if len(start_time.split(':')) == 3:
                # Convert to full date (uses 1900 as default from strptime, that's fine since we'll only use the time later)
                actual_start_time = datetime.strptime(start_time, '%H:%M:%S')
            else:
                actual_start_time = datetime.strptime(start_time, '%H:%M')
        except ValueError:
            return {"error": "Invalid start_time format. Use HH:MM"}

    log_and_print(f"--- Confirming work day for date {target_date_iso}, start time: {actual_start_time.strftime('%H:%M')} ---", "SYSTEM")
    
    template = agent.config.get('work_schedule_template')
    if not template:
        return {"error": "Work schedule template is not set. Please use 'set_work_schedule_template' first."}
    
    target_date_obj = date.fromisoformat(target_date_iso)
    day_name_en = target_date_obj.strftime('%A')
    
    if day_name_en not in template:
        return {"status": "Info", "message": f"No work schedule defined for {day_name_en} ({target_date_iso})."}

    try:
        # Calculate times (like we did before)
        template_start = datetime.strptime(template[day_name_en]['start_time'], '%H:%M')
        template_end = datetime.strptime(template[day_name_en]['end_time'], '%H:%M')
        work_duration = template_end - template_start
        
        # Use the pre-calculation we did at the top of the function
        actual_end_time = actual_start_time + work_duration
        
        event = {
            "summary": "Focused Work Time",
            "start_time": f"{target_date_iso}T{actual_start_time.strftime('%H:%M:%S')}",
            "end_time": f"{target_date_iso}T{actual_end_time.strftime('%H:%M:%S')}",
            "description": "This time was set automatically to help you focus on tasks.",
            "color_id": "9" 
        }

        create_calendar_event(agent, [event])
        
        log_and_print(f"   - Work day for {target_date_iso}: {actual_start_time.strftime('%H:%M')} to {actual_end_time.strftime('%H:%M')}. 'Work Time' event added to calendar.", "SYSTEM")
        
        start_iso_full = f"{target_date_iso}T{actual_start_time.strftime('%H:%M:%S')}"
        end_iso_full = f"{target_date_iso}T{actual_end_time.strftime('%H:%M:%S')}"
        # --- The fix is here ---
        # We pass the updated start time to the scheduling function
        return auto_schedule_tasks_for_day(
            agent, 
            target_date_iso=target_date_iso,
            start_time_iso=start_iso_full,  # Pass the exact start time
            end_time_iso=end_iso_full       # Pass the exact end time
        )

    except (ValueError, KeyError) as e:
        return {"error": f"Failed to calculate work hours. Details: {e}"}


def set_goal(agent, raw_goal_description: str):
    """
    Smart tool for setting a long-term goal. Analyzes free-form requests and produces a structured goal object.
    The tool automatically identifies which existing tools can help advance the goal.
    """
    log_and_print(f"--- Starting smart goal setting process: '{raw_goal_description}' ---", "SYSTEM")

    from ..core.tools_registry import TOOL_MAP
    # Get a list of available tools so the AI can choose from them
    available_tools_for_prompt = "\n".join([f"- {name}: {info.get('description', '')}" for name, info in TOOL_MAP.items()])

    prompt = set_goal_promt.format(
        raw_goal_description=raw_goal_description,
        available_tools_for_prompt=available_tools_for_prompt
    )

    try:
        response = api_key_manager.generate_content(prompt, model_name=GEMINI_MODEL_NAME)
        cleaned_response = response.strip().replace("```json", "").replace("```", "")
        goal_data = json.loads(cleaned_response)

        # Add fields that the AI should not create
        goal_data['status'] = 'active'
        goal_data['last_progress_update'] = date.today().isoformat()

        # Use the existing function to add the record to the new 'goals' table
        return add_record(agent, 'goals', goal_data)

    except (json.JSONDecodeError, ValueError) as e:
        return {"error": "The AI failed to generate a valid goal structure."}


def update_goal_progress(agent, goal_title_query: str, new_current_value: float):
    """
    Updates progress for a specific goal.
    """
    log_and_print(f"--- Updating progress on goal '{goal_title_query}' to value {new_current_value} ---", "SYSTEM")

    # 1. Find the goal by name
    goals_found = find_records(agent, 'goals', {'search_term': goal_title_query})
    if not goals_found or isinstance(goals_found, str) or len(goals_found) > 1:
        return {"error": "Update failed: Did not find a single, unique goal matching the query."}
    
    target_goal = goals_found[0]
    goal_id = target_goal.get('goal_id')
    
    # 2. Prepare the update object
    updated_metrics = target_goal.get('metrics', {})
    updated_metrics['current_value'] = new_current_value
    
    updates = {
        "metrics": updated_metrics,
        "last_progress_update": date.today().isoformat()
    }
    
    # 3. Perform the update
    return update_record(agent, 'goals', {'goal_id': goal_id}, updates)


def add_routine(agent, title: str, frequency: str, days_of_week: Optional[str] = None, icon: str = "fas fa-sync-alt", description: str = ""):
    """
    AI Tool: Creates a template for a recurring task (routine).
    Fixed: Initializes counters to 0.
    """
    log_and_print(f"--- Creating new routine: '{title}' with frequency {frequency} ---", "SYSTEM")

    freq_lower = frequency.lower()
    
    # Validation
    if freq_lower == 'weekly' and not days_of_week:
        return {"error": "For a weekly routine, you must provide the 'days_of_week'."}
    
    if freq_lower == 'daily':
        days_of_week = None
        final_days_list = []
    else:
        # --- Logic to handle multiple days (List or String) ---
        hebrew_to_english_days = {
            'ראשון': 'Sunday', 'שני': 'Monday', 'שלישי': 'Tuesday',
            'רביעי': 'Wednesday', 'חמישי': 'Thursday', 'שישי': 'Friday', 'שבת': 'Saturday',
            'sunday': 'Sunday', 'monday': 'Monday', 'tuesday': 'Tuesday',
            'wednesday': 'Wednesday', 'thursday': 'Thursday', 'friday': 'Friday', 'saturday': 'Saturday'
        }
        
        final_days_list = []
        
        if isinstance(days_of_week, list):
            raw_days = days_of_week
        else:
            clean_str = str(days_of_week).replace(" and ", ",").replace(" ו", ",")
            raw_days = [d.strip() for d in re.split(r'[,\s]+', clean_str) if d.strip()]

        for d in raw_days:
            clean_d = re.sub(r'[^a-zA-Zא-ת]', '', d).lower()
            found_day = None
            for k, v in hebrew_to_english_days.items():
                if k in clean_d or clean_d in k:
                    found_day = v
                    break
            
            if found_day and found_day not in final_days_list:
                final_days_list.append(found_day)
        
        if not final_days_list and days_of_week:
            final_days_list.append(str(days_of_week).capitalize())

    new_routine = {
        "routine_id": f"routine_{uuid.uuid4().hex}",
        "title": title,
        "description": description,
        "frequency": freq_lower,
        "days_of_week": final_days_list,
        "icon": icon,
        "last_created_date": None,
        # --- The fix: Explicit initialization as numbers ---
        "current_streak": 0,
        "longest_streak": 0,
        "total_completions": 0
    }
    
    return add_record(agent, 'routines', new_routine)


def delete_routine(agent, query: dict):
    """
    AI Tool: Deletes one or more routines based on a query.
    The query can be by 'title' or 'routine_id'.
    """
    log_and_print(f"--- Starting routine deletion based on query: {query} ---", "SYSTEM")
    # Call the generic function with the fixed table name "routines"
    return delete_record(agent, 'routines', query)


def create_mission(agent, title: str, goal_description: str):
    """
    יוצר "משימת-על" חדשה עם "מרכז מידע למשימה" (TIC) מבוסס מצב.
    """
    log_and_print(f"--- Creating new super-task: '{title}' ---", "SYSTEM")
    
    new_mission = {
        "mission_id": f"mission_{uuid.uuid4().hex}",
        "title": title,
        "goal_description": goal_description,
        "status": "active",
        # --- התחלת השינוי ---
        "task_information_center": {
            "mission_state": {},  # אובייקט גמיש שמתאר את מצב הישויות במשימה
            "next_actionable_task": "Analyze the mission objective and determine the first steps.",  # The first task is always planning
            "scheduling_preferences":{}
        },
        # --- סוף השינוי ---
        "log": [{
            "timestamp": datetime.now().isoformat(),
            "type": "system",
            "content": f"המשימה '{title}' נוצרה. המטרה: {goal_description}"
        }],
        "created_at": datetime.now().isoformat(),
        "active_reminder_task_id": "0"
    }
    
    add_record(agent, 'missions', new_mission)
    autonomously_advance_mission(agent, new_mission['mission_id'])
    
    return {"status": "your mission created at AI Tasks. continue there your work"}



def update_mission_log(agent, mission_id: str, log_entry: dict):
    """
    מוסיף רשומת לוג חדשה (מחשבה, פעולה, תוצאה) למשימת-על קיימת.
    """
    missions = find_records(agent, 'missions', {'mission_id': mission_id})
    if not missions or isinstance(missions, str):
        return {"error": "Mission not found."}
    
    mission = missions[0]
    mission.get('log', []).append(log_entry)
    return update_record(agent, 'missions', {'mission_id': mission_id}, {'log': mission['log']})


def autonomously_advance_mission(agent, mission_id: str):
    """
    "המוח" של ה-AI. בודק משימת-על, מחליט על הצעד הבא, מבצע אותו,
    ומתזמן את עצמו לרוץ שוב בעתיד.
    """
    with agent.AI_THINKING_LOCK:
        consolidate_mission_log_if_needed(agent, mission_id)
        
        log_and_print(f"--- 🧠 AI starting autonomous thinking cycle on mission {mission_id} ---", "SYSTEM")
        missions = find_records(agent, 'missions', {'mission_id': mission_id})
        if not missions or isinstance(missions, str): 
            return {"error": "Mission not found."}
        
        mission = missions[0]
        log = mission.get('log', [])
        tic = mission.get('task_information_center', {})
        scheduling_prefs = tic.get('scheduling_preferences', {})
        work_days = scheduling_prefs.get('work_days', [])    
        last_log_entry = log[-1] if log else {}
        decision_text = "No decision generated yet" 
        
        should_wait = False
        next_check_time = None
        now = datetime.now()
        # --- התחלה: לוגיקת מעקב חדשה ---
        if log and DEBUG == False:
            now = datetime.now()

            if last_log_entry.get('type') == 'user_question':
                should_wait = True
                log_and_print(f"--- ⏰ Internal follow-up: User has not yet responded to the question in mission {mission_id}. ---", "SYSTEM")
                
                reminder_message = f"היי, רק מזכיר שבמשימה '{mission.get('title')}', אני עדיין ממתין לתשובתך על: {last_log_entry.get('content')}"
                reminder_time = (now + timedelta(minutes=1)).isoformat()
                schedule_one_time_task(agent, f"המטרה שלך היא רק לשלוח תזכורת זה לא משנה מה רשום ב MESSAGE כל מה שאתה צריך לעשות זה לשלוח את זה בעזרת פונקציית send_simple_notification send_simple_notification(message='{reminder_message}')", reminder_time, send_notification=True)
                log_and_print(f"   - Internal reminder scheduled to be sent to user.", "SYSTEM")
                
                last_question_time = datetime.fromisoformat(last_log_entry.get('timestamp'))
                days_waiting = (now - last_question_time).days
                
            elif last_log_entry.get('type') == 'waiting_for_external_response':
                should_wait = True
                content = last_log_entry.get('content', {})
                recipient_name = content.get('recipient_name')
                message_intent = content.get('message_intent')

                log_and_print(f"--- ⏰ External follow-up: No update received from '{recipient_name}' on mission {mission_id}. ---", "SYSTEM")

                reminder_intent = f"תזכורת ידידותית בנוגע ל: {message_intent}"
                reminder_time = (now + timedelta(minutes=1)).isoformat()
                reminder_time_me = (now + timedelta(minutes=3)).isoformat()
                schedule_one_time_task(agent, f"המטרה שלך היא רק לשלוח תזכורת לאותו הבן אדם זה לא משנה מה רשום ב message_intent כל מה שאתה צריך לעשות זה לשלוח את זה בעזרת פונקציית handle_communication_request handle_communication_request(recipient_name='{recipient_name}', message_intent='{reminder_intent}')", reminder_time, send_notification=True)
                log_and_print(f"   - External reminder scheduled to be sent to '{recipient_name}'.", "SYSTEM")
                
                last_question_time = datetime.fromisoformat(last_log_entry.get('timestamp'))
                days_waiting = (now - last_question_time).days
            
            if should_wait:
                if days_waiting > 5:
                    base_time = now + timedelta(days=1)
                elif work_days:
                    next_check_time  = get_next_scheduled_date(work_days)
                else:
                    if now.hour < 8:
                        base_time = now.replace(hour=10, minute=0, second=0)
                    elif now.hour >= 23:
                        base_time = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0)
                    else:
                        base_time = now + timedelta(days=1)
    
                if not next_check_time:
                    next_check_time = base_time.isoformat()
                
                log_and_print(f"--- 📅 Mission on hold. Next check at {next_check_time} ---", "SYSTEM")
                follow_up_prompt = f"autonomously_advance_mission(mission_id='{mission_id}')"
                schedule_result = schedule_one_time_task(agent, follow_up_prompt, next_check_time, send_notification=False)

                if schedule_result and schedule_result.get("status") == "Success":
                    message = schedule_result.get("message", "")
                    task_id_match = re.search(r"task_id: (one-off-[a-f0-9]+)", message)
                    if task_id_match:
                        new_task_id = task_id_match.group(1)
                        update_record(agent, 'missions', {'mission_id': mission_id}, {'active_reminder_task_id': new_task_id})
                        log_and_print(f"   - Next follow-up check scheduled for tomorrow. ID: {new_task_id}.", "SYSTEM")
                
                return {"status": "all the user tasks were complited you dont need to do anything else just return final_answer with some context"}
        # --- סוף: לוגיקת מעקב חדשה ---

        from ..core.tools_registry import TOOL_MAP
        internal_tools_to_hide = ['create_mission', 'update_mission_log', 'autonomously_advance_mission',
                                'add_user_response_to_mission_log', 'perform_advanced_research', "find_quick_answer_online", "execute_search_multi_task_prompt",
                                "handle_communication_request", "set_work_schedule_template", "confirm_and_schedule_work_day"]
        tools_for_prompt = "\n".join([
            f"- `{name}`: {info.get('description', '')}"
            for name, info in TOOL_MAP.items()
            if name not in internal_tools_to_hide
        ])

        planning_prompt = autonomously_advance_mission_planning_prompt.format(
            goal_description=mission.get('goal_description'),
            log_json=json.dumps(mission.get('log', []), ensure_ascii=False, indent=2),
            tools_for_prompt=tools_for_prompt,
            mission_state_json=json.dumps(tic.get('mission_state'), ensure_ascii=False, indent=2),
            newline=chr(10)
        )
        
        try:
            response = api_key_manager.generate_content(planning_prompt, model_name=GEMINI_MODEL_NAME)
            raw_text = response.strip()
            
            # --- מנגנון הגנה וחילוץ JSON ---
            decision_text = ""
            
            start_index = raw_text.find('{')
            end_index = raw_text.rfind('}')

            if start_index != -1 and end_index != -1 and end_index > start_index:
                decision_text = raw_text[start_index : end_index + 1]
            else:
                decision_text = raw_text
            
            if not decision_text:
                log_and_print("AI returned an empty response. Scheduling a retry.", "WARNING")
                update_mission_log(agent, mission_id, {"timestamp": datetime.now().isoformat(), "type": "system", "content": "AI response was empty. The mission will be re-evaluated later."})
                next_check_time = (datetime.now() + timedelta(minutes=5)).isoformat()
                schedule_one_time_task(agent, f"autonomously_advance_mission(mission_id='{mission_id}')", next_check_time, send_notification=False)
            
            
            try:
                decision = json.loads(decision_text)
            except json.JSONDecodeError:
                try:
                    log_and_print("Standard JSON failed. Attempting to repair JSON...", "WARNING")
                    decision = json.loads(repair_json(decision_text))
                except Exception:
                    try:
                        log_and_print("--- ⚠️ WARNING: Standard JSON failed. Trying Python-dict parsing (ast)... ---", "SYSTEM_LOG")
                        decision = ast.literal_eval(decision_text)
                        if not isinstance(decision, dict):
                            raise ValueError("Parsed content is not a dictionary")
                    except (ValueError, SyntaxError) as json_err:
                        raise json_err
            
            update_mission_log(agent, mission_id, {"timestamp": datetime.now().isoformat(), "type": "ai_thought", "content": json.dumps(decision, ensure_ascii=False)})

            action = decision.get("action")
            
            if action == "tool_call":
                tool_name = decision.get("tool_name")
                parameters = decision.get("parameters", {})
                result = agent._execute_tool_with_context(tool_name, parameters, context_mission_id=mission_id)
                update_mission_log(agent, mission_id, {"timestamp": datetime.now().isoformat(), "type": "tool_result", "tool": tool_name, "content": json.dumps(json.loads(result), ensure_ascii=False)})
                return autonomously_advance_mission(agent, mission_id)
            
            elif action == "ask_user":
                question_text = decision.get("question")
                update_mission_log(agent, mission_id, {"timestamp": datetime.now().isoformat(), "type": "user_question", "content": question_text})
                last_user_input = ""
                for entry in reversed(log):
                    if entry.get('type') == 'user_response':
                        last_user_input = entry.get('content', "")
                        break

                _operate_on_tic(agent, mission_id, user_input=last_user_input, ai_response=decision)
                
                scheduling_prefs = tic.get('scheduling_preferences', {})
                work_days = scheduling_prefs.get('work_days', [])

                if work_days and len(work_days) < 7:
                    check_time = get_next_scheduled_date(work_days, remind_day_before=False)
                    log_and_print(f"   - AI asked a question. Scheduling smart follow-up based on prefs {work_days} for {check_time}.", "SYSTEM")
                else:
                    base_time = now + timedelta(days=1)
                    if now.hour >= 23 or now.hour < 8:
                        base_time = base_time.replace(hour=10, minute=0, second=0)
                    check_time = base_time.isoformat()
                    log_and_print(f"   - AI asked a question. Scheduling standard follow-up for tomorrow.", "SYSTEM")

                follow_up_prompt = f"autonomously_advance_mission(mission_id='{mission_id}')"
                
                if DEBUG == False:
                    schedule_result = schedule_one_time_task(agent, follow_up_prompt, check_time, send_notification=False)
                    if schedule_result and schedule_result.get("status") == "Success" and "message" in schedule_result:
                        message = schedule_result.get("message")
                        task_id_parts = message.split("task_id: ")
                        if len(task_id_parts) > 1:
                            task_id = task_id_parts[-1].strip()
                            if task_id:
                                update_record(agent, 'missions', {'mission_id': mission_id}, {'active_reminder_task_id': task_id})
                                log_and_print(f"   - Follow-up task created with ID: {task_id}", "SYSTEM")

            elif action == "waiting_for_external_response":
                recipient = decision.get("recipient_name")
                intent = decision.get("message_intent")
                context = decision.get("context", "")

                log_and_print(f"--- ⏳ AI entering waiting mode for response from '{recipient}' regarding: '{intent}' ---", "SYSTEM")

                last_user_input = ""
                for entry in reversed(log):
                    if entry.get('type') == 'user_response':
                        last_user_input = entry.get('content', "")
                        break
                _operate_on_tic(agent, mission_id, user_input=last_user_input, ai_response=decision)
                
                log_entry = {
                    "timestamp": datetime.now().isoformat(),
                    "type": "waiting_for_external_response",
                    "content": {
                        "recipient_name": recipient,
                        "message_intent": intent,
                        "context": context
                    }
                }
                update_mission_log(agent, mission_id, log_entry)

                scheduling_prefs = tic.get('scheduling_preferences', {})
                work_days = scheduling_prefs.get('work_days', [])

                if work_days and len(work_days) < 7:
                    check_time = get_next_scheduled_date(work_days, remind_day_before=False)
                    log_and_print(f"   - AI asked a question. Scheduling smart follow-up based on prefs {work_days} for {check_time}.", "SYSTEM")
                else:
                    base_time = now + timedelta(days=1)
                    if now.hour >= 23 or now.hour < 8:
                        base_time = base_time.replace(hour=10, minute=0, second=0)
                    check_time = base_time.isoformat()
                    log_and_print(f"   - AI asked a question. Scheduling standard follow-up for tomorrow.", "SYSTEM")

                if DEBUG == False:
                    follow_up_prompt = f"autonomously_advance_mission(mission_id='{mission_id}')"
                    schedule_result = schedule_one_time_task(agent, follow_up_prompt, check_time, send_notification=False)
                    if schedule_result and schedule_result.get("status") == "Success" and "message" in schedule_result:
                        message = schedule_result.get("message")
                        task_id = message.split("task_id: ")[-1].strip()
                        if task_id:
                            update_record(agent, 'missions', {'mission_id': mission_id}, {'active_reminder_task_id': task_id})
                            log_and_print(f"   - Daily follow-up check scheduled. ID: {task_id}", "SYSTEM")

            elif action == "complete_mission":
                update_record(agent, 'missions', {'mission_id': mission_id}, {'status': 'completed'})
                update_records_by_query(
                    agent,
                    'projects',
                    {'mission_id': mission_id},
                    {'mission_id': None}
                )
                log_and_print(f"   - Related projects have been converted to regular projects and will remain active.", "SYSTEM")
                update_mission_log(agent, mission_id, {"timestamp": datetime.now().isoformat(), "type": "system", "content": f"המשימה הושלמה. סיבה: {decision.get('reason')}"})
                delete_record(agent, 'missions', {'mission_id': mission_id})
            else:
                log_and_print(f"--- ⚠️ AI returned an unknown action: '{action}'. Scheduling a retry for tomorrow. ---", "SYSTEM_ERROR")
                next_check_time = (datetime.now() + timedelta(days=1)).isoformat()
                schedule_one_time_task(agent, f"autonomously_advance_mission(mission_id='{mission_id}')", next_check_time, send_notification=False)
                update_mission_log(agent, mission_id, {"timestamp": datetime.now().isoformat(), "type": "system", "content": f"ה-AI לא ביצע פעולה מוכרת. בדיקה אוטונומית הבאה נקבעה ל-{next_check_time}"})

        except Exception as e:
            error_msg = str(e)
            log_and_print(f"Error in autonomous mission: {error_msg} the AI massage {decision_text}", "ERROR")
            
            if "Expecting value" in error_msg or "JSONDecodeError" in error_msg:
                log_and_print("--- ⚠️ AI response JSON parsing error. Scheduling automatic retry. ---", "SYSTEM_WARNING")
                next_check_time = (datetime.now() + timedelta(minutes=2)).isoformat()
                schedule_one_time_task(agent, f"autonomously_advance_mission(mission_id='{mission_id}')", next_check_time, send_notification=False)
                update_mission_log(agent, mission_id, {
                    "timestamp": datetime.now().isoformat(), 
                    "type": "system_retry", 
                    "content": f"ה-AI החזיר תשובה לא תקינה. בוצע תזמון חוזר אוטומטי ל-{next_check_time}."
                })
            else:
                update_mission_log(agent, mission_id, {
                    "timestamp": datetime.now().isoformat(), 
                    "type": "error", 
                    "content": error_msg
                })

        return {"status": "all the user tasks were complited you dont need to do anything else just return final_answer with some context"}


def add_user_response_to_mission_log(agent, mission_id: str, response_text: str):
    """
    מוסיף תגובה מהמשתמש ללוג של משימה ספציפית ומפעיל מיד את סבב החשיבה הבא של ה-AI.
    """
    is_long = len(response_text) > 500
    is_structured = response_text.count('\n') > 10 or response_text.count('\t') > 5
    
    if is_long and is_structured:
        log_and_print(f"--- 🛡️ Blocked attempt to send heavy data to main log (length: {len(response_text)}) ---", "SYSTEM_WARNING")
        return {
            "status": "Blocked",
            "message": (
                "🛑 **עצור!** נראה שאתה מנסה לשלוח רשימת נתונים ארוכה ללוג הראשי.\n\n"
                "זה עלול 'לסתום' את הזיכרון של הבוט ולגרום לשכחת הוראות.\n"
                "**מה לעשות?**\n"
                "1. העתק את המידע.\n"
                "2. לך ללשונית **'התייעצות' (Side Chat)**.\n"
                "3. הדבק שם את המידע ובקש לייבא אותו.\n\n"
                "ההודעה לא נשמרה."
            )
        }
    missions = find_records(agent, 'missions', {'mission_id': mission_id})
    if missions and not isinstance(missions, str) and missions[0]:
        mission = missions[0]
        reminder_task_id = mission.get('active_reminder_task_id')
        
        if reminder_task_id:
            log_and_print(f"--- 🗑️ User responded. Attempting to delete pending follow-up task (ID: {reminder_task_id}). ---", "SYSTEM")
            delete_record(agent, 'one_time_tasks', {'task_id': reminder_task_id})
            log_and_print(f"--- ✅ Successfully deleted one-time task {reminder_task_id}. ---", "SYSTEM")
            update_record(agent, 'missions', {'mission_id': mission_id}, {'active_reminder_task_id': None})

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "type": "user_response",
        "content": response_text
    }
    update_result = update_mission_log(agent, mission_id, log_entry)
    
    if isinstance(update_result, dict) and "error" in update_result:
        return update_result
        
    log_and_print(f"--- User response logged for mission {mission_id}. Triggering AI thinking process... ---", "SYSTEM")
    return autonomously_advance_mission(agent, mission_id)


def create_new_data_table(agent, table_name: str, columns: dict):
    """
    יוצר טבלה חדשה:
    1. יוצר קובץ JSON לאחסון הנתונים.
    2. מעדכן את ה-schema בקונפיגורציה של הסוכן כדי ש-add_record יעבוד.
    """
    if not table_name:
        return {
            "status": "Awaiting Input",
            "message": "איך תרצה לקרוא לטבלה החדשה? (באנגלית, ללא רווחים, למשל: 'leads')",
            "state": {
                "next_action": "create_new_data_table",
                "columns": columns,
                "missing_field": "table_name"
            }
        }
    
    log_and_print(f"--- 🏗️ Creating new table and adding to schema: '{table_name}' ---", "SYSTEM")
    
    if not agent or not agent.config:
        return {"error": "Agent not loaded."}

    clean_table_name = re.sub(r'[^a-zA-Z0-9_]', '', table_name).lower()
    if not clean_table_name:
        return {"error": "Table name must contain English letters/numbers only."}

    id_field = f"{clean_table_name.rstrip('s')}_id"
    if id_field not in columns:
        columns[id_field] = "string (auto-generated)"
        log_and_print(f"   - Automatically added ID field: {id_field}", "SYSTEM")
    
    if 'data_schema' not in agent.config:
        agent.config['data_schema'] = {}
    
    if clean_table_name in agent.config['data_schema']:
        return {
            "status": "Info", 
            "message": f"Table '{clean_table_name}' already exists in schema. Use add_record directly."
        }

    agent.config['data_schema'][clean_table_name] = columns
    agent.save_persona()
    log_and_print(f"   - ✅ Schema updated and saved to configuration file.", "SYSTEM")

    kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
    os.makedirs(kb_path, exist_ok=True)
    file_path = os.path.join(kb_path, f"{clean_table_name}.json")
    
    if not os.path.exists(file_path):
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump([], f)
    
    return {
        "status": "Success", 
        "message": f"Table '{clean_table_name}' created and schema registered. You can now use add_record.",
        "schema": columns
    }


def import_from_chat(agent, mission_id: str, table_name: str = None):
    """
    מייבא נתונים מהצ'אט הצדדי, ולאחר הצלחה - מנקה את הנתונים הגולמיים מההיסטוריה
    כדי לחסוך בטוקנים.
    """
    log_and_print(f"--- 🕵️ Side Chat Import & Clean: Table '{table_name}' ---", "SYSTEM")
    
    if not mission_id: return {"error": "Mission ID is missing."}

    if not table_name:
        return {
            "status": "Awaiting Input",
            "message": "לאיזו טבלה תרצה לשמור את המידע מהצ'אט? (למשל: 'leads')",
            "state": {
                "next_action": "import_from_chat",
                "mission_id": mission_id,
                "missing_field": "table_name"
            }
        }

    missions = find_records(agent, 'missions', {'mission_id': mission_id})
    if not missions: return {"error": "Mission not found."}
    
    side_history = missions[0].get('side_chat_history', [])
    if not side_history: return {"error": "Side chat history is empty."}

    target_index = -1
    target_data_text = None
    
    for i in range(len(side_history) - 1, -1, -1):
        if i == len(side_history) - 1 and len(side_history[i].get('content', '')) < 100:
            continue
            
        content = side_history[i].get('content', '')
        if content and ('\t' in content or content.count('\n') > 2):
            target_data_text = content
            target_index = i
            break
            
    if not target_data_text:
        return {"error": "Could not find raw data in recent history."}

    import_result = import_leads_from_text(agent, table_name, target_data_text)
    
    if import_result.get("status") == "Success":
        record_count = import_result.get('record_count', 0)
        side_history[target_index]['content'] = f"✅ [DATA CLEANUP] Raw data imported to table '{table_name}' ({record_count} records). Content removed to save memory."
        update_record(agent, 'missions', {'mission_id': mission_id}, {'side_chat_history': side_history})
        log_and_print("--- 🧹 Cleanup performed: Raw data deleted from history and replaced with documentation. ---", "SYSTEM")
        import_result['message'] += " (וגם ניקיתי את הטקסט המקורי מהצ'אט כדי לשמור על סדר)."

    return import_result


def generate_and_build_flow(agent, flow_name: str, detailed_description: str, trigger_type: str = "webhook"):
    """
    סוכן אדריכל משופר: בונה תהליכי אוטומציה מורכבים, כולל לוגיקה עמוקה ותיעוד API מלא.
    """
    log_and_print(f"--- 🏗️ AI Architect: Designing advanced flow '{flow_name}' ---", "SYSTEM")

    from ..core.tools_registry import TOOL_MAP
    available_tools_desc = "\n".join([
        f"- `{name}`: {info.get('description', '')}"
        for name, info in TOOL_MAP.items()
        if name not in ["build_automation_flow", "generate_and_build_flow", "send_simple_notification"]
    ])
    
    schema_desc = json.dumps(agent.config.get("data_schema", {}), ensure_ascii=False, indent=2)

    architect_prompt = generate_and_build_flow_promt.format(
        flow_name=flow_name,
        trigger_type=trigger_type,
        detailed_description=detailed_description,
        available_tools_desc=available_tools_desc,
        schema_desc=schema_desc
    )

    try:
        response = api_key_manager.generate_content(architect_prompt, model_name=GEMINI_MODEL_FOR_COMPLEX_NAME)
        raw_text = response.strip()
        
        start_idx = raw_text.find('{')
        end_idx = raw_text.rfind('}')
        if start_idx != -1 and end_idx != -1:
            blueprint_data = json.loads(raw_text[start_idx : end_idx + 1])
        else:
            raise ValueError("No JSON object found in Architect's response.")
        
        flow_id = f"flow_{uuid.uuid4().hex[:8]}"
        blueprint_data["flow_id"] = flow_id

        add_record(agent, "flow_blueprints", blueprint_data)
        
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "localhost"
            
        webhook_url = f"http://{local_ip}:5000/api/flows/webhook/{flow_id}"
        
        input_schema = blueprint_data.get("input_schema", {})
        payload_example = blueprint_data.get("payload_example", {})
        
        result_message = textwrap.dedent(f"""
            ✅ **The automation '{flow_name}' has been successfully built!**

            🆔 **Flow ID:** `{flow_id}`
            🔗 **Webhook URL:** `{webhook_url}`

            📋 **Required data structure (Input Schema):**
            {json.dumps(input_schema, indent=2, ensure_ascii=False)}

            🚀 **Example of what you need to send (Payload Example):**
            ```json
            {json.dumps(payload_example, indent=2, ensure_ascii=False)}
            ```

            *The automation is ready to run.*
            Please write the example here when you return this to the client 
            """)

        return json.dumps({
            "status": "Success",
            "message": result_message,
            "flow_id": flow_id,
            "blueprint": blueprint_data
        }, ensure_ascii=False)

    except Exception as e:
        log_and_print(f"Architect error: {e}", "ERROR")
        return json.dumps({"error": f"Failed to build flow: {str(e)}"}, ensure_ascii=False)


def update_user_context(agent, updates=None, mood_level=None, stress_notes=None, win_description=None):
    """
    כלי מרכזי לעדכון כל היבטי ההקשר של המשתמש: פרופיל, מצב רוח, והישגים.
    """
    profile = get_user_profile(agent)
    messages = []
    db_updates = {}
    
    if updates:
        valid_categories = ["preferences", "anti_preferences", "general_info"]
        updated_categories_in_profile = set()
        
        for update in updates:
            category = update.get("category")
            item = update.get("item")
            action = update.get("action", "add")
            
            if not category or not item or category not in valid_categories:
                messages.append(f"Skipping invalid profile update: {update}")
                continue
            
            current_list = profile.get(category, [])
            
            if action == "add" and item not in current_list:
                current_list.append(item)
                messages.append(f"Added '{item}' to {category}.")
                updated_categories_in_profile.add(category)
            elif action == "remove" and item in current_list:
                current_list.remove(item)
                messages.append(f"Removed '{item}' from {category}.")
                updated_categories_in_profile.add(category)

            profile[category] = current_list

        if updated_categories_in_profile:
            db_updates.update({cat: profile[cat] for cat in updated_categories_in_profile})

    if mood_level:
        db_updates["current_mood"] = mood_level
        if stress_notes:
            db_updates["stress_notes"] = stress_notes
        messages.append(f"Mood updated to '{mood_level}'.")

    if win_description:
        wins = profile.get("unplanned_wins", [])
        wins.append(win_description)
        db_updates["unplanned_wins"] = wins
        messages.append(f"Logged unplanned win: '{win_description}'.")
        
    if db_updates:
        update_record(agent, 'USER_PROFILE', {"type": "user_profile"}, db_updates)
        final_msg = "\n".join(messages)
        log_and_print(f"User context updated:\n{final_msg}", "SYSTEM")
        return f"Context updated successfully.\n{final_msg}"
    else:
        return "No updates were provided or needed."


def visualize_data(agent, analysis_prompt: str, context_data): # Removed dict type hint to allow list fallback
    """
    כלי-על ויזואלי: מקבל נתונים שכבר נאספו בשלבים קודמים, ומשתמש ב-AI
    כדי לכתוב קוד Python שמנתח אותם ומייצר גרף מורכב.
    """
    log_and_print(f"--- Starting advanced simulation based on existing data ---", "SYSTEM")

    if not context_data:
        return {"error": "No context data was provided to visualize."}

    # FIX: If the AI passed a list directly, wrap it in a dictionary automatically
    if isinstance(context_data, list):
        log_and_print("Notice: AI passed a list instead of a dict. Auto-wrapping into 'main_dataset'.", "SYSTEM_LOG")
        context_data = {"main_dataset": context_data}

    if not isinstance(context_data, dict):
        return {"error": "Context data must be a dictionary or a list of records."}

    dataframes = {}
    for key, records in context_data.items():
        if isinstance(records, list) and len(records) > 0:
            # Ensure safe dataframe names
            df_name = key.replace(' ', '_')
            if not df_name.endswith('_df'):
                df_name += '_df'
                
            dataframes[df_name] = pd.DataFrame(records)
            log_and_print(f"   - DataFrame '{df_name}' created with {len(records)} records.", "SYSTEM_LOG")

    if not dataframes:
        return {"error": "Context data was empty or invalid, could not create DataFrames."}
    
    df_info_for_prompt = ""
    for name, df in dataframes.items():
        df_info_for_prompt += f"**DataFrame `{name}`:**\n{df.head().to_string()}\n\n"

    code_generation_prompt = textwrap.dedent(f"""
        You are an expert Python data analyst. Your task is to write a single Python script that uses pre-loaded pandas DataFrames to generate a visualization based on the user's request.

        **User's Request:**
        "{analysis_prompt}"

        **Pre-loaded DataFrames Available:**
        {df_info_for_prompt}

        **Instructions & Constraints (CRITICAL):**
        1.  Your output MUST be ONLY a Python code block. Do not add any explanations.
        2.  The script must use ONLY `pandas` for data manipulation and `matplotlib.pyplot` as `plt` for plotting.
        3.  The final step of your code MUST be `plt.savefig(buf, format='png', bbox_inches='tight')`. The variable `buf` is already defined for you.
        4.  DO NOT use `plt.show()`.
        5.  Add a descriptive title to the chart and ensure labels are readable.
        6.  The DataFrames are already loaded into variables with the names provided above (e.g., `clients_df`, `communications_df`). You can use them directly.

        **Example for 'chart of interactions per client':**
        ```python
        import pandas as pd
        import matplotlib.pyplot as plt

        # The DataFrame 'communications_df' is already available.
        if 'client_name' in communications_df.columns:
            interactions_count = communications_df['client_name'].value_counts()
            
            plt.figure(figsize=(12, 7))
            interactions_count.plot(kind='bar', color='teal')
            plt.title('Number of Communications per Client')
            plt.ylabel('Interaction Count')
            plt.xticks(rotation=45, ha='right')
            plt.grid(axis='y', linestyle='--')
            plt.savefig(buf, format='png', bbox_inches='tight')
        ```
    """)

    try:
        response = api_key_manager.generate_content(code_generation_prompt, model_name=GEMINI_MODEL_NAME)
        code_to_execute = response.strip().replace("```python", "").replace("```", "").strip()
        
        buf = io.BytesIO()
        
        execution_globals = {
            "pd": pd,
            "plt": plt,
            "buf": buf
        }
        execution_globals.update(dataframes)
        
        log_and_print("--- AI generated the following code to execute: ---", "SYSTEM_LOG")
        log_and_print(code_to_execute, "SYSTEM_LOG")
        
        exec(code_to_execute, execution_globals)

        image_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        plt.close()

        log_and_print("✅ Complex chart generated in memory successfully.", "SYSTEM")
        
        return {
            "status": "Success",
            "message": "Here is the analysis you requested, based on a combination of multiple information sources:",
            "image_data": image_base64
        }

    except Exception as e:
        log_and_print(f"Visualization error: {e}", "ERROR")
        return {"error": f"Failed to generate visualization: {str(e)}"}


def repair_json(json_str: str) -> str:
    """
    מנסה לתקן מחרוזת JSON פגומה על ידי הוספת גרשיים חסרים, סגירת סוגריים וכו'.
    """
    import ast
    try:
        parsed = ast.literal_eval(json_str)
        return json.dumps(parsed, ensure_ascii=False)
    except Exception:
        return json_str


def get_user_profile(agent):
    """שולף את פרופיל המשתמש מהמסד. אם לא קיים, יוצר אחד ריק."""
    records = find_records(agent, 'USER_PROFILE', {"type": "user_profile"})
    if records:
        profile = records[0]
        updated = False
        if "unplanned_wins" not in profile: profile["unplanned_wins"] =[]; updated = True
        if "current_mood" not in profile: profile["current_mood"] = "neutral"; updated = True
        if "stress_notes" not in profile: profile["stress_notes"] = "there is nothing yet"; updated = True
        
        if updated:
            update_record(agent, 'USER_PROFILE', {"type": "user_profile"}, profile)
        return profile
    
    default_profile = {
        "type": "user_profile",
        "preferences": [],        
        "anti_preferences": [],   
        "general_info":[],       
        "unplanned_wins":[],    
        "current_mood": "neutral",
        "stress_notes": "there is nothing yet"
    }
    add_record(agent, 'USER_PROFILE', default_profile)
    return default_profile


def import_leads_from_text(agent, table_name: str, raw_text: str):
    """
    Parses raw text (copied from Excel/Sheets) and saves it as a new table.
    """
    log_and_print(f"--- 📥 Starting bulk import to table '{table_name}' ---", "SYSTEM")
    
    if not raw_text or not table_name:
        return {"error": "Missing raw_text or table_name."}

    clean_table_name = re.sub(r'[^a-zA-Z0-9_]', '', table_name).lower()
    
    try:
        f = io.StringIO(raw_text.strip())
        reader = csv.reader(f, delimiter='\t')
        
        headers = next(reader, None)
        if not headers:
            return {"error": "Could not parse headers from text."}
            
        clean_headers = [re.sub(r'[^a-zA-Z0-9_א-ת\s]', '', h.strip()).replace(' ', '_') for h in headers]
        
        records = []
        for row in reader:
            if len(row) == len(clean_headers):
                record = dict(zip(clean_headers, row))
                records.append(record)
        
        if not records:
            return {"error": "No valid data rows found."}

        kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
        os.makedirs(kb_path, exist_ok=True)
        file_path = os.path.join(kb_path, f"{clean_table_name}.json")
        
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                existing_records = json.load(f)
            existing_records.extend(records)
            records = existing_records
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
            
        log_and_print(f"   - ✅ Imported {len(records)} records to table '{clean_table_name}'.", "SYSTEM")
        
        return {
            "status": "Success",
            "message": f"Successfully imported {len(records)} records to '{clean_table_name}'.",
            "record_count": len(records),
            "table_name": clean_table_name
        }
        
    except Exception as e:
        log_and_print(f"   - ❌ Bulk import error: {e}", "ERROR")
        return {"error": f"Failed to import data: {str(e)}"}




def auto_schedule_tasks_for_day(agent, target_date_iso: str, start_time_iso: str = None, end_time_iso: str = None):
    """
    כלי סופר-חכם וגנרי: אוסף משימות לתאריך נתון ומשבץ אותן.
    מקבל אופציונלית שעות התחלה וסיום כדי לחסוך חיפוש ביומן.
    """
    log_and_print(f"--- Starting smart auto-scheduling for date {target_date_iso}... ---", "SYSTEM")

    # --- Step 1: Determine the work block time frame ---
    if start_time_iso and end_time_iso:
        work_start_time = start_time_iso
        work_end_time = end_time_iso
        log_and_print(f"   - Using manually defined hours: {work_start_time} to {work_end_time}", "SYSTEM")
    else:
        log_and_print("   - Searching for 'Focused Work Time' event in calendar...", "SYSTEM")
        calendar_events_raw = list_calendar_events(agent, query="Focused Work Time", start_date_iso=target_date_iso, end_date_iso=target_date_iso)
        work_block = calendar_events_raw.get('events', [None])[0]
        
        if not work_block:
            return {"error": f"Could not find the 'Work Block' event in the calendar for {target_date_iso}."}

        work_start_time = work_block['start_time']
        work_end_time = work_block['end_time']

    # Find tasks whose due date is the target date
    tasks_for_day_raw = find_records(agent, 'tasks', {'due_date': target_date_iso, 'status': 'pending'})
    if not tasks_for_day_raw or not isinstance(tasks_for_day_raw, list):
        return {"status": "Success", "message": f"No pending tasks found for {target_date_iso}. Your schedule is clear!"}
    
    tasks_to_schedule = [{"title": t.get('title'), "description": t.get('description')} for t in tasks_for_day_raw]

    primary_task_list_id = get_primary_task_list_id(agent)
    if not primary_task_list_id:
        return {"error": "Could not find the primary task list in Google Calendar."}
        
    # --- Step 2: Find all existing events on that day ---
    all_day_events_raw = list_calendar_events(agent, start_date_iso=target_date_iso, end_date_iso=target_date_iso)
    
    # Filter: avoid any event that is NOT "Focused Work Time"
    existing_appointments = [
        evt for evt in all_day_events_raw.get('events', []) 
        if evt.get('summary') != "Focused Work Time"
    ]
    
    formatted_appointments = [
        {"summary": appt.get("summary"), "start_time": appt.get("start_time"), "end_time": appt.get("end_time")}
        for appt in existing_appointments
    ]

    # --- Step 3: The scheduling prompt ---
    scheduling_prompt = textwrap.dedent(f"""
        You are an expert scheduler. Your task is to intelligently schedule a list of tasks into the available time slots of a work block for the date {target_date_iso}, while strictly avoiding any existing appointments.

        **Constraints & Context:**
        1.  **Work Block:** Your scheduling canvas is from {work_start_time} to {work_end_time}.
        2.  **Existing Appointments (CRITICAL: DO NOT OVERLAP):** These time slots are already booked.
            ```json
            {json.dumps(formatted_appointments, ensure_ascii=False, indent=2)}
            ```
        3.  **Tasks to Schedule:** These are the tasks you need to fit into the empty spaces.
            ```json
            {json.dumps(tasks_to_schedule, ensure_ascii=False, indent=2)}
            ```

        **Your Goal:**
        Generate a list of task objects to be created in Google Tasks. You must decide on a logical start time for each task within the **available empty slots**.

        **Output Rules:**
        - Your output MUST be a valid JSON object with a single key: "tasks_to_create".
        - Each task object MUST have `title`, `notes`, and `due` (the start time in full ISO 8601 format like '{target_date_iso}T09:30:00Z').
        - The `title` of the created task should be prefixed with the scheduled time range, e.g., "(09:30-11:00) Work on presentation".

        Now, create the optimal, conflict-free schedule for the tasks.
    """)

    try:
        # --- Step 4 and 5: Get AI response, process it, and create tasks ---
        response = api_key_manager.generate_content(scheduling_prompt, model_name=GEMINI_MODEL_NAME)
        cleaned_response = response.strip().replace("```json", "").replace("```", "")
        schedule_plan = json.loads(cleaned_response)
        
        tasks_from_ai = schedule_plan.get("tasks_to_create", [])
        if not tasks_from_ai:
            return {"status": "Success", "message": "The AI analyzed your schedule and decided there are no optimal slots for new tasks."}
        
        creation_result = create_google_task(agent, task_list_id=primary_task_list_id, tasks=tasks_from_ai)
        
        return {
            "status": "Success",
            "message": f"I have automatically scheduled {creation_result.get('created_count', 0)} tasks in the free slots of your calendar for {target_date_iso}.",
            "details": creation_result
        }
    except (json.JSONDecodeError, ValueError) as e:
        return {"error": "The AI failed to generate a valid task schedule structure."}


def schedule_one_time_task(agent, prompt_to_execute=None, execution_datetime=None, send_notification=True, tasks=None):
    """
    מתזמן משימה חד-פעמית לביצוע בזמן עתידי.
    """
    log_and_print(f"--- Scheduling one-time task for {execution_datetime} ---", "SYSTEM")
    
    if not execution_datetime:
        return {"error": "execution_datetime is required."}
    
    new_task = {
        "task_id": f"one-off-{uuid.uuid4().hex}",
        "prompt": prompt_to_execute,
        "execution_time": execution_datetime,
        "send_notification": send_notification,
        "status": "scheduled",
        "created_at": datetime.now().isoformat()
    }
    
    result = add_record(agent, 'one_time_tasks', new_task)
    
    return {
        "status": "Success",
        "message": f"One-time task scheduled for {execution_datetime}. task_id: {new_task['task_id']}",
        "task_id": new_task['task_id']
    }


def _operate_on_tic(agent, mission_id: str, user_input, ai_response):
    """
    "אופרטור TIC": מנתח את ההודעה האחרונה ומפיק את הגרסה המעודכנת של כל ה-TIC.
    """
    log_and_print(f"--- 🧠 TIC Operator analyzing message for mission {mission_id} ---", "SYSTEM")
    mission_records = find_records(agent, 'missions', {'mission_id': mission_id})
    if not mission_records or isinstance(mission_records, str):
        log_and_print(f"--- ❌ TIC Operator: No mission found with ID {mission_id} ---", "ERROR")
        return
        
    mission = mission_records[0]
    current_tic = mission.get('task_information_center')
    goal = mission.get('goal_description')
    
    operator_prompt = _operate_on_tic_promt.format(
        goal=goal,
        current_tic_json=json.dumps(current_tic, ensure_ascii=False, indent=2),
        user_input=user_input,
        ai_response=ai_response
    )

    max_retries = 3
    new_tic = None

    for attempt in range(max_retries):
        try:
            response = api_key_manager.generate_content(operator_prompt, model_name=GEMINI_MODEL_NAME)
            raw_text = response.strip()
            
            start_index = raw_text.find('{')
            end_index = raw_text.rfind('}')
            
            if start_index != -1 and end_index != -1:
                json_str = raw_text[start_index : end_index + 1]
            else:
                json_str = raw_text

            new_tic = json.loads(json_str)
            break 

        except (json.JSONDecodeError, ValueError) as e:
            log_and_print(f"--- ⚠️ TIC Operator: JSON parsing error (attempt {attempt + 1}/{max_retries}). Error: {e} ---", "SYSTEM_WARNING")
            
            if attempt == max_retries - 1:
                log_and_print("--- ❌ TIC Operator: Final failure after all attempts. TIC will not be updated. ---", "ERROR")
                return
            
            import time
            time.sleep(1)

    if new_tic:
        if 'mission_state' in new_tic and 'next_actionable_task' in new_tic:
            if not new_tic['mission_state']:
                tic_for_update = {
                    "mission_state": current_tic.get('mission_state', {}),
                    "next_actionable_task": new_tic['next_actionable_task']
                }
                update_record(agent, 'missions', {'mission_id': mission_id}, {'task_information_center': tic_for_update})
                log_and_print(f"--- ✅ TIC updated (mission only). Next action: {tic_for_update.get('next_actionable_task')} ---", "SYSTEM")
            else:
                update_record(agent, 'missions', {'mission_id': mission_id}, {'task_information_center': new_tic})
                log_and_print(f"--- ✅ TIC updated. Next action: {new_tic.get('next_actionable_task')} ---", "SYSTEM")


def consolidate_mission_log_if_needed(agent, mission_id: str):
    """
    בודק את אורך הלוג של המשימה. אם יש יותר מ-7 תגובות משתמש,
    הוא מכווץ את כל ההיסטוריה לסיכום מנהלים אחד ושומר אותו במקום הלוג הישן.
    """
    missions = find_records(agent, 'missions', {'mission_id': mission_id})
    if not missions:
        return

    mission = missions[0]
    log = mission.get('log', [])
    
    user_response_count = sum(1 for entry in log if entry.get('type') == 'user_response')
    
    if user_response_count > 7:
        log_and_print(f"--- 🗜️ Compressing log for mission {mission_id} ({user_response_count} user responses) ---", "SYSTEM")
        
        consolidation_prompt = consolidate_mission_log_if_needed_promt.format(
            goal_description=mission.get('goal_description'),
            history_json=json.dumps(log, ensure_ascii=False, indent=2)
        )
        
        try:
            response = api_key_manager.generate_content(consolidation_prompt, model_name=GEMINI_MODEL_NAME)
            raw_text = response.strip()
            start_index = raw_text.find('{')
            end_index = raw_text.rfind('}')
            
            if start_index != -1 and end_index != -1:
                summary_data = json.loads(raw_text[start_index : end_index + 1])
                
                new_log = [{
                    "timestamp": datetime.now().isoformat(),
                    "type": "system_consolidation",
                    "content": f"לוג המשימה כוונס. סיכום: {json.dumps(summary_data, ensure_ascii=False)}"
                }]
                
                update_record(agent, 'missions', {'mission_id': mission_id}, {'log': new_log})
                log_and_print(f"--- ✅ Mission log compressed successfully ---", "SYSTEM")
        except Exception as e:
            log_and_print(f"--- ❌ Log compression error: {e} ---", "ERROR")



