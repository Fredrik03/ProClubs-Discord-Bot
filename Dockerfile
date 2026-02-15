# Use official Playwright Python image (includes Chromium + required OS deps)
FROM mcr.microsoft.com/playwright/python:v1.52.0-jammy

# Install Google Chrome (bypasses Akamai bot detection that blocks bundled Chromium)
RUN npx playwright install chrome

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Run the bot
CMD ["python", "src/bot_new.py"]
