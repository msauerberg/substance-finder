# Use official Python 3.12 slim (Debian) base
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Avoid interactive prompts during package installs
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies needed by pandas, matplotlib, spaCy, spaczz, etc.
# We install rustc & cargo since some tokenizers / wheels may trigger Rust builds.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    libc6-dev \
    libffi-dev \
    libpq-dev \
    libxml2-dev \
    libxslt1-dev \
    libjpeg-dev \
    zlib1g-dev \
    pkg-config \
    ca-certificates \
    curl \
    fonts-dejavu-core \
  && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt /app/requirements.txt

# Upgrade pip / setuptools / wheel and install requirements
RUN python -m pip install --upgrade pip setuptools wheel \
  && pip install --no-cache-dir -r /app/requirements.txt

# Copy app code
COPY . /app

# Ensure matplotlib uses non-interactive backend (app already sets Agg but we set env too)
ENV MPLBACKEND="Agg"

# Expose port used by the app
EXPOSE 8020

# Default command: run with gunicorn (4 workers). Use app:app as Flask instance.
# If you prefer development server (auto-reload), change this to: ["python","app.py"]
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8020", "app:app", "--timeout", "120"]
