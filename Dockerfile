# ── Base image ──────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Prevents .pyc files, ensures stdout/stderr are unbuffered
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ── System deps ──────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download the blank German spaCy model used by the matcher.
# 'de_core_news_sm' is not needed — we only use spacy.blank("de") —
# but spaCy requires at least one model to be importable in some versions.
# The blank model is always available; no download needed.

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# Results directory (CSV outputs)
RUN mkdir -p /app/results

# ── Expose & run ──────────────────────────────────────────────────────────────
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
