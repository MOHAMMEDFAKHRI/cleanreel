"""
modal_enhance_app.py — CleanReel's neural ENHANCE microservice (serverless GPU).

What it does
------------
True neural enhance/upscale: Real-ESRGAN (x2plus) restores compression damage and
upscales; GFPGAN v1.4 optionally restores faces on top (its background pass uses
the same Real-ESRGAN model). This replaces the "interpolation + sharpening"
quality pass for the ENHANCE mode — the ffmpeg chain remains the automatic
fallback on the Render box if this service is unreachable (see
watermark_remover.enhance_video).

Deploy (same pattern as gpu/modal_app.py — the LaMa inpaint service)
------
    pip install modal
    modal setup                                   # one-time auth (browser)
    modal deploy gpu/modal_enhance_app.py

`modal deploy` prints the endpoint URL, e.g.
    https://<workspace>--cleanreel-enhance-enhance-enhance.modal.run
Set it on Render as WR_ENHANCE_URL. Auth reuses the SAME shared token as inpaint:
the Modal secret `cleanreel-inpaint` (INPAINT_TOKEN) / Render WR_INPAINT_TOKEN —
no new secret needed.

Wire format (matches watermark_remover._enhance_video_neural)
    POST {url}
    body : {"token": <INPAINT_TOKEN>,
            "scale": 1.0 | 2.0,                  # network output scale
            "face_enhance": true|false,          # GFPGAN face restore on top
            "items": [{"image": <b64 jpg/png bgr>}, ...]}
    resp : {"results": [<b64 jpg bgr>, ...]}     # one frame per item, same order

Notes
-----
* Weights (~530 MB total) are baked into the image at build time so cold starts
  never wait on downloads. Pins matter: basicsr 1.4.2 needs torchvision<0.17.
* tile=512 keeps VRAM bounded for any input size (4K sources included).
* scale=1.0 still runs the network (restoration without enlargement): the model
  computes x2 and the result is resized back — same cost, cleaner pixels.
"""
import os
import base64

import modal

app = modal.App("cleanreel-enhance")

_W = "/root/models"
_FXW = "/root/gfpgan/weights"    # where GFPGAN's facexlib helper looks (cwd=/root)
_BAKE = (
    f"mkdir -p {_W} {_FXW} && python - <<'PY'\n"
    "import urllib.request\n"
    "dl = [\n"
    " ('https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth',\n"
    f"  '{_W}/RealESRGAN_x2plus.pth'),\n"
    " ('https://github.com/TencentARC/GFPGAN/releases/download/v1.3.4/GFPGANv1.4.pth',\n"
    f"  '{_W}/GFPGANv1.4.pth'),\n"
    " ('https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth',\n"
    f"  '{_FXW}/detection_Resnet50_Final.pth'),\n"
    " ('https://github.com/xinntao/facexlib/releases/download/v0.2.2/parsing_parsenet.pth',\n"
    f"  '{_FXW}/parsing_parsenet.pth'),\n"
    "]\n"
    "for u, p in dl:\n"
    "    print('fetch', u); urllib.request.urlretrieve(u, p)\n"
    "import os\n"
    "assert all(os.path.getsize(p) > 1_000_000 for _, p in dl), 'truncated weight file'\n"
    "print('weights baked OK')\n"
    "PY"
)

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("libgl1", "libglib2.0-0")
    # TWO layers on purpose: basicsr's setup.py needs torch ALREADY INSTALLED
    # while its metadata builds (its setup_requires pulls a fresh torch + CUDA
    # packages otherwise, which version-conflict and kill the build).
    .pip_install(
        "torch==2.0.1",           # CUDA build from the default index
        "torchvision==0.15.2",    # <0.17: basicsr imports functional_tensor
        "numpy<2",
        "pillow<10",
        "opencv-python-headless",
        "fastapi[standard]",
    )
    .pip_install(
        "basicsr==1.4.2",
        "facexlib==0.3.0",
        "realesrgan==0.3.0",
        "gfpgan==1.3.8",
    )
    # FINAL pin layer — must come after basicsr: its deps drag numpy to 2.x and
    # Pillow to 12.x, and torch 2.0.1 then dies at runtime with
    # "RuntimeError: Numpy is not available". Last install wins.
    .pip_install("numpy<2", "pillow<10")
    .run_commands(_BAKE)
)

with image.imports():
    import numpy as np
    import cv2


@app.cls(
    gpu="T4",
    image=image,
    scaledown_window=120,          # stay warm 2 min between batches of a job
    secrets=[modal.Secret.from_name("cleanreel-inpaint")],  # same shared token
    timeout=300,                   # roomy for a cold-start batch
)
class Enhance:
    @modal.enter()
    def load(self):
        os.chdir("/root")          # so GFPGAN finds ./gfpgan/weights (baked above)
        from basicsr.archs.rrdbnet_arch import RRDBNet
        from realesrgan import RealESRGANer
        from gfpgan import GFPGANer
        rrdb = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                       num_block=23, num_grow_ch=32, scale=2)
        self.esrgan = RealESRGANer(
            scale=2, model_path=f"{_W}/RealESRGAN_x2plus.pth", model=rrdb,
            tile=512, tile_pad=10, pre_pad=0, half=True)   # tiled: any input size fits VRAM
        self.gfpgan = GFPGANer(
            model_path=f"{_W}/GFPGANv1.4.pth", upscale=2, arch="clean",
            channel_multiplier=2, bg_upsampler=self.esrgan)

    def _one(self, img_b64: str, scale: float, face: bool) -> str:
        img = cv2.imdecode(np.frombuffer(base64.b64decode(img_b64), np.uint8),
                           cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("bad image payload")
        h, w = img.shape[:2]
        if face:
            _, _, out = self.gfpgan.enhance(img, has_aligned=False,
                                            only_center_face=False, paste_back=True)
        else:
            out, _ = self.esrgan.enhance(img, outscale=2)
        if scale < 1.5:            # restoration-only: back to source size
            out = cv2.resize(out, (w, h), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        if not ok:
            raise RuntimeError("jpg encode failed")
        return base64.b64encode(buf).decode()

    @modal.fastapi_endpoint(method="POST")
    def enhance(self, payload: dict):
        from fastapi import HTTPException
        token = os.environ.get("INPAINT_TOKEN", "")
        if not token or (payload or {}).get("token") != token:
            raise HTTPException(status_code=401, detail="unauthorized")
        items = (payload or {}).get("items", [])
        if not isinstance(items, list) or not items:
            raise HTTPException(status_code=400, detail="no items")
        scale = float((payload or {}).get("scale", 2.0) or 2.0)
        face = bool((payload or {}).get("face_enhance", True))
        try:
            results = [self._one(it["image"], scale, face) for it in items]
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"enhance failed: {e}")
        return {"results": results}
