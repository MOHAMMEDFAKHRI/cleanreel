"""
modal_propainter_app.py — CleanReel's TEMPORAL video inpainting microservice.

What it does
------------
Flow-guided temporal video inpainting: instead of filling each frame
independently (LaMa), it propagates real pixels from neighbouring frames along
optical flow and only hallucinates what no frame ever saw. Result: fills that
stay CONSISTENT across frames — no shimmer on moving watermarks/objects. The
engine sends a fixed crop region across a whole chunk of frames; per-frame
LaMa and classical cv2 remain the automatic fallbacks.

NOTE: uses third-party research code/weights; the usage arrangement with the
rights holder is on file (see the private ops manual — keep specifics OUT of
this public repo and out of all user-facing copy). Do not redistribute weights.

Deploy
------
    py -3 -m modal deploy gpu/modal_propainter_app.py   (or deploy_propainter.bat)
Prints the endpoint URL -> set on Render as WR_INPAINT_SEQ_URL.
Auth reuses the shared token: Modal secret `cleanreel-inpaint` / Render
WR_INPAINT_TOKEN. No new secret needed.

Wire format (matches watermark_remover.Inpainter.inpaint_sequence)
    POST {url}
    body : {"token": <INPAINT_TOKEN>,
            "frames": [<b64 jpg bgr>, ...],      # SAME region, consecutive frames
            "masks":  [<b64 png gray 0/255>, ...]}
    resp : {"results": [<b64 jpg bgr>, ...]}     # inpainted, same order & size
"""
import os
import base64

import modal

app = modal.App("cleanreel-propainter")

_W = "/root/ProPainter/weights"
_BAKE = (
    "git clone --depth 1 https://github.com/sczhou/ProPainter /root/ProPainter && "
    f"mkdir -p {_W} && python - <<'PY'\n"
    "import urllib.request\n"
    "base = 'https://github.com/sczhou/ProPainter/releases/download/v0.1.0/'\n"
    "for f in ('ProPainter.pth', 'recurrent_flow_completion.pth', 'raft-things.pth'):\n"
    f"    print('fetch', f); urllib.request.urlretrieve(base + f, '{_W}/' + f)\n"
    "import os\n"
    f"assert all(os.path.getsize('{_W}/' + f) > 1_000_000 for f in\n"
    "            ('ProPainter.pth', 'recurrent_flow_completion.pth', 'raft-things.pth'))\n"
    "print('weights baked OK')\n"
    "PY"
)

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("git", "libgl1", "libglib2.0-0")
    .pip_install(
        "torch==2.0.1",
        "torchvision==0.15.2",
        "numpy<2",
        "opencv-python-headless",
        "einops",
        "scipy",
        "av",
        # the repo's own utility imports (matplotlib bit us at runtime once):
        "matplotlib",
        "imageio",
        "scikit-image",
        "tqdm",
        "addict",
        "timm",
        "fastapi[standard]",
    )
    .run_commands(_BAKE)
    # Import EVERYTHING the service touches at BUILD time — a missing module
    # must fail the deploy, never a customer's job.
    .run_commands(
        "python -c \"import sys; sys.path.insert(0, '/root/ProPainter'); "
        "from model.modules.flow_comp_raft import RAFT_bi; "
        "from model.recurrent_flow_completion import RecurrentFlowCompleteNet; "
        "from model.propainter import InpaintGenerator; "
        "from core.utils import to_tensors; "
        "print('propainter imports OK')\""
    )
)

with image.imports():
    import sys
    sys.path.insert(0, "/root/ProPainter")
    import numpy as np
    import cv2
    import torch
    from model.modules.flow_comp_raft import RAFT_bi
    from model.recurrent_flow_completion import RecurrentFlowCompleteNet
    from model.propainter import InpaintGenerator
    from core.utils import to_tensors
    from PIL import Image


