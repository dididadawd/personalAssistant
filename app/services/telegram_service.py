# app/services/telegram_service.py
# Telegram bot service functions

import os
import asyncio
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

ASYNC_LOOP = None


def _split_message_intelligently(text: str, limit: int = 4096) -> list[str]:
    """
    Helper function to split long text into parts under the limit.
    Splits intelligently: first by paragraphs, then by lines.
    """
    if len(text) <= limit:
        return [text]

    parts = []
    current_part = ""
    # Split by paragraphs (double newlines) is the best way to preserve context
    paragraphs = text.split('\n\n')

    for paragraph in paragraphs:
        # If adding the next paragraph would exceed the limit
        if len(current_part) + len(paragraph) + 2 > limit:
            # Save current part if not empty
            if current_part:
                parts.append(current_part)
            current_part = paragraph
        else:
            # If there's room, add paragraph to current part
            if current_part:
                current_part += "\n\n" + paragraph
            else:
                current_part = paragraph
    
    # Add the last remaining part
    if current_part:
        parts.append(current_part)
        
    return parts


async def send_proactive_message(chat_id: int, message: str):
    """
    Send proactive message. If message is too long, splits it automatically into multiple messages.
    """
    if not TELEGRAM_BOT_TOKEN:
        return

    if not message or not message.strip():
        print("--- ⚠️ WARNING: Attempted to send an empty message to Telegram. Suppressed. ---", "SYSTEM_ERROR")
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    # Step 1: Split message into valid parts
    message_parts = _split_message_intelligently(message)

    # Step 2: Send each part separately in a loop
    for i, part in enumerate(message_parts):
        try:
            # First attempt: send with HTML formatting
            await bot.send_message(
                chat_id=chat_id, 
                text=part, 
                parse_mode=ParseMode.HTML
            )

        except BadRequest as e:
            if 'parse' in str(e):
                print(f"--- ⚠️ WARNING: Could not parse message part {i+1} as HTML. Falling back to plain text. ---", "SYSTEM_ERROR")
                try:
                    # Second attempt: if failed, send as plain text
                    await bot.send_message(
                        chat_id=chat_id, 
                        text=part
                    )
                except Exception as final_e:
                    print(f"--- ❌ CRITICAL ERROR: Failed to send message part {i+1} even as plain text. Error: {final_e} ---", "SYSTEM_ERROR")
            else:
                # Other BadRequest error (like 'Message is too long' - though shouldn't happen now)
                print(f"--- ❌ CRITICAL ERROR: An unexpected BadRequest occurred on part {i+1}. Error: {e} ---", "SYSTEM_ERROR")
                
        except Exception as e:
            print(f"--- ❌ CRITICAL ERROR: An unexpected error occurred while sending part {i+1}. Error: {e} ---", "SYSTEM_ERROR")

        # Add a short delay between messages to ensure they arrive in order
        if len(message_parts) > 1 and i < len(message_parts) - 1:
            await asyncio.sleep(0.5)  # Half a second
