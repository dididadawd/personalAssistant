

import os
import json
import uuid
from typing import List
from datetime import date, timedelta

from ..utils.logger import log_and_print
from ..db.json_manager import find_records, update_all_records, add_record, update_record, delete_record

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PERSONAS_DIR = os.path.join(BASE_DIR, "personas")


def _get_last_scheduled_date_iso(routine, current_date_obj):
    """
    מחשב את התאריך האחרון שבו השגרה הייתה אמורה לרוץ, לפני התאריך הנוכחי.
    """
    frequency = routine.get('frequency')
    
    if frequency == 'daily':
        return (current_date_obj - timedelta(days=1)).isoformat()
    
    elif frequency == 'weekly':
        days_list = routine.get('days_of_week', [])
        
        if isinstance(days_list, str):
            days_list = [d.strip().lower() for d in days_list.split(',')]
        else:
            days_list = [d.strip().lower() for d in days_list]
            
        days_map_int_to_name = {
            0: 'monday', 1: 'tuesday', 2: 'wednesday', 3: 'thursday', 
            4: 'friday', 5: 'saturday', 6: 'sunday'
        }
        
        for i in range(1, 8):
            check_date = current_date_obj - timedelta(days=i)
            day_name = days_map_int_to_name[check_date.weekday()]
            
            if day_name in days_list:
                return check_date.isoformat()
                
    return None


def get_user_profile(agent):
    """שולף את פרופיל המשתמש מהמסד. אם לא קיים, יוצר אחד ריק."""
    records = find_records(agent, 'USER_PROFILE', {"type": "user_profile"})
    if records:
        profile = records[0]
        updated = False
        if "unplanned_wins" not in profile: profile["unplanned_wins"] =[]; updated = True
        if "current_mood" not in profile: profile["current_mood"] = "neutral"; updated = True
        if "stress_notes" not in profile: profile["stress_notes"] = ""; updated = True
        
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


def _process_end_of_day_goals(agent):
    """
    פונקציית עזר פנימית: 
    1. מזהה יעדים שהושלמו ומוחקת אותם.
    2. מזהה יעדים שפג תוקפם, דוחה את תאריך היעד שלהם, ומחזירה אותם לדיווח.
    """
    log_and_print("   - שלב מיוחד: מעבד סטטוס של יעדים ארוכי טווח...", "SYSTEM")
    
    active_goals = find_records(agent, 'goals', {'status': 'active'})
    if not isinstance(active_goals, list) or not active_goals:
        return []

    today = date.today()
    tomorrow_str = (today + timedelta(days=1)).isoformat()
    expired_goals_for_prompt = []

    for goal in active_goals:
        goal_id = goal.get('goal_id')
        metrics = goal.get('metrics', {})
        current_value = metrics.get('current_value')
        target_value = metrics.get('target_value')
        target_date_str = goal.get('target_date')

        if not all([goal_id, current_value is not None, target_value is not None, target_date_str]):
            continue

        try:
            target_date = date.fromisoformat(target_date_str)
        except ValueError:
            continue

        if current_value >= target_value:
            log_and_print(f"     > ✅ יעד הושג! '{goal.get('title')}'. מסמן להסרה.", "SYSTEM")
            update_record(agent, 'goals', {'goal_id': goal_id}, {'status': 'completed'})
        
        elif today > target_date:
            log_and_print(f"     > ⌛ תוקף היעד '{goal.get('title')}' פג. דוחה את תאריך היעד למחר.", "SYSTEM")
            update_record(agent, 'goals', {'goal_id': goal_id}, {'target_date': tomorrow_str})
            expired_goals_for_prompt.append(goal)

    result = delete_record(agent, 'goals', {'status': 'completed'})
    return expired_goals_for_prompt


def list_client_shows_sorted(agent, client_name: str):
    """
    Finds all shows for a specific client and sorts them by date, from newest to oldest.
    """
    log_and_print(f"--- Fetching and sorting shows for client: {client_name} ---", "SYSTEM")
    
    query = {"client_name": client_name}
    records = find_records(agent, "shows", query)
    
    if not isinstance(records, list) or not records:
        return records
    
    try:
        sorted_records = sorted(records, key=lambda r: r.get("date", ""), reverse=True)
        log_and_print(f"   Found {len(sorted_records)} shows, sorted newest to oldest.", "SYSTEM")
        return sorted_records
    except Exception as e:
        log_and_print(f"   Sorting error: {e}", "WARNING")
        return records


