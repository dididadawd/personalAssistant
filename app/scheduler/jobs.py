# app/scheduler/jobs.py

import os
import json
import schedule
import time
import threading
import asyncio
import uuid
import re
from datetime import datetime, timedelta, date
from typing import List, Dict, Any

# --- 1. Imports from our modules ---
from ..db.json_manager import find_records, update_record, delete_record, DATA_FILE_LOCKS, add_record
from ..db.query_parser import _get_last_scheduled_date_iso
from ..utils.logger import log_and_print
from ..utils.date_helpers import get_logical_date, is_connected
from ..utils.system_state import get_system_state, save_system_state, is_system_frozen, manage_freeze_mode
from ..services.task_manager import add_task
from ..services.telegram_service import ASYNC_LOOP, send_proactive_message
from ..core.tools_registry import TOOL_MAP, _set_active_agent, _get_active_agent
from ..utils.date_helpers import is_successful_response
import inspect

# --- Constants & Regex Patterns ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PERSONAS_DIR = os.path.join(BASE_DIR, "personas")

PROCRASTINATION_MARKER = "⏳"
POSTPONEMENT_COUNTER_TEMPLATE = "(נדחתה כבר {} ימים)"
POSTPONEMENT_COUNTER_PATTERN = re.compile(r"\(נדחתה כבר (\d+) ימים\)")


# =========================================================================================
#  1. מנוע הרצה ראשי (The Runner)
# =========================================================================================

# Global agent reference for scheduler
_active_agent = None

def set_scheduler_agent(agent):
    """Set the global agent for the scheduler."""
    global _active_agent
    _active_agent = agent
    _set_active_agent(agent)

def schedule_runner():
    """
    פונקציה שתרוץ ב-Thread נפרד ותהיה אחראית על התזמון.
    *** גרסה משופרת: בודקת חיבור לאינטרנט וחסינה בפני שגיאות. ***
    """
    global _active_agent
    
    # כמה זמן לחכות (בשניות) בין בדיקות אם אין אינטרנט או לאחר שגיאה
    RETRY_INTERVAL_SECONDS = 60 # דקה

    while True:
        # --- שלב 1: בדיקת חיבור לאינטרנט ---
        if is_connected():
            try:
                # --- שלב 2: אם יש אינטרנט, נסה להריץ את המשימות הממתינות ---
                schedule.run_pending()
                if _active_agent:
                    _check_and_run_one_time_tasks(_active_agent)
            except Exception as e:
                # --- שלב 3: טיפול בשגיאות שקרו *בתוך* המשימה (למשל, שגיאת API) ---
                # זה מונע קריסה של המתזמן גם אם יש באג במשימה ספציפית
                log_and_print(f"[Scheduler] An unexpected error occurred in a scheduled job. The scheduler will not crash. Error: {e}  ERROR")
                # המתן לפני הניסיון הבא כדי למנוע הצפה של בקשות כושלות
                time.sleep(RETRY_INTERVAL_SECONDS)
        else:
            # --- שלב 4: אם אין אינטרנט, המתן ונסה שוב ---
            log_and_print("[Scheduler] No internet connection detected. Retrying in a minute. WARNING")

            time.sleep(RETRY_INTERVAL_SECONDS)

        # המתנה קצרה (שנייה אחת) בלולאה הראשית כדי למנוע שימוש יתר במעבד
        # כאשר אין משימות להרצה והכל תקין.
        time.sleep(1)

# =========================================================================================
#  2. ניהול שגרות (Routines) ואיחורים
# =========================================================================================

