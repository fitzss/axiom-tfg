FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for layer caching.
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir .

# Copy source code.
COPY axiom_tfg/ axiom_tfg/
COPY axiom_server/ axiom_server/
COPY examples/ examples/

# Re-install in editable mode so the package is importable.
RUN pip install --no-cache-dir -e .

# Data directory (SQLite + evidence) — mount as a volume.
RUN mkdir -p /app/data/runs

EXPOSE 8000

CMD ["uvicorn", "axiom_server.app:app", "--host", "0.0.0.0", "--port", "8000"]
