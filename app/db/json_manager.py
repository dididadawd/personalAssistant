# app/db/json_manager.py

import os
import json
import uuid
import threading
import re
import copy
from datetime import datetime, date
from collections import defaultdict
from functools import cmp_to_key
from typing import List, Dict, Any, Tuple
from threading import RLock

# --- יבוא מודולים פנימיים בפרויקט ---
# שימוש ב-.. כדי "לעלות" תיקייה אחת למעלה מ-db ל-app
from ..utils.logger import log_and_print

# --- הגדרות ומשתנים גלובליים שקשורים ישירות לניהול קבצים ---
# הנתיב מחושב באופן דינמי כדי שיעבוד מכל מקום
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PERSONAS_DIR = os.path.join(BASE_DIR, "personas")

DATA_FILE_LOCKS = defaultdict(threading.Lock)
_FILE_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_FILE_CACHE_LOCK = RLock()
_SENTINEL = object()
_DATE_FORMATS = ("%Y-%m-%d",) # Add other formats if needed
_PERSONAS_DIR = globals().get("PERSONAS_DIR", ".")
_DATA_FILE_LOCKS: Dict[str, RLock] = globals().get("DATA_FILE_LOCKS", {})

# =========================================================================================
#  CORE DATABASE FUNCTIONS (CRUD)
# =========================================================================================

def add_record(agent, table_name: str, data: dict):
    """
    מוסיף רשומה חדשה באופן לא-חוסם. 
    בודק פרמטרים חסרים ומחזיר שאלת הבהרה במקרה הצורך.
    """
    persona_name = agent.name
    schema = agent.config.get('data_schema', {}).get(table_name)
    if not schema:
        return {"error": f"Schema for table '{table_name}' not found."}

    actual_schema = schema
    if isinstance(schema, list) and schema:
        actual_schema = schema[0]
    elif not isinstance(actual_schema, dict):
        return {"error": f"Invalid schema format for '{table_name}'."}

    final_entry = data.copy()
    missing_fields = []

    # 1. בדוק אם יש שדות חובה שחסרים בנתוני ה-AI
    for column, col_type in actual_schema.items():
        if column.endswith("_id"):
            if column not in final_entry:
                final_entry[column] = str(uuid.uuid4().hex)
            continue

        # 2. בדוק אם השדה קיים בנתונים שסופקו ואינו ריק
        if column not in final_entry or final_entry[column] is None:
            
            # --- כאן נמצא השינוי המרכזי ---
            # אם זה שדה קישור לטבלה אחרת, ננסה לאכלס אותו מרשומות קיימות
            is_link_field = column.endswith('_name') # נתמקד בשדה השם כדי להציג למשתמש
            if is_link_field:
                potential_table_singular = column.rsplit('_', 1)[0]
                target_table_name = f"{potential_table_singular}s"
                kb_path = os.path.join(PERSONAS_DIR, persona_name, "knowledge_base")
                target_file_path = os.path.join(kb_path, f"{target_table_name}.json")

                if os.path.exists(target_file_path):
                    # נסה למצוא רשומות קיימות בטבלה המקושרת
                    linked_records = find_records(agent, target_table_name, {})
                    
                    # אם נמצאו רשומות, שלח אותן כאפשרויות בחירה למשתמש
                    if linked_records and not isinstance(linked_records, str) and len(linked_records) > 0:
                        # החזר אובייקט שמכיל את האפשרויות, כפי שה-JavaScript מצפה לקבל
                        return {
                            "status": "Awaiting Input",
                            "message": f"נמצאו מספר רשומות קיימות. אנא בחר '{column}' מהרשימה: או תרשום אחד משלך",
                            "options": linked_records,       # רשימת הרשומות המלאה
                            "display_key": column,           # המפתח להצגה על הכפתור
                            "state": {
                                "next_action": "add_record",
                                "table_name": table_name,
                                "missing_field": column,
                                "data": final_entry
                            }
                        }
            
            # אם זה לא שדה קישור, או שדה קישור ללא רשומות קיימות, הוסף לרשימת החסרים
            missing_fields.append((column, col_type))

    # --- שלב 2: אם עדיין יש שדות חסרים (שאינם קישורים או שאין להם אפשרויות), בקש קלט רגיל ---
    if missing_fields:
        first_missing_field, field_format_example = missing_fields[0]
        
        question = f"נראה שחסר מידע. אנא הזן ערך עבור '{first_missing_field}':"

        if field_format_example and field_format_example.lower() != 'string':
            question += f"\n(פורמט צפוי: {field_format_example})"

        return {
            "status": "Awaiting Input",
            "message": question,
            "state": {
                "next_action": "add_record",
                "table_name": table_name,
                "missing_field": first_missing_field,
                "data": final_entry
            }
        }

    # --- שלב 3: שמירת הרשומה (אם אין שדות חסרים) ---
    final_entry = data.copy() # נניח שהגענו לכאן עם רשומה מלאה
    final_entry.setdefault(f"{table_name.rstrip('s')}_id", uuid.uuid4().hex)

    kb_path = os.path.join(PERSONAS_DIR, persona_name, "knowledge_base")
    file_path = os.path.join(kb_path, f"{table_name}.json")
    
    lock = DATA_FILE_LOCKS[table_name]
    with lock: # <-- נעילת הקובץ לפני כתיבה
        try:
            records = []
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                with open(file_path, 'r', encoding='utf-8') as f:
                    records = json.load(f)
            records.append(final_entry)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
            
            log_and_print(f"✅ New record saved to table '{table_name}'.", "SYSTEM")
            return {"status": "Success", "entry": final_entry}
        except Exception as e:
            #logger.error(f"Error saving new record to {table_name}: {e}")
            return {"error": f"Error saving record to '{table_name}': {e}"}