def process_routines(agent):
    """
    פונקציית מערכת יומית:
    1. בודקת שבירת רצפים (רק אם הגיע מחזור חדש והקודם לא הושלם).
    2. יוצרת משימות להיום אם צריך.
    """
    if is_system_frozen():
        log_and_print("[Scheduler] System is frozen. Skipping routine creation.", "SYSTEM")
        return
    log_and_print("[Scheduler] Starting daily routine processing...", "SYSTEM")
    
    table_name = "routines"
    lock = DATA_FILE_LOCKS[table_name]
    
    today = get_logical_date()
    today_iso = today.isoformat()
    
    # מיפוי שמות ימים באנגלית למספרים
    days_map_en = {
        0: 'monday', 1: 'tuesday', 2: 'wednesday', 3: 'thursday', 
        4: 'friday', 5: 'saturday', 6: 'sunday'
    }

    with lock:
        kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
        file_path = os.path.join(kb_path, f"{table_name}.json")
        
        if not os.path.exists(file_path): return

        with open(file_path, 'r', encoding='utf-8') as f:
            all_routines = json.load(f)
        
        list_modified = False
        tasks_created_count = 0

        for routine in all_routines:
            
            frequency = routine.get('frequency')
            today_day_name = days_map_en[today.weekday()]
            
            # --- בדיקה האם צריך ליצור משימה היום? ---
            should_create_today = False
            
            if frequency == 'daily':
                should_create_today = True
            elif frequency == 'weekly':
                days_list = routine.get('days_of_week')
                clean_days_db = []
                if isinstance(days_list, list):
                    clean_days_db = [d.lower().strip() for d in days_list]
                elif isinstance(days_list, str):
                    clean_days_db = [d.lower().strip() for d in days_list.split(',')]
                
                if today_day_name in clean_days_db:
                    should_create_today = True

            # --- חלק 1: ניהול רצף (Streak Checker) ---
            # הלוגיקה החדשה: בודקים שבירת רצף רק ביום שבו צריך ליצור משימה חדשה
            # (כלומר, "נסגר החלון" של המשימה הקודמת)
            
            current_streak = routine.get('current_streak', 0)
            last_completed = routine.get('last_completed_date') # ISO string YYYY-MM-DD
            
            if should_create_today and int(current_streak) > 0:
                # מצא מתי הייתה הפעם הקודמת שהיינו אמורים לבצע (לא כולל היום)
                last_scheduled_iso = _get_last_scheduled_date_iso(routine, today)
                
                if last_scheduled_iso:
                    # אם לא ביצענו את המשימה מאז הפעם הקודמת שהיא שובצה
                    # (כלומר: תאריך הביצוע ��אחרון הוא לפני תאריך השיבוץ הקודם)
                    if not last_completed or last_completed < last_scheduled_iso:
                        log_and_print(f"   💔 Streak for '{routine.get('title')}' broken. Opportunity window closed (should have been done by {last_scheduled_iso} but wasn't).", "SYSTEM")
                        routine['current_streak'] = 0
                        list_modified = True
                    else:
                        # המשימה בוצעה בזמן (או באיחור אבל לפני היום), הרצף נשמר
                        pass

            # --- חלק 2: יצירת משימות להיום ---
            # מונע יצירת כפילויות אם כבר נוצר היום
            if should_create_today and routine.get('last_created_date') != today_iso:
                log_and_print(f"   -> Creating task from routine: '{routine.get('title')}'", "SYSTEM")
                
                add_task(
                    agent=agent,
                    title=routine.get('title'),
                    description=routine.get('description', "Routine task"),
                    due_date=today_iso,
                    category='routine',
                    priority='MEDIUM',
                    icon=routine.get('icon', 'fas fa-sync-alt'),
                    routine_id=routine.get('routine_id')
                )
                
                routine['last_created_date'] = today_iso
                list_modified = True
                tasks_created_count += 1

        if list_modified:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(all_routines, f, indent=2, ensure_ascii=False)
        

def check_and_execute_freeze_schedule(agent):
    """
    פונקציה שרצה כל בוקר (למשל ב-00:01 או לפני process_routines).
    תפקידה: לבדוק אם היום מתחילה �חופשה מתוכננת, או מסתיימת חופשה.
    """
    state = get_system_state()
    today_iso = datetime.now().strftime("%Y-%m-%d")
    freeze_data = state.get("freeze_mode", {})
    scheduled_list = freeze_data.get("scheduled_freezes", [])
    
    dirty = False # דגל לדעת אם צריך לשמור שינויים

    # 1. בדיקה אם צריך *להתחיל* הקפאה היום
    # אנחנו בודקים רק אם המערכת *לא* קפואה כרגע
    if not freeze_data.get("is_active"):
        for i, schedule in enumerate(scheduled_list):
            if schedule["start"] == today_iso:
                # הגיע הזמן להקפיא!
                log_and_print(f"❄️ Activating scheduled freeze: {schedule['reason']}", "SYSTEM")
                
                # מפעילים את הלוגיקה של ההקפאה
                # אפשר לקרוא ל-manage_freeze_mode או לעדכן ידנית את ה-State
                state["freeze_mode"]["is_active"] = True
                state["freeze_mode"]["start_date"] = today_iso
                state["freeze_mode"]["reason"] = schedule["reason"]
                
                # מסירים את האירוע מהרשימה כי הוא הופעל
                scheduled_list.pop(i)
                dirty = True
                break # מפסיקים לחפש

    # 2. בדיקה אם צריך *לסיים* הקפאה היום
    # בודקים רק אם המערכת *כן* קפואה
    elif freeze_data.get("is_active"):
        expected_end = freeze_data.get("end_date")
        if expected_end and expected_end == today_iso:
            log_and_print("☀️ Vacation end date reached. Performing automatic exit...", "SYSTEM")
            # קריאה לפונקציה המלאה שעושה את ה-Time Shift
            manage_freeze_mode(agent, action="stop")
            dirty = False # הפונקציה manage_freeze_mode כבר שומרת, אז לא צריך לשמור שוב

    # שמירה אם היו שינויים
    if dirty:
        state["freeze_mode"]["scheduled_freezes"] = scheduled_list
        save_system_state(state)


