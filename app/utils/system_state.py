# app/utils/system_state.py

import os
import json
from datetime import datetime, timedelta, date
import sys

# Add parent directory to path to find system_state_manager
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# --- 1. Imports from our modules ---
from ..db.json_manager import find_records, update_record
from ..utils.logger import log_and_print
# Import from the root-level module
from system_state_manager import get_system_state, save_system_state, is_system_frozen

# =========================================================================================
#  1. Freeze Mode Manager
# =========================================================================================


def manage_freeze_mode(agent, action: str, reason: str = "general", start_date: str = None, end_date: str = None):
    """
    Manages freeze mode.
    action: 'start' (immediate), 'stop' (immediate), 'schedule' (future).
    start_date/end_date: YYYY-MM-DD format (required for schedule).
    """
    # Ensure imports exist at the top of the file or here
    from system_state_manager import get_system_state, save_system_state
    from datetime import datetime, timedelta

    state = get_system_state()
    today_iso = datetime.now().strftime("%Y-%m-%d")

    # --- Option 1: Immediate activation ---
    if action == "start":
        if state["freeze_mode"]["is_active"]:
            return "The system is already in freeze mode."
        
        # Save the state
        state["freeze_mode"]["is_active"] = True
        state["freeze_mode"]["start_date"] = today_iso
        state["freeze_mode"]["reason"] = reason
        # If an end date was sent, save it too so we know when to exit automatically
        if end_date:
             state["freeze_mode"]["end_date"] = end_date

        save_system_state(state)
        log_and_print(f"❄️ Freeze mode activated. Reason: {reason}", "SYSTEM")
        return f"Freeze mode activated successfully. The system will not generate new tasks or check for delays until you return."

    # --- Option 2: Immediate exit ---
    elif action == "stop":
        if not state["freeze_mode"]["is_active"]:
            return "The system is not currently in freeze mode."
        
        start_date_str = state["freeze_mode"]["start_date"]
        
        # Calculate freeze days (Time Shift Calculation)
        days_frozen = 0
        if start_date_str:
            start_date_obj = datetime.strptime(start_date_str, "%Y-%m-%d")
            end_date_obj = datetime.now()
            days_frozen = (end_date_obj - start_date_obj).days
        
        # If at least one day has passed, activate the time shift mechanism
        updated_counts = {"tasks": 0, "projects": 0, "routines": 0}
        
        if days_frozen > 0:
            log_and_print(f"❄️➡️☀️ Exiting freeze. Shifting dates by {days_frozen} days...", "SYSTEM")
            # וודא שהפונקציה _apply_unfreeze_time_shift קיימת ונגישה
            updated_counts = _apply_unfreeze_time_shift(agent, days_frozen)
        
        # Turn off the mode
        state["freeze_mode"] = {
            "is_active": False, 
            "start_date": None, 
            "reason": None, 
            "scheduled_freezes": state["freeze_mode"].get("scheduled_freezes", [])
        }
        save_system_state(state)
        
        return (f"Welcome back! The system has exited freeze mode. "
                f"You were away for {days_frozen} days. "
                f"I automatically updated {updated_counts['tasks']} tasks, {updated_counts['projects']} project steps "
                f"and {updated_counts['routines']} routines.")

    # --- Option 3: Future scheduling ---
    elif action == "schedule":
        if not start_date:
            return "Must provide a start_date for future scheduling."
        
        # Add to the waiting list
        new_schedule = {
            "start": start_date,
            "end": end_date, 
            "reason": reason
        }
        
        # If the array doesn't exist yet, create it
        if "scheduled_freezes" not in state["freeze_mode"]:
            state["freeze_mode"]["scheduled_freezes"] = []
            
        state["freeze_mode"]["scheduled_freezes"].append(new_schedule)
        save_system_state(state)
        
        return f"Understood. I've scheduled a freeze mode ('{reason}') from {start_date} to {end_date if end_date else 'until further notice'}."

    return "Invalid action."


# =========================================================================================
#  2. Time Shifting Logic
# =========================================================================================


def _apply_unfreeze_time_shift(agent, days_delta):
    """
    Internal helper function: shifts all deadlines forward to prevent 'late' status.
    """
    counts = {"tasks": 0, "projects": 0, "routines": 0}
    
    # 1. Shift tasks (Tasks)
    pending_tasks = find_records(agent, 'tasks', {'status': 'pending'})
    for task in pending_tasks:
        if task.get('due_date'):
            try:
                # Convert date, add days, and save again
                old_date = datetime.strptime(task['due_date'], "%Y-%m-%d")
                new_date = old_date + timedelta(days=days_delta)
                update_record(agent, 'tasks', {'task_id': task['task_id']}, {'due_date': new_date.strftime("%Y-%m-%d")})
                counts["tasks"] += 1
            except Exception: pass

    # 2. Shift projects (Projects)
    active_projects = find_records(agent, 'projects', {'status': 'active'})
    for proj in active_projects:
        steps = proj.get('steps', [])
        changed = False
        for step in steps:
            if step.get('due_date'):
                try:
                    old_date = datetime.strptime(step['due_date'], "%Y-%m-%d")
                    # Shift only tasks that haven't passed or passed during freeze
                    # (Actually shift everything to maintain relative spacing in the project)
                    new_date = old_date + timedelta(days=days_delta)
                    step['due_date'] = new_date.strftime("%Y-%m-%d")
                    changed = True
                    counts["projects"] += 1
                except Exception: pass
        
        if changed:
            # Update the projects database (requires a full update function or similar logic)
            # Assumption: you have update_record that can update an entire field (like steps)
            update_record(agent, 'projects', {'project_id': proj['project_id']}, {'steps': steps})

    # 3. Rescue routines (Routines Streak Protection)
    # We shift the last_completed_date as if you performed the routine during
    # This is a trick that prevents process_routines from resetting the Streak on the next run
    routines = find_records(agent, 'routines', {})
    for r in routines:
        if r.get('last_completed_date'):
            try:
                old_date = datetime.strptime(r['last_completed_date'], "%Y-%m-%d")
                new_date = old_date + timedelta(days=days_delta)
                update_record(agent, 'routines', {'routine_id': r['routine_id']}, {'last_completed_date': new_date.strftime("%Y-%m-%d")})
                counts["routines"] += 1
            except Exception: pass
            
    return counts

