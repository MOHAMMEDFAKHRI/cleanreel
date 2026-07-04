# One container that serves BOTH the website (front end) and the API.
# Build context = this folder (WatermarkRemover/).
FROM python:3.12-slim

# ffmpeg (engine) + libglib2.0-0 (needed by opencv-python-headless on slim images)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (better layer caching). Light build — NO torch here. The
# neural inpaint (LaMa) runs off-box on the Modal GPU service (see gpu/modal_app.py);
# this image only needs the classical fallback + the thin HTTPS client (requests).
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# App code
COPY watermark_remover.py /app/watermark_remover.py
COPY backend /app/backend

# Inpaint backend selection (watermark_remover.Inpainter):
#   * Set WR_INPAINT_URL (+ WR_INPAINT_TOKEN) -> neural LaMa on the Modal GPU.
#   * Otherwise this 'classical' default keeps the fast OpenCV fallback on-box.
ENV WR_ENGINE=classical
# Set your real domain so robots/sitemap/SEO are correct:
# ENV SITE_URL=https://your-domain.com

WORKDIR /app/backend
EXPOSE 8000
# Shell form so $PORT (injected by Render/Railway/Fly) is honored; defaults to 8000 locally.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
