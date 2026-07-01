"""
main.py — FastAPI backend for CleanReel (MVP).

Implements the hybrid model from PRODUCT_BRIEF.md:
    * /api/upload                 store a short video the user owns
    * POST /api/jobs (preview)    FREE  — cleans the first few seconds
    * POST /api/jobs (export)     PAID  — full clip; checks length limit + credits
    * /api/jobs/{id}              poll status/progress
    * /api/result/{id}            download the cleaned file
A tiny test UI is served at "/".

Run:
    pip install -r requirements.txt
    uvicorn main:app --reload        (from this backend/ folder)

PROD notes are inline. Credits/auth here are in-memory stubs to demonstrate the
free-vs-paid gate; wire Stripe + real auth before launch.
"""
import os, uuid, shutil
from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import cv2
import numpy as np, base64
from jobs import JobManager, MAX_EXPORT_SECONDS, PREVIEW_SECONDS
import watermark_remover as wr   # jobs.py puts the engine dir on sys.path on import

HERE = os.path.dirname(os.path.abspath(__file__))
STORAGE = os.path.join(HERE, "storage")
UPLOADS = os.path.join(STORAGE, "uploads")
os.makedirs(UPLOADS, exist_ok=True)

MAX_UPLOAD_MB = 200
MAX_UPLOAD_SECONDS = 60          # hard ceiling for uploads (MVP)
FREE_EXPORT_CREDITS = 3          # PROD: per-user, from DB/Stripe

