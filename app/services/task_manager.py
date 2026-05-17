# app/services/task_manager.py

import uuid
import json
import os
import time
from datetime import datetime, timedelta, timezone, date 

# --- 1. Imports from other modules in our project ---
from ..db.json_manager import find_records, update_record, add_record
from ..utils.logger import log_and_print
from ..utils.date_helpers import get_logical_date, is_valid_iso_date
from ..core.llm_provider import api_key_manager, GEMINI_MODEL_NAME
from ..services.google_workspace import list_calendar_events
# Import functions from other services that this manager depends on
from ..services.project_manager import complete_current_step
# Import prompts from centralized system
from ..prompts.prompt_loader import (
    _check_for_duplicate_task_promt,
    _ai_optimize_task_schedule_prompt
)

# --- Constants & Global Variables (if any) for this module ---
# This helps locate the personas directory from within the services folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PERSONAS_DIR = os.path.join(BASE_DIR, "personas")


# =========================================================================================
#  PUBLIC TASK MANAGEMENT FUNCTIONS
# =========================================================================================

def add_task(agent, title: str, description: str = "", due_date: str = None, category: str = "general", priority: str = None, icon: str = "fas fa-tasks", project_id: str = None, routine_id: str = None, deadline_limit: str = None, allow_duplicates: bool = False):
    """
    Adds a new task with duplicate protection mechanism.
    """
    scheduling_constraint = None
    
    if due_date and not is_valid_iso_date(due_date):
        # If due_date has a value but it's not a valid date (e.g., "after Monday")
        log_and_print(f"--- 📅 Detected verbal scheduling constraint: '{due_date}' ---", "SYSTEM")
        scheduling_constraint = due_date # Save the constraint
        due_date = None # Reset to activate the smart engine

    # --- Duplicate protection mechanism (new) ---
    # Only check if the user hasn't pre-approved duplicates and it's not a routine task (routines are auto-created and allowed to repeat)
    if not allow_duplicates and not routine_id:
        log_and_print(f"--- 🕵️ Checking duplicates for: '{title}'... ---", "SYSTEM")
        duplicate_result = _check_for_duplicate_task(agent, title)
        
        if duplicate_result:
            dup_title = duplicate_result.get('existing_task_title')
            # Return a response that prevents creation
            return {
                "status": "Info",
                "message": f"⚠️ I did not create the task. There seems to be a similar task already on the list: '{dup_title}'. If you still want to create it, ask again and add 'create anyway'."
            }
    # -------------------------------------

    ai_suggestion = None
    
    # If date or priority is missing - activate the smart engine
    if not due_date or not priority:
        log_and_print(f"--- 🧠 Activating smart analysis (date/priority) for task: '{title}'... ---", "SYSTEM")
        
        limit = deadline_limit if deadline_limit and deadline_limit.strip() else None
        
        # Call the updated smart engine
        ai_suggestion = _ai_optimize_task_schedule(
            agent, 
            title, 
            description, 
            max_deadline=limit,
            current_priority=priority,
            scheduling_constraint=scheduling_constraint  # <-- Pass the constraint to the engine
        )
        
        # Fill in date if missing
        if not due_date:
            due_date = ai_suggestion.get('recommended_date')
            log_and_print(f"   📅 Date set to {due_date}", "SYSTEM")
            
        # Fill in priority if missing
        if not priority:
            priority = ai_suggestion.get('priority', 'medium').lower()
            log_and_print(f"   ⚡ Priority set to {priority} (based on task content analysis)", "SYSTEM")

    # Handle missing values in extreme cases
    if due_date and due_date.lower() == 'today':
        due_date = get_logical_date().isoformat()
    if not due_date: due_date = get_logical_date().isoformat()
    if not priority: priority = "medium"
    if description == '' or description == None:
        description = ' '
    
    log_and_print(f"--- Saving task: '{title}' [Date: {due_date}, Prio: {priority}] ---", "SYSTEM")
        
    new_task = {
        "task_id": uuid.uuid4().hex,
        "title": title,
        "description": description,
        "due_date": due_date,
        "category": category,
        "priority": priority,
        "icon": icon,
        "project_id": project_id,
        "routine_id": routine_id,
        "progress": 0,
        "status": "pending"
    }
    
    # Save to DB
    result = add_record(agent, 'tasks', new_task)
    if "error" in result:
        return {"error": result["error"]}
        
    # Smart summary message for the user
    msg = f"The task '{title}' has been added."
    if ai_suggestion:
        msg += f"\n(Scheduled for {new_task['due_date']} with priority {new_task['priority']}. Reason: {ai_suggestion.get('reasoning')})"
        
    return {"status": "Success", "task": new_task, "message": msg}