def find_records(agent, table_name: str, query: Dict[str, Any] | None = None, *, keep_computed: bool = False) -> List[Dict[str, Any]]:
    """
    Load records and return those matching `query`.
    *** UNIVERSAL UPGRADE: Includes Logic to prevent Fuzzy Fallback on system queries. ***
    """
    query = {} if query is None else copy.deepcopy(query)
    
    # שומרים עותק של השאילתה המקורית לבדיקת הצורך ב-Fallback
    original_query_params = copy.deepcopy(query)

    persona_name = getattr(agent, "name", "default")
    kb_path = os.path.join(_PERSONAS_DIR, persona_name, "knowledge_base")
    file_path = os.path.join(kb_path, f"{table_name}.json")

    file_lock = _DATA_FILE_LOCKS.get(table_name, RLock())
    records = _load_json_cached(file_path, file_lock)
    if not records:
        return []

    records = [r.copy() for r in records]

    # --- 1. Handle $compute (Virtual Fields) ---
    computed_fields = set()
    if "$compute" in query:
        compute_rules = query.pop("$compute")
        augmented = []
        for rec in records:
            rec_local = rec.copy()
            for new_field, rule in (compute_rules or {}).items():
                computed_fields.add(new_field)
                try:
                    if "from_array" in rule and "select_by_index_from" in rule and "get_field" in rule:
                        arr = rec_local.get(rule["from_array"], [])
                        idx_field = rule["select_by_index_from"]
                        idx = rec_local.get(idx_field)
                        tgt_field = rule["get_field"]
                        if isinstance(arr, list) and isinstance(idx, int) and 0 <= idx < len(arr):
                            val = arr[idx]
                            if isinstance(val, dict):
                                rec_local[new_field] = val.get(tgt_field)
                    elif "count_of" in rule:
                        arr = rec_local.get(rule["count_of"], [])
                        if isinstance(arr, list):
                            rec_local[new_field] = len(arr)
                except Exception as e:
                    pass
            augmented.append(rec_local)
        records = augmented

    # --- 2. Extract Control Parameters ---
    sort_spec = query.pop("$sort", None)
    limit_count = query.pop("$limit", None)

    # --- 3. Primary Filtering Logic ---
    filtered = []
    
    if "search_term" in query:
        # אם המשתמש/AI ביקש מראש חיפוש רחב
        st = str(query.pop("search_term")).strip().lower()
        if not st:
            filtered = records
        else:
            filtered = [
                r for r in records
                if any(
                    str(v).lower() == st or st in str(v).lower()
                    for v in _iter_record_values(r)
                )
            ]
        # אם נשארו עוד פילטרים בנוסף ל-search_term
        if query:
            parsed_query = _preprocess_query(query)
            filtered = [r for r in filtered if _match_record(r, parsed_query)]
    else:
        # חיפוש מדויק רגיל
        if not query:
            filtered = records
        else:
            parsed_query = _preprocess_query(query)
            filtered = [r for r in records if _match_record(r, parsed_query)]

    # --- 4. UNIVERSAL AUTO-FUZZY FALLBACK (התיקון: הגנה מפני הפעלה שגויה) ---
    # התנאים:
    # 1. לא נמצאו תוצאות בחיפוש המדויק.
    # 2. היו פרמטרים לחיפוש.
    # 3. זה לא היה כבר חיפוש רחב מלכתחילה.
    # 4. (חדש) השאילתה לא מכילה שדות מערכת קשיחים כמו תאריכים או מזהים.
    
    system_fields = ['due_date', 'date', 'task_id', 'project_id', 'routine_id', 'status', 'type']
    is_system_query = any(k in query for k in system_fields)

    if not filtered and query and "search_term" not in original_query_params and not is_system_query:
        
        # ננסה לחלץ מילת חיפוש מתוך הערכים שה-AI שלח
        fallback_term = None
        for k, v in query.items():
            if k.startswith("$") or k.endswith("_id"):
                continue
            if isinstance(v, (str, int, float)) and str(v).strip():
                fallback_term = str(v).strip()
                break 
        
        if fallback_term:
            term_lower = fallback_term.lower()
            filtered = [
                r for r in records
                if any(
                    term_lower in str(v).lower()
                    for v in _iter_record_values(r)
                )
            ]

    # --- 5. Apply Sorting & Limiting ---
    if sort_spec and isinstance(sort_spec, dict):
        sorted_records = _apply_sorting(filtered, sort_spec)
    else:
        sorted_records = filtered

    final = sorted_records[:limit_count] if limit_count and isinstance(limit_count, int) and limit_count > 0 else sorted_records

    # --- 6. Cleanup Computed Fields ---
    if computed_fields and not keep_computed:
        out = []
        for r in final:
            rc = r.copy()
            for k in computed_fields:
                rc.pop(k, None)
            out.append(rc)
        return out

    return [r.copy() for r in final]


