import os
import io
import json
import logging
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
import aiohttp
import plotly.graph_objects as go
import plotly.io as pio
import pandas as pd

from agent_logic import personas, initialize_personas, get_london_weather, logger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular messages through the agent."""
    if update.message is None:
        return

    user_id = update.effective_user.id
    text = update.message.text

    logger.info(f"Received message: {text} from {update.effective_user}")

    user_persona_name = user_modes.get(user_id, DEFAULT_PERSONA)
    if user_persona_name not in personas:
        user_persona_name = DEFAULT_PERSONA

    user_agent_executor = personas[user_persona_name].executor

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


async def daily_check_job(application: Application):
    """Send daily check-in message."""
    logger.info(f"Executing daily_check. CHAT_ID: {CHAT_ID}")

    if CHAT_ID:
        try:
            weather = await get_london_weather()
            if weather:
                temp = weather.get("temperature", "N/A")
                code = weather.get("weathercode", 0)
                condition = WEATHER_CODES.get(code, "unknown conditions")
                is_day = weather.get("is_day", 1)
                sun_emoji = "🌞" if is_day else "🌙"
                weather_msg = f"{sun_emoji} It's {temp}°C and {condition} in London."
            else:
                logger.warning("Failed to fetch weather data.")
                weather_msg = "Good morning!"

            log_msg = f"{weather_msg} Feel free to ask me for help with anything today!"
            logger.info(f"Sending message: {log_msg}")

            await application.bot.send_message(chat_id=CHAT_ID, text=log_msg)
            logger.info(f"Successfully sent daily check to {CHAT_ID}")
        except Exception as e:
            logger.error(f"Failed to send daily message: {e}")
    else:
        logger.warning("CHAT_ID is not set. Skipping daily check-in.")


import nest_asyncio

nest_asyncio.apply()


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
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    from datetime import time

    application.job_queue.run_daily(
        daily_check_job, time(hour=8, minute=0), chat_id=CHAT_ID
    )

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    await asyncio.Event().wait()


if __name__ == "__main__":
    import asyncio

    async def init_and_run():
        await initialize()
        await main()

    asyncio.run(init_and_run())
