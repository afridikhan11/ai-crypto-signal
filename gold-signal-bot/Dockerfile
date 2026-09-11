FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY gold_signals/ ./gold_signals/
COPY run.py ./

# Unbuffered, so `docker compose logs` shows what the bot is doing as it
# happens rather than in blocks whenever a buffer fills.
ENV PYTHONUNBUFFERED=1

CMD ["python", "run.py"]
