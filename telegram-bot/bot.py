import os
import io
import json
import logging
import base64
import aiohttp
from datetime import datetime

import telegram
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
import telegramify_markdown
import plotly.graph_objects as go
import plotly.io as pio
import pandas as pd

from agent_logic import personas, initialize_personas, get_cambridge_weather, logger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-lite-preview-02-05:free")
TRANSCRIPTION_MODEL = "google/gemini-3-flash-preview"

WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}

user_modes = {}
DEFAULT_PERSONA = "general"

CONFIRM_RESET = range(1)
CONFIRM_RUST_RESTART = range(1)


async def call_mcp_tool(tool_name: str, args: dict = None):
    """Call MCP tool via the persona's executor."""
    return None


async def initialize():
    """Initialize personas on startup."""
    await initialize_personas()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message."""
    await update.message.reply_text(
        "Welcome! I'm your Telegram bot assistant.\n\n"
        "Use /modes to see available personas.\n"
        "Use /mode <name> to switch modes.\n"
        "Or just chat with me!"
    )


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch your interaction mode (general, weight, rust, cpp, python)."""
    user_id = update.effective_user.id

    if not context.args:
        current = user_modes.get(user_id, DEFAULT_PERSONA)
        if current not in personas:
            current = DEFAULT_PERSONA
        await update.message.reply_text(
            f"Current mode: {current}. Available modes: {', '.join(personas.keys())}."
        )
        return

    persona_name = context.args[0].lower()
    if persona_name in personas:
        user_modes[user_id] = persona_name
        await update.message.reply_text(
            f"Switched to {personas[persona_name].name} mode. {personas[persona_name].description}"
        )
    else:
        await update.message.reply_text(
            f"Unknown mode '{persona_name}'. Available modes: {', '.join(personas.keys())}"
        )
        return

    persona_name = context.args[0].lower()
    if persona_name in personas:
        user_modes[user_id] = persona_name
        await update.message.reply_text(
            f"Switched to {personas[persona_name].name} mode. {personas[persona_name].description}"
        )
    else:
        await update.message.reply_text(
            f"Unknown mode '{persona_name}'. Available modes: {', '.join(personas.keys())}"
        )


