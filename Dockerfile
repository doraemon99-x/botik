FROM python:3.12-slim

# Install ffmpeg dan mediainfo untuk metadata & thumbnail video
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    mediainfo \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependencies dan install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy seluruh source code
COPY . .

# Buat direktori temporary jika belum ada
RUN mkdir -p downloads/bot_temp

# Expose port untuk healthcheck Koyeb
EXPOSE 8000

# Jalankan bot
CMD ["python", "bot.py"]