def update_record(agent, table_name: str, query: dict, updates: dict):
    """
    מעדכן רשומה בקובץ JSON תוך שימוש במנגנון נעילה (פעולה אטומית).
    """
    records_to_update = find_records(agent, table_name, query)
    if not records_to_update or isinstance(records_to_update, str) or len(records_to_update) > 1:
        return {"error": "Update failed: Did not find a single, unique record."}
    
    record_to_update = records_to_update[0]
    id_key = next((k for k in record_to_update if k.endswith('_id')), None)
    if not id_key or not record_to_update.get(id_key):
        return {"error": "Update failed: Cannot find unique ID for record."}
    
    record_id = record_to_update[id_key]

    kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
    file_path = os.path.join(kb_path, f"{table_name}.json")
    
    lock = DATA_FILE_LOCKS[table_name]
    with lock: # <-- נעילת הקובץ לכל אורך הפעולה (קריאה-שינוי-כתיבה)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                all_records = json.load(f)
        except Exception as e:
            return {"error": f"Failed to load table '{table_name}': {e}"}

        updated_record = None
        for i, record in enumerate(all_records):
            if record.get(id_key) == record_id:
                all_records[i].update(updates)
                updated_record = all_records[i]
                break
        
        if not updated_record:
            return {"error": "Internal error: Record found but could not be updated in list."}

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(all_records, f, indent=2, ensure_ascii=False)
            log_and_print(f"✅ Record updated in table '{table_name}'.", "SYSTEM")
            return {"status": "Success", "updated_record": updated_record}
        except Exception as e:
            return {"error": f"Failed to save updated table '{table_name}': {e}"}

