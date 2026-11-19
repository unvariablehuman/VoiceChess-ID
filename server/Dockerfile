# VoiceChess-ID server ASR (untuk Hugging Face Spaces / Render / Railway / Cloud Run).
# Build dari folder Project:   docker build -f server/Dockerfile -t voicechess-server .
# Jalankan:                    docker run -p 7860:7860 voicechess-server
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

# Unduh model saat build, supaya server tidak mengunduh 460 MB setiap kali menyala
ENV HF_HOME=/code/.cache
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"

COPY app/ app/
COPY server/ server/
RUN mkdir -p logs && chmod -R 777 /code

ENV PORT=7860
EXPOSE 7860
CMD ["python", "server/server.py"]
