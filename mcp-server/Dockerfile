FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install uv && uv pip install --system --no-cache -r requirements.txt

COPY server.py .
# Create data directory and copy all curricula
RUN mkdir -p data
COPY data/*.json data/

CMD ["python", "server.py"]
