"""
LangGraph-based state machine for Telegram bot.
"""

import logging
import telegramify_markdown
from typing import TypedDict, Annotated, Literal
from datetime import datetime
from dotenv import load_dotenv

from langgraph.graph import StateGraph, add_messages, END
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_classic.agents import create_tool_calling_agent

# Import from agent_logic
from agent_logic import load_persona_tools, SYSTEM_INSTRUCTIONS, OPENROUTER_API_KEY, OPENROUTER_MODEL
from state_persistence import state_persistence

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# ============================================================================
# TOOL LOADING
# ============================================================================

# Global variable to store loaded tools
_persona_tools_cache = None

async def get_cached_persona_tools():
    global _persona_tools_cache
    if _persona_tools_cache is None:
        _persona_tools_cache = await load_persona_tools()
    return _persona_tools_cache

# ============================================================================
# STATE PERSISTENCE HELPERS
# ============================================================================

async def load_user_state(user_id: str) -> "BotState":
    """Load user state from MongoDB."""
    saved_state = await state_persistence.load_state(user_id)

    if saved_state:
        # Convert saved state to BotState
        return {
            "messages": saved_state.get("messages", []),
            "current_input": "",
            "current_response": "",
            "active_persona": saved_state.get("active_persona", "general"),
            "persona_confidence": 0.0,
            "input_type": "text",
            "transcription": None,
            "user_id": user_id,
            "last_interaction": datetime.now(),
            "tool_calls": [],
            "tool_results": [],
            "_previous_persona": saved_state.get("active_persona", "general")
        }

    # Default state
    return {
        "messages": [],
        "current_input": "",
        "current_response": "",
        "active_persona": "general",
        "persona_confidence": 0.0,
        "input_type": "text",
        "transcription": None,
        "user_id": user_id,
        "last_interaction": datetime.now(),
        "tool_calls": [],
        "tool_results": [],
        "_previous_persona": None
    }


async def save_user_state(state: "BotState"):
    """Save user state to MongoDB."""
    await state_persistence.save_state(state["user_id"], state)

# ============================================================================
# STATE SCHEMA
# ============================================================================

class BotState(TypedDict):
    """State schema for the bot state machine."""

    # Core input/output
    messages: Annotated[list, add_messages]  # Conversation history
    current_input: str  # Current user input
    current_response: str  # Generated response

    # Persona management
    active_persona: str  # Current active persona
    persona_confidence: float  # LLM confidence in persona selection

    # Input metadata
    input_type: Literal["text", "voice", "command"]  # Type of input
    transcription: str | None  # Voice transcription if applicable

    # User context
    user_id: str  # Telegram user ID
    last_interaction: datetime  # Timestamp of last interaction

    # Tool execution
    tool_calls: list  # List of tool calls made
    tool_results: list  # List of tool results
    
    # Internal state for persona switching
    _previous_persona: str | None

# ============================================================================
# NODE FUNCTIONS
# ============================================================================

async def input_classifier(state: BotState) -> BotState:
    """Classify input type (text, voice, command)."""
    input_text = state["current_input"]

    if input_text.startswith("/"):
        input_type = "command"
    elif state.get("transcription"):
        input_type = "voice"
    else:
        input_type = "text"

    logger.info(f"Input classified as: {input_type}")

    return {
        **state,
        "input_type": input_type,
        "last_interaction": datetime.now()
    }


async def voice_handler(state: BotState) -> BotState:
    """Handle voice messages (transcription done before graph)."""
    if not state.get("transcription"):
        return {
            **state,
            "current_input": "[Voice transcription failed]",
            "transcription": None
        }

    return {
        **state,
        "current_input": state["transcription"]
    }


async def persona_router(state: BotState) -> BotState:
    """Route to appropriate persona based on intent."""
    input_text = state.get("transcription") or state["current_input"]
    input_text = input_text.strip()

    # Check for explicit commands
    if input_text.startswith("/mode"):
        parts = input_text.split()
        if len(parts) > 1:
            requested_persona = parts[1].lower()
            valid_personas = ["general", "weight", "rust", "cpp", "python", "todo", "daily_report"]
            if requested_persona in valid_personas:
                logger.info(f"Explicit persona switch: {requested_persona}")
                return {
                    **state,
                    "active_persona": requested_persona,
                    "persona_confidence": 1.0
                }

    # Command to persona mapping
    command_to_persona = {
        "/todo": "todo",
        "/weight": "weight",
        "/rust": "rust",
        "/cpp": "cpp",
        "/python": "python",
        "/daily": "daily_report"
    }

    for cmd, persona in command_to_persona.items():
        if input_text.startswith(cmd):
            logger.info(f"Command mapped to persona: {persona}")
            return {
                **state,
                "active_persona": persona,
                "persona_confidence": 1.0
            }

    # LLM-based routing
    routing_llm = ChatOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        model=OPENROUTER_MODEL,
        temperature=0
    )

    routing_prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a persona router for a Telegram bot. Analyze the user's input and determine which persona should handle it.

