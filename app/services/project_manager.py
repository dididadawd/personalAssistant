# app/services/project_manager.py

import os
import json
import uuid
import time
import textwrap
from datetime import datetime, timedelta, date
from typing import List, Dict, Any

# --- 1. Imports from our modules ---
from ..db.json_manager import (
    add_record, find_records, update_record, 
    delete_record, update_records_by_query, DATA_FILE_LOCKS
)
from ..utils.logger import log_and_print
from ..utils.date_helpers import get_logical_date
from ..core.llm_provider import api_key_manager, GEMINI_MODEL_NAME
# Import prompts from centralized system
from ..prompts.prompt_loader import (
    create_project_plan_promt,
)

# Note: _operate_on_tic should be imported in functions where needed to avoid circular imports

# --- Constants ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PERSONAS_DIR = os.path.join(BASE_DIR, "personas")

# =========================================================================================
#  1. Functions for creating and planning projects
# =========================================================================================


def create_project_timeboxed_action_plan(agent, goal: str = None, title: str = "", scope_description: str = "", total_duration_days: int = 30, mission_id: str = None, first_due_date: str = None, **kwargs):
    """
    Creates a new project intelligently.
    Now accepts optional first_due_date parameter (YYYY-MM-DD) for setting the first deadline.
    """
    # --- 1. Build full context ---
    full_context = f"{title} {goal or ''} {scope_description}".strip()
    if not full_context: full_context = "New project"

    # --- 2. Duplicate protection mechanism (Duplicate Guard) ---
    if title:
        clean_title = title.split(':')[0].strip()
        existing_projects = find_records(agent, 'projects', {'search_term': clean_title, 'status': 'active'})
        
        if existing_projects and isinstance(existing_projects, list):
            for proj in existing_projects:
                if clean_title.lower() in proj.get('title', '').lower():
                    log_and_print(f"--- ⚠️ Detected duplicate project creation attempt ('{proj.get('title')}'). Canceling creation. ---", "SYSTEM")
                    return {
                        "status": "Info", 
                        "message": f"Project '{proj.get('title')}' (ID: {proj.get('project_id')}) already exists. Please use the existing project."
                    }

    log_and_print(f"--- Starting smart project planning for: '{full_context}' ---", "SYSTEM")

    try:
        duration = int(total_duration_days)
    except:
        duration = 30
        
    num_steps = max(1, duration // 7) 
    
    # --- New date logic ---
    today = date.today()
    due_dates_list = []

    if first_due_date:
        try:
            # If the user provided a first due date, start from it
            provided_date = date.fromisoformat(first_due_date)
            # Ensure the due date is not in the past - use at least today + 1 week
            base_date = max(provided_date, today + timedelta(weeks=1))
            # The first week is the given date, subsequent weeks are +1, +2, etc.
            due_dates_list = [(base_date + timedelta(weeks=i)).isoformat() for i in range(num_steps)]
            log_and_print(f"📅 Initial due date set: {first_due_date}", "SYSTEM")
            if provided_date < today:
                log_and_print(f"⚠️ Provided date ({first_due_date}) was in the past, adjusted to {base_date.isoformat()}", "SYSTEM")
        except ValueError:
            log_and_print(f"⚠️ Invalid date format ({first_due_date}), falling back to default.", "SYSTEM")
            # Fallback in case of error
            due_dates_list = [(today + timedelta(weeks=i+1)).isoformat() for i in range(num_steps)]
    else:
        # Default: First deadline is one week from today
        due_dates_list = [(today + timedelta(weeks=i+1)).isoformat() for i in range(num_steps)]

    due_dates_str = ", ".join(due_dates_list)

    # --- 3. Project planning by AI ---
    prompt = create_project_plan_promt.format(
        goal=goal,
        full_context=full_context,
        total_duration_days=total_duration_days,
        num_steps=num_steps,
        due_dates_str=due_dates_str
    )

    try:
        response = api_key_manager.generate_content(prompt, model_name=GEMINI_MODEL_NAME)
        cleaned_response = response.strip().replace("```json", "").replace("```", "")
        project_data = json.loads(cleaned_response)

        steps = project_data.get('steps', [])
        tasks_to_create_in_db = []
        
        # --- 4. Process steps ---
        if steps:
            for i, step in enumerate(steps):
                raw_desc = step.get('raw_description', step.get('description', ''))
                step['description'] = raw_desc 
                
                # Ensure the date from AI matches what we calculated (for safety) or use what AI returned
                # In this case, AI should use the list we provided it
                
                if i == 0:
                    processed_data = process_and_generate_step_content(raw_desc)
                    step['description'] = processed_data['summary']
                    tasks_to_create_in_db = processed_data['tasks']

        new_project = {
            "project_id": uuid.uuid4().hex,
            "title": project_data.get('title'),
            "mission_id": mission_id,
            "description": project_data.get('description', ''),
            "icon": project_data.get('icon', 'fas fa-rocket'),
            "steps": steps,
            "current_step_index": 0,
            "status": "active"
        }
        
        add_record_result = add_record(agent, 'projects', new_project)
        
        if "error" not in add_record_result and tasks_to_create_in_db:
            # Use the first step's date as the deadline for tasks
            first_step_due_date = steps[0].get('due_date')
            
            _create_sub_tasks_in_db(
                agent, 
                new_project['project_id'], 
                new_project['title'], 
                tasks_to_create_in_db, 
                first_step_due_date,
                project_icon=new_project.get('icon', 'fas fa-rocket') 
            )
        
        if 'project_id' in new_project:
            notification_text = f"New project created: '{new_project['title']}'. First target: {steps[0].get('due_date')}"
            _notify_specific_mission(agent, new_project['project_id'], notification_text)

        return {
            "status": "Success", 
            "message": f"Project '{new_project['title']}' created starting with due date {steps[0].get('due_date')}."
        }

    except (json.JSONDecodeError, ValueError) as e:
        return {"error": "The AI failed to generate a valid project structure."}
    except Exception as e:
        return {"error": f"An unexpected error occurred: {e}"}


def _notify_specific_mission(agent, mission_id: str, message: str):
    """
    Updates a specific mission (log + TIC) about an external event (such as project progress).
    """
    from .main_service import _operate_on_tic
    log_and_print(f"--- 🔗 Syncing update to linked mission {mission_id}... ---", "SYSTEM")

    # Check that the mission exists and is active
    missions = find_records(agent, 'missions', {'mission_id': mission_id, 'status': 'active'})
    if not missions or isinstance(missions, str):
        log_and_print(f"--- ⚠️ Linked mission {mission_id} not found or is not active. ---", "WARNING")
        return

    # 1. Add to the mission's log
    system_log = {
        "timestamp": datetime.now().isoformat(),
        "type": "system",
        "content": message
    }
    #update_mission_log(agent, mission_id, system_log)
    
    # 2. Update the TIC (so the brain knows about the change next time)
    # We use a special identification string "PROJECT_UPDATE"
    _operate_on_tic(agent, mission_id, message, "PROJECT_TRACKER")



# =========================================================================================
#  2. Step Management
# =========================================================================================

def complete_current_step(agent, project_id: str):
    """
    Marks the current step as completed, advances the project to the next step,
    and automatically generates tasks for the new step.
    Includes mission update if the project is completed.
    """
    log_and_print(f"--- 🚀 Advancing project (ID: {project_id}) to next step... ---", "SYSTEM")
    table_name = "projects"
    lock = DATA_FILE_LOCKS[table_name]
    
    kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
    file_path = os.path.join(kb_path, f"{table_name}.json")
    
    # נתונים שנצטרך להמשך התהליך
    step_data_for_generation = None
    project_title = ""
    project_icon = "fas fa-tasks"
    mission_id_to_update = None  # משתנה לשמירת ה-ID של המשימה לעדכון
    
    # --- שלב 1: עדכון האינדקס בקובץ הפרויקטים ---
    with lock:
        if not os.path.exists(file_path): 
            return {"error": "Projects file not found."}
        try:
            with open(file_path, 'r', encoding='utf-8') as f: 
                projects = json.load(f)
        except Exception as e: 
            return {"error": f"Failed to load projects: {e}"}

        # מציאת הפרויקט ועדכון (by reference)
        target_project = next((p for p in projects if p.get("project_id") == project_id), None)
        
        if not target_project:
            return {"error": f"Project {project_id} not found."}
            
        current_index = target_project.get("current_step_index", 0)
        steps = target_project.get("steps", [])
        total_steps = len(steps)
        project_title = target_project.get("title", "Project")
        project_icon = target_project.get("icon", project_icon)
        
        # שמירת ה-Mission ID לעדכון מאוחר יותר
        mission_id_to_update = target_project.get("mission_id")

        # בדיקה אם יש לאן להתקדם
        if current_index < total_steps - 1:
            next_step_index = current_index + 1
            target_project["current_step_index"] = next_step_index
            
            raw_step = steps[next_step_index]
            step_data_for_generation = {
                "description": raw_step.get('description', ''),
                "due_date": raw_step.get('due_date'),
                "title": raw_step.get('title')
            }
            
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(projects, f, indent=2, ensure_ascii=False)
            except Exception as e:
                return {"error": f"Failed to save project update: {e}"}
                
        else:
            # --- הפרויקט הסתיים ---
            target_project["status"] = "completed"
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(projects, f, indent=2, ensure_ascii=False)
            
            # >>> תיקון: עדכון ה-MISSION <<<
            if mission_id_to_update:
                log_and_print(f"--- 🔗 Updating linked mission (ID: {mission_id_to_update}) on project completion ---", "SYSTEM")
                
                # אופציה א': רק הודעה ללוג (הסוכן יעדכן את ה-TIC בפעם הבאה שירוץ)
                notification_text = f"SYSTEM UPDATE: Linked Project '{project_title}' has been COMPLETED successfully."
                _notify_specific_mission(agent, mission_id_to_update, notification_text)
                
                # אופציה ב' (מומלצת): עדכון ישיר של ה-TIC אם יש שם שדה סטטוס
                # כאן אנחנו סומכים על המנגנון של _notify שיידע את הסוכן
            
            return {"status": "Success", "message": f"Project '{project_title}' completed! 🎉"}

    # --- שלב 2: יצירת משימות לשלב החדש (אם יש) ---
    if step_data_for_generation:
        # ... (שאר הקוד נשאר אותו דבר) ...
        log_and_print(f"--- 🔄 Generating tasks for new step: {step_data_for_generation['title']} ---", "SYSTEM")
        delete_record(agent, 'tasks', {'project_id': project_id, 'status': 'pending'})
        
        try:
            processed_data = process_and_generate_step_content(step_data_for_generation['description'])
            if processed_data.get('tasks'):
                _create_sub_tasks_in_db(
                    agent=agent,
                    project_id=project_id,
                    project_title=project_title,
                    tasks_list=processed_data['tasks'],
                    due_date=step_data_for_generation['due_date'],
                    project_icon=project_icon
                )
                
                # עדכון המשימה גם במעבר שלב (אופציונלי)
                if mission_id_to_update:
                    _notify_specific_mission(agent, mission_id_to_update, f"Project '{project_title}' advanced to step: {step_data_for_generation['title']}")

                return {"status": "Success", "message": f"Advanced to '{step_data_for_generation['title']}' and generated tasks."}
            else:
                return {"status": "Success", "message": "Advanced to next step, but AI generated no tasks."}
                
        except Exception as e:
            return {"error": f"Advanced step but failed to generate tasks: {e}"}

    return {"status": "Error", "message": "Unknown state"}


def set_current_step_task_project(agent, project_id: str, description: str):
    """
    מעדכן שלב בפרויקט: יוצר גיבוי לתיאור קיים, מעבד תיאור חדש ל-AI,
    מנקה משימות ישנות ומקים חדשות.
    """
    log_and_print(f"--- 🔄 Starting step update for project '{project_id}' ---", "SYSTEM")

    # 1. שליפת הפרויקט (בדיקה ראשונית ללא נעילה ארוכה)
    project_records = find_records(agent, 'projects', {'project_id': project_id})
    if not project_records:
        return {"error": "Project not found."}
    
    project = project_records[0]
    idx = project.get("current_step_index", 0)
    steps = project.get('steps', [])

    if idx >= len(steps):
        return {"error": "Project index out of bounds."}

    # 2. גיבוי (Snapshot) - רק אם קיים תיאור קודם
    current_step = steps[idx]
    old_description = current_step.get('description', '')
    
    if old_description:
        log_and_print(f"--- 💾 Backing up existing description for step {idx} ---", "DEBUG")
        _snapshot_step_state(agent, project_id, idx, old_description, project)

    # 3. עיבוד AI (מחוץ לנעילה - פעולה איטית)
    # אנחנו עושים את זה לפני ה-Update כדי לא לתקוע את ה-DB
    log_and_print(f"--- 🤖 Processing description using AI... ---", "SYSTEM")
    processed_data = process_and_generate_step_content(description)
    
    # 4. עדכון ה-DB (בתוך נעילה במידת הצורך בתוך update_record)
    # מנקים משימות פתוחות ישנות
    log_and_print(f"--- 🧹 Cleaning up old open tasks... ---", "SYSTEM")
    delete_record(agent, 'tasks', {'project_id': project_id, 'status': 'pending'})

    # מעדכנים את התיאור החדש (התקציר מה-AI) בתוך רשימת השלבים
    steps[idx]['description'] = processed_data['summary']
    
    # שמירה אחת מרוכזת של הפרויקט
    update_record(agent, 'projects', {'project_id': project_id}, {'steps': steps})

    # 5. יצירת המשימות החדשות
    log_and_print(f"--- 📝 Creating {len(processed_data['tasks'])} new tasks ---", "SYSTEM")
    _create_sub_tasks_in_db(
        agent, 
        project_id, 
        project['title'], 
        processed_data['tasks'], 
        steps[idx].get('due_date'),
        project_icon=project.get('icon', 'fas fa-tasks') 
    )

    return {
        "status": "Success", 
        "message": "Step updated successfully.",
        "summary": processed_data['summary']
    }

# =========================================================================================
#  3. מחיקה ותחזוקה
# =========================================================================================


def delete_project(agent, query: dict):
    """
    מוחק פרויקט אחד או יותר על בסיס שאילתה, ומוחק גם את כל המשימות הקשורות אליו (Cascade).
    """
    log_and_print(f"--- Starting project deletion based on query: {query} ---", "SYSTEM")
    
    # 1. שלב מקדים: מציאת הפרויקטים המיועדים למחיקה כדי לשמור את ה-IDs שלהם
    projects_to_delete = find_records(agent, 'projects', query)
    
    # בדיקה אם נמצאו פרויקטים
    if not projects_to_delete or isinstance(projects_to_delete, str):
        # אם לא נמצא כלום, נקרא לפונקציה הרגילה שתחזיר את השגיאה המתאימה
        return delete_record(agent, 'projects', query)

    # איסוף רשימת ה-IDs של הפרויקטים שנמצאו
    project_ids_to_remove = [p.get('project_id') for p in projects_to_delete if p.get('project_id')]

    # 2. מחיקת הפרויקטים עצמם (הפעולה המקורית)
    delete_result = delete_record(agent, 'projects', query)

    # 3. אם מחיקת הפרויקטים הצליחה, מוחקים את המשימות הקשורות
    if "status" in delete_result and delete_result["status"] == "Success" and project_ids_to_remove:
        log_and_print(f"--- 🧹 Performing task cleanup for {len(project_ids_to_remove)} deleted projects... ---", "SYSTEM")
        
        total_tasks_deleted = 0
        for pid in project_ids_to_remove:
            # מחיקת כל המשימות שבהן project_id שווה ל-ID של הפרויקט שנמחק
            task_deletion = delete_record(agent, 'tasks', {'project_id': pid})
            
            if isinstance(task_deletion, dict):
                count = task_deletion.get('deleted_count', 0)
                total_tasks_deleted += count
        
        # עדכון ההודעה הסופית למשתמש
        original_msg = delete_result.get('message', '')
        # טיפול במקרה שבו message לא קיים או אינו סטרינג
        if not isinstance(original_msg, str):
            original_msg = f"{delete_result.get('deleted_count', 0)} projects deleted."
            
        delete_result['message'] = f"{original_msg} Additionally, {total_tasks_deleted} associated tasks were deleted."
        log_and_print(f"   - Deleted {total_tasks_deleted} tasks associated with these projects.", "SYSTEM")

    return delete_result


def bulk_update_steps_by_query_project(agent, project_query: dict, step_selector: str, updates: dict):
    """
    כלי-על לעדכון אצווה: מעדכן שלב ספציפי (למשל, הנוכחי) על פני
    כל הפרויקטים שתואמים לשאילתה מסוימת.
    """
    log_and_print(f"--- Starting batch update of project steps by query: {project_query} ---", "SYSTEM")

    # שלב 1: מצא את כל הפרויקטים המיועדים לעדכון בעזרת מנוע השאילתות
    projects_to_update = find_records(agent, 'projects', project_query)

    if not projects_to_update or isinstance(projects_to_update, str):
        return {"status": "Info", "message": "No projects found matching the query. Nothing to update."}

    # שלב 2: אסוף את המזהים הייחודיים של הפרויקטים
    project_ids_to_update = {p.get('project_id') for p in projects_to_update if p.get('project_id')}
    log_and_print(f"   - Found {len(project_ids_to_update)} projects to update.", "SYSTEM")

    # שלב 3: בצע פעולת עדכון אטומית על הקובץ כולו
    table_name = "projects"
    lock = DATA_FILE_LOCKS[table_name]
    with lock: # <-- נועלים את הקובץ למניעת התנגשויות
        kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
        file_path = os.path.join(kb_path, f"{table_name}.json")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                all_projects = json.load(f)
        except Exception as e:
            return {"error": f"Failed to load projects file for update: {e}"}

        updated_count = 0
        # עבור על כל הפרויקטים בקובץ
        for project in all_projects:
            # אם הפרויקט הנוכחי הוא אחד מאלה שצריך לעדכן
            if project.get("project_id") in project_ids_to_update:
                
                # בחר את השלב/ים לעדכון על בסיס הבורר
                if step_selector == 'current':
                    current_index = project.get("current_step_index", 0)
                    steps = project.get("steps", [])
                    if 0 <= current_index < len(steps):
                        # החל את השינויים על השלב הנוכחי
                        steps[current_index].update(updates)
                        updated_count += 1
                # (ניתן להוסיף כאן בוררים נוספים בעתיד, כמו 'all_steps')

        # אם בוצעו שינויים, שמור את הקובץ המעודכן
        if updated_count > 0:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(all_projects, f, indent=2, ensure_ascii=False)
                log_and_print(f"✅ Batch update completed. {updated_count} projects updated.", "SYSTEM")
                return {"status": "Success", "projects_updated": updated_count}
            except Exception as e:
                return {"error": f"Failed to save updated projects file: {e}"}
        else:
            return {"status": "Info", "message": "Projects were found, but no applicable steps could be updated (e.g., project has no steps)."}


# =========================================================================================
#  4. עזר (Private Helpers)
# =========================================================================================

def process_and_generate_step_content(raw_description: str):
    """
    הפונקציה המיוחדת: מקבלת תיאור גולמי (ארוך/מסורבל) של שלב,
    ומשתמשת ב-AI כדי להחזיר אובייקט עם:
    1. תקציר קולע (לשדה description).
    2. רשימת משימות מעשיות (לביצוע).
    """
    if not raw_description:
        return {"summary": "ממתין להגדרה", "tasks": []}

    prompt = textwrap.dedent(f"""
        You are an expert project manager. Analyze the following project step description.
        
        **Raw Step Description:** "{raw_description}"

        **Your Goal:**
        1. Create a **short, concise summary** of this step (max 10 words) in English.
        2. Break this step down into **specific, actionable sub-tasks** (list of strings) in English (You MUST generate at least one task and no more than four tasks).

        **Output format (JSON only):**
        {{
        "summary": "Set up development environment",
        "tasks": ["Install Python", "Configure Git", "Create virtual environment"]
        }}
    """)

    try:
        response = api_key_manager.generate_content(prompt, model_name=GEMINI_MODEL_NAME)
        cleaned_response = response.strip().replace("```json", "").replace("```", "")
        return json.loads(cleaned_response)
    except Exception as e:
        log_and_print(f"Error in process_and_generate_step_content: {e}", "ERROR")
        # Fallback במקרה של שגיאה
        return {"summary": raw_description[:50], "tasks": [raw_description]}

def _create_sub_tasks_in_db(agent, project_id, project_title, tasks_list, due_date, project_icon):
    """
    יוצר משימות בטבלת המשימות עבור שלב בפרויקט.
    כאן אנחנו קוראים ישירות ל-add_record של טבלת tasks.
    """
    from .task_manager import add_task  # Lazy import to avoid circular dependency

    for task_title in tasks_list:
        result = add_task(
            agent,
            title=task_title,
            description="",
            due_date=None,
            category="project",
            priority="MEDIUM",
            icon=project_icon,
            project_id=project_id,
            routine_id=None,
            deadline_limit=due_date
        )
        if "error" in result:
            log_and_print(f"⚠️ Failed to create task '{task_title}': {result.get('error')}", "SYSTEM_WARNING")
        time.sleep(0.1)

def _snapshot_step_state(agent, project_id: str, step_index: int, current_description: str, project_data: dict):
    """
    פונקציית עזר: שומרת צילום מצב (Snapshot) של השלב הנוכחי בהיסטוריה לפני שינוי.
    מקבלת את אובייקט הפרויקט המלא כדי לעדכן אותו בזיכרון לפני השמירה.
    """

    # 1. שליפת המשימות הקיימות כרגע (לפני המחיקה)
    # אנו שומרים רק את הכותרות והסטטוס כדי לדעת מה היה שם
    existing_tasks = find_records(agent, 'tasks', {'project_id': project_id, 'status': 'pending'})
    if isinstance(existing_tasks, str): existing_tasks = [] # טיפול בשגיאות
    
    tasks_snapshot = [
        {"title": t.get("title"), "id": t.get("task_id")} 
        for t in existing_tasks
    ]

    # 2. יצירת אובייקט ההיסטוריה
    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "description": current_description,
        "tasks_snapshot": tasks_snapshot,
        "reason": "Automatic backup before update"
    }

    # 3. דחיפה למערך ההיסטוריה של השלב הספציפי
    steps = project_data.get('steps', [])
    if 0 <= step_index < len(steps):
        if "history" not in steps[step_index]:
            steps[step_index]["history"] = []
        
        # שומרים רק את 5 הגרסאות האחרונות כדי לא להעמיס
        steps[step_index]["history"].append(snapshot)
        if len(steps[step_index]["history"]) > 5:
            steps[step_index]["history"].pop(0)
            
        log_and_print(f"📸 Backed up step {step_index+1} in project (saved {len(tasks_snapshot)} tasks to archive).", "SYSTEM")



