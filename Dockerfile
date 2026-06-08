FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md ./
COPY apps ./apps
COPY packages ./packages
COPY config ./config
COPY data ./data
COPY eval ./eval

RUN pip install -U pip && pip install -e .

EXPOSE 8000

CMD ["uvicorn", "apps.gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
