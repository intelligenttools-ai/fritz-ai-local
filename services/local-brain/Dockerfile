FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOCAL_BRAIN_SKILLS_DIR=/app/skills

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY services/local-brain/pyproject.toml /app/pyproject.toml
COPY services/local-brain/fritz_local_brain /app/fritz_local_brain
COPY skills /app/skills
COPY VERSION /app/VERSION

RUN pip install --no-cache-dir .

EXPOSE 8765

CMD ["fritz-local-brain"]
