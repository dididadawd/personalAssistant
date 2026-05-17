# app/utils/date_helpers.py
# Date and time utility functions only

from datetime import datetime, timedelta, date
from typing import Optional


def get_logical_date() -> date:
    """
    Returns the "logical" date of activity.
    NOTE: The day changes at 6:00 AM, not at midnight.
    TODO: The 6 AM threshold is outdated and should be updated to match the intended business logic.
    """
    now = datetime.now()
    if now.hour < 6:
        return date.today() - timedelta(days=1)
    return date.today()


def is_valid_iso_date(date_str: str) -> bool:
    """Check if string is a valid date in YYYY-MM-DD format"""
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except (ValueError, TypeError):
        return False


def calculate_relative_date(agent, days_from_now: int) -> str:
    """
    Calculate a date relative to today.
    days_from_now: positive for future, negative for past
    """
    target_date = date.today() + timedelta(days=days_from_now)
    return target_date.isoformat()


def get_current_datetime(agent) -> str:
    """Returns current datetime as ISO string"""
    try:
        # Use the datetime library to get the local time
        now = datetime.now()
        # Format the response in a clear and readable way
        if now.hour < 6:
            return date.today() - timedelta(days=1)
        formatted_datetime = now.strftime("%A, %d %B %Y, %H:%M:%S")
        return {"current_datetime": formatted_datetime}
    except Exception as e:
        #logger.error(f"Failed to get current datetime: {e}")
        return {"error": str(e)}


def get_next_scheduled_date(preferred_days: list, remind_day_before: bool = False) -> str:
    """
    Calculate the next scheduled date based on preferred days.
    preferred_days: list of day names (e.g., ['monday', 'wednesday'])
    """
    if not preferred_days:
        return (datetime.now() + timedelta(days=1)).isoformat()

    days_map = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6
    }
    
    now = datetime.now()
    current_weekday = now.weekday()
    
    for days_ahead in range(0, 8):
        check_weekday = (current_weekday + days_ahead) % 7
        day_name = [k for k, v in days_map.items() if v == check_weekday]
        if day_name and day_name[0].lower() in [d.lower() for d in preferred_days]:
            if days_ahead == 0:
                target_date = now + timedelta(days=7)
            else:
                target_date = now + timedelta(days=days_ahead)
            return target_date.date().isoformat()
    
    return (now + timedelta(days=1)).isoformat()


def is_connected(host: str = "8.8.8.8", port: int = 53, timeout: int = 3) -> bool:
    """Check if network is connected"""
    import socket
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except OSError:
        return False


def is_successful_response(response) -> bool:
    """Determine if a task response is considered 'successful with value'"""
    negative_keywords = [
        "לא נמצאו", "לא מצאתי", "אין חדש", "אין עדכונים",
        "הכל מעודכן", "no new", "nothing found"
    ]

    if isinstance(response, dict):
        if "error" in response:
            return False
        if response.get("status") in ["error", "not_found"]:
            return False
        return True

    if isinstance(response, str):
        response_lower = response.lower()
        for keyword in negative_keywords:
            if keyword.lower() in response_lower:
                return False
        return bool(response and response.strip())

    return bool(response)
