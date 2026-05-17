# app/core/tools_registry.py

import json
from typing import Dict, Any, Optional

_active_agent: Optional[Any] = None

def _set_active_agent(agent):
    global _active_agent
    _active_agent = agent

def _get_active_agent():
    return _active_agent

# =========================================================================================
#  1. Import all tool functions from split modules
# =========================================================================================

# --- Database management tools (JSON) ---
from ..db.json_manager import (
    add_record,
    find_records,
    update_record,
    update_all_records,
    update_records_by_query,
    delete_record
)

# --- Google Workspace tools ---
from ..services.google_workspace import (
    check_gmail_for_updates,
    list_calendar_events,
    create_calendar_event,
    find_conflicting_events,
    create_google_task,
    import_google_sheet
)

# --- Communication tools (WhatsApp) ---
from ..services.communication_manager import (
    handle_communication_request,
    send_simple_notification,
    draft_message_universal,
    generate_daily_briefing,
    generate_end_of_day_report
)

# --- Task and project management tools (Logic) ---
# Assuming you created services/task_manager.py and services/project_manager.py
from ..services.task_manager import (
    add_task,
    check_for_existing_task,
    complete_task
)
from ..services.project_manager import (
    create_project_timeboxed_action_plan,
    set_current_step_task_project,
    complete_current_step,
    define_project_step,
    get_project_step_history,
    revert_project_step,
    delete_project,
    bulk_update_steps_by_query_project
)

# --- Analytics and financial tools ---
from ..services.analytics import (
    find_clients_to_contact,
    find_and_sort_communications,
    get_last_communication,
    calculate_strategic_price
)

# --- Scheduling and background job tools ---
from ..db.query_parser import (
    add_scheduled_job,
    list_scheduled_jobs,
    remove_scheduled_job,
    list_client_shows_sorted,
    add_fields_and_update_all_records
)
from ..services.main_service import (
    schedule_one_time_task
)

# --- Special tools (meta-tasks, automations, files) ---
from ..services.file_handler import (
    generate_presentation_feature,
    extract_text_from_file, # if you want to expose it as a tool
    analyze_document_and_update_tic,
    inspect_file_content
)

# --- Web research and information processing tools ---
from ..services.web_research import (
    generate_analytical_roadmap,
    execute_search_multi_task_prompt,
    find_quick_answer_online,
    perform_advanced_research
)


# --- System state-related tools ---
from ..utils.system_state import manage_freeze_mode

# --- General helper functions ---
from ..utils.date_helpers import get_current_datetime
from ..services.main_service import (
    calculate_relative_date,
    set_work_schedule_template,
    confirm_and_schedule_work_day,
    set_goal,
    update_goal_progress,
    visualize_data,
    add_routine,
    delete_routine,
    create_mission,
    update_mission_log,
    autonomously_advance_mission,
    add_user_response_to_mission_log,
    create_new_data_table,
    import_from_chat,
    generate_and_build_flow,
    update_user_context,
    add_payment
)

# =========================================================================================
#  2. Define the central tool map (TOOL_MAP)
# =========================================================================================


