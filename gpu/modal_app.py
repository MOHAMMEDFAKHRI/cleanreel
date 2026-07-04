"""
modal_app.py — CleanReel's neural inpainting microservice, on a serverless GPU.

Why this exists
---------------
LaMa (big-lama) is the quality engine behind erase / watermark-remove. It needs a
CUDA GPU: on the CPU-only Render box the TorchScript model dispatches an op to the
CUDA backend and can't run ("aten::empty_strided ... CUDA backend"), and even if it
could, CPU inference is far too slow for exports. So the heavy inpaint lives here on
a warm Modal GPU, and the Render engine calls it per ROI-crop over HTTPS. If this
service is unreachable, the Render engine falls back to the classical inpaint, so
the product never hard-fails.

Deploy
------
    pip install modal
    modal setup                                   # one-time auth (browser)
    modal secret create cleanreel-inpaint INPAINT_TOKEN=<a-long-random-string>
    modal deploy gpu/modal_app.py

`modal deploy` prints the endpoint URL, e.g.
    https://<workspace>--cleanreel-lama-lama-inpaint.modal.run
Set that (and the SAME token) on Render as WR_INPAINT_URL / WR_INPAINT_TOKEN.

Wire format (matches watermark_remover.Inpainter._remote)
    POST {url}
    body : {"token": <INPAINT_TOKEN>,
            "items": [{"image": <b64 png bgr>, "mask": <b64 png gray 0/255>}, ...]}
    resp : {"results":[<b64 png bgr>, ...]}   # one inpainted crop per item, same order
The token travels in the JSON body (over HTTPS) rather than a header, so `modal
deploy` needs only the `modal` package locally — no FastAPI types in the signature.
"""
import os
import base64

import modal

app = modal.App("cleanreel-lama")

# big-lama weights (~196 MB) baked into the image at build time so cold starts never
# wait on a download. torch.hub's cache dir for root is /root/.cache/torch/hub, and
# simple-lama looks in <hub>/checkpoints/big-lama.pt — write it exactly there.
_BAKE_WEIGHTS = (
    "mkdir -p /root/.cache/torch/hub/checkpoints && "
    "python -c \"import urllib.request; urllib.request.urlretrieve("
    "'https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt',"
    "'/root/.cache/torch/hub/checkpoints/big-lama.pt')\" && "
    "test -s /root/.cache/torch/hub/checkpoints/big-lama.pt"
)

# Default-index torch == the CUDA build (what we want on a GPU). Pins match the pair
# that loads big-lama cleanly; numpy<2 / pillow<10 keep simple-lama happy.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0")  # cv2 (full opencv, pulled in by simple-lama) needs libGL
    .pip_install(
        "torch==2.2.2",
        "torchvision==0.17.2",
        "simple-lama-inpainting==0.1.2",
        "pillow<10",
        "numpy<2",
        "opencv-python-headless",
        "fastapi[standard]",
    )
    .run_commands(_BAKE_WEIGHTS)
)

with image.imports():
    import numpy as np
    import cv2
    from PIL import Image


@app.cls(
    gpu="T4",                       # plenty for LaMa; cheapest GPU tier
    image=image,
    scaledown_window=120,           # stay warm 2 min after the last call (fewer cold starts mid-job)
    secrets=[modal.Secret.from_name("cleanreel-inpaint")],
    timeout=120,
)
class Lama:
    @modal.enter()
    def load(self):
        # SimpleLama picks CUDA automatically when a GPU is present (it is, here),
        # so the model runs natively — no empty_strided/CPU issue.
        from simple_lama_inpainting import SimpleLama
        self.model = SimpleLama()

    def _one(self, img_b64: str, mask_b64: str) -> str:
        img = cv2.imdecode(np.frombuffer(base64.b64decode(img_b64), np.uint8), cv2.IMREAD_COLOR)
        msk = cv2.imdecode(np.frombuffer(base64.b64decode(mask_b64), np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None or msk is None:
            raise ValueError("bad image/mask payload")
        if msk.shape[:2] != img.shape[:2]:
            msk = cv2.resize(msk, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
        rgb = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        m = Image.fromarray(((msk > 127).astype("uint8") * 255))
        res = np.array(self.model(rgb, m))                       # RGB, may be padded to /8
        out = cv2.cvtColor(res, cv2.COLOR_RGB2BGR)[: img.shape[0], : img.shape[1]]
        ok, buf = cv2.imencode(".png", out)
        if not ok:
            raise RuntimeError("png encode failed")
        return base64.b64encode(buf).decode()

    @modal.fastapi_endpoint(method="POST")
    def inpaint(self, payload: dict):
        # fastapi is only imported here (runs in-container), so `modal deploy`
        # needs nothing but the `modal` package locally.
        from fastapi import HTTPException
        token = os.environ.get("INPAINT_TOKEN", "")
        if not token or (payload or {}).get("token") != token:
            raise HTTPException(status_code=401, detail="unauthorized")
        items = (payload or {}).get("items", [])
        if not isinstance(items, list) or not items:
            raise HTTPException(status_code=400, detail="no items")
        try:
            results = [self._one(it["image"], it["mask"]) for it in items]
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"inpaint failed: {e}")
        return {"results": results}
