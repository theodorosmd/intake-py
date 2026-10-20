FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py schema.sql ./
COPY evals evals
COPY connaissances connaissances
COPY dossiers dossiers
EXPOSE 8000
CMD ["fastapi", "run", "main.py", "--port", "8000"]
