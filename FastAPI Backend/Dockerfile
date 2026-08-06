FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Strip CR from the entrypoint. The script is committed from a Windows
# working tree where it can pick up CRLF endings, and `sh` then fails with
# an unreadable "\r: not found". This is not hypothetical - setup_module1.sh
# in this repository already has CRLF endings. chmod because the executable
# bit does not survive a Windows checkout either.
RUN sed -i 's/\r$//' /app/scripts/docker-entrypoint.sh \
 && chmod +x /app/scripts/docker-entrypoint.sh

# ENTRYPOINT, not CMD, deliberately.
#
# The entrypoint waits for PostgreSQL and runs `alembic upgrade head`
# before exec'ing whatever CMD resolves to. Because a compose file's
# `command:` overrides CMD but NOT ENTRYPOINT, the wait and the migration
# cannot be skipped by overriding the server arguments - which is exactly
# what the previous `CMD ["sh", "-c", "alembic upgrade head && uvicorn ..."]`
# allowed, since both compose files replaced that whole string wholesale.
ENTRYPOINT ["sh", "/app/scripts/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
