# syntax=docker/dockerfile:1
FROM python:3.12-slim
WORKDIR /app
COPY apps/backend/requirements.lock .
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir -r requirements.lock
COPY apps/backend/ .
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "aho.api:app", "--host", "0.0.0.0", "--port", "8000"]
