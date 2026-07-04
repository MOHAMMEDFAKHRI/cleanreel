# One container that serves BOTH the website (front end) and the API.
# Build context = this folder (WatermarkRemover/).
FROM python:3.12-slim

# ffmpeg (engine) + libglib2.0-0 (needed by opencv-python-headless on slim images)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (better layer caching).
# CPU-only torch (much smaller than the default CUDA build) so LaMa fits a 2 GB box.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# ---- LaMa weights: baked in at BUILD time so runtime never downloads --------
# simple-lama-inpainting fetches big-lama.pt (~200 MB) from GitHub releases the
# first time SimpleLama() is constructed, caching it under
# <torch hub dir>/checkpoints (torch hub dir = $TORCH_HOME/hub). At runtime on
# Render that download silently failed, so LaMa fell back to classical.
# TORCH_HOME must be an ENV (not RUN-local) so the runtime process resolves the
# same cache dir the build wrote to.
ENV TORCH_HOME=/app/.cache/torch
RUN python -c "from simple_lama_inpainting import SimpleLama; SimpleLama()" \
    && test -f /app/.cache/torch/hub/checkpoints/big-lama.pt
# Point the package's own LAMA_MODEL env var straight at the baked file: runtime
# then never attempts a network download and a missing file fails loudly.
# (Must be set AFTER the pre-warm RUN above — if set before, SimpleLama() would
# raise FileNotFoundError at build instead of downloading.)
ENV LAMA_MODEL=/app/.cache/torch/hub/checkpoints/big-lama.pt
# ------------------------------------------------------------------------------

# App code
COPY watermark_remover.py /app/watermark_remover.py
COPY backend /app/backend

# Neural (LaMa) ships in the image but stays OFF until you set WR_ENGINE=auto on a
# 2 GB+ instance. Default 'classical' is memory-safe on any tier.
ENV WR_ENGINE=classical
# Set your real domain so robots/sitemap/SEO are correct:
# ENV SITE_URL=https://your-domain.com

WORKDIR /app/backend
EXPOSE 8000
# Shell form so $PORT (injected by Render/Railway/Fly) is honored; defaults to 8000 locally.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