def update_all_records(agent, table_name: str, updates: dict):
    """
    כלי לעדכון אצווה: מעדכן את כל הרשומות בטבלה נתונה עם הנתונים החדשים.
    """
    log_and_print(f"--- Starting batch update of all records in table '{table_name}'... ---", "SYSTEM")

    # 1. טען את כל מאגר הנתונים
    kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
    file_path = os.path.join(kb_path, f"{table_name}.json")
    
    if not os.path.exists(file_path):
        return {"error": f"Table '{table_name}' does not exist."}

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            all_records = json.load(f)
    except Exception as e:
        return {"error": f"Failed to load table '{table_name}': {e}"}

    # 2. עבור בלולאה על כל הרשומות ועדכן אותן
    for i in range(len(all_records)):
        all_records[i].update(updates)
        
    log_and_print(f"   - {len(all_records)} records updated with data: {updates}", "SYSTEM")

    # 3. שמור את כל מאגר הנתונים המעודכן בחזרה לקובץ
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(all_records, f, indent=2, ensure_ascii=False)
        log_and_print(f"✅ Batch update completed successfully for table '{table_name}'.", "SYSTEM")
        return {"status": "Success", "records_updated": len(all_records)}
    except Exception as e:
        return {"error": f"Failed to save updated table '{table_name}': {e}"}

def update_records_by_query(agent, table_name: str, query: dict, updates: dict):
    """
    כלי עוצמתי לעדכון אצווה: מעדכן את כל הרשומות התואמות לשאילתה ספציפית.
    שימושי לעדכון מספר רשומות בבת אחת שעומדות בתנאי מסוים.
    :param table_name: שם הטבלה לעדכון.
    :param query: שאילתה למציאת הרשומות לעדכון (למשל, {'status': 'pending'}).
    :param updates: מילון המכיל את השדות והערכים החדשים (למשל, {'status': 'archived'}).
    """
    log_and_print(f"--- Starting batch update by query on table '{table_name}'... ---", "SYSTEM")

    # 1. מצא את כל הרשומות המיועדות לעדכון
    records_to_update = find_records(agent, table_name, query)

    if not records_to_update or isinstance(records_to_update, str):
        return {"status": "No records found matching the query.", "updated_count": 0}

    # 2. אסוף את המזהים הייחודיים של הרשומות לעדכון
    first_record = records_to_update[0]
    id_key = next((key for key in first_record if key.endswith('_id')), None)
    if not id_key:
        return {"error": "Cannot safely update records because they have no unique ID field."}

    ids_to_update_set = {record.get(id_key) for record in records_to_update if record.get(id_key)}
    
    log_and_print(f"   - Found {len(ids_to_update_set)} records to update.", "SYSTEM")

    # 3. טען את כל מאגר הנתונים
    kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
    file_path = os.path.join(kb_path, f"{table_name}.json")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            all_records = json.load(f)
    except Exception as e:
        return {"error": f"Failed to load table '{table_name}': {e}"}

    # 4. עבור על כל הרשומות ועדכן את הרלוונטיות
    updated_count = 0
    for i in range(len(all_records)):
        # בדוק אם המזהה של הרשומה הנוכחית נמצא בסט המזהים שזיהינו לעדכון
        if all_records[i].get(id_key) in ids_to_update_set:
            all_records[i].update(updates)
            updated_count += 1

    # 5. שמור את הרשימה המעודכנת בחזרה לקובץ
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(all_records, f, indent=2, ensure_ascii=False)
        log_and_print(f"✅ {updated_count} records updated successfully in table '{table_name}'.", "SYSTEM")
        return {"status": "Success", "updated_count": updated_count}
    except Exception as e:
        return {"error": f"Failed to save updated table '{table_name}': {e}"}


