# One container that serves BOTH the website (front end) and the API.
# Build context = this folder (WatermarkRemover/).
FROM python:3.12-slim

# ffmpeg (engine) + libglib2.0-0 (needed by opencv-python-headless on slim images)
# + fonts-dejavu-core (libass caption rendering — the captions task's ASS style
#   names "DejaVu Sans"; without a real font libass silently renders nothing)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 \
        fonts-dejavu-core \
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

# Neural face detector (YuNet, ~230 KB ONNX) — fetched from the official OpenCV
# zoo at build time (kept out of git), then load-tested so a bad download fails
# the BUILD, never production. At runtime the engine falls back to the classical
# Haar cascades if this file is missing (see _yunet() in watermark_remover.py).
RUN mkdir -p /app/models && \
    python -c "import urllib.request; urllib.request.urlretrieve(\
'https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx',\
'/app/models/face_detection_yunet_2023mar.onnx')" && \
    python -c "import cv2; cv2.FaceDetectorYN_create(\
'/app/models/face_detection_yunet_2023mar.onnx','',(320,320)); print('YuNet load OK')"

# Audio denoiser — DeepFilterNet's standalone `deep-filter` Rust CLI (MIT/Apache,
# no torch). --version load-test makes a bad download fail the BUILD. At runtime
# the engine keeps the original audio if this binary is missing or errors
# (clean_audio_track in watermark_remover.py is strictly best-effort).
RUN python -c "import urllib.request; urllib.request.urlretrieve(\
'https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-x86_64-unknown-linux-musl',\
'/usr/local/bin/deep-filter')" && \
    chmod +x /usr/local/bin/deep-filter && \
    /usr/local/bin/deep-filter --version && echo "deep-filter OK"

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