TOOL_MAP = {
    
    "query_guide": {
        "target_function": None,
        "description": """
        📘 **QUERY GUIDE – for `find_records` tools**

        Use with any tool that accepts a `query` parameter (e.g. `query_database`, etc).

        ---

        ### ⚙️ `$compute` – Dynamic Fields
        Create temporary fields before filtering.

        **Rules:**
        - `from_array`, `select_by_index_from`, `get_field`: extract a field from an array using an index.
        - `count_of`: count items in an array.

        **Examples:**
        ```json
        {"$compute": {
            "current_step_due_date": {
                "from_array": "steps",
                "select_by_index_from": "current_step_index",
                "get_field": "due_date"
            }},
            "status": "active",
            "current_step_due_date": {"$lt": "2025-09-22"}
        }

        {"$compute": {"total_steps": {"count_of": "steps"}},
        "status": "active", "total_steps": {"$gt": 5}}
        ```

        ---

        ### 🔍 Field Filters
        `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`, `$exists`, `$regex`

        Example:
        ```json
        {"price": {"$gte": 500}, "client_name": {"$regex": "^S"}}
        ```

        ---

        ### 🧠 Logical Operators
        `$or`, `$and`, `$not`

        Example:
        ```json
        {"$or": [{"status": "pending"}, {"priority": "urgent"}]}
        ```

        ---

        ### 📊 Sorting & Limiting
        ```json
        {"$sort": {"date": -1}, "$limit": 5}
        ```

        ---

        ### ✅ Common Use Cases
        - Most recent show:
        ```json
        {"$sort": {"date": -1}, "$limit": 1}
        ```
        - Tasks due today OR high priority:
        ```json
        {"$or": [{"due_date": "2025-09-23"}, {"priority": "high"}]}
        ```
        - Clients starting with “S”:
        ```json
        {"client_name": {"$regex": "^S"}}
        ```
        - Top 5 unpaid invoices in 2025:
        ```json
        {"$and": [{"status": "unpaid"}, {"date": {"$regex": "^2025"}}],
        "$sort": {"date": 1}, "$limit": 5}
        ```
        """
    },

    
    # --- Existing tools ---
    "add_record": {
        "target_function": add_record,
        "static_params": {},
        "description": (
            "Generic insert utility that adds a record to a given table.",
            "Parameters: table_name (str), data (dict)."
        )
    },
    "find_records": {
        "target_function": find_records,
        "static_params": {"table_name": "goals", "query": {}},
        "description": "Use to get data from table gets table_name and query: Dict[str, Any] as param"
    },
    "list_gigs": {
        "target_function": find_records,
        "static_params": {"table_name": "shows", "query": {}},
        "description": "Use to get a list of all shows that are already saved in the system."
    },
    "list_clients": {
        "target_function": find_records,
        "static_params": {"table_name": "clients", "query": {}},
        "description": "Use to get a list of all clients that are already saved in the system."
    },
    "find_clients_to_contact": {
        "target_function": find_clients_to_contact,
        "static_params": {},
        # Improved description indicating the ability to receive a time parameter
        "description": "Analyzes communication logs to find clients that haven't been contacted recently. It can accept a time duration (e.g., '3 months', '90 days') to define 'recently'."
    },

    # --- Improved and new tools ---
    "check_gmail_for_updates": {
        "target_function": check_gmail_for_updates,
        "static_params": {},
        "description": "Use this ONLY to scan Gmail for NEW emails from clients. Can accept `days_back` and `client_name` can leave empty to check all the clients."
    },
    # *** The new and important tool ***
    "list_communications": {
        "target_function": find_records,
        "static_params": {"table_name": "communications"},
        "description": "Use to find and list past communication events (emails, calls, meetings) that are ALREADY LOGGED in the system. Must get the param `query`!. It must be empty"
    },
    
    
    "list_client_communications_sorted": {
        "target_function": find_and_sort_communications,
        "static_params": {"table_name": "communications"},
        "description": "Use to get a FULL HISTORY of all communications with a specific client, sorted by date (from oldest to newest). Requires the `client_name` parameter."
    },

    "get_last_client_communication": {
        "target_function": get_last_communication,
        "static_params": {"table_name": "communications"},
        "description": "Finds the SINGLE most recent communication (the very last one) with a specific client. Requires the `client_name` parameter."
    },
    ### --- New calendar tools --- ###
    "list_calendar_events": {
        "target_function": list_calendar_events,
        "static_params": {},
        "description": "Searches Google Calendar for events. Can filter by a text 'query' (e.g., 'rehearsal'), a specific date range ('start_date_iso', 'end_date_iso'), or a number of 'days_ahead'. CRITICAL for cross-referencing information, like finding all rehearsals within a known vacation period. this can return also dates of events so if the user ask for someting that can be in the calendar it is inevitable that you use list_calendar_events"
    },
    "find_conflicting_events": {
    "target_function": find_conflicting_events,
        "static_params": {},
        "description": (
            "A powerful specialist tool to find events of one type that conflict with another event. this is espically used to find events that at the same time with other events"
            "Use this for questions like: 'Which rehearsals will I miss during my vacation?', 'Are there any gigs scheduled during the conference?', or 'Find meetings that overlap with my family trip'. "
            "It requires two parameters: "
            "1. `primary_event_query`: The name of the main event that defines the time range (e.g., 'vacation', 'trip to greece'). "
            "2. `secondary_event_query`: The name of the events you are looking for within that range (e.g., 'rehearsal', 'gig')."
        )
    },
    "create_calendar_event": {
        "target_function": create_calendar_event,
        "static_params": {},
        "description": "Use this to create one or more new events in Google Calendar. The `events` parameter must be a list of event objects, each requiring a `summary`, `start_time`, and `end_time`. The agent will **autonomously select a color** based on the event's title (e.g., meetings will be blue, deadlines will be red). The user can override this by specifying a `color_id` (a string from '1' to '11')."
    },
    "update_record": {
            "target_function": update_record,
            "static_params": {},
            "description": (
                "Updates a SINGLE existing record in any table. "
                "It requires three parameters:\n"
                "1. `table_name` (string): The name of the table to modify.\n"
                "2. `query` (dict): A query object to uniquely identify the record to update (e.g., {'task_id': 'some_id'}).\n"
                "3. `updates` (dict): A dictionary of the fields to change."
            )
        },
    "update_records_by_query": {
        "target_function": update_records_by_query,
        "static_params": {},
        "description": (
            "Updates MULTIPLE records in a table that match a specific query. "
            "It requires three parameters:\n"
            "1. `table_name` (string): The name of the table to modify.\n"
            "2. `query` (dict): A query object to select the records for updating.\n"
            "3. `updates` (dict): A dictionary where keys are the field names to change and values are their new values. "
            "**CRITICAL: This parameter MUST be named 'updates'.**"
        )
    },
    ### --- New batch update tool --- ###
    "update_all_records": {
        "target_function": update_all_records,
        "static_params": {},
        "description": "Use this to update ALL records in a specific table with the same new data. WARNING: This tool modifies the entire table."
    },
    "get_current_datetime": {
        "target_function": get_current_datetime,
        "static_params": {},
        "description": "Use this to find out the current, real-world date and time. Essential for answering questions like 'what day is it?' or 'what time is it?' it is also usefull when you need to know what is today date"
    },
    "check_for_existing_task": {
        "target_function": check_for_existing_task,
        "static_params": {},
        "description": "Checks if a task with the given title (PARAM NAME) exists in the database. The title can be in natural language; no specific format is required. use this if the user ask something like 'can you check if a task exists'"
    },
    "add_task": {
        "target_function": add_task,
        "static_params": {},
        "description": (
            "Creates a new task. "
            "REQUIRED: 'title', 'icon' "
            "OPTIONAL: 'description', 'project_id', 'due_date', 'priority','deadline_limit', 'allow_duplicates'. "
            "SMART BEHAVIOR (Priority): You do NOT need to ask for 'priority'. The AI calculates it."
            "SMART BEHAVIOR (Date): "
            "1. If the user gives a specific date, provide it in 'due_date' (YYYY-MM-DD)."
            "2. If the user gives a vague constraint (e.g., 'after Monday', 'next week', 'not before 2024-05-01'), WRITE THAT TEXT directly into 'due_date'."
            "   The system will detect it is not a date and run an optimization algorithm to find the best specific slot based on that constraint."
            "3. if there is no 'due_date' you can keep it enmty and the system will choose the best date"
            "DEADLINE: Use 'deadline_limit' (YYYY-MM-DD) only if the user specifically says 'MUST be done BEFORE X'."
            "ICONS: Select the best Font Awesome icon based on keywords (e.g., 'fas fa-phone' for calls, 'fas fa-shopping-cart' for buying)."
        )
    },
    "list_tasks": {
        "target_function": find_records,
        "static_params": {"table_name": "tasks"},
        "description": " Use this for specific searches only. For general searches or task that you dont know exaclly the title, use check_for_existing_task. Lists existing tasks. A query parameter is mandatory. Use it to filter results (e.g., {'status': 'pending'}). To list all tasks, provide an empty dictionary: {{}}."    
        },
    "delete_gig": {
        "target_function": delete_record,
        "static_params": {"table_name": "shows"},
        "description": "Use to delete a specific gig or show from the system. Requires a query to identify the gig (e.g., by name or date)."
    },
    "delete_client": {
        "target_function": delete_record,
        "static_params": {"table_name": "clients"},
        "description": "Use to delete a specific client from the system. Requires a query to identify the client (e.g., by name)."
    },
    "delete_communication": {
        "target_function": delete_record,
        "static_params": {"table_name": "communications"},
        "description": "Use to delete a specific communication log from the system. Requires a query to identify the log."
    },
    "delete_task": {
        "target_function": delete_record,
        "static_params": {"table_name": "tasks"},
        "description": "Use to delete a specific task from the to-do list it can also delete moltiple tasks. Requires a query to identify the task. This action requires explicit user confirmation before use"
    },
    "complete_task": {
        "target_function": complete_task,
        "static_params": {},
        "description": "Use this tool to mark a task as 'completed'. Provide a `task_description` or 'task_id' to identify the task . This changes the task's status but does NOT delete it from the database."
    },
    
    "calculate_relative_date": {
        "target_function": calculate_relative_date,
        "static_params": {},
        "description": "Use this to calculate a future date. It takes an integer `days_from_now`. For 'tomorrow', use `days_from_now=1`. For 'the day after tomorrow', use `days_from_now=2`."
    },
    "add_scheduled_job": {
        "target_function": add_scheduled_job,
        "static_params": {},
        "description": "Schedules a recurring task. Requires `prompt`, `time_hh_mm`, and optionally a `days` parameter (a list of English day names, e.g., [\"Sunday\"]). You can also set `weeks_interval` (integer) to define frequency (e.g., set to 2 for 'every two weeks'). If `days` is omitted, the task runs daily. Also accepts `send_notification` (true, false, or 'on_success')."
    },
    "list_scheduled_jobs": {
        "target_function": list_scheduled_jobs,
        "static_params": {},
        "description": "Use this to see all the existing recurring daily tasks that are currently scheduled."
    },
    "remove_scheduled_job": {
        "target_function": remove_scheduled_job,
        "static_params": {},
        "description": "Removes one or more scheduled tasks based on a list of their `job_ids`. Requires a `job_ids` parameter which must be a list of strings."
    },
    "schedule_one_time_task": {
        "target_function": schedule_one_time_task,
        "static_params": {},
        "description": "Schedule one or more **system actions or AI commands** to run once at a future time. \n\n**Single task mode** — provide: 1. `prompt_to_execute` (string): The command for the agent to run. 2. `execution_datetime` (string): Exact time in ISO 8601 (YYYY-MM-DDTHH:MM:SS). 3. `send_notification` (true/false/'on_success', default True).\n\n**Bulk mode** — provide a `tasks` list where each object has `prompt_to_execute` (or `prompt`), `execution_datetime`, and optionally `send_notification`."
    },
    "generate_daily_briefing": {
        "target_function": generate_daily_briefing,
        "static_params": {},
        "description": "Generates a smart morning briefing, including the day's schedule, critical tasks, and workload analysis. Use this for a 'good morning', 'daily summary', or 'what's my plan for today' request."
    },
    "generate_end_of_day_report": {
        "target_function": generate_end_of_day_report,
        "static_params": {},
        "description": "Generates a smart end of the day briefing, including the day's schedule, critical tasks, and workload analysis. Use this for a 'end day summary', 'end of the daily summary', or 'how I did on this day' request."
    },
    "find_client_shows_sorted": {
        "target_function": list_client_shows_sorted,
        "static_params": {},
        "description": "Finds all shows for a specific client and sorts them by date, from the newest to the oldest. Requires the `client_name` parameter."
    },
    "add_fields_to_table": {
        "target_function": add_fields_and_update_all_records,
        "static_params": {},
        "description": "Use this to add new columns or fields to an existing table's structure (schema) and set a default value for all existing records. For example, if the user says 'add a start_time to all gigs', use this tool."
    },
    "generate_analytical_roadmap": {
        "target_function": generate_analytical_roadmap,
        "static_params": {},
        "description": (
            "For complex tasks that require a multi-step thought process to achieve an organized answer. "
            "This tool creates an execution plan (a 'roadmap') to combine data from multiple tables. "
            "Use it for comprehensive summaries ('Tell me everything about client Hezi') or strategic questions ('Who is our most valuable client?'). or complex tasks (delete all the data form client a and create new one)"
            "**Parameters:**\n"
            "- `user_prompt` (string): The user's full, original question.\n"
            "- Additionally, use this function when the user asks for a 'stronger answer' or explicitly requests to use this function. "
            "- When this is requested, understand what the user asked for and call the function to perform the action the user requested."
        )
    },
    "calculate_strategic_price": {
    "target_function": calculate_strategic_price,
    "static_params": {},
    "description": "Analyzes past gigs and current schedule to calculate and recommend a strategic price for a new gig. Use this as the FIRST step before drafting a price quote."
    },
    "draft_message_universal": {
        "target_function": draft_message_universal,
        "static_params": {},
        "description": (
            "The FINAL tool for drafting any personalized message. It requires a 'purpose' (to find the base template (it's important to ask clarifying question about the backround of the purpose, for example, if the user wants to send a message to group, you should ask for the history of the group before calling this function, since a brand-new group is different from one that's in its second year, etc)), 'context_data' (all the information you've gathered Also includes the name of the place where the message is being sent.), "
            "and 'user_request_for_context' Provide the user's full picture, so the tool can understand the specific nuance and goal of the new message."
        )
    },
    "send_simple_notification": {
        "target_function": send_simple_notification,
        "static_params": {},
        "description": "Send a **direct reminder or alert message** to the user's Telegram account. Use ONLY when the user explicitly asks to be reminded or notified about something ('remind me', 'send me a notification about...', 'send_simple_notification'). The purpose is to send an immediate or scheduled message that reminds the user to take an action. The function receives a 'message' as a parameter — this will be the exact reminder text sent to the user."
    },
    "handle_communication_request": {
        "target_function": handle_communication_request,
        "static_params": {},
        "description": (
            "CRITICAL: Use this tool IMMEDIATELY whenever the user asks to send a message, a reminder, or a WhatsApp to someone. "
            "DO NOT reply with 'I understand' or 'I will send it'. DO NOT explain how you will do it. You MUST actually EXECUTE this tool right now to perform the action. "
            "PARAMS: 'recipient_name' (exact or approximate, e.g., 'dad', 'the band', 'worker bot') and 'message_intent'. "
            "The 'message_intent' must include as much context as possible — clearly describe the situation, relevant background, and the exact goal of the message. The more precise the context, the better the result. "
            "The tool itself will handle finding the contact, drafting the text, and sending it. "
            "ACTION REQUIRED: If the user explicitly tells you to send a message, CALL THIS TOOL NOW."
        )
    },
    "list_projects": {
        "target_function": find_records,
        "static_params": {"table_name": "projects"},
        "description": "Use to list all existing long-term projects. Can be filtered, e.g., {'status': 'active'}."
    },
    "complete_current_step": {
        "target_function": complete_current_step,
        "static_params": {},
        "description": "Use this to mark the CURRENT step of a multi-step project as complete and move to the next one. Requires the `project_id`."
    },
    "create_project_timeboxed_action_plan": {
        "target_function": create_project_timeboxed_action_plan,
        "static_params": {'goal' : 'use goal', 'total_duration_days' : 'for exmaple 90d'},
        "description": "Use this to create a step-by-step action plan. Parameters: `goal` (str), `title` (str), `scope_description` (str), `total_duration_days` (int), and optionally `first_due_date` (str, YYYY-MM-DD) to set the deadline for the first week." 
    },
    "set_current_step_task_project": {
        "target_function": set_current_step_task_project,
        "static_params": {},
        "description": "Use to define the specific task for the CURRENT active step of a project. Requires `project_id` and the `description` of the task."
    },
    "delete_project": {
        "target_function": delete_project,
        "static_params": {},
        "description": "Use to delete one or more projects from the system based on a query (e.g., by title or project_id)."
    },
    "bulk_update_steps_by_query_project": {
        "target_function": bulk_update_steps_by_query_project,
        "static_params": {},
        "description": (
            "A powerful batch tool to update a specific step across MULTIPLE projects that match a query. "
            "Use this for sweeping changes like 'postpone the weekly task in all active projects'.\n"
            "Parameters:\n"
            "- `project_query` (dict): A query to select which projects to modify (e.g., {'status': 'active'}).\n"
            "- `step_selector` (str): Which step to update. Currently, only 'current' is supported.\n"
            "- `updates` (dict): The changes to apply to the step (e.g., {'due_date': 'YYYY-MM-DD'})."
        )
    },
    "define_project_step": {
        "target_function": define_project_step,
        "static_params": {},
        "description": (
            "Use this to define tasks for ONE or MULTIPLE weeks in a project. "
            "Requires `project_id` and `steps_updates`. "
            "`steps_updates` must be a LIST of objects, where each object has `week_number` (int) and `description` (string). "
            "Example: `steps_updates=[{'week_number': 2, 'description': 'Research'}, {'week_number': 3, 'description': 'Drafting'}]`"
        )
    },
    "get_project_step_history": {
        "target_function": get_project_step_history,
        "static_params": {},
        "description": "Use this to see previous versions of the current project step (descriptions and tasks that were there before changes). Requires `project_id`."
    },
    "revert_project_step": {
        "target_function": revert_project_step,
        "static_params": {},
        "description": (
            "Use this to UNDO changes to a project step. It restores the previous description and regenerates the tasks based on it. "
            "Requires `project_id`. Optional: `history_versions_back` (int, default 1 for the most recent backup)."
        )
    },
    "set_work_schedule_template": {
        "target_function": set_work_schedule_template,
        "static_params": {},
        "description": "Sets or updates the user's general weekly work schedule template example of use input: Thursday between 8 and 4. IMPORTANT EVERY TIME THE USER SAY SOMETHING LIKE DAY AND THEM HOUR - HOUR HE MEAN THIS FUNCTION Expects a dictionary mapping English day names to start/end times for example: {'schedule_data': {'Sunday': {'start_time': '08:00', 'end_time': '16:00'}}}."
    },
    "confirm_and_schedule_work_day": {
        "target_function": confirm_and_schedule_work_day,
        "static_params": {},
        "description": "Confirms and schedules a work day. It takes a `start_time` and an optional `target_date_iso`. If the date is not provided, it defaults to today. This tool blocks the time in the calendar and then automatically schedules that day's tasks within the block. Use this for requests like 'I'm starting my day at 09:00' or 'Tomorrow I'll start at 9 AM'. if the user say I start the day now YOU MUST NOT PUT ANYTHING IN start_time "
    },
    "set_goal": {
        "target_function": set_goal,
        "static_params": {'raw_goal_description' : 'data'},
        "description": "Use this to set a long-term, measurable objective that you want to track over time. It must get also the start point as number (if the user does not add this ask him for this and add this to raw_goal_description). Ideal for requests like 'I want to increase my monthly income by 15%' or 'Set a goal to maintain my desk cleanliness at a rating of 9 out of 10'."    },
    "list_goals": {
        "target_function": find_records,
        "static_params": {"table_name": "goals", "query": {}},
        "description": "Use to list all currently active long-term goals."
    },
    "update_goal_progress": {
        "target_function": update_goal_progress,
        "static_params": {},
        "description": "Use to update the 'current_value' of a specific goal's metric. Requires a 'goal_title_query' to identify the goal and the 'new_current_value'."
    },
    "visualize_data": {
        "target_function": visualize_data,
        "static_params": {},
        "description": (
            "The FINAL tool to generate any data visualization (chart/graph). "
            "It requires the user's 'analysis_prompt' and a 'context_data' dictionary containing all the data gathered by PREVIOUS tool calls. "
            "CRITICAL: 'context_data' MUST be a dictionary (JSON object) where keys are descriptive names of the datasets, and values are the lists of records. "
            "Example format: {'dataset_name': [{'col1': 'val1'}, {'col1': 'val2'}]}. "
            "DO NOT pass a flat list directly; always wrap the list of records inside a dictionary with a descriptive key name."
        )
    },
    "execute_search_multi_task_prompt": {
        "target_function": execute_search_multi_task_prompt,
        "static_params": {},
        "description": (
            "This function is designed to handle multiple small tasks within a single tool call. It takes a 'prompt' as a parameter, which can include several tasks—for example, retrieving contact information about a hospital as well as details about a specific house. The function returns this information accordingly. It is intended for general data retrieval rather than for handling detailed or complex queries."
            "CRITICAL: This tool accepts no more than 10 questions. You must limit your total number of questions to 10."
        )
    },
    "find_quick_answer_online": {
        "target_function": find_quick_answer_online,
        "static_params": {},
        "description": (
            "**Information Gathering Step:** Retrieves a specific, factual piece of information from the internet. "
            "CRITICAL: This tool returns its findings **internally to the AI agent** so they can be used in the next step of the reasoning process. "
            "Use this when you need a single data point to continue a process, for example: 'Find the dates of the upcoming music festival' (so you can then check them against the calendar), 'What is the current exchange rate?', or 'Verify who the CEO of company X is'."
            "it gets query as paramet with the quation that we want to ask"
        )
    },
    "perform_advanced_research": {
        "target_function": perform_advanced_research,
        "static_params": {},
        "description": (
            "🛑 **Final Action:** Performs in-depth research and synthesizes it into a comprehensive report that is sent **directly to the user as a final answer**. "
            "The AI agent **will NOT get the research results back**, and the process terminates after this tool is called. "
            "Therefore, it is **strictly forbidden** to use it as an information-gathering step in a multi-step plan. It must ONLY be used as the **final action**. "
            "Ideal for standalone requests like: 'Give me a detailed overview of the latest trends in AI' or 'Compare the pros and cons of moving to Lisbon'."
            "it gets query as paramet with the quation that we want to ask"
        )
    },
    "add_routine": {
        "target_function": add_routine,
        "static_params": {},
        "description":"Used to create a recurring task/routine. Requires 'title', 'frequency' ('daily' or 'weekly'). If 'weekly', you MUST provide 'days_of_week'. You can provide multiple days in the 'days_of_week' string, for example: 'Sunday, Tuesday' or 'Monday and Thursday'."
    },
    "delete_routine": {
        "target_function": delete_routine,
        "static_params": {},
        "description": "Use to delete one or more routines from the system. Requires a `query` parameter to identify the routine(s) to delete (e.g., by 'title' or 'routine_id')."
    },
    "create_mission": {
        "target_function": create_mission,
        "static_params": {},
        "description": "Initializes a new long-term autonomous mission. The AI will manage and monitor this mission over time. when the user say something like create me helper .... user this.  Requires both a 'title' and a 'goal_description' (exact numerical values are not necessary; for example, 'Schedule performances for 2 players in Be'er Sheva' is acceptable). Do NOT call 'autonomously_advance_mission' after invoking this function."
    },
    "update_mission_log": {
        "target_function": update_mission_log,
        "static_params": {},
        "description": "For internal use. Adds an entry to a mission's log. Requires 'mission_id' and 'log_entry'."
    },
    "autonomously_advance_mission": {
        "target_function": autonomously_advance_mission,
        "static_params": {},
        "description": "DO NOT use autonomously_advance_mission after create_mission function. For internal use. use this only if the user spesific ask for this. Requires 'mission_id'."
    },
    "add_user_response_to_mission_log": {
        "target_function": add_user_response_to_mission_log,
        "static_params": {},
        "description": "Adds a user's text response to a mission's log and immediately triggers the AI's next thinking cycle. Requires 'mission_id' and 'response_text'."
    },
    "add_payment": {
        "target_function": add_payment,
        "static_params": {},
        "description": "Creates a new recurring payment. Requires 'name' (string), 'amount' (number), and 'payment_day' (integer 1-31). Optional: 'currency'."    
        },
    "delete_payment": {
        "target_function": delete_record,
        "static_params": {}, 
        "description": "Deletes a payment. **CRITICAL: You MUST provide the parameter 'table_name' with the value 'payments'.** Also requires a 'query' parameter (e.g. {'name': 'Netflix'})."
    },
    "create_new_data_table": 
        {"target_function": create_new_data_table,
        "static_params": {},
        "description": (
            "Creates a generic EMPTY table structure. "
            "Use this ONLY when you want to start tracking a NEW topic from scratch and you have NO data yet. "
            "⛔ IF THE USER PROVIDES A LIST OF DATA (pasted text, excel, etc) -> DO NOT USE THIS. Use 'import_from_last_user_message' instead."
            "⛔ make sure that the user does not have previous data from sheet or somewher else",
            "Requires 'table_name' MUST BE IN ENGLISH and 'columns' (dict of field_name: type). "
        )
    },
    "import_from_chat":    
        {"target_function": import_from_chat,
    "static_params": {}, # mission_id is automatically injected by the system
    "description": (
        "Use this tool ONLY when the user asks to save/import data they pasted in this chat. "
        "Requires 'table_name' (string) which the user should provide MUST BE IN ENGLISH. if not ask him"
        "It automatically grabs the large text data from the chat history, so you don't need to pass the text itself."
    )
    },
    "import_google_sheet" : 
        {"target_function": import_google_sheet,
        "static_params": {},
        "description": (
            "The best way to import structured data. "
            "It reads a Google Sheet directly and creates a new database table from it. "
            "Requires 'sheet_url' (the full link to the sheet) and 'table_name' (the desired name for the new table in the system MUST BE IN ENGLISH)."
            )
        },
    "build_automation_flow": {
            "target_function": generate_and_build_flow,
            "static_params": {},
            "description": (
                "Acts as an Expert AI Architect to design and build an Automation Flow. "
                "Use this when the user asks to create an automation, pipeline, or webhook background process. "
                "You DO NOT need to write the JSON steps yourself. Your ONLY job is to provide three parameters: "
                "1. 'flow_name' (string): A short English name for the flow (snake_case). "
                "2. 'detailed_description' (string): A HIGHLY detailed explanation of what the flow should do, input payload, logic/conditions, and examples. "
                "3. 'trigger_type' (string): Usually 'webhook' or 'schedule'. "
                "The internal Architect will take your description and build the final executable structure."
            )
        },
    "update_user_context": {
        "target_function": update_user_context,
        "static_params": {},
        "description": """A comprehensive tool to update the user's state, profile, and daily achievements in one go.
        Use this for any of the following situations:
        - The user mentions something they like, dislike, or a general fact about themselves. (Use 'updates')
        - The user expresses stress, exhaustion, happiness, or frustration. (Use 'mood_level' and 'stress_notes')
        - The user mentions completing a productive action that was NOT on their task list. (Use 'win_description')
        
        You can provide any or all of these parameters in a single call, depending on what the user has said.

        Parameters (all optional):
        1. 'updates' (list of dicts): For profile info. Each dict needs 'category', 'item', and optional 'action'.
        2. 'mood_level' (string): The user's current mood (e.g., 'stressed', 'happy', 'overwhelmed').
        3. 'stress_notes' (string): A brief explanation for the mood change.
        4. 'win_description' (string): Description of an unplanned achievement.

        Example of a complex update:
        {
        "mood_level": "happy",
        "stress_notes": "Finished a side project",
        "win_description": "Finished the unplanned annual report",
        "updates": [
            {
            "category": "preferences",
            "item": "enjoys listening to jazz music while working",
            "action": "add"
            }
        ]
        }"""
    }
}