def delete_record(agent, table_name: str, query: dict):
    """
    מוחק רשומות מקובץ JSON תוך שימוש במנגנון נעילה.
    """
    records_to_delete = find_records(agent, table_name, query)
    if not records_to_delete or isinstance(records_to_delete, str):
        return {"error": "No records found matching the query to delete."}

    id_key = next((k for k in records_to_delete[0] if k.endswith('_id')), None)
    if not id_key: return {"error": "Cannot safely delete, no ID field found."}
    
    ids_to_remove = {r.get(id_key) for r in records_to_delete if r.get(id_key)}

    kb_path = os.path.join(PERSONAS_DIR, agent.name, "knowledge_base")
    file_path = os.path.join(kb_path, f"{table_name}.json")

    lock = DATA_FILE_LOCKS[table_name]
    with lock: # <-- נעילת הקובץ
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                all_records = json.load(f)
        except Exception as e:
            return {"error": f"Failed to load table '{table_name}': {e}"}

        new_records = [r for r in all_records if r.get(id_key) not in ids_to_remove]
        deleted_count = len(all_records) - len(new_records)

        if deleted_count == 0:
            return {"error": "Internal error: Records found but could not be removed."}

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(new_records, f, indent=2, ensure_ascii=False)
            log_and_print(f"✅ {deleted_count} records deleted from table '{table_name}'.", "SYSTEM")
            return {"status": "Success", "deleted_count": deleted_count}
        except Exception as e:
            return {"error": f"Failed to save updated table '{table_name}': {e}"}

    # 4. צור רשימה חדשה ללא הרשומות המיועדות למחיקה
    original_count = len(all_records)
    # השאר רק את הרשומות שהמזהה שלהן *אינו* נמצא בסט המזהים למחיקה
    new_records = [record for record in all_records if record.get(id_key) not in ids_to_remove]
    
    deleted_count = original_count - len(new_records)

    if deleted_count == 0:
        # מצב שיכול לקרות אם הייתה אי-התאמה בין מציאת הרשומה לבין הסרתה מהרשימה
        return {"error": "An internal error occurred: Records were found but could not be removed from the main list."}

    # 5. שמור את הרשימה המעודכנת בחזרה לקובץ
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(new_records, f, indent=2, ensure_ascii=False)
        log_and_print(f"✅ {deleted_count} records successfully deleted from table '{table_name}'.", "SYSTEM")
        return {"status": "Success", "deleted_count": deleted_count, "deleted_record_ids": list(ids_to_remove)}
    except Exception as e:
        return {"error": f"Failed to save updated table '{table_name}': {e}"}

# =========================================================================================
#  PRIVATE HELPER FUNCTIONS FOR find_records
# =========================================================================================

def _load_json_cached(path: str, file_lock: RLock) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with file_lock:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return []

        with _FILE_CACHE_LOCK:
            cached = _FILE_CACHE.get(path)
            if cached and cached[0] == mtime:
                # return the cached list (do not modify it)
                return cached[1]

        try:
            with open(path, "r", encoding="utf-8") as fh:
                txt = fh.read()
                if not txt.strip():
                    records = []
                else:
                    records = json.loads(txt)
                    if not isinstance(records, list):
                        records = []
        except (OSError, json.JSONDecodeError) as e:
            records = []

        with _FILE_CACHE_LOCK:
            _FILE_CACHE[path] = (mtime, records)
        return records



# --------------------------------------------------
# Query preprocessing and helpers
# --------------------------------------------------

def _preprocess_query(query: Any) -> Any:
    """Recursively preprocess a query: compile regex, parse literals (dates/numbers), normalize strings."""
    if not isinstance(query, dict):
        return query
    # logical
    if "$or" in query and isinstance(query["$or"], list):
        return {"$or": [_preprocess_query(sub) for sub in query["$or"]]}
    if "$and" in query and isinstance(query["$and"], list):
        return {"$and": [_preprocess_query(sub) for sub in query["$and"]]}
    if "$not" in query:
        return {"$not": _preprocess_query(query["$not"]) }

    out: Dict[str, Any] = {}
    for k, v in query.items():
        if isinstance(v, dict):
            parsed_ops: Dict[str, Any] = {}
            for op, op_val in v.items():
                if op == "$regex":
                    if isinstance(op_val, dict):
                        pattern = op_val.get("pattern", "")
                        flags = 0
                        if "i" in str(op_val.get("flags", "")):
                            flags |= re.IGNORECASE
                        parsed_ops[op] = re.compile(pattern, flags)
                    else:
                        parsed_ops[op] = re.compile(str(op_val), re.IGNORECASE)
                elif op in ("$in", "$nin"):
                    if isinstance(op_val, (list, tuple, set)):
                        parsed_ops[op] = [_try_parse_literal(x) for x in op_val]
                    else:
                        parsed_ops[op] = [_try_parse_literal(op_val)]
                elif op == "$exists":
                    parsed_ops[op] = bool(op_val)
                else:
                    parsed_ops[op] = _try_parse_literal(op_val)
            out[k] = parsed_ops
        else:
            out[k] = _try_parse_literal(v)
    return out



