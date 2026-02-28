import os
import logging
import asyncio
import aiohttp
from langchain_openai import ChatOpenAI
from langchain_classic.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langchain_mcp_adapters.client import MultiServerMCPClient

# --- Observability Setup (Arize Phoenix) ---
try:
    from phoenix.otel import register
    from openinference.instrumentation.langchain import LangChainInstrumentor

    # PHOENIX_COLLECTOR_ENDPOINT is set in docker-compose.yml
    tracer_provider = register()
    LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
    logging.getLogger(__name__).info("Arize Phoenix instrumentation initialized.")
except ImportError:
    logging.getLogger(__name__).warning(
        "Arize Phoenix libraries not found. Tracing disabled."
    )
except Exception as e:
    logging.getLogger(__name__).warning(f"Failed to initialize Arize Phoenix: {e}")

# Configure logging
logger = logging.getLogger(__name__)

# Environment variables
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    logger.warning(
        "OPENROUTER_API_KEY is not set. The agent will not be able to process requests."
    )
    OPENROUTER_API_KEY = "sk-placeholder-key-set-your-own"

OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL", "google/gemini-2.0-flash-lite-preview-02-05:free"
)
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp-server:8000/mcp")

# --- Local Tool Definitions ---


async def get_cambridge_weather():
    """Fetch current weather for Cambridge, UK from Open-Meteo API."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": 52.1951,
        "longitude": 0.1313,
        "current_weather": "true",
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                return data
        except Exception as e:
            logger.error(f"Failed to fetch weather: {e}")
            return None


@tool
async def get_current_weather_cambridge():
    """Get the current weather in Cambridge, UK."""
    return await get_cambridge_weather()


# --- Persona Definition ---


class Persona:
    def __init__(self, name, description, system_instructions, tools, llm_model):
        self.name = name
        self.description = description
        self.tools = tools

        self.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_instructions),
                ("human", "{input}"),
                ("placeholder", "{agent_scratchpad}"),
            ]
        )
        self.agent = create_tool_calling_agent(llm_model, tools, self.prompt)
        self.executor = AgentExecutor(agent=self.agent, tools=tools, verbose=True)


# --- Agent & Tool Setup ---

llm = ChatOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
    model=OPENROUTER_MODEL,
    temperature=0,
)

# Global personas dictionary
personas = {}


async def initialize_personas():
    """Initialize personas by loading MCP tools and local tools."""
    global personas

    mcp_tools = []
    max_retries = 5
    retry_delay = 5

    logger.info(f"Connecting to MCP server at {MCP_SERVER_URL}...")

    for attempt in range(max_retries):
        try:
            # MultiServerMCPClient handles discovery of sub-paths automatically
            # We point it directly to the SSE endpoint which is standard for FastMCP
            client = MultiServerMCPClient(
                {"main": {"url": MCP_SERVER_URL, "transport": "http"}}
            )
            # Automatically discover all tools from the MCP server
            mcp_tools = await client.get_tools()
            if mcp_tools:
                logger.info(
                    f"Successfully loaded {len(mcp_tools)} tools from MCP server."
                )
                break
        except Exception as e:
            logger.warning(
                f"Attempt {attempt + 1}/{max_retries} failed to load MCP tools: {e}"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                logger.error("All attempts to load MCP tools failed.")

    # Filter tools for specific personas
    weight_mcp = [
        t for t in mcp_tools if any(word in t.name for word in ["weight", "data"])
    ]
    rust_mcp = [t for t in mcp_tools if "rust" in t.name]
    cpp_mcp = [t for t in mcp_tools if "cpp" in t.name]
    python_mcp = [t for t in mcp_tools if "python" in t.name]
    history_mcp = [t for t in mcp_tools if "history" in t.name]
    todo_mcp = [t for t in mcp_tools if "todo" in t.name]
    models_mcp = [t for t in mcp_tools if "models" in t.name]

    # Define Tool Sets
    general_tools = [get_current_weather_cambridge] + history_mcp + models_mcp
    weight_tools = weight_mcp
    rust_tools = rust_mcp
    cpp_tools = cpp_mcp
    python_tools = python_mcp
    todo_tools = todo_mcp
    daily_report_tools = [get_current_weather_cambridge] + todo_mcp + models_mcp

    # Create Personas
    personas["daily_report"] = Persona(
        name="Daily Reporter",
        description="Generates a consolidated daily morning report with weather, todos, and AI rankings.",
        system_instructions=(
            "You are a helpful Daily Briefing Assistant. Your goal is to generate a friendly daily morning report for a user in Cambridge, UK. "
            "You MUST call these tools to gather information: \n"
            "1. 'get_current_weather_cambridge' for the current weather.\n"
            "2. 'list_todos' to get the user's pending tasks.\n"
            "3. 'get_top_efficient_models' to get the latest AI model rankings.\n\n"
            "After gathering the data, create a consolidated report with the following sections:\n"
            "1. Start with a friendly morning greeting and a relevant emoji.\n"
            "2. '🌤 Weather': Describe current conditions in Cambridge.\n"
            "3. '📝 Pending TODOs': List all todos clearly.\n"
            "4. '🤖 AI Efficiency Rankings': List top models across Intelligence, Coding, and Tau2 per dollar categories.\n"
            "   - DO NOT USE MARKDOWN TABLES. Use a clear vertical list.\n"
            "   - Format: '1. **Model Name** - Value: Score/$$ (Score: XX, Price: $YY)'\n"
            "   - Highlight the top model in each category with a special emoji.\n"
            "5. Use Markdown for headings and emphasis. Keep the tone conversational, professional, and friendly.\n"
            "6. Keep it concise (under 600 words).\n"
            "Return ONLY the markdown formatted report. No internal monologues or commentary."
        ),
        tools=daily_report_tools,
        llm_model=llm,
    )

    personas["general"] = Persona(
        name="General",
        description="A helpful assistant for general queries, weather, and history.",
        system_instructions=(
            "You are a helpful AI assistant. When a user asks about historical events for today, you MUST: "
            "1. Call these THREE tools: 'get_history_today', 'get_history_britannica', AND 'get_history_on_this_day'. "
            "Do NOT skip any of them. Each provides unique events. "
            "When a user asks about AI model efficiency rankings, use the 'get_top_efficient_models' tool "
            "and summarize the top models across intelligence, coding, and tau2 per dollar. "
            "Combine and cross-reference the information from all sources. "
            "Provide ONLY the final summarized response organized into these sections: "
            "   - 🌟 Featured Events "
            "   - 📅 Other Notable Events "
            "   - 👶 Notable Births "
            "   - 🕯️ Notable Deaths "
            "   - 🤖 AI Efficiency Rankings (if requested) "
            "CRITICAL: Do not output your thinking process, internal monologues, or 'Wait, let me check' style commentary. "
            "Return ONLY the final formatted summary with emojis. No meta-talk."
        ),
        tools=general_tools,
        llm_model=llm,
    )

    personas["weight"] = Persona(
        name="Weight Tracker",
        description="Focused on tracking and visualizing weight loss progress.",
        system_instructions=(
            "You are a dedicated Weight Tracking Assistant. Help the user log their weight and view their progress. "
            "If the user discusses unrelated topics, suggest switching to general mode."
        ),
        tools=weight_tools,
        llm_model=llm,
    )

    personas["rust"] = Persona(
        name="Rust Tutor",
        description="An interactive Rust programming language tutor.",
        system_instructions=(
            "You are a Rust Programming Tutor (Crab Mode 🦀). Your goal is to teach the user Rust. "
            "Explain concepts clearly with code examples. Be encouraging and use crab emojis! 🦀 "
            "If the user asks about other topics, suggest switching to general mode."
        ),
        tools=rust_tools,
        llm_model=llm,
    )

    personas["cpp"] = Persona(
        name="C++ Tutor",
        description="An interactive C++ programming language tutor.",
        system_instructions=(
            "You are a C++ Programming Tutor. Your goal is to teach the user C++. "
            "Explain concepts clearly with modern C++ examples (C++11 and later). Be precise and helpful. "
            "If the user asks about other topics, suggest switching to general mode."
        ),
        tools=cpp_tools,
        llm_model=llm,
    )

    personas["python"] = Persona(
        name="Python Tutor",
        description="An interactive Python programming language tutor.",
        system_instructions=(
            "You are a Python Programming Tutor. Your goal is to teach the user Python. "
            "Explain concepts clearly with idiomatic Python (Pythonic) examples. Be friendly and helpful. "
            "If the user asks about other topics, suggest switching to general mode."
        ),
        tools=python_tools,
        llm_model=llm,
    )

    personas["todo"] = Persona(
        name="Todo Tracker",
        description="Manage your todo list with add, list, and remove capabilities.",
        system_instructions=(
            "You are a Todo List Manager. Help the user manage their tasks. "
            "Available actions:\n"
            "- Add a todo: Use add_todo tool with title and optional description\n"
            "- List todos: Use list_todos tool to see all todos (numbered 1, 2, 3...)\n"
            "- Remove a todo: Use remove_todo tool with the todo NUMBER (single)\n"
            "- Remove multiple: Use remove_todos tool with comma-separated numbers or ranges (e.g., '1,3,4' or '1-5')\n"
            "When listing todos, always show numbers (1, 2, 3...) so users can reference them. "
            "Users remove todos by saying 'remove todo 1' or 'delete todo 3' or 'remove todo 1,3,4'. "
            "If the user asks about other topics, suggest switching to general mode."
        ),
        tools=todo_tools,
        llm_model=llm,
    )

    logger.info("Personas initialized successfully.")


# Since the bot and Gradio need to wait for initialization,
# we'll trigger this in their respective startup logic.