def define_project_step(agent, project_id: str, steps_updates: list):
    """
    מגדיר משימות לשבועות עתידיים או נוכחיים בפרויקט.
    תיקון: אם הנושא של השבוע הנוכחי משתנה, המערכת מוחקת משימות ישנות ומייצרת חדשות.
    """
    log_and_print(f"--- Updating step descriptions for project '{project_id}' ---", "SYSTEM")
    
    if not isinstance(steps_updates, list):
        return {"error": "Input 'steps_updates' must be a list of objects (e.g., [{'week_number': 2, 'description': '...'}])"}

    table_name = "projects"
    lock = DATA_FILE_LOCKS[table_name]
    
    should_regenerate_tasks = False
    regeneration_data = {} # נתונים שנצטרך כדי ליצור את המשימות מחדש (מחוץ לנעילה)
    
    project_found = False
    updates_log = []
    errors_log = []

    # --- שלב 1: עדכון הכותרות בקובץ הפרויקטים (בתוך נעילה) ---
    with lock:
        kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
        file_path = os.path.join(kb_path, f"{table_name}.json")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f: projects = json.load(f)
        except Exception as e: return {"error": f"Failed to load projects: {e}"}

        for i, project in enumerate(projects):
            if project.get("project_id") == project_id:
                project_found = True
                steps = project.get("steps", [])
                total_steps = len(steps)
                current_index = project.get("current_step_index", 0)
                
                for item in steps_updates:
                    week_num = item.get('week_number')
                    desc = item.get('description')
                    
                    if not week_num or not desc:
                        errors_log.append(f"Missing data in item: {item}")
                        continue
                        
                    step_index = int(week_num) - 1
                    
                    if step_index >= total_steps or step_index < 0:
                        errors_log.append(f"Week number {week_num} is out of bounds.")
                        continue
                    
                    
                    # אם אנחנו משנים את השלב הנוכחי (שזה השלב הקריטי למשימות)
                    if step_index == current_index:
                        current_desc = steps[step_index].get('description', '')
                        # קריאה לפונקציית הגיבוי שיצרנו למעלה (מעבירים את אובייקט הפרויקט)
                        _snapshot_step_state(agent, project_id, step_index, current_desc, projects[i])
                        
                        should_regenerate_tasks = True
                        regeneration_data = {
                            "description": desc,
                            "title": project.get("title"),
                            "due_date": steps[step_index].get("due_date"),
                            "icon": project.get("icon", "fas fa-tasks")
                        }
                    
                    # עדכון התיאור בקובץ
                    projects[i]['steps'][step_index]['description'] = desc
                    updates_log.append(f"Week {week_num}")

                break
        
        if not project_found:
            return {"error": f"Project ID '{project_id}' not found."}

        if updates_log:
            try:
                with open(file_path, 'w', encoding='utf-8') as f: json.dump(projects, f, indent=2, ensure_ascii=False)
            except Exception as e: return {"error": f"Failed to save project: {e}"}

    # --- שלב 2: מחיקה ויצירה מחדש (מחוץ לנעילה) ---
    extra_msg = ""
    
    if should_regenerate_tasks and regeneration_data:
        log_and_print(f"--- 🔄 Weekly subject changed. Regenerating tasks... ---", "SYSTEM")
        
        # 1. מחיקת המשימות הישנות
        delete_record(agent, 'tasks', {'project_id': project_id, 'status': 'pending'})
        
        # 2. הפעלת ה-AI לפירוק הנושא החדש למשימות
        # משתמשים בפונקציה הקיימת שלך שמייצרת תוכן
        processed_data = process_and_generate_step_content(regeneration_data["description"])
        
        # 3. יצירת המשימות החדשות ב-DB
        if processed_data.get('tasks'):
            _create_sub_tasks_in_db(
                agent=agent,
                project_id=project_id,
                project_title=regeneration_data["title"],
                tasks_list=processed_data['tasks'],
                due_date=regeneration_data["due_date"],
                project_icon=regeneration_data["icon"]
            )
            extra_msg = " (Old tasks deleted, new tasks generated based on updated subject)."
        else:
            extra_msg = " (Old tasks deleted, but AI did not generate new tasks)."

    # הרכבת הודעת סיום
    status_msg = f"Updated: {', '.join(updates_log)}.{extra_msg}"
    if errors_log:
        status_msg += f" Errors: {'; '.join(errors_log)}"
        
    return {"status": "Success", "message": status_msg}