def add_fields_and_update_all_records(agent, table_name: str, new_fields: dict):
    """
    כלי עוצמתי: מוסיף עמודות/שדות חדשים לסכמה של טבלה,
    ולאחר מכן מעדכן את כל הרשומות הקיימות עם ערכי ברירת המחדל שסופקו.
    זוהי פעולת שינוי סכמה + עדכון נתונים.
    """
    log_and_print(f"--- Starting schema update process to add fields to table '{table_name}'... ---", "SYSTEM")
    
    # --- חלק א': עדכון קובץ הסכמה (persona.json) ---
    persona_config_path = os.path.join(PERSONAS_DIR, f"{agent.name}.json")
    try:
        # טען את כל קובץ התצורה
        with open(persona_config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # נווט לסכמה של הטבלה הרצויה
        if table_name not in config.get('data_schema', {}):
            return {"error": f"Schema for table '{table_name}' not found in configuration."}
        
        # הוסף את השדות החדשים לסכמה
        # הערה: אנחנו מניחים שהסוג הוא 'string' כברירת מחדל.
        # זו מגבלה מודעת של הפשטות כאן.
        schema_to_update = config['data_schema'][table_name]
        for field_name in new_fields.keys():
            if field_name not in schema_to_update:
                schema_to_update[field_name] = "string (auto-added)"
        
        # שמור את קובץ התצורה המעודכן
        with open(persona_config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        log_and_print(f"   ✅ Table schema '{table_name}' updated successfully.", "SYSTEM")
        
        # עדכן את התצורה הפעילה של הסוכן בזיכרון
        agent.config = config

    except Exception as e:
        error_msg = f"Failed to update schema file for '{table_name}': {e}"
        #logger.error(error_msg)
        return {"error": error_msg}

    # --- חלק ב': עדכון קובץ הנתונים (כמו ב-update_all_records) ---
    # הפונקציה הזו משתמשת ב-updates=new_fields
    return update_all_records(agent, table_name, updates=new_fields)


def remove_scheduled_job(agent, job_ids: str):
    """
    מוחק משימה מתוזמנת אחת או יותר לפי רשימת מזהים.
    """
    # ודא שהקלט הוא רשימה
    if not isinstance(job_ids, list):
        return {"error": "Input must be a list of job IDs."}

    # שימוש ב-set לביצועים אופטימליים בבדיקת שייכות
    ids_to_remove = set(job_ids)
    
    log_and_print(f"--- Attempting to delete {len(ids_to_remove)} scheduled tasks... ---", "SYSTEM")
    
    kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
    file_path = os.path.join(kb_path, "schedule.json")
    
    if not os.path.exists(file_path):
        return {"error": "Schedule file not found. No jobs to remove."}
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            jobs = json.load(f)
        
        original_job_count = len(jobs)
        
        # צור רשימה חדשה שמשאירה רק את המשימות שה-ID שלהן *אינו* בסט המזהים למחיקה
        jobs_after_removal = [job for job in jobs if job.get("job_id") not in ids_to_remove]
        
        # בדוק אם משהו נמחק
        if len(jobs_after_removal) == original_job_count:
            return {"error": f"No jobs found matching the provided IDs: {', '.join(job_ids)}"}
        
        deleted_count = original_job_count - len(jobs_after_removal)
            
        # שמור את הרשימה המעודכנת בחזרה לקובץ
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(jobs_after_removal, f, indent=2, ensure_ascii=False)
            
        # טען מחדש את כל התזמון
        from ..scheduler.jobs import setup_runtime_schedule
        setup_runtime_schedule(agent)
        
        log_and_print(f"--- {deleted_count} tasks successfully deleted ---", "SYSTEM")
        
        # החזר תגובה מפורטת
        return {
            "status": "Success",
            "message": f"{deleted_count} jobs removed successfully.",
            "deleted_count": deleted_count
        }
        
    except json.JSONDecodeError:
        return {"error": "Failed to read schedule file (invalid JSON)."}
    except Exception as e:
        return {"error": f"Failed to remove scheduled jobs: {e}"}

def list_scheduled_jobs(agent):
    """מציג את כל המשימות המתוזמנות הקיימות."""
    log_and_print("--- Displaying all scheduled tasks ---", "SYSTEM")
    kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
    file_path = os.path.join(kb_path, "schedule.json")
    
    if not os.path.exists(file_path):
        return {"status": "No scheduled jobs found."}
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            jobs = json.load(f)
        return {"scheduled_jobs": jobs}
    except Exception as e:
        return {"error": f"Failed to list scheduled jobs: {e}"}

def add_scheduled_job(agent, prompt: str, time_hh_mm: str, days: List[str] = None, weeks_interval: int = 1, send_notification: bool = False):
    """
    מנסה להריץ את המשימה במצב "שקט" לפני שהוא קובע אותה.
    רק אם ההרצה מצליחה, המשימה המתוזמנת תיווצר.
    *** גרסה משודרגת: תומכת בתזמון לימים ספציפיים בשבוע וגם במרווחי שבועות (למשל פעם בשבועיים). ***
    """
    log_and_print(f"--- Starting verification and scheduling of recurring task: '{prompt}' (every {weeks_interval} weeks) ---", "SYSTEM")
    
    # --- שלב 1: הרצת בדיקה מקדימה במצב שקט ---
    log_and_print(f"--- Running preliminary check (Dry Run) in 'silent' mode... ---", "SYSTEM")
    dry_run_output = agent.execute_task(prompt, execution_mode="silent")

    # --- שלב 2: בדוק את תוצאת הרצת הבדיקה ---
    if isinstance(dry_run_output, dict):
        if "user_facing_error" in dry_run_output:
            error_message = f"לא ניתן לקבוע את המשימה. במהלך הבדיקה, הבוט נתקל בבעיה: {dry_run_output['user_facing_error']}"
            return {"user_facing_error": error_message}
        elif "error" in dry_run_output:
            error_message = f"לא ניתן לקבוע את המשימה. במהלך הבדיקה, הבוט נתקל בשגיאה פנימית: {dry_run_output['error']}"
            return {"user_facing_error": error_message}
        elif "status" in dry_run_output and dry_run_output["status"] == "Awaiting Input":
            missing_info_message = f"לא ניתן לקבוע את המשימה. היא אינה אוטונומית ודורשת מידע נוסף מהמשתמש: '{dry_run_output['message']}'"
            return {"user_facing_error": missing_info_message}

    # --- שלב 3: אם הגענו לכאן, הרצת הבדיקה הצליחה! ---
    log_and_print("--- Preliminary check succeeded. Proceeding to create recurring task... ---", "SYSTEM")
    
    new_job = {
        "job_id": f"job_{uuid.uuid4().hex}",
        "prompt": prompt,
        "time_hh_mm": time_hh_mm,
        "is_active": True,
        "days": days if days else [], # הוספת שדה הימים, או רשימה ריקה אם לא סופק
        "weeks_interval": weeks_interval, # <--- התוספת החדשה: כל כמה שבועות
        "send_notification": send_notification,
        "last_run_date": None
    }
    kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
    file_path = os.path.join(kb_path, "schedule.json")
    
    try:
        jobs = []
        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            with open(file_path, 'r', encoding='utf-8') as f:
                jobs = json.load(f)
        jobs.append(new_job)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(jobs, f, indent=2, ensure_ascii=False)
        
        from ..scheduler.jobs import setup_runtime_schedule
        setup_runtime_schedule(agent)
        
        interval_str = ""
        if weeks_interval == 2:
            interval_str = " (פעם בשבועיים)"
        elif weeks_interval > 1:
            interval_str = f" (כל {weeks_interval} שבועות)"

        day_str = f"בימים: {', '.join(days)}" if days else "כל יום"
        final_message = f"✅ המשימה '{prompt}' נקבעה בהצלחה לשעה {time_hh_mm}, {day_str}{interval_str}."
        log_and_print(f"--- {final_message} ---", "SYSTEM")
        return {"status": "Success", "message": f"the Repetitive task {final_message}"}
        
    except Exception as e:
        error_msg = f"שגיאה קריטית בשמירת המשימה המתוזמנת לאחר הבדיקה: {e}"
        return {"error": error_msg}
