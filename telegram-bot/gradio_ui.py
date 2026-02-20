import gradio as gr
import asyncio
import os
from dotenv import load_dotenv
from agent_logic import personas, initialize_personas, logger

# ... (rest of the imports)


async def chat_response(message, history, persona_name):
    """Handle chat messages through the selected agent persona."""
    # Ensure personas are initialized
    if not personas:
        await initialize_personas()

    if persona_name not in personas:
        persona_name = "general"

    agent_executor = personas[persona_name].executor

    try:
        # LangChain agent executor handle async invoke
        response = await agent_executor.ainvoke({"input": message})
        return response.get("output", "I'm sorry, I couldn't process that.")
    except Exception as e:
        logger.error(f"Error in Gradio chat: {e}")
        return f"Error: {str(e)}"


def predict(message, history, persona_name):
    """Bridge sync Gradio to async chat logic."""
    return asyncio.run(chat_response(message, history, persona_name))


# Create Gradio Interface
with gr.Blocks(title="Telegram Bot Test Interface") as demo:
    gr.Markdown("# Bot Test Interface")
    gr.Markdown("Test the bot's personas and tools without needing Telegram.")

    with gr.Row():
        persona_selector = gr.Dropdown(
            choices=["general", "weight", "rust", "cpp", "python"],
            value="general",
            label="Select Persona",
            interactive=True,
        )

    chatbot = gr.ChatInterface(
        fn=predict,
        additional_inputs=[persona_selector],
        examples=[
            ["What is the weather in London?", "general"],
            ["I weigh 75 kg", "weight"],
            ["Tell me about Rust", "rust"],
            ["Teach me some C++", "cpp"],
            ["How do I use lists in Python?", "python"],
            ["What happened today in history?", "general"],
        ],
        cache_examples=False,
    )

if __name__ == "__main__":
    # Get port from environment or default to 7860
    port = int(os.environ.get("GRADIO_PORT", 7860))
    # Run the app
    demo.launch(server_name="0.0.0.0", server_port=port, theme=gr.themes.Soft())
