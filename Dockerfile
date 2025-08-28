FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt ./

RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY data/ ./data/
COPY backend/static/ ./backend/static/
COPY backend/templates/ ./backend/templates/

EXPOSE 5000

CMD ["python", "backend/app.py"]