def _try_parse_literal(val: Any) -> Any:
    if isinstance(val, (date, datetime, int, float, re.Pattern)):
        return val
    if isinstance(val, str):
        s = val.strip()
        # try iso/datetime/date
        try:
            dt = datetime.fromisoformat(s)
            return dt.date() if isinstance(dt, datetime) else dt
        except Exception:
            pass
        for fmt in _DATE_FORMATS:
            try:
                dt = datetime.strptime(s, fmt)
                return dt.date()
            except Exception:
                continue
        # try numbers
        if s.isdigit():
            try:
                return int(s)
            except Exception:
                pass
        try:
            f = float(s)
            return f
        except Exception:
            pass
        return s.lower()
    return val


def _iter_record_values(record: Dict[str, Any]):
    for v in record.values():
        yield v

# --------------------------------------------------
# Matching & operators
# --------------------------------------------------

def _get_nested_value(record: Dict[str, Any], key: str) -> Any:
    cur: Any = record
    for part in key.split("."):
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
            else:
                return _SENTINEL
        elif isinstance(cur, list):
            if part.isdigit():
                idx = int(part)
                if 0 <= idx < len(cur):
                    cur = cur[idx]
                else:
                    return _SENTINEL
            else:
                return _SENTINEL
        else:
            return _SENTINEL
    return cur


def _coerce_record_for_comparison(rec_val: Any, q_sample: Any) -> Tuple[Any, Any]:
    # sentinel stays sentinel
    if rec_val is _SENTINEL:
        return (_SENTINEL, q_sample)
    if isinstance(q_sample, re.Pattern):
        return (rec_val, q_sample)
    # date
    if isinstance(q_sample, date) and not isinstance(rec_val, date):
        if isinstance(rec_val, str):
            try:
                dt = datetime.fromisoformat(rec_val.strip())
                return (dt.date(), q_sample)
            except Exception:
                for fmt in _DATE_FORMATS:
                    try:
                        dt = datetime.strptime(rec_val.strip(), fmt)
                        return (dt.date(), q_sample)
                    except Exception:
                        continue
        return (rec_val, q_sample)
    # number
    if isinstance(q_sample, (int, float)) and not isinstance(rec_val, (int, float)):
        if isinstance(rec_val, str):
            try:
                if "." in rec_val:
                    return (float(rec_val), q_sample)
                return (int(rec_val), q_sample)
            except Exception:
                return (rec_val, q_sample)
    # strings
    if isinstance(q_sample, str) and isinstance(rec_val, str):
        return (rec_val.lower(), q_sample)
    return (rec_val, q_sample)


def _are_values_equal(rec_val: Any, q_val: Any) -> bool:
    """
    IMPROVED version of equality check.
    Tries to compare values flexibly, handling mismatched types (str vs int/float).
    """
    if rec_val is _SENTINEL:
        return q_val is None
    if q_val is None:
        return rec_val is None # Match if the record's value is explicitly null

    # 1. Direct comparison (fastest)
    if rec_val == q_val:
        return True

    # 2. Flexible numeric comparison
    try:
        # Try to compare as floating point numbers. This handles int vs float, "123" vs 123, etc.
        if float(rec_val) == float(q_val):
            return True
    except (ValueError, TypeError):
        # This is expected if values are not numeric, so we just continue
        pass

    # 3. Case-insensitive string comparison as a fallback
    try:
        if str(rec_val).strip().lower() == str(q_val).strip().lower():
            return True
    except (AttributeError, TypeError):
        # Should not happen if everything is converted to string, but as a safeguard
        pass
        
    return False


