# Use official Playwright Python image (includes Chromium + required OS deps)
FROM mcr.microsoft.com/playwright/python:v1.52.0-jammy

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Google Chrome (bypasses Akamai bot detection that blocks bundled Chromium)
RUN python -m playwright install chrome

# Copy source code
COPY src/ ./src/

# Run the bot
CMD ["python", "src/bot_new.py"]
