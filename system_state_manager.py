import json
import os
from datetime import datetime
AGENT_NAME = "show_manager"

# נתיב לקובץ הסטייט הגלובלי (שמור בתיקיית ה-Personas או בתיקייה ראשית)
script_dir = os.path.dirname(os.path.abspath(__file__))
PERSONAS_DIR = os.path.join(script_dir, "personas")
kb_path = os.path.join(PERSONAS_DIR, AGENT_NAME, "knowledge_base")
STATE_FILE_PATH = os.path.join(kb_path, "system_state.json")


def get_system_state():
    if not os.path.exists(STATE_FILE_PATH):
        return {"freeze_mode": {"is_active": False, "start_date": None, "reason": None}}
    
    try:
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"freeze_mode": {"is_active": False, "start_date": None, "reason": None}}

def save_system_state(state):
    with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def is_system_frozen():
    state = get_system_state()
    return state.get("freeze_mode", {}).get("is_active", False)

def get_freeze_details():
    """מחזיר את פרטי ההקפאה (תאריך התחלה וסיבה)"""
    return get_system_state().get("freeze_mode", {})