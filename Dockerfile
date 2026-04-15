FROM mcr.microsoft.com/playwright/python:v1.44.0-focal

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY data/ ./data/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# CMD is overridden per Render service:
#   python -m based_inventory.jobs.quantity_alerts
#   python -m based_inventory.jobs.atc_audit
#   python -m based_inventory.jobs.weekly_snapshot
CMD ["python", "-m", "based_inventory.jobs.quantity_alerts"]
