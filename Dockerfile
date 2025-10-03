# Use Python 3.11+ slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY .env .env

# Create database directory
RUN mkdir -p /app/data

# Set environment variable for database path
ENV DB_PATH=/app/data/guild_settings.sqlite3

# Run the bot
CMD ["python", "src/bot_new.py"]

