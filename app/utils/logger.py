# app/utils/logger.py

import os
import logging
from datetime import datetime

# --- הגדרת נתיבים באופן דינמי ---
# חישוב נתיב השורש של הפרויקט (תיקיית yahli-gemma-agent)
# זה מבטיח שהנתיבים יעבדו לא משנה מאיפה תריץ את הקוד
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# נתיב לקובץ תיעוד השיחה
TRANSCRIPT_FILE_PATH = os.path.join(BASE_DIR, "conversation_transcript.txt")

# נתיב לקובץ הלוג הטכני
TECHNICAL_LOG_PATH = os.path.join(BASE_DIR, "app_activity.log")


# =========================================================================================
#  1. לוג שיחה (עבור המשתמש והבנת תהליך החשיבה)
# =========================================================================================

def log_and_print(message: str, speaker: str = "SYSTEM"):
    """
    מדפיסה הודעה למסך (console) וגם כותבת אותה לקובץ תיעוד השיחה
    (conversation_transcript.txt) למעקב אחר האינטראקציה.

    Args:
        message (str): ההודעה להדפסה ולכתיבה.
        speaker (str): מי "אמר" את ההודעה (למשל: "SYSTEM", "BOT", "USER", "ERROR").
    """
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted_message = f"[{timestamp}] [{speaker}] {str(message)}"
    
    # הדפסה לקונסול
    print(formatted_message)
    
    # כתיבה לקובץ תיעוד השיחה
    try:
        with open(TRANSCRIPT_FILE_PATH, 'a', encoding='utf-8') as f:
            f.write(formatted_message + "\n")
    except Exception as e:
        # במקרה חירום שלא ניתן לכתוב לקובץ, לפחות נראה את השגיאה בקונסול
        print(f"[CRITICAL_LOG_ERROR] Failed to write to transcript file: {e}")


# =========================================================================================
#  2. לוג טכני (עבור דיבאגינג, שגיאות ואזהרות מערכת)
# =========================================================================================

def setup_technical_logger():
    """
    מגדיר את הלוגר הטכני הראשי של האפליקציה.
    יש לקרוא לפונקציה זו פעם אחת בלבד בתחילת ריצת התוכנית (ב-main.py).
    """
    # קובע את רמת הלוג המינימלית שתירשם (INFO ומעלה)
    logging.basicConfig(
        level=logging.INFO,
        # פורמט ההודעה: זמן - רמת חומרה - שם המודול - ההודעה
        format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
        # קובע לאן הלוג ייכתב
        handlers=[
            logging.FileHandler(TECHNICAL_LOG_PATH, encoding='utf-8'),
            logging.StreamHandler() # שולח את הלוג גם לקונסול
        ]
    )
    log_and_print("Technical logger initialized. Activity will be logged to app_activity.log", "LOGGER_SETUP")