@app.cls(
    gpu="T4",
    image=image,
    scaledown_window=120,
    secrets=[modal.Secret.from_name("cleanreel-inpaint")],
    timeout=300,
)
class ProPainter:
    @modal.enter()
    def load(self):
        self.device = torch.device("cuda")
        self.raft = RAFT_bi(f"{_W}/raft-things.pth", self.device)
        self.flow_model = RecurrentFlowCompleteNet(f"{_W}/recurrent_flow_completion.pth")
        for p in self.flow_model.parameters():
            p.requires_grad = False
        self.flow_model.to(self.device).eval()
        self.model = InpaintGenerator(model_path=f"{_W}/ProPainter.pth").to(self.device).eval()

    @torch.no_grad()
    def _seq(self, frames_np, masks_np):
        """frames_np: list of HxWx3 BGR uint8 (same size); masks_np: HxW 0/255.
        Returns list of inpainted BGR uint8 frames, original size."""
        H0, W0 = frames_np[0].shape[:2]
        # ProPainter wants dims divisible by 8 (flow + transformer strides)
        W8, H8 = max(64, W0 // 8 * 8), max(64, H0 // 8 * 8)
        frames = [Image.fromarray(cv2.cvtColor(cv2.resize(f, (W8, H8)), cv2.COLOR_BGR2RGB))
                  for f in frames_np]
        # binary masks, dilated like the reference script (flow: more, image: less)
        flow_masks, dil_masks = [], []
        for m in masks_np:
            mb = (cv2.resize(m, (W8, H8), interpolation=cv2.INTER_NEAREST) > 127
                  ).astype(np.uint8)
            flow_masks.append(Image.fromarray(
                cv2.dilate(mb, np.ones((3, 3), np.uint8), iterations=8) * 255))
            dil_masks.append(Image.fromarray(
                cv2.dilate(mb, np.ones((3, 3), np.uint8), iterations=4) * 255))
        T = len(frames)
        imgs = to_tensors()(frames).unsqueeze(0) * 2 - 1           # 1,T,3,H,W in [-1,1]
        flow_m = to_tensors()(flow_masks).unsqueeze(0)
        masks_d = to_tensors()(dil_masks).unsqueeze(0)
        imgs, flow_m, masks_d = (x.to(self.device) for x in (imgs, flow_m, masks_d))

        # 1) bidirectional flow + completion inside the masked region
        gt_flows_bi = self.raft(imgs, iters=20)
        pred_flows_bi, _ = self.flow_model.forward_bidirect_flow(gt_flows_bi, flow_m)
        pred_flows_bi = self.flow_model.combine_flow(gt_flows_bi, pred_flows_bi, flow_m)

        # 2) propagate real pixels along the completed flow
        masked_frames = imgs * (1 - masks_d)
        b, t, _, h, w = masks_d.size()
        prop_imgs, updated_local_masks = self.model.img_propagation(
            masked_frames, pred_flows_bi, masks_d, "nearest")
        updated_frames = (imgs * (1 - masks_d)
                          + prop_imgs.view(b, t, 3, h, w) * masks_d)
        updated_masks = updated_local_masks.view(b, t, 1, h, w)

        # 3) transformer fills whatever propagation couldn't (all frames local:
        #    the engine sends short chunks, so no sliding-window machinery)
        pred_img = self.model(updated_frames, pred_flows_bi, masks_d,
                              updated_masks, t)
        pred_img = pred_img.view(-1, 3, h, w)
        comp = (pred_img * masks_d.view(-1, 1, h, w)
                + imgs.view(-1, 3, h, w) * (1 - masks_d.view(-1, 1, h, w)))
        comp = ((comp + 1) / 2).clamp(0, 1).cpu().permute(0, 2, 3, 1).numpy() * 255

        out = []
        for i in range(T):
            fr = cv2.cvtColor(comp[i].astype(np.uint8), cv2.COLOR_RGB2BGR)
            if (W8, H8) != (W0, H0):
                fr = cv2.resize(fr, (W0, H0), interpolation=cv2.INTER_LINEAR)
            # outside the (dilated, original-size) mask keep ORIGINAL pixels —
            # resizing round-trips must never soften untouched areas
            mb = (cv2.dilate((masks_np[i] > 127).astype(np.uint8),
                             np.ones((3, 3), np.uint8), iterations=5) > 0)
            res = frames_np[i].copy()
            res[mb] = fr[mb]
            out.append(res)
        return out

    @modal.fastapi_endpoint(method="POST")
    def inpaint(self, payload: dict):
        from fastapi import HTTPException
        token = os.environ.get("INPAINT_TOKEN", "")
        if not token or (payload or {}).get("token") != token:
            raise HTTPException(status_code=401, detail="unauthorized")
        fr_b64 = (payload or {}).get("frames", [])
        mk_b64 = (payload or {}).get("masks", [])
        if not fr_b64 or len(fr_b64) != len(mk_b64):
            raise HTTPException(status_code=400, detail="frames/masks mismatch")
        if len(fr_b64) > 48:
            raise HTTPException(status_code=400, detail="chunk too long (max 48)")
        try:
            frames, masks = [], []
            for fb, mb in zip(fr_b64, mk_b64):
                f = cv2.imdecode(np.frombuffer(base64.b64decode(fb), np.uint8),
                                 cv2.IMREAD_COLOR)
                m = cv2.imdecode(np.frombuffer(base64.b64decode(mb), np.uint8),
                                 cv2.IMREAD_GRAYSCALE)
                if f is None or m is None:
                    raise ValueError("bad frame/mask payload")
                if m.shape[:2] != f.shape[:2]:
                    m = cv2.resize(m, (f.shape[1], f.shape[0]),
                                   interpolation=cv2.INTER_NEAREST)
                frames.append(f); masks.append(m)
            if len({f.shape for f in frames}) != 1:
                raise ValueError("all frames in a chunk must share one size")
            results = self._seq(frames, masks)
            outs = []
            for r in results:
                ok, buf = cv2.imencode(".jpg", r, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                if not ok:
                    raise RuntimeError("jpg encode failed")
                outs.append(base64.b64encode(buf).decode())
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"propainter failed: {e}")
        return {"results": outs}
