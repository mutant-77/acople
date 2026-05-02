FROM node:20-slim AS node-base

FROM python:3.12-slim

COPY --from=node-base /usr/local/bin/node /usr/local/bin/
COPY --from=node-base /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY acople ./acople
RUN pip install --no-cache-dir ".[server]"

RUN useradd -m appuser
USER appuser

ENV ACOPLE_AGENT=gemini
EXPOSE 8000
CMD ["uvicorn", "acople.server:app", "--host", "0.0.0.0", "--port", "8000"]