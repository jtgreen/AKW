FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app code (ask_api.py, config.yaml, etc.)
COPY . .

# Expose the API port
EXPOSE 4000

# Run the FastAPI app
CMD ["uvicorn", "ask_api:app", "--host", "0.0.0.0", "--port", "4000"]
