FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libglib2.0-0 \
        libgl1 \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

COPY config ./config
COPY models ./models
COPY services ./services

# Automatically create an index.html alias for dashboard.html during build
RUN cp services/operations/static/dashboard.html services/operations/static/index.html

# Serve the static folder directly so the dashboard loads at the root URL
CMD ["sh", "-c", "python -m http.server ${PORT:-10000} --directory services/operations/static"]
