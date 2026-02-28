from fastmcp import FastMCP
import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
import requests
from bs4 import BeautifulSoup
from pymongo import MongoClient
import logging
import pandas as pd
from playwright_session import fetch_html_sync

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("Weight Tracker MCP Server")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://mongodb:27017/bot_db")
CURRICULUM_PATH = os.environ.get("CURRICULUM_PATH", "data/rust_curriculum.json")
AA_API_KEY = "aa_iebTMsqZPzykexkOGcoJnHDsqnNoPJQL"
AA_API_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"

# Initialize MongoDB client
# We use a lazy initialization or a global client
client = MongoClient(MONGO_URI)
db = client.get_database()
weights_col = db["weights"]
learning_progress_col = db["learning_progress"]
todos_col = db["todos"]


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

        # Migrate existing todos without todo_number
        existing_todos = list(
            todos_col.find({"todo_number": {"$exists": False}}).sort("created_at", 1)
        )
        if existing_todos:
            for idx, todo in enumerate(existing_todos, start=1):
                todos_col.update_one(
                    {"_id": todo["_id"]}, {"$set": {"todo_number": idx}}
                )
            logger.info(f"Migrated {len(existing_todos)} existing todos with numbers.")

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
        html = fetch_html_sync(url, timeout=15)
        soup = BeautifulSoup(html, "html.parser")

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
        html = fetch_html_sync(url, timeout=15)
        soup = BeautifulSoup(html, "html.parser")
        facts = ["--- WIKIPEDIA EVENTS ---"]

        content = soup.find("div", class_="mw-parser-output")
        if not content:
            return "No Wikipedia facts found."

        events_ul = None
        for ul in content.find_all("ul", recursive=False):
            if ul.find("li"):
                events_ul = ul
                break

        if events_ul:
            for item in events_ul.find_all("li", limit=8):
                facts.append(item.get_text().strip())

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
        html = fetch_html_sync(url, timeout=15)
        soup = BeautifulSoup(html, "html.parser")
        facts = ["--- ONTHISDAY.COM EVENTS ---"]

        event_list = soup.find("ul", class_="event-list")
        if event_list:
            for li in event_list.find_all("li", class_="event", limit=8):
                facts.append(li.get_text().strip())

        birthdays = soup.find("ul", class_="photo-list")
        if birthdays:
            for li in birthdays.find_all("li", limit=5):
                facts.append(f"Birth: {li.get_text().strip()}")

        return "\n".join(facts) if len(facts) > 1 else "No OnThisDay facts found."
    except Exception as e:
        logger.error(f"OnThisDay error: {e}")
        return f"Error fetching OnThisDay: {e}"


# --- Todo Tools ---
@mcp.tool
def add_todo(title: str, description: str = "") -> str:
    """Add a new todo item to your list.

    Args:
        title: The todo title (required)
        description: Optional description/details for the todo

    Returns:
        Confirmation message with the todo number
    """
    todo_count = todos_col.count_documents({})
    todo_number = todo_count + 1
    entry = {
        "todo_number": todo_number,
        "title": title,
        "description": description,
        "created_at": datetime.utcnow(),
        "completed": False,
    }
    todos_col.insert_one(entry)
    return f'✅ Added todo #{todo_number}: "{title}"'


@mcp.tool
def list_todos() -> List[Dict[str, Any]]:
    """Get all todos ordered by creation time (newest last - oldest first).

    Returns:
        List of todo items with number, title, description, and timestamp
    """
    cursor = todos_col.find({}, {"_id": 0}).sort("created_at", 1)
    results = list(cursor)
    for r in results:
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()
    return results


@mcp.tool
def remove_todo(todo_number: str) -> str:
    """Remove a todo item by its number.

    Args:
        todo_number: The todo number to remove (1, 2, 3, ...)

    Returns:
        Confirmation message
    """
    try:
        num = int(todo_number)
    except (ValueError, TypeError):
        return f"❌ Invalid todo number: {todo_number}"

    result = todos_col.delete_one({"todo_number": num})
    if result.deleted_count > 0:
        return f"✅ Todo #{num} removed."
    return f"❌ Todo #{num} not found."


@mcp.tool
def remove_todos(todo_numbers: str) -> str:
    """Remove multiple todo items by their numbers.

    Args:
        todo_numbers: Comma-separated numbers or ranges (e.g., "1,3,4" or "1-5")

    Returns:
        Confirmation message with list of removed todos
    """
    numbers = []
    parts = todo_numbers.replace(",", " ").split()
    for part in parts:
        if "-" in part:
            try:
                start, end = map(int, part.split("-"))
                numbers.extend(range(start, end + 1))
            except ValueError:
                pass
        else:
            try:
                numbers.append(int(part))
            except ValueError:
                pass

    if not numbers:
        return "❌ Invalid format. Use: 1,3,4 or 1-5"

    removed = []
    for num in numbers:
        result = todos_col.delete_one({"todo_number": num})
        if result.deleted_count > 0:
            removed.append(num)

    if removed:
        return f"✅ Removed todo(s): {', '.join(f'#{n}' for n in removed)}"
    return "❌ No todos found to remove."


@mcp.tool
def get_top_efficient_models(limit: int = 10) -> Dict[str, Any]:
    """Fetch AI model data and return top price-efficient models across indices.
    
    Returns:
        A dictionary with top N models for intelligence, coding, and tau2 values per dollar.
    """
    headers = {"x-api-key": AA_API_KEY}
    try:
        response = requests.get(AA_API_URL, headers=headers)
        response.raise_for_status()
        json_data = response.json()
    except Exception as e:
        logger.error(f"Error fetching data from Artificial Analysis: {e}")
        return {"error": str(e)}

    if 'data' not in json_data:
        return {"error": "'data' key not found in API response"}

    models = json_data['data']
    processed_data = []
    for m in models:
        evals = m.get('evaluations', {})
        pricing = m.get('pricing', {})
        processed_data.append({
            'name': m.get('name'),
            'intelligence_index': evals.get('artificial_analysis_intelligence_index'),
            'coding_index': evals.get('artificial_analysis_coding_index'),
            'tau2': evals.get('tau2'),
            'price_blended': pricing.get('price_1m_blended_3_to_1')
        })

    df = pd.DataFrame(processed_data)
    df = df[df['price_blended'] > 0].copy()
    
    # Calculate Value Indices (Per Dollar)
    df['intel_value'] = df['intelligence_index'] / df['price_blended']
    df['coding_value'] = df['coding_index'] / df['price_blended']
    df['tau2_value'] = df['tau2'] / df['price_blended']
    
    def get_top(df_in, col, value_col, lim):
        return df_in.dropna(subset=[value_col]).sort_values(by=value_col, ascending=False).head(lim)[['name', col, 'price_blended', value_col]].to_dict(orient='records')

    limit_val = int(limit)
    return {
        "top_intelligence": get_top(df, 'intelligence_index', 'intel_value', limit_val),
        "top_coding": get_top(df, 'coding_index', 'coding_value', limit_val),
        "top_tau2": get_top(df, 'tau2', 'tau2_value', limit_val)
    }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
