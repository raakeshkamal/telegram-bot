#!/bin/bash

# --- Step 0: Aggressive Cleanup ---
echo "🧹 Cleaning up previous instances..."
# Kill by ports
lsof -ti:8000,7860,6006 | xargs kill -9 2>/dev/null || true
# Kill by name (be careful not to kill our current script)
pgrep -f "python.*bot.py" | grep -v $$ | xargs kill -9 2>/dev/null || true
pgrep -f "python.*server.py" | grep -v $$ | xargs kill -9 2>/dev/null || true
pgrep -f "python.*gradio_ui.py" | grep -v $$ | xargs kill -9 2>/dev/null || true
pgrep -f "python.*phoenix.server.main" | grep -v $$ | xargs kill -9 2>/dev/null || true
sleep 1

# Configuration
VENV_DIR=".venv"
MCP_PORT=8000
GRADIO_PORT=7860
PHOENIX_PORT=6006

# --- Helper: Check if a port is in use ---
check_port() {
  lsof -i :"$1" > /dev/null
}

# --- Step 1: Check Environment ---
echo "🚀 Starting local setup..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 is not installed."
    exit 1
fi

# --- Step 2: Setup Virtual Environment ---
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
echo "🛠️ Installing/Updating dependencies..."
# Use uv if available for speed, else pip
if command -v uv &> /dev/null; then
    uv pip install -r mcp-server/requirements.txt -r telegram-bot/requirements.txt
else
    pip install -q -r mcp-server/requirements.txt -r telegram-bot/requirements.txt
fi

# --- Step 3: Ensure MongoDB is running ---
echo "🍃 Checking MongoDB..."
if ! nc -z localhost 27017 &> /dev/null; then
    echo "⚠️ MongoDB is not running on localhost:27017."
    echo "💡 Suggestion: Run 'docker run -d -p 27017:27017 --name mongodb mongo:latest' to start it quickly."
    # We'll continue anyway, but the app might fail if it tries to connect
fi

# --- Step 4: Set Environment Variables for Local Run ---
# We load the existing .env if it exists, then override the Docker service names
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Override service names for local execution
export MONGO_URI="mongodb://localhost:27017/bot_db"
export MCP_SERVER_URL="http://localhost:8000/mcp"
export PHOENIX_COLLECTOR_ENDPOINT="http://localhost:4317"
export PHOENIX_WORKING_DIR="$(pwd)/phoenix_data"

# --- Step 5: Start Components ---

# Function to kill background processes on exit
cleanup() {
    echo ""
    echo "🛑 Shutting down..."
    # Kill the whole process group to ensure all background tasks stop
    kill 0
    exit
}
trap cleanup SIGINT SIGTERM

echo "🔥 Starting Arize Phoenix (Port $PHOENIX_PORT)..."
python -m phoenix.server.main serve > phoenix.log 2>&1 &
PHOENIX_PID=$!

# Wait for Phoenix to be ready
echo "⏳ Waiting for Phoenix to initialize..."
MAX_RETRIES=15
COUNT=0
while ! nc -z localhost $PHOENIX_PORT &> /dev/null; do
    sleep 1
    COUNT=$((COUNT + 1))
    if [ $COUNT -ge $MAX_RETRIES ]; then
        echo "❌ Phoenix failed to start in time. Check phoenix.log for errors."
        kill $PHOENIX_PID
        exit 1
    fi
done
echo "✅ Arize Phoenix is ready at http://localhost:$PHOENIX_PORT."

echo "📡 Starting MCP Server (Port $MCP_PORT)..."
cd mcp-server
python server.py > ../mcp_server.log 2>&1 &
MCP_PID=$!
cd ..

# Wait for MCP server to be ready
echo "⏳ Waiting for MCP server to initialize..."
MAX_RETRIES=10
COUNT=0
while ! nc -z localhost $MCP_PORT &> /dev/null; do
    sleep 1
    COUNT=$((COUNT + 1))
    if [ $COUNT -ge $MAX_RETRIES ]; then
        echo "❌ MCP server failed to start in time. Check mcp_server.log for errors."
        kill $MCP_PID
        exit 1
    fi
done
echo "✅ MCP server is ready."

echo "🤖 Starting Telegram Bot..."
cd telegram-bot
python bot.py > ../telegram_bot.log 2>&1 &
BOT_PID=$!
echo "✅ Telegram Bot started in background (logs in telegram_bot.log)."

echo "🖥️ Starting Web UI (Gradio at http://localhost:$GRADIO_PORT)..."
python gradio_ui.py