async def modes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all available personas."""
    msg = "Available Modes:\n"
    for p_id, p in personas.items():
        msg += f"• {p_id}: {p.description}\n"
    msg += "\nUse /mode <name> to switch."
    await update.message.reply_text(msg)


async def weight_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Explicitly record weight via command."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /weight <value> [unit]\nExample: /weight 75 kg"
        )
        return

    try:
        value = float(context.args[0])
        unit = context.args[1] if len(context.args) > 1 else "kg"
    except ValueError:
        await update.message.reply_text("Please provide a valid number.")
        return

    user_id = update.effective_user.id
    user_persona = user_modes.get(user_id, DEFAULT_PERSONA)

    if user_persona not in personas:
        user_persona = DEFAULT_PERSONA

    try:
        executor = personas[user_persona].executor
        response = await executor.ainvoke(
            {"input": f"Record my weight: {value} {unit}"}
        )
        output = response.get("output", "")
        await update.message.reply_text(
            telegramify_markdown.markdownify(output)
            if output
            else f"✅ Recorded: {value} {unit}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        logger.error(f"Weight error: {e}")
        await update.message.reply_text(f"✅ Recorded: {value} {unit}")


async def last_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the last recorded weight."""
    user_id = update.effective_user.id
    user_persona = user_modes.get(user_id, DEFAULT_PERSONA)

    if user_persona not in personas:
        user_persona = DEFAULT_PERSONA

    try:
        executor = personas[user_persona].executor
        response = await executor.ainvoke(
            {"input": "What was my last recorded weight?"}
        )
        output = response.get("output", "No weight records found.")
        await update.message.reply_text(
            telegramify_markdown.markdownify(output), parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Last weight error: {e}")
        await update.message.reply_text("No weight records found.")


async def plot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show last 10 readings and progress graph."""
    user_id = update.effective_user.id
    user_persona = user_modes.get(user_id, DEFAULT_PERSONA)

    if user_persona not in personas:
        user_persona = DEFAULT_PERSONA

    try:
        executor = personas[user_persona].executor
        response = await executor.ainvoke({"input": "Show my weight progress chart"})
        output = response.get("output", "")

        await update.message.reply_text(
            telegramify_markdown.markdownify(output)
            if output
            else "Generating chart...",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        logger.error(f"Plot error: {e}")
        await update.message.reply_text("No records found.")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete all records - with confirmation."""
    await update.message.reply_text(
        "⚠️ Are you sure you want to delete ALL data? Reply with 'yes' to confirm."
    )
    return CONFIRM_RESET


async def reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle confirmation for reset."""
    if update.message.text.lower() == "yes":
        user_id = update.effective_user.id
        user_persona = user_modes.get(user_id, DEFAULT_PERSONA)

        if user_persona not in personas:
            user_persona = DEFAULT_PERSONA

        try:
            executor = personas[user_persona].executor
            response = await executor.ainvoke({"input": "Delete all my weight records"})
            output = response.get("output", "✅ All records deleted\\.")
            await update.message.reply_text(
                telegramify_markdown.markdownify(output),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(f"Reset error: {e}")
            await update.message.reply_text("✅ All records deleted.")
    else:
        await update.message.reply_text("Operation cancelled.")

    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel a conversation."""
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


async def rust_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rust learning commands."""
    user_id = update.effective_user.id
    user_persona = user_modes.get(user_id, DEFAULT_PERSONA)

    if user_persona not in personas:
        user_persona = DEFAULT_PERSONA

    try:
        executor = personas[user_persona].executor
        response = await executor.ainvoke({"input": "Show me my current Rust topic"})
        output = response.get("output", "Use /rust_progress to see your current topic!")
        await update.message.reply_text(
            telegramify_markdown.markdownify(output), parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Rust error: {e}")
        await update.message.reply_text("Use /rust_progress to see your current topic!")


async def rust_progress_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current Rust learning progress."""
    user_id = update.effective_user.id
    user_persona = user_modes.get(user_id, DEFAULT_PERSONA)

    if user_persona not in personas:
        user_persona = DEFAULT_PERSONA

    try:
        executor = personas[user_persona].executor
        response = await executor.ainvoke(
            {"input": "What is my current Rust topic and progress?"}
        )
        output = response.get("output", "Could not retrieve progress.")
        await update.message.reply_text(
            telegramify_markdown.markdownify(output), parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Rust progress error: {e}")
        await update.message.reply_text("Could not retrieve progress.")


async def rust_restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset Rust learning progress - with confirmation."""
    await update.message.reply_text(
        "⚠️ Are you sure you want to reset your Rust progress? Reply with 'yes' to confirm."
    )
    return CONFIRM_RUST_RESTART


async def rust_restart_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle confirmation for rust restart."""
    if update.message.text.lower() == "yes":
        user_id = update.effective_user.id
        user_persona = user_modes.get(user_id, DEFAULT_PERSONA)

        if user_persona not in personas:
            user_persona = DEFAULT_PERSONA

        try:
            executor = personas[user_persona].executor
            response = await executor.ainvoke(
                {"input": "Reset my Rust progress to the beginning"}
            )
            output = response.get("output", "🦀 Rust progress reset!")
            await update.message.reply_text(
                telegramify_markdown.markdownify(output),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(f"Rust restart error: {e}")
            await update.message.reply_text("🦀 Rust progress reset!")
    else:
        await update.message.reply_text("Operation cancelled.")

    return ConversationHandler.END


async def todo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch to todo mode and show current todos."""
    user_id = update.effective_user.id

    if not context.args:
        user_modes[user_id] = "todo"
        try:
            executor = personas["todo"].executor
            response = await executor.ainvoke({"input": "Show me all my todos"})
            output = response.get("output", "")
            if output:
                await update.message.reply_text(
                    telegramify_markdown.markdownify(output),
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await update.message.reply_text(
                    "📝 Todo mode activated! Use natural language to:\n"
                    "• Add todos: 'Add todo buy milk'\n"
                    "• List todos: 'Show my todos'\n"
                    "• Remove todos: 'Remove todo <id>'"
                )
        except Exception as e:
            logger.error(f"Todo error: {e}")
            await update.message.reply_text(
                "📝 Todo mode activated! Use natural language to:\n"
                "• Add todos: 'Add todo buy milk'\n"
                "• List todos: 'Show my todos'\n"
                "• Remove todos: 'Remove todo <id>'"
            )
    else:
        title = " ".join(context.args)
        user_modes[user_id] = "todo"
        try:
            executor = personas["todo"].executor
            response = await executor.ainvoke({"input": f"Add todo: {title}"})
            output = response.get("output", "")
            await update.message.reply_text(
                telegramify_markdown.markdownify(output)
                if output
                else f"✅ Added: {title}",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(f"Todo add error: {e}")
            await update.message.reply_text(f"✅ Added: {title}")


async def transcribe_voice(voice_bytes: bytearray) -> str:
    """Transcribe audio using OpenRouter."""
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY not found.")
        return ""

    audio_base64 = base64.b64encode(voice_bytes).decode("utf-8")
    
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "Telegram Bot Transcription"
    }

    payload = {
        "model": TRANSCRIPTION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Please transcribe this audio exactly. Just the text, nothing else."
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": audio_base64,
                            "format": "ogg"
                        }
                    }
                ]
            }
        ]
    }

    logger.info(f"Sending audio to {TRANSCRIPTION_MODEL} via OpenRouter...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    result = await response.json()
                    try:
                        return result['choices'][0]['message']['content']
                    except (KeyError, IndexError):
                        logger.error(f"Unexpected response format: {result}")
                        return ""
                else:
                    logger.error(f"OpenRouter Error: {response.status} - {await response.text()}")
                    return ""
    except Exception as e:
        logger.error(f"Error calling OpenRouter: {e}")
        return ""


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages."""
    if update.message is None or update.message.voice is None:
        return

    # Inform the user that we are processing the voice message
    processing_msg = await update.message.reply_text("🎤 Processing voice message...")

    try:
        # Download the file
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        voice_bytes = await voice_file.download_as_bytearray()
        
        # Transcribe
        transcription = await transcribe_voice(voice_bytes)
        
        if transcription:
            logger.info(f"Transcription: {transcription}")
            # Replace the message text with the transcription
            # Update objects are immutable in some ways, but we can call handle_message 
            # with the transcription logic.
            # We'll create a fake update or just pass the text to our logic.
            
            # For simplicity, we'll inform the user and then process.
            safe_transcription = telegramify_markdown.markdownify(f"📝 *Transcription:* {transcription}")
            await processing_msg.edit_text(safe_transcription, parse_mode=ParseMode.MARKDOWN_V2)
            
            # Now call handle_message logic
            await _process_agent_message(update, context, transcription)
        else:
            await processing_msg.edit_text("Sorry, I couldn't transcribe that voice message.")
            
    except Exception as e:
        logger.error(f"Error in handle_voice: {e}")
        await processing_msg.edit_text("An error occurred while processing your voice message.")


async def _process_agent_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Refactored logic to handle agent processing for both text and voice."""
    user_id = update.effective_user.id
    
    user_persona_name = user_modes.get(user_id, DEFAULT_PERSONA)
    if user_persona_name not in personas:
        user_persona_name = DEFAULT_PERSONA

    user_agent_executor = personas[user_persona_name].executor

    # Show typing action while the agent is "thinking"
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=telegram.constants.ChatAction.TYPING)

    try:
        response = await user_agent_executor.ainvoke({"input": text})
        output = response.get("output")
        if output:
            await send_long_message(
                update.message, telegramify_markdown.markdownify(output)
            )
    except Exception as e:
        logger.error(f"Agent error for persona {user_persona_name}: {e}")
        await update.message.reply_text(
            "I'm having a bit of trouble thinking right now. Please try again."
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular messages through the agent."""
    if update.message is None or update.message.text is None:
        return

    text = update.message.text
    logger.info(f"Received message: {text} from {update.effective_user}")
    
    await _process_agent_message(update, context, text)


async def send_long_message(message, content: str, max_length: int = 4096):
    """Send a message, splitting it if it exceeds Telegram's character limit."""
    if len(content) <= max_length:
        await message.reply_text(content, parse_mode=ParseMode.MARKDOWN_V2)
        return

    chunks = []
    lines = content.split("\n")
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 1 > max_length:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            if len(line) > max_length:
                words = line.split(" ")
                for word in words:
                    if len(current_chunk) + len(word) + 1 > max_length:
                        if current_chunk:
                            chunks.append(current_chunk)
                        current_chunk = word
                    else:
                        current_chunk = (
                            current_chunk + " " + word if current_chunk else word
                        )
            else:
                current_chunk = line
        else:
            current_chunk = current_chunk + "\n" + line if current_chunk else line

    if current_chunk:
        chunks.append(current_chunk)

    for chunk in chunks:
        await message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN_V2)


async def daily_check_job(context: ContextTypes.DEFAULT_TYPE):
    """Send daily check-in message with weather and todos."""
    logger.info(f"Executing daily_check. CHAT_ID: {CHAT_ID}")
    
    application = context.application
    job_chat_id = CHAT_ID

    if job_chat_id:
        try:
            # Convert job_chat_id to int if it looks like one
            try:
                chat_id_int = int(job_chat_id)
            except (ValueError, TypeError):
                chat_id_int = job_chat_id

            # Use the consolidated Daily Report persona
            logger.info("Generating consolidated daily report...")
            report_msg = "Error generating daily report."
            
            try:
                if "daily_report" in personas:
                    daily_executor = personas["daily_report"].executor
                    response = await daily_executor.ainvoke({"input": "Generate today's daily morning report for Cambridge, UK."})
                    report_msg = response.get("output", "No report generated.")
                else:
                    logger.error("Daily report persona not found.")
                    report_msg = "Daily report configuration error."
            except Exception as e:
                logger.error(f"Consolidated report generation failed: {e}")
                report_msg = f"Failed to generate report: {e}"
            
            # Use telegramify_markdown to ensure safe output
            safe_msg = telegramify_markdown.markdownify(report_msg)
            
            logger.info(f"Sending daily report to {chat_id_int}")
            await application.bot.send_message(
                chat_id=chat_id_int, 
                text=safe_msg, 
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.info(f"Successfully sent daily report to {chat_id_int}")
        except Exception as e:
            logger.error(f"Failed to send daily report: {e}", exc_info=True)
    else:
        logger.warning("CHAT_ID is not set. Skipping daily check-in.")


async def main():
    """Run the bot."""
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN not found.")
        return

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("mode", mode_command))
    application.add_handler(CommandHandler("modes", modes_command))
    application.add_handler(CommandHandler("weight", weight_command))
    application.add_handler(CommandHandler("last", last_command))
    application.add_handler(CommandHandler("plot", plot_command))
    application.add_handler(CommandHandler("rust", rust_command))
    application.add_handler(CommandHandler("rust_progress", rust_progress_command))
    application.add_handler(CommandHandler("todo", todo_command))

    conv_reset = ConversationHandler(
        entry_points=[CommandHandler("reset", reset_command)],
        states={
            CONFIRM_RESET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reset_confirm)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    application.add_handler(conv_reset)

    conv_rust_restart = ConversationHandler(
        entry_points=[CommandHandler("rust_restart", rust_restart_command)],
        states={
            CONFIRM_RUST_RESTART: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, rust_restart_confirm)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    application.add_handler(conv_rust_restart)

    application.add_handler(
        MessageHandler(filters.VOICE, handle_voice)
    )

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    from datetime import time

    # Schedule the daily job
    application.job_queue.run_daily(
        daily_check_job, time(hour=8, minute=0), chat_id=CHAT_ID
    )

    await application.initialize()
    await application.start()
    
    # Run once on startup to verify and provide immediate feedback
    application.job_queue.run_once(daily_check_job, 1)

    await application.updater.start_polling()
    await asyncio.Event().wait()


if __name__ == "__main__":
    import asyncio

    async def init_and_run():
        await initialize()
        await main()

    asyncio.run(init_and_run())