def postpone_overdue_tasks(agent):
    log_and_print("[Scheduler] Starting overdue tasks scan...", "SYSTEM")
    
    if is_system_frozen():
        log_and_print("[Scheduler] System is frozen. Skipping overdue check.", "SYSTEM")
        return
    
    # פורמט ISO תקין: YYYY-MM-DD
    today_iso = date.today().isoformat() 
    tasks_updated_count = 0
    projects_updated_count = 0

    def update_text_with_counter(text):
        if not text: return ""
        days_postponed = 1
        
        # חיפוש המונה הקיים בעזרת ה-RegEx שלך
        match = POSTPONEMENT_COUNTER_PATTERN.search(text)
        if match:
            days_postponed = int(match.group(1)) + 1
            
        # ניקוי הטקסט: 
        # 1. מסירים את המונה הישן
        clean_text = POSTPONEMENT_COUNTER_PATTERN.sub("", text)
        # 2. מסירים את האימוג'י הישן (כדי שלא ישתכפל פעמיים)
        clean_text = clean_text.replace(PROCRASTINATION_MARKER, "")
        # 3. מנקים רווחים מיותרים
        clean_text = clean_text.strip()
        
        # בנייה מחדש לפי התבנית שלך
        counter_str = POSTPONEMENT_COUNTER_TEMPLATE.format(days_postponed)
        return f"{clean_text} {PROCRASTINATION_MARKER} {counter_str}"

    # --- חלק 1: משימות רגילות ---
    # שים לב: אם find_records לא מחזיר כלום, וודא שהתאריכים ב-DB הם בפורמט ISO (2026-01-19)
    overdue_tasks = find_records(agent, 'tasks', {
        'status': 'pending',
        'due_date': {'': today_iso} 
    })
    
    if overdue_tasks:
        for task in overdue_tasks:
            new_desc = update_text_with_counter(task.get('description', ''))
            update_record(agent, 'tasks', 
                        {'task_id': task.get('task_id')}, 
                        {'due_date': today_iso, 'description': new_desc})
            tasks_updated_count += 1

    # --- חלק 2: פרויקטים (Projects) ---
    table_name = "projects"
    kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
    file_path = os.path.join(kb_path, f"{table_name}.json")
    
    if os.path.exists(file_path):
        with DATA_FILE_LOCKS[table_name]:
            with open(file_path, 'r', encoding='utf-8') as f:
                projects = json.load(f)
            
            changes_made = False
            for project in projects:
                if project.get('status') == 'active':
                    idx = project.get('current_step_index', 0)
                    steps = project.get('steps', [])
                    
                    if 0 <= idx < len(steps):
                        step = steps[idx]
                        s_due_date = step.get('due_date')
                        
                        # הדפסת דיבאג ללוג - כדי שתראה למה זה לא משווה
                        # log_and_print(f"DEBUG: Checking step '{step.get('title')}' date: {s_due_date} vs {today_iso}", "DEBUG")
                        
                        if s_due_date and s_due_date < today_iso:
                            # עדכון השלב ישירות בתוך האובייקט
                            step['due_date'] = today_iso
                            step['title'] = update_text_with_counter(step.get('title', ''))
                            changes_made = True
                            projects_updated_count += 1

            if changes_made:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(projects, f, indent=2, ensure_ascii=False)
    update_record(agent, 'USER_PROFILE', {"type": "user_profile"}, {
            "unplanned_wins": [],
            "current_mood": "neutral",
            "stress_notes": ""
        })
    log_and_print(f"[Scheduler] Complete. Updated {tasks_updated_count} tasks and {projects_updated_count} projects.", "SYSTEM")


