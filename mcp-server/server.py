from fastmcp import FastMCP
import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
import requests
from bs4 import BeautifulSoup
from pymongo import MongoClient
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("Weight Tracker MCP Server")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://mongodb:27017/bot_db")
CURRICULUM_PATH = os.environ.get("CURRICULUM_PATH", "data/rust_curriculum.json")

# Initialize MongoDB client
# We use a lazy initialization or a global client
client = MongoClient(MONGO_URI)
db = client.get_database()
weights_col = db["weights"]
learning_progress_col = db["learning_progress"]


def init_db():
    try:
        # Initialize learning_progress for each language if it doesn't exist
        languages = ["rust", "cpp", "python"]
        for lang in languages:
            if learning_progress_col.count_documents({"_id": f"{lang}_progress"}) == 0:
                learning_progress_col.insert_one(
                    {
                        "_id": f"{lang}_progress",
                        "language": lang,
                        "current_topic_index": 0,
                        "updated_at": datetime.utcnow(),
                    }
                )
        logger.info("MongoDB collections initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize MongoDB: {e}")


def load_curriculum(language: str) -> List[Dict[str, Any]]:
    """Load the curriculum for a specific language from JSON file."""
    path = f"data/{language}_curriculum.json"
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"Curriculum not found for {language} at {path}")
        return []


init_db()


@mcp.tool
def record_weight(weight: float, unit: str = "kg") -> str:
    """Record a new weight entry for the user. Unit should be 'kg' or 'lbs'.

    Args:
        weight: The weight value to record
        unit: The unit of measurement (default: 'kg')

    Returns:
        Confirmation message with recorded weight
    """
    entry = {"weight": weight, "unit": unit, "timestamp": datetime.utcnow()}
    weights_col.insert_one(entry)
    return f"✅ Recorded: {weight} {unit}"


@mcp.tool
def get_weights() -> List[Dict[str, Any]]:
    """Get all weight records ordered by timestamp (most recent first).

    Returns:
        List of weight records with weight, unit, and timestamp
    """
    cursor = weights_col.find({}, {"_id": 0}).sort("timestamp", -1)
    results = list(cursor)
    for r in results:
        if isinstance(r.get("timestamp"), datetime):
            r["timestamp"] = r["timestamp"].isoformat()
    return results


@mcp.tool
def get_last_weight() -> Dict[str, Any]:
    """Get the most recent weight record.

    Returns:
        The last weight record with weight, unit, and timestamp
    """
    last = weights_col.find_one({}, {"_id": 0}, sort=[("timestamp", -1)])
    if last:
        if isinstance(last.get("timestamp"), datetime):
            last["timestamp"] = last["timestamp"].isoformat()
        return last
    return {"error": "No weight records found"}


@mcp.tool
def delete_all_weights() -> str:
    """Delete all weight records. Use with caution!

    Returns:
        Confirmation message with number of deleted records
    """
    result = weights_col.delete_many({})
    return f"Deleted {result.deleted_count} records"


def _get_topic(language: str) -> Dict[str, Any]:
    """Internal helper to get the current topic for a language."""
    curriculum = load_curriculum(language)
    if not curriculum:
        return {"error": f"{language.capitalize()} curriculum not found"}

    progress = learning_progress_col.find_one({"_id": f"{language}_progress"})
    current_index = progress["current_topic_index"] if progress else 0

    if current_index >= len(curriculum):
        return {
            "error": "All topics completed",
            "language": language,
            "current_index": current_index,
            "total_topics": len(curriculum),
        }

    topic = curriculum[current_index]
    return {
        "index": topic["index"],
        "section": topic["section"],
        "exercise": topic["exercise"],
        "title": topic["title"],
        "explanation": topic["explanation"],
        "hint": topic["hint"],
        "current_index": current_index + 1,
        "total_topics": len(curriculum),
        "language": language,
    }


def _advance_topic(language: str) -> Dict[str, Any]:
    """Internal helper to advance the topic for a language."""
    curriculum = load_curriculum(language)
    if not curriculum:
        return {"error": f"{language.capitalize()} curriculum not found"}

    progress = learning_progress_col.find_one({"_id": f"{language}_progress"})
    current_index = progress["current_topic_index"] if progress else 0

    if current_index >= len(curriculum):
        return {
            "error": "All topics completed",
            "language": language,
            "current_index": current_index,
            "total_topics": len(curriculum),
        }

    new_index = current_index + 1
    learning_progress_col.update_one(
        {"_id": f"{language}_progress"},
        {"$set": {"current_topic_index": new_index, "updated_at": datetime.utcnow()}},
    )

    if new_index >= len(curriculum):
        return {
            "message": f"Congratulations! You've completed all {language.capitalize()} topics!",
            "language": language,
            "current_index": new_index,
            "total_topics": len(curriculum),
        }

    topic = curriculum[new_index]
    return {
        "index": topic["index"],
        "section": topic["section"],
        "exercise": topic["exercise"],
        "title": topic["title"],
        "explanation": topic["explanation"],
        "hint": topic["hint"],
        "current_index": new_index + 1,
        "total_topics": len(curriculum),
        "language": language,
    }


def _reset_progress(language: str) -> str:
    """Internal helper to reset progress for a language."""
    learning_progress_col.update_one(
        {"_id": f"{language}_progress"},
        {"$set": {"current_topic_index": 0, "updated_at": datetime.utcnow()}},
    )
    return f"{language.capitalize()} progress successfully reset. Ready to start fresh!"


