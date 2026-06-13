# Acople HTTP server image.
#
# NOTE: This image does NOT include any CLI agent (claude, gemini, codex, etc.).
# The agent must be present in the host environment or in a derived image.
# To add claude, for example, build from this image and run:
#   RUN npm install -g @anthropic-ai/claude-code
# then mount or bake in credentials as needed.

FROM python:3.10-slim

WORKDIR /app

COPY pyproject.toml .
COPY acople ./acople/

RUN pip install --no-cache-dir ".[server]"

EXPOSE 47334

CMD ["uvicorn", "acople.server:app", "--host", "0.0.0.0", "--port", "47334"]