def check_for_existing_task(agent, title: str):
    """
    Checks if there is an open task with the same meaning as the new task.
    Uses AI to understand semantic duplication (not just string comparison).
    """
    # 1. Fetch only open tasks
    existing_tasks = find_records(agent, 'tasks', {'status': 'pending'})
    
    if not existing_tasks or isinstance(existing_tasks, str):
        return None # No tasks, no duplicates

    # Optimization: If there are many tasks, take only titles to save tokens
    # Build a readable list for AI
    tasks_context = "\n".join([f"- ID: {t.get('task_id')} | Title: {t.get('title')}" for t in existing_tasks])

    # 2. Prompt for duplicate detection
    prompt = _check_for_duplicate_task_promt.format(
        new_title=title,
        tasks_context=tasks_context
    )

    try:
        # Use Generate Content with speed limit (we want a quick answer)
        response = api_key_manager.generate_content(prompt, model_name=GEMINI_MODEL_NAME)
        cleaned_response = response.strip().replace("```json", "").replace("```", "")
        result = json.loads(cleaned_response)
        
        if result.get("is_duplicate"):
            return result # Return duplicate details
            
    except Exception as e:
        print(f"Error checking duplicates: {e}")
        # In case of error, assume no duplicates to not block the user
        pass

    return None

def complete_task(agent, task_id: str = None, task_description: str = None, task_date: str = None, task_ids_list: list = None):
    """
    Marks a task (or list of tasks) as 'completed'.
    Integrates the original precise logic for checking delays, updating routines, and advancing projects.
    """
    
    # --- Step A: Normalize input to a list of IDs ---
    ids_to_process = []
    if task_ids_list:
        ids_to_process = task_ids_list
    elif task_id:
        ids_to_process = [task_id]
    
    # If no IDs received, try to find by description (original logic)
    if not ids_to_process and task_description:
        query = {'status': 'pending'}
        
        # Exact or broad search
        tasks_found = find_records(agent, 'tasks', {'title': task_description, 'status': 'pending'})
        if not tasks_found:
            tasks_found = find_records(agent, 'tasks', {'search_term': task_description, 'status': 'pending'})
            
        if tasks_found and isinstance(tasks_found, list):
            # Additional filter by date if provided
            if task_date:
                ids_to_process = [t.get('task_id') for t in tasks_found if t.get('due_date') == task_date]
            else:
                ids_to_process = [t.get('task_id') for t in tasks_found]

    if not ids_to_process:
        return {"error": "Could not find a PENDING task matching the request."}

    # --- Step B: Loop preparations ---
    completed_count = 0
    # Calculate logical date once (important for consistency)
    logical_date_iso = get_logical_date().isoformat()
    
    # Set to prevent duplicate project advancement in the same run (if we completed 3 tasks of the same project at once)
    projects_advanced_in_batch = set()

    # --- Step C: Execution loop ---
    for current_id in ids_to_process:
        # 1. Find the specific task
        tasks_found = find_records(agent, 'tasks', {'task_id': current_id})
        
        if not tasks_found or isinstance(tasks_found, str):
            continue 
            
        task_to_complete = tasks_found[0]

        # 3. Update the routine - exactly per the original
        routine_id = task_to_complete.get('routine_id')
        extra_msg = ""
        
        if routine_id:
            log_and_print(f"--- 🔄 Routine task completed on time. Updating statistics... ---", "SYSTEM")
            # Note: _update_routine_stats_logic moved to jobs - using inline approach
            # For now, just log the routine completion
            extra_msg = f" (Routine completed!)"

        # 4. Prepare the DB update
        update_query = {'task_id': current_id}
        updates = {
            'status': 'completed',
            'completion_date': logical_date_iso 
        }
        
        # 5. Handle projects (Project Logic)
        project_id = task_to_complete.get('project_id')
        
        if project_id:
            # A. First update the current task as completed
            update_record(agent, 'tasks', update_query, updates)
            
            # B. If this project hasn't been advanced in this run yet (to prevent duplication)
            if project_id not in projects_advanced_in_batch:
                # C. Fetch all remaining open tasks for the project
                all_project_tasks = find_records(agent, 'tasks', {'project_id': project_id, 'status': 'pending'})
                
                if isinstance(all_project_tasks, list):
                    # D. Critical filter: Ignore tasks that are in the current deletion list (ids_to_process)
                    # because they will be deleted momentarily, so no need to wait for them
                    remaining_tasks = [
                        t for t in all_project_tasks 
                        if t.get('task_id') != current_id and t.get('task_id') not in ids_to_process
                    ]
                    
                    # E. If nothing remains -> advance the project
                    if not remaining_tasks:
                        log_and_print(f"--- ✅ All tasks for this step in project {project_id} completed! Auto-advancing... ---", "SYSTEM")
                        complete_current_step(agent, project_id)
                        projects_advanced_in_batch.add(project_id) # Mark that we advanced to avoid advancing again by mistake
        else:
            # Regular update for a non-project task
            update_record(agent, 'tasks', update_query, updates)
            
        completed_count += 1

    return {
        "status": "Success", 
        "message": f"Successfully completed {completed_count} tasks{extra_msg}.",
        "completed_count": completed_count
    }