def get_project_step_history(agent, project_id: str):
    """
    מחזיר את היסטוריית השינויים של השלב הנוכחי בפרויקט.
    """
    projects = find_records(agent, 'projects', {'project_id': project_id})
    if not projects: return {"error": "Project not found"}
    
    project = projects[0]
    idx = project.get("current_step_index", 0)
    steps = project.get("steps", [])
    
    if idx >= len(steps): return "No active step found."
    
    current_step = steps[idx]
    history = current_step.get("history", [])
    
    if not history:
        return {"message": "No history found for the current step (it might be new)."}
    
    # מפרמט את התשובה בצורה קריאה ל-AI
    readable_history = []
    for i, h in enumerate(reversed(history)): # מהחדש לישן
        tasks_titles = [t['title'] for t in h.get('tasks_snapshot', [])]
        readable_history.append(f"Version -{i+1} (Date: {h.get('timestamp')}):\n   Description: {h.get('description')}\n   Tasks that were there: {', '.join(tasks_titles)}")
        
    return "\n".join(readable_history)

def revert_project_step(agent, project_id: str, history_versions_back: int = 1):
    """
    משחזר את השלב הנוכחי בפרויקט לגרסה קודמת מההיסטוריה.
    history_versions_back=1 אומר "הגרסה האחרונה לפני השינוי הנוכחי".
    """
    log_and_print(f"--- ⏪ Performing version restore for project '{project_id}'... ---", "SYSTEM")
    
    table_name = "projects"
    lock = DATA_FILE_LOCKS[table_name]
    
    restored_description = ""
    project_title = ""
    project_icon = ""
    due_date = ""

    # שלב 1: שליפת המידע מההיסטוריה (בתוך נעילה)
    with lock:
        kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
        file_path = os.path.join(kb_path, f"{table_name}.json")
        
        with open(file_path, 'r', encoding='utf-8') as f: projects = json.load(f)
        
        target_project = next((p for p in projects if p.get("project_id") == project_id), None)
        if not target_project: return {"error": "Project not found"}
        
        idx = target_project.get("current_step_index", 0)
        steps = target_project.get("steps", [])
        
        if "history" not in steps[idx] or not steps[idx]["history"]:
            return {"error": "No history available to revert to."}
        
        history = steps[idx]["history"]
        
        # חישוב האינדקס לשחזור (האחרון ברשימה הוא הכי חדש)
        if history_versions_back > len(history):
            return {"error": f"Cannot go back {history_versions_back} versions. Only {len(history)} available."}
            
        # שליפת הגרסה הרצויה (pop מוציא אותה מההיסטוריה כי אנחנו חוזרים אליה)
        # אם רוצים לשמור את ההיסטוריה גם אחרי שחזור, משתמשים ב-negative index בלי pop
        version_to_restore = history[-history_versions_back]
        
        # מעדכנים משתנים לשימוש מחוץ לנעילה
        restored_description = version_to_restore.get("description")
        project_title = target_project.get("title")
        project_icon = target_project.get("icon", "fas fa-tasks")
        due_date = steps[idx].get("due_date")
        
        # מעדכנים את קובץ הפרויקט חזרה לתיאור הישן
        steps[idx]["description"] = restored_description
        
        # אופציונלי: מחיקת רשומת ההיסטוריה שחזרנו אליה (כדי שלא נחזור אליה שוב בטעות)
        # או השארתה לתיעוד. כאן נשאיר אותה.

        with open(file_path, 'w', encoding='utf-8') as f: json.dump(projects, f, indent=2, ensure_ascii=False)

    # שלב 2: שחזור המשימות (מחוץ לנעילה)
    if restored_description:
        # 1. מחיקת המשימות "השגויות" הנוכחיות
        delete_record(agent, 'tasks', {'project_id': project_id, 'status': 'pending'})
        
        # 2. יצירה מחדש של המשימות לפי התיאור הישן
        # משתמשים ב-AI שוב כדי לייצר משימות טריות מהתיאור הישן.
        # זה עדיף על שחזור ID ישנים שאולי התנגשו.
        processed_data = process_and_generate_step_content(restored_description)
        
        if processed_data.get('tasks'):
            _create_sub_tasks_in_db(
                agent=agent,
                project_id=project_id,
                project_title=project_title,
                tasks_list=processed_data['tasks'],
                due_date=due_date,
                project_icon=project_icon
            )
            return {
                "status": "Success", 
                "message": f"Reverted to previous version. Description restored: '{restored_description[:30]}...'. Tasks regenerated."
            }
    
    return {"error": "Failed to restore description."}