# =========================================================================================
#  3. Executor function
# =========================================================================================


def execute_mapped_tool(tool_name: str, parameters: dict) -> str:
    """Executes a conceptual tool by mapping it to a real function."""
    if tool_name not in TOOL_MAP:
        return json.dumps({"error": f"Tool '{tool_name}' is not a valid tool."})

    mapping = TOOL_MAP[tool_name]
    target_function = mapping['target_function']
    
    # Convert parameters to a regular dictionary
    plain_dict_params = {key: value for key, value in parameters.items()}
    
    # Initialize arguments with static parameters (if any)
    final_args = mapping.get('static_params', {}).copy()
    
    # Add the agent (required for all functions)
    # Assumption: the variable self or active_agent is available here. If inside a class, use self
    # If this is a global function that calls the global active_agent:
    if _active_agent:
        final_args['agent'] = _active_agent
    elif 'self' in locals():
        final_args['agent'] = self # type: ignore # if inside the class
    else:
        # Fallback in case this is executed from outside and passed as a hidden or global parameter
        pass 

    # --- Dedicated logic for add_record ---
    if target_function == add_record:
        # 1. Extract table name
        if 'table_name' in plain_dict_params:
            final_args['table_name'] = plain_dict_params.pop('table_name')
        
        # 2. Everything remaining in parameters is the data to save (data)
        final_args['data'] = plain_dict_params

    # --- Dedicated logic for find_records ---
    elif target_function == find_records:
        if 'table_name' in plain_dict_params:
            final_args['table_name'] = plain_dict_params.pop('table_name')
        
        # For search, the remaining parameters are the query
        final_args['query'] = plain_dict_params

    # --- Dedicated logic for delete_record ---
    elif target_function == delete_record:
        if 'table_name' in plain_dict_params:
            final_args['table_name'] = plain_dict_params.pop('table_name')
            
        # Delete expects a query
        if 'query' in plain_dict_params:
            final_args['query'] = plain_dict_params.pop('query')
        else:
            final_args['query'] = plain_dict_params

    # --- Default for all other tools ---
    else:
        final_args.update(plain_dict_params)

    # Final verification before call - ensure table_name is not missing for critical functions
    if target_function in [add_record, delete_record] and 'table_name' not in final_args:
        return json.dumps({"error": f"Error: Tool '{tool_name}' requires 'table_name' parameter but it was missing."})

    try:
        result = target_function(**final_args)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
