FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for matplotlib (if needed) and chromium for kaleido
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6-dev \
    libpng-dev \
    chromium \
    && rm -rf /var/lib/apt/lists/*

# Set local browser path for kaleido if needed, or rely on PATH
ENV KALEIDO_CHROMIUM_PATH=/usr/bin/chromium

COPY requirements.txt .
RUN pip install uv && uv pip install --system --no-cache -r requirements.txt

COPY . .

EXPOSE 7860

CMD ["python", "bot.py"]