# =========================================================================================
#  5. Runtime Schedule Setup (from server.py)
# =========================================================================================

# Import required for one-time tasks
from datetime import datetime as datetime_class


def _check_and_run_one_time_tasks(agent):
    """
    פונקציית עזר פנימית שרצה ברקע.
    בודקת אם יש משימות חד-פעמיות שהגיע זמנן, מריצה אותן (סדרתית), ומעדכנת את הסטטוס שלהן.
    """
    if not agent:
        return

    # 1. חפש את כל המשימות שעדיין ממתינות לביצוע
    pending_tasks = find_records(agent, 'one_time_tasks', {'status': 'pending'})
    
    if not pending_tasks or isinstance(pending_tasks, str):
        return  # אין משימות ממתינות

    # מיון לפי תאריך יעד (מהמוקדם למאוחר)
    pending_tasks.sort(key=lambda x: x.get('execution_datetime', ''))

    now = datetime_class.now()

    for task in pending_tasks:
        task_id = task.get('task_id')
        exec_time_str = task.get('execution_datetime')
        prompt = task.get('prompt')

        if not all([task_id, exec_time_str, prompt]):
            continue

        try:
            exec_time = datetime_class.fromisoformat(exec_time_str)
        except ValueError:
            update_record(agent, 'one_time_tasks', {'task_id': task_id}, {'status': 'failed', 'result': 'Invalid datetime format'})
            continue

        # 2. בדוק אם הגיע זמן הביצוע
        if now >= exec_time:
            log_and_print(f"--- 🚀 Running one-time task (serial): '{prompt}' ---", "SYSTEM")

            try:
                # מריצים את המשימה וממתינים שתסתיים
                agent.run_single_scheduled_task(task) 
                
                # רק אחרי שסיימנו, מוחקים אותה
                delete_record(agent, 'one_time_tasks', {'task_id': task_id})
                log_and_print(f"--- ✅ One-time task '{prompt}' completed and deleted. ---", "SYSTEM")
                
            except Exception as e:
                log_and_print(f"--- ❌ Error running one-time task: {e} ---", "ERROR")
                update_record(agent, 'one_time_tasks', {'task_id': task_id}, {'status': 'failed_execution'})


def setup_runtime_schedule(agent):
    """
    Loads scheduled jobs from schedule.json, clears existing schedules,
    and re-registers active jobs.
    """
    import schedule
    import json
    
    schedule.clear()
    log_and_print("[Scheduler] Cleared all existing jobs.", "SYSTEM")

    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    PERSONAS_DIR = os.path.join(BASE_DIR, "personas")
    
    kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
    file_path = os.path.join(kb_path, "schedule.json")

    if not os.path.exists(file_path):
        log_and_print("[Scheduler] schedule.json not found. No tasks scheduled.", "SYSTEM")
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            jobs = json.load(f)
    except Exception as e:
        log_and_print(f"[Scheduler] Failed to load schedule.json: {e}", "ERROR")
        return

    if not isinstance(jobs, list) or not jobs:
        log_and_print("[Scheduler] schedule.json is empty or invalid.", "SYSTEM")
        return

    valid_days = {"sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"}

    jobs_loaded = 0

    for job in jobs:
        if not isinstance(job, dict):
            continue

        if not job.get("is_active"):
            continue

        job_id = job.get("job_id", "UNKNOWN")
        time_str = job.get("time_hh_mm")
        days_to_schedule = job.get("days", [])

        if not time_str:
            log_and_print(f"[Scheduler] Skipping job {job_id}: missing time_hh_mm.", "WARNING")
            continue

        if isinstance(days_to_schedule, list) and days_to_schedule:
            for day in days_to_schedule:
                day_lower = str(day).lower()

                if day_lower not in valid_days:
                    log_and_print(f"[Scheduler] Invalid day '{day}' in job {job_id}. Skipped.", "WARNING")
                    continue

                scheduler_unit = getattr(schedule.every(), day_lower)
                scheduler_unit.at(time_str).do(
                    agent.launch_task_in_thread,
                    job=job
                )
                jobs_loaded += 1
        else:
            schedule.every().day.at(time_str).do(
                agent.launch_task_in_thread,
                job=job
            )
            jobs_loaded += 1

    log_and_print(f"[Scheduler] ✅ Successfully loaded {jobs_loaded} scheduled jobs.", "SYSTEM")