app = FastAPI(title="CleanReel API", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

manager = JobManager(STORAGE)
FILES: dict[str, dict] = {}      # file_id -> {path, w, h, seconds}   (PROD: DB + object storage)
CREDITS: dict[str, int] = {}     # user_id -> remaining export credits (PROD: DB/Stripe)


def _user(x_user: str | None) -> str:
    return x_user or "anon"

def credits_of(uid: str) -> int:
    return CREDITS.setdefault(uid, FREE_EXPORT_CREDITS)


@app.get("/api/health")
def health():
    return {"ok": True, "preview_seconds": PREVIEW_SECONDS,
            "max_export_seconds": MAX_EXPORT_SECONDS}

@app.get("/api/credits")
def get_credits(x_user: str | None = Header(default=None)):
    uid = _user(x_user)
    return {"user": uid, "export_credits": credits_of(uid)}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"):
        raise HTTPException(400, "Unsupported video format.")
    fid = uuid.uuid4().hex
    path = os.path.join(UPLOADS, fid + ext)
    size = 0
    with open(path, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_MB << 20:
                f.close(); os.remove(path)
                raise HTTPException(413, f"File too large (>{MAX_UPLOAD_MB} MB).")
            f.write(chunk)
    cap = cv2.VideoCapture(path)
    ok, _ = cap.read()
    if not ok:
        cap.release(); os.remove(path)
        raise HTTPException(400, "Could not read that video.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    seconds = round(n / max(fps, 1), 1)
    if seconds > MAX_UPLOAD_SECONDS:
        os.remove(path)
        raise HTTPException(413, f"Video too long ({seconds}s). MVP limit is {MAX_UPLOAD_SECONDS}s.")
    FILES[fid] = {"path": path, "w": w, "h": h, "seconds": seconds}
    return {"file_id": fid, "width": w, "height": h, "seconds": seconds}


def _mean_std(meta):
    """Cache the temporal mean + std per file so re-previews don't recompute it."""
    c = meta.get("_ms")
    if c is None:
        c = wr.mean_and_std(meta["path"]); meta["_ms"] = c
    return c


@app.get("/api/reference/{fid}")
def reference(fid: str):
    """A sharp still with the static watermark highlighted — the canvas to mark on."""
    meta = FILES.get(fid)
    if not meta:
        raise HTTPException(404, "Unknown file_id (upload first).")
    cache = meta.get("_ref")
    if cache is None:
        meanf, _ = _mean_std(meta)
        ok, buf = cv2.imencode(".jpg", wr.reference_image(meta["path"], meanf),
                               [cv2.IMWRITE_JPEG_QUALITY, 86])
        cache = buf.tobytes(); meta["_ref"] = cache
    return Response(cache, media_type="image/jpeg")


@app.post("/api/autodetect/{fid}")
def autodetect(fid: str):
    """Run auto-detection and return its mask as a PNG (white = watermark)."""
    meta = FILES.get(fid)
    if not meta:
        raise HTTPException(404, "Unknown file_id (upload first).")
    det = meta.get("_det")
    if det is None:
        det = wr.detect(meta["path"]); meta["_det"] = det
    m = det.get("mask")
    if m is None:
        m = np.zeros((meta["h"], meta["w"]), np.uint8)
    ok, buf = cv2.imencode(".png", (m > 0).astype(np.uint8) * 255)
    return Response(buf.tobytes(), media_type="image/png",
                    headers={"X-Watermark-Type": det.get("type", "none")})


class JobRequest(BaseModel):
    file_id: str
    mode: str = "preview"                 # 'preview' (free) | 'export' (paid)
    owns_rights: bool = False
    boxes: list[list[int]] | None = None  # [[x,y,w,h], ...]
    mask: str | None = None               # base64 PNG (white = remove) from the canvas editor
    auto: bool = True
    upscale: bool = True
    protect: bool = True


@app.post("/api/jobs")
def create_job(req: JobRequest, x_user: str | None = Header(default=None)):
    if not req.owns_rights:
        raise HTTPException(403, "You must confirm you own/have rights to edit this video.")
    meta = FILES.get(req.file_id)
    if not meta:
        raise HTTPException(404, "Unknown file_id (upload first).")
    if req.mode not in ("preview", "export"):
        raise HTTPException(400, "mode must be 'preview' or 'export'.")

    uid = _user(x_user)
    if req.mode == "export":
        if meta["seconds"] > MAX_EXPORT_SECONDS:
            raise HTTPException(413, f"Export limited to {MAX_EXPORT_SECONDS}s in this tier.")
        if credits_of(uid) <= 0:
            raise HTTPException(402, "Out of export credits. Top up to export full video.")
        CREDITS[uid] -= 1            # PROD: charge via Stripe / decrement real balance

    params = {
        "video_path": meta["path"],
        "boxes": [tuple(b) for b in req.boxes] if req.boxes else None,
        "upscale": req.upscale, "protect": req.protect,
    }
    if req.mask:
        raw = base64.b64decode(req.mask.split(",", 1)[-1])     # tolerate data: URL prefix
        mpath = os.path.join(UPLOADS, req.file_id + "_mask.png")
        with open(mpath, "wb") as mf:
            mf.write(raw)
        meanf, std_gray = _mean_std(meta)
        params.update(mask_path=mpath, meanf=meanf, std_gray=std_gray)
    job_id = manager.submit(req.mode, params)
    return {"job_id": job_id, "mode": req.mode}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job.")
    data = job.public()
    if job.status == "done":
        data["result_url"] = f"/api/result/{job_id}"
    return data


@app.get("/api/result/{job_id}")
def result(job_id: str):
    job = manager.get(job_id)
    if not job or job.status != "done" or not job.result_path:
        raise HTTPException(404, "Result not ready.")
    return FileResponse(job.result_path, media_type="video/mp4",
                        filename="cleaned.mp4")


# tiny built-in test UI
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

@app.get("/", response_class=HTMLResponse)
def index():
    p = os.path.join(HERE, "static", "index.html")
    return HTMLResponse(open(p, encoding="utf-8").read())

# SEO: serve robots + sitemap at the site root. Set SITE_URL to your real domain.
SITE_URL = os.environ.get("SITE_URL", "https://cleanreel.app")

@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n"

@app.get("/sitemap.xml")
def sitemap():
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           f'  <url><loc>{SITE_URL}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>\n'
           '</urlset>\n')
    return Response(xml, media_type="application/xml")