# =========================================================================================
#  PRIVATE HELPER FUNCTIONS FOR THIS MODULE
# =========================================================================================


def _check_for_duplicate_task(agent, new_title: str):
    """
    Checks if there is an open task with the same meaning as the new task.
    Uses AI to understand semantic duplication (not just string comparison).
    """
    # 1. Fetch only open tasks
    existing_tasks = find_records(agent, 'tasks', {'status': 'pending'})
    
    if not existing_tasks or isinstance(existing_tasks, str):
        return None # No tasks, no duplicates

    # Optimization: If there are many tasks, take only titles to save tokens
    # Build a readable list for AI
    tasks_context = "\n".join([f"- ID: {t.get('task_id')} | Title: {t.get('title')}" for t in existing_tasks])

    # 2. Prompt for duplicate detection
    prompt = _check_for_duplicate_task_promt.format(
        new_title=new_title,
        tasks_context=tasks_context
    )

    try:
        # Use Generate Content with speed limit (we want a quick answer)
        response = api_key_manager.generate_content(prompt, model_name=GEMINI_MODEL_NAME)
        cleaned_response = response.strip().replace("```json", "").replace("```", "")
        result = json.loads(cleaned_response)
        
        if result.get("is_duplicate"):
            return result # Return duplicate details
            
    except Exception as e:
        print(f"Error checking duplicates: {e}")
        # In case of error, assume no duplicates to not block the user
        pass

    return None