def _apply_operator(record_value: Any, op: str, q_val: Any) -> bool:
    # This function uses the new _are_values_equal, so it's automatically upgraded.
    # We only need to ensure it exists and is called correctly.
    if op == "$exists":
        return (record_value is not _SENTINEL) if bool(q_val) else (record_value is _SENTINEL)

    # <-- CHANGE: For all other operators, if the record value doesn't exist, it's a mismatch.
    if record_value is _SENTINEL:
        return False

    if op == "$regex":
        if not isinstance(q_val, re.Pattern) or not isinstance(record_value, str):
            return False
        return bool(q_val.search(record_value))

    if op in ("$in", "$nin"):
        if not isinstance(q_val, (list, tuple)):
            return False
        # Check if the record's value is equal to ANY of the items in the query list
        is_in_list = any(_are_values_equal(record_value, item) for item in q_val)
        return is_in_list if op == "$in" else not is_in_list

    # Comparisons (eq/ne) use the new flexible logic
    if op == "$eq":
        return _are_values_equal(record_value, q_val)
    if op == "$ne":
        return not _are_values_equal(record_value, q_val)

    # For ordering, we must have comparable types
    rv, qv = _coerce_record_for_comparison(record_value, q_val)
    try:
        if op == "$lt": return rv < qv
        if op == "$lte": return rv <= qv
        if op == "$gt": return rv > qv
        if op == "$gte": return rv >= qv
    except TypeError:
        # Happens if you try to compare incompatible types, e.g., "apple" < 10
        return False
    except Exception:
        return False

    return False


def _match_record(record: Dict[str, Any], query: Dict[str, Any]) -> bool:
    # This function's logic remains the same, but it's now more powerful
    # because it calls the upgraded _apply_operator.
    if not isinstance(query, dict):
        return False
    if "$or" in query:
        return any(_match_record(record, s) for s in query["$or"])
    if "$and" in query:
        return all(_match_record(record, s) for s in query["$and"])
    if "$not" in query:
        return not _match_record(record, query["$not"])

    for key, q_val in query.items():
        rv = _get_nested_value(record, key)
        if isinstance(q_val, dict):
            # Operators like $gt, $in, etc.
            if not all(_apply_operator(rv, op, v) for op, v in q_val.items()):
                return False
        else:
            # Shorthand for equality
            if not _apply_operator(rv, "$eq", q_val):
                return False
    return True

# --------------------------------------------------
# Sorting
# --------------------------------------------------

def _apply_sorting(records: List[Dict[str, Any]], sort_spec: Dict[str, int]) -> List[Dict[str, Any]]:
    """Sort using comparator that considers missing values and coerces types when possible."""
    def comparator(a: Dict[str, Any], b: Dict[str, Any]) -> int:
        for key, direction in sort_spec.items():
            va = _get_nested_value(a, key)
            vb = _get_nested_value(b, key)

            if va is _SENTINEL and vb is _SENTINEL:
                continue
            if va is _SENTINEL:
                return -1 * (1 if direction >= 0 else -1)
            if vb is _SENTINEL:
                return 1 * (1 if direction >= 0 else -1)

            # try numeric/date coercion
            # prefer comparing like types: attempt float then date then string
            try:
                a_num = float(va)
                b_num = float(vb)
                if a_num < b_num:
                    return -1 * direction
                if a_num > b_num:
                    return 1 * direction
                continue
            except Exception:
                pass
            try:
                a_date = datetime.fromisoformat(str(va)).date() if isinstance(va, str) else (va if isinstance(va, date) else None)
                b_date = datetime.fromisoformat(str(vb)).date() if isinstance(vb, str) else (vb if isinstance(vb, date) else None)
                if isinstance(a_date, date) and isinstance(b_date, date):
                    if a_date < b_date:
                        return -1 * direction
                    if a_date > b_date:
                        return 1 * direction
                    continue
            except Exception:
                pass
            # fallback to string compare
            try:
                sa = str(va).lower()
                sb = str(vb).lower()
                if sa < sb:
                    return -1 * direction
                if sa > sb:
                    return 1 * direction
            except Exception:
                continue
        return 0

    return sorted(records, key=cmp_to_key(comparator))


