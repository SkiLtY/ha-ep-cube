FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/mock_server
EXPOSE 8765

CMD ["uvicorn", "mock_server.main:app", "--host", "0.0.0.0", "--port", "8765"]
