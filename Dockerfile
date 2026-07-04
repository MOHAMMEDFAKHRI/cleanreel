# One container that serves BOTH the website (front end) and the API.
# Build context = this folder (WatermarkRemover/).
FROM python:3.12-slim

# ffmpeg (engine) + libglib2.0-0 (needed by opencv-python-headless on slim images)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (better layer caching).
# CPU-only torch + torchvision, matched versions from the SAME index.
# simple-lama-inpainting REQUIRES torchvision (>=0.14.1); if it's pulled from the
# default PyPI index (via requirements.txt) instead, its build mismatches this
# CPU torch and `import torchvision` crashes -> LaMa was silently falling back.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# ---- LaMa weights baked in at BUILD time so runtime never downloads ----------
# Download big-lama.pt (~196 MB) directly with urllib — no torch import in this
# step, so a torch/torchvision problem can't be confused with a download problem.
# TORCH_HOME is an ENV (not RUN-local) so runtime resolves the same cache dir;
# LAMA_MODEL then points the package straight at the file (no runtime fetch).
ENV TORCH_HOME=/app/.cache/torch
RUN mkdir -p /app/.cache/torch/hub/checkpoints \
 && python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt','/app/.cache/torch/hub/checkpoints/big-lama.pt')" \
 && test -s /app/.cache/torch/hub/checkpoints/big-lama.pt \
 && python -c "import torch, torchvision, simple_lama_inpainting; print('[build] LaMa import OK', torch.__version__, torchvision.__version__)"
# Point the package's own LAMA_MODEL env var straight at the baked file so the
# runtime never attempts a network download (and a missing file fails loudly).
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
