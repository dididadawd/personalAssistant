import os
import logging
import json
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import asyncio
from telegram.error import NetworkError
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ParseMode
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.config import Config

ALLOWED_USER_IDS = [int(Config.ADMIN_CHAT_ID)] if Config.ADMIN_CHAT_ID else []

# --- ייבוא והגדרת ה-Agent שלך ---
# ודא שהקובץ הראשי שלך (server.py) נמצא באותה תיקייה
# אנו מייבאים את האובייקט הפעיל ואת פונקציית ההמשך

# --------------------------------

# הגדרת לוגינג כדי לראות מה קורה
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# --- בדיקת טעינת ה-Agent ---

# --- הגדרת הפונקציות המטפלות ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_name = update.effective_user.first_name

    # --- חסימת משתמשים לא מורשים ---
    if chat_id not in ALLOWED_USER_IDS:
        logger.warning(f"Unauthorized user tried to start: {user_name} ({chat_id})")
        await update.message.reply_text("⛔ אין לך גישה לבוט זה.")
        return
    # --------------------------------

    agent = context.bot_data['agent']
    register_chat_id_func = context.bot_data['register_chat_id']

    logger.info(f"User {user_name} (ID: {chat_id}) started the bot. Registering chat_id...")
    try:
        # כאן אנחנו קוראים לפונקציה ב-Server (שגם לה הוספנו הגנה ליתר ביטחון)
        result = await asyncio.to_thread(register_chat_id_func, agent, chat_id)
        
        if result.get("status") == "Success":
            welcome_message = f"שלום {user_name}! נרשמת בהצלחה. אני העוזר האישי שלך."
            await update.message.reply_text(welcome_message, reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text("שגיאה: אינך מורשה להירשם כמנהל הבוט.")

    except Exception as e:
        logger.error(f"Failed to register chat ID {chat_id}. Error: {e}")
        await update.message.reply_text("אירעה שגיאה ברישום.")
    
    context.user_data.pop('agent_state', None)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    מטפל בכל הודעות הטקסט.
    """
    agent = context.bot_data['agent']
    handle_follow_up_func = context.bot_data['handle_follow_up']
    user_message = update.message.text
    chat_id = update.message.chat_id
    
    
    if chat_id not in ALLOWED_USER_IDS:
        return # פשוט להתעלם, לא להגיב אפילו
    agent_state = context.user_data.get('agent_state')

    if agent_state:
        logger.info(f"📩 [Follow-up] Received from {chat_id}: '{user_message}'")
        bot_response = await asyncio.to_thread(handle_follow_up_func, agent_state, user_message)
    else:
        logger.info(f"📩 [New Task] Received from {chat_id}: '{user_message}'")
        bot_response = await asyncio.to_thread(agent.execute_task, user_message)

    await process_and_reply(update, context, bot_response)


async def process_and_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_response) -> None:
    """
    מעבד את התגובה מה-Agent, שולח תשובה למשתמש ומנהל את מצב השיחה.
    נוסף טיפול דינמי להצגת כפתורי בחירה.
    """
    chat_id = update.effective_chat.id
    text_to_send = ""
    reply_markup = ReplyKeyboardRemove()  # כברירת מחדל, הסר כל מקלדת קיימת בסוף התשובה
    
    context.user_data.pop('agent_state', None)
    
    if isinstance(bot_response, dict):
        if bot_response.get("status") == "Awaiting Input":
            # מקרה 1: ה-Agent שואל שאלת הבהרה
            text_to_send = bot_response.get("message", "אני צריך מידע נוסף...")
            context.user_data['agent_state'] = bot_response.get("state")
            logger.info(f"Awaiting input from {chat_id}. State has been saved.")

            # --- ✨ התחלת הלוגיקה החדשה להצגת כפתורים ✨ ---
            options = bot_response.get("options")
            display_key = bot_response.get("display_key")

            # בדוק אם השרת שלח רשימת אפשרויות תקינה
            if options and isinstance(options, list) and len(options) > 0 and display_key:
                logger.info(f"Found {len(options)} options to display for user {chat_id}.")
                
                # יצירת רשימת הטקסטים שיופיעו על הכפתורים
                button_texts = [opt.get(display_key, "אפשרות לא תקינה") for opt in options]
                
                # סידור הכפתורים בשורות (לדוגמה, 2 כפתורים בכל שורה)
                keyboard_layout = [button_texts[i:i + 2] for i in range(0, len(button_texts), 2)]
                
                # יצירת אובייקט המקלדת
                reply_markup = ReplyKeyboardMarkup(
                    keyboard_layout,
                    resize_keyboard=True,     # התאם את גודל הכפתורים
                    one_time_keyboard=True    # הסתר את המקלדת לאחר לחיצה
                )
            # --- 🔚 סוף הלוגיקה החדשה ---

        elif "confirmation_request" in bot_response:
            confirmation_data = bot_response["confirmation_request"]
            text_to_send = confirmation_data.get("message", "האם אתה בטוח?")
            # יצירת כפתורי כן/לא לאישור
            keyboard_layout = [['כן, אני בטוח'], ['לא, בטל']]
            reply_markup = ReplyKeyboardMarkup(keyboard_layout, resize_keyboard=True, one_time_keyboard=True)
            # שמירת המצב כדי לדעת שהתשובה הבאה היא כן/לא
            context.user_data['agent_state'] = confirmation_data.get("state")

        elif "reply" in bot_response:
            text_to_send = bot_response["reply"]
            
        elif "error" in bot_response:
            text_to_send = f"אירעה שגיאה: {bot_response['error']}"
        
        else:
            text_to_send = "קיבלתי תגובה לא צפויה מהסוכן:\n" + json.dumps(bot_response, ensure_ascii=False, indent=2)

    elif isinstance(bot_response, str):
        text_to_send = bot_response
        
    else:
        text_to_send = "אירעה שגיאה לא ידועה בסוג התגובה מהסוכן."

    # <--- ✨ התחלה: קטע קוד חדש לטיפול בפורמט ובהודעות ארוכות ✨ --->
    if text_to_send:
        # --- ✨ FIX: Convert Markdown to HTML for safe parsing ---
        # Replace **bold** with <b>bold</b>
        import re
        processed_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text_to_send)
        # You can add more rules here if needed, for example:
        # processed_text = re.sub(r'## (.*?)\n', r'<b>\1</b>\n', processed_text) # Headers
        # processed_text = processed_text.replace('*', '•') # List items
        
        MAX_MESSAGE_LENGTH = 4096

        if len(processed_text) <= MAX_MESSAGE_LENGTH:
            # If the message is short, send it as usual with HTML parsing
            await update.message.reply_text(
                processed_text, 
                reply_markup=reply_markup, 
                parse_mode=ParseMode.HTML
            )
            logger.info(f"↪️ Sent reply to {chat_id}: '{processed_text[:100]}...'")
        else:
            # If the message is long, split it into parts
            logger.info(f"Message to {chat_id} is too long ({len(processed_text)} chars). Splitting into chunks.")
            parts = [processed_text[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(processed_text), MAX_MESSAGE_LENGTH)]
            
            for i, part in enumerate(parts[:-1]):
                await update.message.reply_text(part, parse_mode=ParseMode.HTML)
                logger.info(f"↪️ Sent chunk {i+1}/{len(parts)} to {chat_id}")
                await asyncio.sleep(0.5)

            # Send the final part with the keyboard and HTML parsing
            await update.message.reply_text(
                parts[-1], 
                reply_markup=reply_markup, 
                parse_mode=ParseMode.HTML
            )
            logger.info(f"↪️ Sent final chunk {len(parts)}/{len(parts)} to {chat_id}")
    # <--- ✨ סיום: קטע קוד חדש ✨ --->




# <--- 2. הוספת פונקציית מטפל השגיאות החדשה --->
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """לוכדת את כל השגיאות ומטפלת בשגיאות רשת באופן מיוחד."""
    
    # אם השגיאה היא שגיאת רשת (כמו חוסר אינטרנט)
    if isinstance(context.error, NetworkError):
        logger.warning(f"Network error detected: {context.error}. The bot will continue to try and reconnect.")
        # אנחנו לא עושים כלום מעבר לרישום, כי הספרייה תנסה להתחבר מחדש אוטומטית.
        # הפעולה החשובה היא שתפסנו את השגיאה ומנענו מהתוכנית לקרוס.
        return

    # עבור כל שגיאה אחרת, נרצה לראות את כל הפרטים כדי לתקן אותה
    logger.error("An exception was raised while handling an update:", exc_info=context.error)


def main() -> None:
    """הפונקציה הראשית שמפעילה את הבוט במצב Polling"""
    TELEGRAM_BOT_TOKEN = Config.TELEGRAM_BOT_TOKEN
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Security Error: The TELEGRAM_BOT_TOKEN is not set!")
        return

    
    
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    logger.info("Bot is starting in Polling mode...")
    application.run_polling()



def start_telegram_bot_polling(application):
    """
    פונקציית עטיפה חדשה שמפעילה את ה-polling בלולאת אירועים חדשה.
    זו הפונקציה שתרוץ בתוך ה-Thread.
    """
    # 1. צור לולאת אירועים חדשה וקבע אותה כלולאה הנוכחית עבור ה-Thread הזה.
    asyncio.set_event_loop(asyncio.new_event_loop())
    
    # 2. הפעל את ה-polling. זו פעולה חוסמת שתחזיק את הלולאה בחיים.
    application.run_polling()


def create_telegram_application(agent, handle_follow_up, register_chat_id):
    """
    בונה ומחזירה את אפליקציית הטלגרם, ומזריקה את התלויות.
    """
    TELEGRAM_BOT_TOKEN = Config.TELEGRAM_BOT_TOKEN
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Security Error: The TELEGRAM_BOT_TOKEN is not set!")
        return None

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.bot_data['agent'] = agent
    application.bot_data['handle_follow_up'] = handle_follow_up
    application.bot_data['register_chat_id'] = register_chat_id

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    return application
# if __name__ == '__main__':
#     main()