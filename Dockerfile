FROM python:3.12-slim-bookworm

# Java runtime for Vineflower and tiny-remapper.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py cleanup.py ./
# Download Vineflower, tiny-remapper and Yarn at build time so startup is fast.
RUN python -c "import bot; bot.ensure_tools()"

ENV PYTHONUNBUFFERED=1
CMD ["python", "bot.py"]
