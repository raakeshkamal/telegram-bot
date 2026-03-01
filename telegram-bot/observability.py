"""
Graph visualization and observability utilities.
"""

import logging
from graph import app
from datetime import datetime

# Configure logging
logger = logging.getLogger(__name__)

def visualize_graph():
    """Generate and display graph visualization."""
    try:
        # Generate Mermaid diagram
        mermaid_code = app.get_graph().draw_mermaid()

        # Save to file
        with open("graph_diagram.mmd", "w") as f:
            f.write(mermaid_code)

        logger.info("Graph diagram saved to graph_diagram.mmd")
        return mermaid_code
    except Exception as e:
        logger.error(f"Visualization error: {e}")
        return None


async def get_user_state_info(user_id: str) -> str:
    """Get formatted user state information for debugging."""
    from graph import load_user_state
    
    state = await load_user_state(user_id)
    
    if not state:
        return "No state found for user."
        
    last_interaction = state.get('last_interaction', datetime.now())
    if isinstance(last_interaction, datetime):
        last_str = last_interaction.strftime('%Y-%m-%d %H:%M:%S')
    else:
        last_str = str(last_interaction)

    info = f"""📊 *Current State*

*User ID:* `{user_id}`
*Active Persona:* `{state.get('active_persona', 'unknown')}`
*Last Interaction:* {last_str}
*Messages:* {len(state.get('messages', []))}
*Tool Calls:* {len(state.get('tool_calls', []))}
"""
    
    return info