Available personas:
- general: Weather queries, general knowledge, historical events, AI model rankings
- weight: Recording weight, tracking weight progress, weight visualization
- rust: Rust programming questions, tutorials, learning progress
- cpp: C++ programming questions, tutorials, learning progress
- python: Python programming questions, tutorials, learning progress
- todo: Adding tasks, listing tasks, removing tasks, task management
- daily_report: Morning briefings

Respond with just the persona name (lowercase)."""),
        ("human", "User input: {input}")
    ])

    try:
        response = await routing_llm.ainvoke(routing_prompt.format_messages(input=input_text))
        selected_persona = response.content.strip().lower()

        valid_personas = ["general", "weight", "rust", "cpp", "python", "todo", "daily_report"]
        if selected_persona not in valid_personas:
            selected_persona = "general"

        logger.info(f"LLM routed to persona: {selected_persona}")
        return {
            **state,
            "active_persona": selected_persona,
            "persona_confidence": 0.8
        }
    except Exception as e:
        logger.error(f"Persona routing error: {e}")
        return {
            **state,
            "active_persona": "general",
            "persona_confidence": 0.0
        }


async def persona_switch(state: BotState) -> BotState:
    """Handle persona transitions and context preservation."""
    old_persona = state.get("_previous_persona")
    new_persona = state["active_persona"]

    if old_persona and old_persona != new_persona:
        # Save current messages to old persona's history
        await state_persistence.save_persona_history(
            state["user_id"],
            old_persona,
            state["messages"]
        )

        # Load new persona's history
        new_history = await state_persistence.get_persona_history(
            state["user_id"],
            new_persona
        )

        logger.info(f"Persona switch: {old_persona} -> {new_persona}")

        return {
            **state,
            "messages": new_history
        }

    # First time or same persona
    return state

async def execute_persona(state: BotState, persona_name: str) -> BotState:
    """Generic persona execution function."""
    system_instructions = SYSTEM_INSTRUCTIONS.get(persona_name, SYSTEM_INSTRUCTIONS["general"])
    persona_tools_map = await get_cached_persona_tools()
    tools = persona_tools_map.get(persona_name, [])

    llm = ChatOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        model=OPENROUTER_MODEL,
        temperature=0
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_instructions),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)

    # Use existing executor logic from agent_logic's Persona but within LangGraph
    from langchain_classic.agents import AgentExecutor
    executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

    try:
        # LangGraph passes messages in state["messages"]
        response = await executor.ainvoke({
            "input": state["current_input"],
            "chat_history": state["messages"]
        })

        output = response.get("output", "")
        # AgentExecutor returns intermediate_steps as a list of (AgentAction, Observation)
        tool_calls = response.get("intermediate_steps", [])

        return {
            **state,
            "current_response": output,
            "tool_calls": tool_calls
        }
    except Exception as e:
        logger.error(f"Persona {persona_name} execution error: {e}")
        return {
            **state,
            "current_response": f"Sorry, I encountered an error: {str(e)}",
            "tool_calls": []
        }


async def persona_general(state: BotState) -> BotState:
    """General persona node."""
    return await execute_persona(state, "general")


async def persona_weight(state: BotState) -> BotState:
    """Weight tracking persona node."""
    return await execute_persona(state, "weight")


async def persona_rust(state: BotState) -> BotState:
    """Rust tutoring persona node."""
    return await execute_persona(state, "rust")


async def persona_cpp(state: BotState) -> BotState:
    """C++ tutoring persona node."""
    return await execute_persona(state, "cpp")


async def persona_python(state: BotState) -> BotState:
    """Python tutoring persona node."""
    return await execute_persona(state, "python")


async def persona_todo(state: BotState) -> BotState:
    """Todo management persona node."""
    return await execute_persona(state, "todo")


async def persona_daily_report(state: BotState) -> BotState:
    """Daily report persona node."""
    return await execute_persona(state, "daily_report")


async def response_formatter(state: BotState) -> BotState:
    """Format response for Telegram markdown."""
    raw_response = state["current_response"]

    if not raw_response:
        return {
            **state,
            "current_response": "I'm sorry, I couldn't generate a response."
        }

    try:
        # Format for Telegram markdown
        formatted = telegramify_markdown.markdownify(raw_response)
        return {
            **state,
            "current_response": formatted
        }
    except Exception as e:
        logger.error(f"Markdown formatting error: {e}")
        return state


async def output_handler(state: BotState) -> BotState:
    """Handle output (logging, sending to Telegram)."""
    logger.info(f"Response generated for {state['active_persona']} persona")
    return state

# ============================================================================
# CONDITIONAL EDGES
# ============================================================================

def route_after_classifier(state: BotState) -> str:
    """Route after input classification."""
    if state["input_type"] == "voice":
        return "voice_handler"
    return "persona_router"


def route_after_router(state: BotState) -> str:
    """Route after persona router."""
    old_persona = state.get("_previous_persona")
    new_persona = state["active_persona"]
    
    if old_persona and old_persona != new_persona:
        return "persona_switch"
    
    return new_persona


def route_to_persona(state: BotState) -> str:
    """Route to specific persona node."""
    return state["active_persona"]


# ============================================================================
# GRAPH CONSTRUCTION
# ============================================================================

def create_bot_graph() -> StateGraph:
    """Create and compile the bot state graph."""

    # Create graph
    graph = StateGraph(BotState)

    # Add all nodes
    graph.add_node("input_classifier", input_classifier)
    graph.add_node("voice_handler", voice_handler)
    graph.add_node("persona_router", persona_router)
    graph.add_node("persona_switch", persona_switch)
    graph.add_node("persona_general", persona_general)
    graph.add_node("persona_weight", persona_weight)
    graph.add_node("persona_rust", persona_rust)
    graph.add_node("persona_cpp", persona_cpp)
    graph.add_node("persona_python", persona_python)
    graph.add_node("persona_todo", persona_todo)
    graph.add_node("persona_daily_report", persona_daily_report)
    graph.add_node("response_formatter", response_formatter)
    graph.add_node("output_handler", output_handler)

    # Set entry point
    graph.set_entry_point("input_classifier")

    # Add conditional edges
    graph.add_conditional_edges(
        "input_classifier",
        route_after_classifier,
        {
            "voice_handler": "voice_handler",
            "persona_router": "persona_router"
        }
    )

    # Direct edges from voice_handler
    graph.add_edge("voice_handler", "persona_router")

    # Conditional edges from persona_router to personas or switch
    graph.add_conditional_edges(
        "persona_router",
        route_after_router,
        {
            "persona_switch": "persona_switch",
            "general": "persona_general",
            "weight": "persona_weight",
            "rust": "persona_rust",
            "cpp": "persona_cpp",
            "python": "persona_python",
            "todo": "persona_todo",
            "daily_report": "persona_daily_report"
        }
    )
    
    # Conditional edges from persona_switch to personas
    graph.add_conditional_edges(
        "persona_switch",
        route_to_persona,
        {
            "general": "persona_general",
            "weight": "persona_weight",
            "rust": "persona_rust",
            "cpp": "persona_cpp",
            "python": "persona_python",
            "todo": "persona_todo",
            "daily_report": "persona_daily_report"
        }
    )

    # Direct edges from all personas to response_formatter
    graph.add_edge("persona_general", "response_formatter")
    graph.add_edge("persona_weight", "response_formatter")
    graph.add_edge("persona_rust", "response_formatter")
    graph.add_edge("persona_cpp", "response_formatter")
    graph.add_edge("persona_python", "response_formatter")
    graph.add_edge("persona_todo", "response_formatter")
    graph.add_edge("persona_daily_report", "response_formatter")

    # Direct edge from response_formatter to output_handler
    graph.add_edge("response_formatter", "output_handler")

    # Direct edge from output_handler to END
    graph.add_edge("output_handler", END)

    # Compile graph
    app = graph.compile()

    return app


# ============================================================================
# GRAPH ENTRY POINT
# ============================================================================

# Create and compile the graph
app = create_bot_graph()


# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == "__main__":
    # Test the graph structure
    print("Graph compiled successfully!")
    print(f"Graph nodes: {app.nodes}")
