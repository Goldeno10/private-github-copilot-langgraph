FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    ripgrep \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY Pipfile Pipfile.lock ./
RUN pip install --no-cache-dir pipenv && \
    pipenv install --system --deploy

COPY . .

ENV PYTHONPATH=/app

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