# --- Rust Tools ---
@mcp.tool
def get_rust_topic() -> Dict[str, Any]:
    """Get the current Rust topic the user is learning."""
    return _get_topic("rust")


@mcp.tool
def advance_rust_topic() -> Dict[str, Any]:
    """Advance to the next Rust topic and return it."""
    return _advance_topic("rust")


@mcp.tool
def reset_rust_progress() -> str:
    """Reset Rust learning progress."""
    return _reset_progress("rust")


# --- C++ Tools ---
@mcp.tool
def get_cpp_topic() -> Dict[str, Any]:
    """Get the current C++ topic the user is learning."""
    return _get_topic("cpp")


@mcp.tool
def advance_cpp_topic() -> Dict[str, Any]:
    """Advance to the next C++ topic and return it."""
    return _advance_topic("cpp")


@mcp.tool
def reset_cpp_progress() -> str:
    """Reset C++ learning progress."""
    return _reset_progress("cpp")


# --- Python Tools ---
@mcp.tool
def get_python_topic() -> Dict[str, Any]:
    """Get the current Python topic the user is learning."""
    return _get_topic("python")


@mcp.tool
def advance_python_topic() -> Dict[str, Any]:
    """Advance to the next Python topic and return it."""
    return _advance_topic("python")


@mcp.tool
def reset_python_progress() -> str:
    """Reset Python learning progress."""
    return _reset_progress("python")


@mcp.tool
def get_history_britannica() -> str:
    """Get raw historical events from Britannica for today. Use this alongside Wikipedia for a comprehensive view."""
    now = datetime.now()
    month_name = now.strftime("%B")
    day = now.day
    url = f"https://www.britannica.com/on-this-day/{month_name}-{day}"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, timeout=10, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        facts = ["--- BRITANNICA EVENTS ---"]

        featured = soup.find("div", class_="otd-featured-event")
        if featured:
            year = featured.find("div", class_="date-label")
            title = featured.find("div", class_="title")
            if year and title:
                facts.append(
                    f"Featured: {year.get_text().strip()}: {title.get_text().strip()}"
                )

        events = soup.find_all("div", class_="md-history-event", limit=5)
        for event in events:
            year = event.find("div", class_="date-label")
            body = event.find("div", class_="card-body")
            if year and body:
                text = body.get_text(separator=" ").strip()
                if "Read today's edition" in text:
                    text = text.split("Read today's edition")[0].strip()
                text = " ".join(text.split())
                facts.append(f"{year.get_text().strip()}: {text}")

        born_section = soup.find_all("div", class_="md-history-born", limit=5)
        for born in born_section:
            year = born.find("div", class_="date-label")
            name = born.find("a", class_="font-weight-bold")
            desc = born.find("div", class_="identifier")
            if year and name:
                info = f"Birth: {year.get_text().strip()} - {name.get_text().strip()}"
                if desc:
                    info += f" ({desc.get_text().strip()})"
                facts.append(info)

        return "\n".join(facts) if len(facts) > 1 else "No Britannica facts found."
    except Exception as e:
        logger.error(f"Britannica error: {e}")
        return f"Error fetching Britannica: {e}"


@mcp.tool
def get_history_today() -> str:
    """Get raw historical events from Wikipedia for today. Use this alongside Britannica for a comprehensive view."""
    url = "https://en.wikipedia.org/wiki/Wikipedia:On_this_day/Today"

    try:
        headers = {"User-Agent": "TelegramBot/1.0"}
        response = requests.get(url, timeout=10, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        facts = ["--- WIKIPEDIA EVENTS ---"]

        content = soup.find("div", class_="mw-parser-output")
        if not content:
            return "No Wikipedia facts found."

        # Get the first <ul> for main events
        events_ul = None
        for ul in content.find_all("ul", recursive=False):
            if ul.find("li"):
                events_ul = ul
                break

        if events_ul:
            for item in events_ul.find_all("li", limit=8):
                facts.append(item.get_text().strip())

        # Extract births/deaths from hlist sections
        hlist_divs = content.find_all("div", class_="hlist")
        for hlist_div in hlist_divs:
            for li in hlist_div.find_all("li", limit=5):
                text = li.get_text().strip()
                if "b." in text or "born" in text.lower():
                    facts.append(f"Birth: {text}")
                elif "d." in text or "died" in text.lower():
                    facts.append(f"Death: {text}")

        return "\n".join(facts) if len(facts) > 1 else "No Wikipedia facts found."
    except Exception as e:
        logger.error(f"Wikipedia error: {e}")
        return f"Error fetching Wikipedia: {e}"


@mcp.tool
def get_history_on_this_day() -> str:
    """Get raw historical events from onthisday.com for today. Use this alongside other tools for a comprehensive view."""
    url = "https://www.onthisday.com/"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, timeout=10, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        facts = ["--- ONTHISDAY.COM EVENTS ---"]

        # Events
        event_list = soup.find("ul", class_="event-list")
        if event_list:
            for li in event_list.find_all("li", class_="event", limit=8):
                facts.append(li.get_text().strip())

        # Birthdays
        # Usually in a photo-list or similar on the home page
        birthdays = soup.find("ul", class_="photo-list")
        if birthdays:
            for li in birthdays.find_all("li", limit=5):
                facts.append(f"Birth: {li.get_text().strip()}")

        return "\n".join(facts) if len(facts) > 1 else "No OnThisDay facts found."
    except Exception as e:
        logger.error(f"OnThisDay error: {e}")
        return f"Error fetching OnThisDay: {e}"


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
