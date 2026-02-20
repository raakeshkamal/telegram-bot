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
    logging.getLogger(__name__).warning("Arize Phoenix libraries not found. Tracing disabled.")
except Exception as e:
    logging.getLogger(__name__).warning(f"Failed to initialize Arize Phoenix: {e}")

# Configure logging
logger = logging.getLogger(__name__)

# Environment variables
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    logger.warning("OPENROUTER_API_KEY is not set. The agent will not be able to process requests.")
    OPENROUTER_API_KEY = "sk-placeholder-key-set-your-own"

OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL", "google/gemini-2.0-flash-lite-preview-02-05:free"
)
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp-server:8000/mcp")

# --- Local Tool Definitions ---

async def get_london_weather():
    """Fetch current weather for London from Open-Meteo API."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": 51.5072,
        "longitude": -0.1276,
        "current_weather": "true",
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                return data.get("current_weather")
        except Exception as e:
            logger.error(f"Failed to fetch weather: {e}")
            return None

@tool
async def get_current_weather_london():
    """Get the current weather in London."""
    return await get_london_weather()

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
                {
                    "main": {
                        "url": MCP_SERVER_URL,
                        "transport": "http"
                    }
                }
            )
            # Automatically discover all tools from the MCP server
            mcp_tools = await client.get_tools()
            if mcp_tools:
                logger.info(f"Successfully loaded {len(mcp_tools)} tools from MCP server.")
                break
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{max_retries} failed to load MCP tools: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                logger.error("All attempts to load MCP tools failed.")

    # Filter tools for specific personas
    weight_mcp = [t for t in mcp_tools if any(word in t.name for word in ["weight", "data"])]
    rust_mcp = [t for t in mcp_tools if "rust" in t.name]
    cpp_mcp = [t for t in mcp_tools if "cpp" in t.name]
    python_mcp = [t for t in mcp_tools if "python" in t.name]
    history_mcp = [t for t in mcp_tools if "history" in t.name]

    # Define Tool Sets
    general_tools = [get_current_weather_london] + history_mcp
    weight_tools = weight_mcp
    rust_tools = rust_mcp
    cpp_tools = cpp_mcp
    python_tools = python_mcp

    # Create Personas
    personas["general"] = Persona(
        name="General",
        description="A helpful assistant for general queries, weather, and history.",
        system_instructions=(
            "You are a helpful AI assistant. When a user asks about historical events for today, you MUST: "
            "1. Call these THREE tools: 'get_history_today', 'get_history_britannica', AND 'get_history_on_this_day'. "
            "Do NOT skip any of them. Each provides unique events. "
            "2. Combine and cross-reference the information from all 3 sources. "
            "3. Provide ONLY the final summarized response organized into these sections: "
            "   - 🌟 Featured Events "
            "   - 📅 Other Notable Events "
            "   - 👶 Notable Births "
            "   - 🕯️ Notable Deaths "
            "CRITICAL: Do not output your thinking process, internal monologues, or 'Wait, let me check' style commentary. "
            "Return ONLY the final formatted summary with emojis. No meta-talk."
        ),
        tools=general_tools,
        llm_model=llm
    )
    
    personas["weight"] = Persona(
        name="Weight Tracker",
        description="Focused on tracking and visualizing weight loss progress.",
        system_instructions=(
            "You are a dedicated Weight Tracking Assistant. Help the user log their weight and view their progress. "
            "If the user discusses unrelated topics, suggest switching to general mode."
        ),
        tools=weight_tools,
        llm_model=llm
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
        llm_model=llm
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
        llm_model=llm
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
        llm_model=llm
    )
    
    logger.info("Personas initialized successfully.")

# Since the bot and Gradio need to wait for initialization, 
# we'll trigger this in their respective startup logic.