def _ai_optimize_task_schedule(agent, task_title: str, task_description: str, max_deadline: str = None, current_priority: str = None, scheduling_constraint=None):
    """
    The smart engine: Analyzes the task and determines date and priority.
    Upgraded version: Avoids Saturdays, minimizes Fridays, and analyzes workload based on task content.
    """
    # Fetch detailed schedule (with task names)
    schedule_context = _get_weekly_workload_summary(agent, days_ahead=14)
    
    # Build dynamic instructions
    deadline_instruction = f"\nCRITICAL: Must be done on or before {max_deadline}." if max_deadline else "there is not deadline"
    
    scheduling_instruction = f"\nConstraint: The user specifically asked for time: '{scheduling_constraint}'. You MUST find a valid YYYY-MM-DD date that satisfies this."   if scheduling_constraint else "there is not scheduling_constraint"
    
    priority_instruction = ""
    if current_priority:
        priority_instruction = f"\n**CONSTRAINT:** The user explicitly set Priority to '{current_priority}'. You MUST respect this."
    else:
        priority_instruction = """**Instructions for Priority (Analyze Semantics):**
        - **HIGH**: urgent
        - **MEDIUM**: Not so urgent 
        - **LOW**: Not urgent at all"""

    prompt = _ai_optimize_task_schedule_prompt.format(
        task_title=task_title,
        task_description=task_description,
        deadline_instruction=deadline_instruction,
        priority_instruction=priority_instruction,
        scheduling_instruction=scheduling_instruction,
        schedule_context=schedule_context
    )
    
    # Set default fallback in case of complete failure (tomorrow, unless it's Saturday)
    tomorrow = date.today() + timedelta(days=1)
    if tomorrow.weekday() == 5: # 5 = Saturday
        tomorrow = tomorrow + timedelta(days=1) # Move to Sunday

    fallback_result = {
        "recommended_date": tomorrow.isoformat(), 
        "priority": current_priority or "medium",
        "reasoning": "Error in automatic calculation (Fallback)."
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = api_key_manager.generate_content(prompt, model_name=GEMINI_MODEL_NAME)
            cleaned_response = response.strip().replace("```json", "").replace("```", "")
            
            # 1. Try to parse JSON
            result = json.loads(cleaned_response)
            
            # 2. Validation: Check that all fields exist
            if "recommended_date" not in result or "priority" not in result:
                raise ValueError("Missing required fields (recommended_date or priority)")
            
            # 3. Validate date format
            try:
                datetime.fromisoformat(result["recommended_date"])
            except ValueError:
                raise ValueError(f"Invalid date format: {result['recommended_date']}")

            # If everything is valid - return the result
            return result

        except (json.JSONDecodeError, ValueError, Exception) as e:
            log_and_print(f"⚠️ Schedule analysis error (attempt {attempt + 1}/{max_retries}): {e}", "SYSTEM_WARNING")
            if attempt < max_retries - 1:
                time.sleep(1) 
    
    return fallback_result




def _get_weekly_workload_summary(agent, days_ahead: int = 14) -> str:
    """
    Helper function: Produces a *detailed* textual summary of workload for the upcoming two weeks (or per days_ahead).
    Includes event and task names so the AI can assess real workload.
    """
    today = date.today()
    summary_lines = []
    
    # 1. Fetch events from calendar (ensure the inner function receives the updated range)
    calendar_res = list_calendar_events(agent, days_ahead=days_ahead)
    events = calendar_res.get('events', [])
    
    # 2. Fetch existing tasks
    tasks_res = find_records(agent, 'tasks', {'status': 'pending'})
    tasks = tasks_res if isinstance(tasks_res, list) else []
    
    # 3. Build summary for each day (iterates over the defined range, now default 14 days)
    for i in range(days_ahead):
        current_date = today + timedelta(days=i)
        date_iso = current_date.isoformat()
        day_name = current_date.strftime('%A')
        
        # Filter events for this day and format text
        day_events = [e for e in events if e.get('start_time', '').startswith(date_iso)]
        events_desc = "No events"
        if day_events:
            # Create a list like: "Meeting (10:00-11:00), Dentist (14:00-15:00)"
            events_desc = ", ".join([
                f"{e.get('summary', 'Unknown')} ({e['start_time'][11:16]}-{e['end_time'][11:16]})" 
                for e in day_events
            ])
        
        # Filter tasks for this day and format text
        day_tasks = [t for t in tasks if t.get('due_date') == date_iso]
        tasks_desc = "No tasks"
        if day_tasks:
            # Create a list of task names
            tasks_desc = ", ".join([f"'{t.get('title')}'" for t in day_tasks])
        
        # Build the final line for the day
        summary_lines.append(f"📅 {date_iso} ({day_name}):\n   - Events: {events_desc}\n   - Tasks: {tasks_desc}")
    return "\n".join(summary_lines)
