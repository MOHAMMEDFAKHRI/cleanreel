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
import os, uuid, shutil, time, threading
from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import cv2
import numpy as np, base64
from jobs import JobManager, MAX_EXPORT_SECONDS, PREVIEW_SECONDS
import watermark_remover as wr   # jobs.py puts the engine dir on sys.path on import
import accounts                  # users, magic-link auth, credit balances, packs
try:
    import stripe
except Exception:
    stripe = None

HERE = os.path.dirname(os.path.abspath(__file__))
STORAGE = os.path.join(HERE, "storage")
UPLOADS = os.path.join(STORAGE, "uploads")
os.makedirs(UPLOADS, exist_ok=True)

MAX_UPLOAD_MB = 200
MAX_UPLOAD_SECONDS = MAX_EXPORT_SECONDS   # aligned: if it uploads, it can be exported

STRIPE_SECRET = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
if stripe and STRIPE_SECRET:
    stripe.api_key = STRIPE_SECRET

app = FastAPI(title="CleanReel API", version="0.2")
# CORS is locked to the public site. For local dev, add your dev origin here
# (e.g. "http://localhost:8000" / "http://127.0.0.1:5500") — do NOT ship "*".
app.add_middleware(CORSMiddleware,
                   allow_origins=["https://cleanreel.app", "https://www.cleanreel.app"],
                   allow_methods=["*"], allow_headers=["*"])
accounts.init_db()

manager = JobManager(STORAGE)
FILES: dict[str, dict] = {}      # file_id -> {path, w, h, seconds}

# --------------------------------------------------------------------------- #
# Abuse guards — tiny, in-memory, single-process (matches the one-box deploy).
# If you ever scale past one instance, swap this for Redis.
# --------------------------------------------------------------------------- #
MAX_QUEUE_DEPTH      = int(os.environ.get("MAX_QUEUE_DEPTH", "6"))       # jobs queued+running
RATE_AUTH_PER_HOUR   = int(os.environ.get("RATE_AUTH_PER_HOUR", "5"))    # protects the Resend quota
RATE_UPLOAD_PER_HOUR = int(os.environ.get("RATE_UPLOAD_PER_HOUR", "20"))
RATE_JOBS_PER_HOUR   = int(os.environ.get("RATE_JOBS_PER_HOUR", "40"))

_rl_hits: dict[tuple, list[float]] = {}   # (scope, ip) -> recent request timestamps
_rl_lock = threading.Lock()

def _client_ip(request: Request | None) -> str:
    """Client identity for rate limiting. Render fronts us with a proxy, so the
    first X-Forwarded-For hop is the real client; the socket peer is the
    fallback for local/dev runs."""
    if request is None:
        return "?"
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"

def rate_limit(request: Request | None, scope: str, limit: int, window: float = 3600.0):
    """Sliding-window limiter: allow at most `limit` hits per `window` seconds
    per client IP for this scope; raise a friendly 429 past that."""
    now = time.time()
    key = (scope, _client_ip(request))
    with _rl_lock:
        hits = [t for t in _rl_hits.get(key, []) if now - t < window]
        if len(hits) >= limit:
            _rl_hits[key] = hits
            raise HTTPException(429, "You're doing that a bit too often — "
                                     "please wait a while and try again.")
        hits.append(now)
        _rl_hits[key] = hits
        if len(_rl_hits) > 20000:         # opportunistic GC: drop idle clients
            for k in [k for k, v in _rl_hits.items() if not v or now - v[-1] >= window]:
                _rl_hits.pop(k, None)


def current_user(authorization: str | None) -> str | None:
    """Email of the signed-in user from the Bearer session token, or None."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return accounts.verify_token(authorization.split(" ", 1)[1], "session")


@app.get("/api/health")
def health():
    return {"ok": True, "preview_seconds": PREVIEW_SECONDS,
            "max_export_seconds": MAX_EXPORT_SECONDS}

class EmailReq(BaseModel):
    email: str

class TokenReq(BaseModel):
    token: str

class CheckoutReq(BaseModel):
    pack: str

@app.post("/api/auth/request")
def auth_request(req: EmailReq, request: Request):
    """Email the user a one-click magic-link to sign in."""
    rate_limit(request, "auth", RATE_AUTH_PER_HOUR)   # protects the Resend email quota
    email = (req.email or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1] or len(email) > 200:
        raise HTTPException(400, "Please enter a valid email address.")
    link = accounts.send_magic_link(email)          # returns link only if email unconfigured
    return {"sent": True, **({"dev_link": link} if link else {})}

@app.post("/api/auth/verify")
def auth_verify(req: TokenReq):
    """Exchange a magic-link token for a 30-day session token."""
    email = accounts.verify_token(req.token, "login")
    if not email:
        raise HTTPException(400, "This sign-in link is invalid or has expired.")
    credits = accounts.ensure_user(email)
    session = accounts.sign_token(email, "session", ttl=60 * 60 * 24 * 30)
    return {"email": email, "credits": credits, "session": session}

@app.get("/api/me")
def me(authorization: str | None = Header(default=None)):
    email = current_user(authorization)
    if not email:
        raise HTTPException(401, "Not signed in.")
    return {"email": email, "credits": accounts.get_credits(email)}

@app.get("/api/packs")
def packs():
    return {"packs": accounts.PACKS, "configured": bool(stripe and STRIPE_SECRET)}

@app.post("/api/checkout")
def checkout(req: CheckoutReq, authorization: str | None = Header(default=None)):
    email = current_user(authorization)
    if not email:
        raise HTTPException(401, "Sign in first.")
    if not (stripe and STRIPE_SECRET):
        raise HTTPException(503, "Payments aren't set up yet — check back soon.")
    pack = accounts.PACKS.get(req.pack)
    if not pack:
        raise HTTPException(400, "Unknown pack.")
    s = stripe.checkout.Session.create(
        mode="payment", customer_email=email, client_reference_id=email,
        line_items=[{"quantity": 1, "price_data": {"currency": "usd",
            "unit_amount": pack["amount"],
            "product_data": {"name": f"CleanReel — {pack['label']}"}}}],
        metadata={"email": email, "credits": str(pack["credits"])},
        success_url=f"{accounts.SITE_URL}/#paid",
        cancel_url=f"{accounts.SITE_URL}/#tool")
    return {"url": s.url}

@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    if not (stripe and STRIPE_SECRET and STRIPE_WEBHOOK_SECRET):
        raise HTTPException(503, "Webhook not configured.")
    import json
    payload = await request.body()
    try:
        stripe.Webhook.construct_event(
            payload, request.headers.get("stripe-signature", ""), STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(400, "Invalid signature.")
    # Parse the (verified) raw payload as a plain dict — avoids StripeObject
    # attribute quirks (e.g. AttributeError on .get across library versions).
    try:
        event = json.loads(payload)
    except Exception:
        raise HTTPException(400, "Bad payload.")
    if event.get("type") == "checkout.session.completed":
        obj = (event.get("data") or {}).get("object") or {}
        md = obj.get("metadata") or {}
        email = md.get("email") or obj.get("customer_email")
        credits = int(md.get("credits", 0) or 0)
        # Consume the idempotency guard only when we actually have work to do, so a
        # transient error elsewhere can't "poison" the event and lose paid credits.
        if email and credits and accounts.event_is_new(event.get("id", "")):
            accounts.add_credits(email, credits)
    return {"received": True}


@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    rate_limit(request, "upload", RATE_UPLOAD_PER_HOUR)
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
        raise HTTPException(413, f"That clip is {seconds:.0f}s — the limit is {MAX_UPLOAD_SECONDS}s per video in this tier. Trim it and try again.")
    FILES[fid] = {"path": path, "w": w, "h": h, "seconds": seconds, "fps": fps}
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


@app.get("/api/frame/{fid}")
def frame(fid: str):
    """A clean sharp still with NO highlight — the canvas for erase/reframe modes.
    Also remembers the frame's time so tracked erases template-match the exact
    frame the user brushed on."""
    meta = FILES.get(fid)
    if not meta:
        raise HTTPException(404, "Unknown file_id (upload first).")
    cache = meta.get("_frame")
    if cache is None:
        f, idx = wr.sharpest_frame(meta["path"], with_index=True)
        if f is None:
            raise HTTPException(400, "Could not read a frame from that video.")
        ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 86])
        cache = buf.tobytes()
        meta["_frame"] = cache
        meta["_sharp_t"] = idx / max(meta.get("fps") or 24.0, 1e-6)
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


@app.post("/api/detect_regions/{fid}")
def detect_regions(fid: str, targets: str = "face"):
    """Privacy-blur helper: run the face/plate detectors on the SAME still the
    editor canvas shows and return the boxes, so the user can preview what
    will be blurred. targets = comma list from {'face', 'plate'}."""
    meta = FILES.get(fid)
    if not meta:
        raise HTTPException(404, "Unknown file_id (upload first).")
    tgs = [t.strip().lower() for t in (targets or "").split(",") if t.strip()]
    if not tgs or any(t not in ("face", "plate") for t in tgs):
        raise HTTPException(400, "targets must be a comma list of 'face' / 'plate'.")
    cache = meta.get("_frame")
    if cache is not None:                       # reuse the canvas still if cached
        f = cv2.imdecode(np.frombuffer(cache, np.uint8), cv2.IMREAD_COLOR)
    else:
        f, idx = wr.sharpest_frame(meta["path"], with_index=True)
        if f is not None:
            meta["_sharp_t"] = idx / max(meta.get("fps") or 24.0, 1e-6)
    if f is None:
        raise HTTPException(400, "Could not read a frame from that video.")
    boxes = [[int(round(v)) for v in b] for b in wr.detect_privacy_boxes(f, tgs)]
    return {"boxes": boxes, "targets": tgs}


class JobRequest(BaseModel):
    file_id: str
    mode: str = "preview"                 # 'preview' (free) | 'export' (paid)
    task: str = "remove"                  # remove | erase | enhance | reframe | blur
    owns_rights: bool = False
    boxes: list[list[int]] | None = None  # [[x,y,w,h], ...]
    mask: str | None = None               # base64 PNG (white = remove) from the canvas editor
    auto: bool = True
    upscale: bool = True
    protect: bool = True
    track: bool = False                   # erase/blur: follow a moving marked region
    scale: float = 1.0                    # enhance: 1.0 | 2.0
    denoise: bool = True                  # enhance: hqdn3d + deblock
    strength: float = 0.6                 # enhance: sharpen 0..1 | blur: coarseness 0..1
    ratio: str = "9:16"                   # reframe: 9:16 | 1:1 | 4:5 ...
    fit: str = "crop"                     # reframe: crop | blur
    focus: list[float] | None = None      # reframe: [x, y] normalized 0..1 pins the crop center
    targets: list[str] | None = None      # blur: subset of {'face', 'plate'}
    style: str | None = None              # blur: 'blur' | 'pixelate'


@app.post("/api/jobs")
def create_job(req: JobRequest, request: Request,
               authorization: str | None = Header(default=None)):
    rate_limit(request, "jobs", RATE_JOBS_PER_HOUR)
    if not req.owns_rights:
        raise HTTPException(403, "You must confirm you own/have rights to edit this video.")
    meta = FILES.get(req.file_id)
    if not meta:
        raise HTTPException(404, "Unknown file_id (upload first).")
    if req.mode not in ("preview", "export"):
        raise HTTPException(400, "mode must be 'preview' or 'export'.")
    task = (req.task or "remove").lower()
    if task not in ("remove", "erase", "enhance", "reframe", "blur"):
        raise HTTPException(400, "task must be remove | erase | enhance | reframe | blur.")
    if task == "erase" and not (req.mask or req.boxes):
        raise HTTPException(400, "Brush over what you want erased first.")
    if task == "blur":
        blur_targets = [str(t).lower() for t in (req.targets or [])]
        if any(t not in ("face", "plate") for t in blur_targets):
            raise HTTPException(400, "targets may only include 'face' and/or 'plate'.")
        blur_style = (req.style or "blur").lower()
        if blur_style not in ("blur", "pixelate"):
            raise HTTPException(400, "style must be 'blur' or 'pixelate'.")
        if not blur_targets and not (req.mask or req.boxes):
            raise HTTPException(400, "Pick faces/plates to blur — or brush a region first.")
    if task == "reframe":
        try:
            wr.parse_ratio(req.ratio)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if req.fit not in ("crop", "blur"):
            raise HTTPException(400, "fit must be 'crop' or 'blur'.")
        if req.focus is not None and len(req.focus) != 2:
            raise HTTPException(400, "focus must be [x, y] with 0..1 values.")

    # Backpressure: one worker renders one job at a time, so a deep queue means
    # a silent multi-hour wait. Refuse early instead — and BEFORE any credit is
    # deducted, so a rejected request never costs the customer anything.
    if manager.pending() >= MAX_QUEUE_DEPTH:
        raise HTTPException(429, "We're at capacity right now — please try again "
                                 "in a few minutes.")

    refund_email = None
    if req.mode == "export":                 # preview stays free & anonymous
        if meta["seconds"] > MAX_EXPORT_SECONDS:
            raise HTTPException(413, f"Export limited to {MAX_EXPORT_SECONDS}s in this tier.")
        email = current_user(authorization)
        if not email:
            raise HTTPException(401, "Please sign in to export.")
        if not accounts.use_credit(email):
            raise HTTPException(402, "Out of export credits. Buy a pack to export the full video.")
        refund_email = email                 # paid: worker refunds this credit on failure

    params = {
        "video_path": meta["path"], "task": task,
        "boxes": [tuple(b) for b in req.boxes] if req.boxes else None,
        "upscale": req.upscale, "protect": req.protect,
    }
    if refund_email:
        # If the render fails, jobs.JobManager._worker returns this credit.
        params["refund_on_fail"] = refund_email
    if task in ("remove", "erase", "blur") and req.mask:
        raw = base64.b64decode(req.mask.split(",", 1)[-1])     # tolerate data: URL prefix
        mpath = os.path.join(UPLOADS, f"{req.file_id}_{uuid.uuid4().hex[:8]}_mask.png")
        with open(mpath, "wb") as mf:
            mf.write(raw)
        params["mask_path"] = mpath
        if task != "blur" and not (task == "erase" and req.track):
            # remove — and now the static erase too — uses the (cached) temporal
            # mean/std so the engine can tell semi-transparent from opaque and
            # pick reverse-blend vs straight inpaint. Tracked erases stay opaque;
            # blur needs NO watermark analysis at all (detection + obscuring only).
            meanf, std_gray = _mean_std(meta)
            params.update(meanf=meanf, std_gray=std_gray)
    if task == "erase":
        params["track"] = bool(req.track)
        params["track_ref"] = meta.get("_sharp_t")   # frame the user brushed on
    elif task == "blur":
        params["targets"] = blur_targets
        params["style"] = blur_style
        params["strength"] = max(0.0, min(1.0, float(req.strength)))
        params["track"] = bool(req.track)
        params["track_ref"] = meta.get("_sharp_t")   # frame the user brushed on
    elif task == "enhance":
        params["scale"] = 2.0 if (req.scale or 1.0) >= 1.5 else 1.0
        params["denoise"] = bool(req.denoise)
        params["strength"] = max(0.0, min(1.0, float(req.strength)))
    elif task == "reframe":
        params["ratio"] = req.ratio
        params["fit"] = req.fit
        if req.focus is not None:
            params["focus"] = (max(0.0, min(1.0, float(req.focus[0]))),
                               max(0.0, min(1.0, float(req.focus[1]))))
    job_id = manager.submit(req.mode, params)
    return {"job_id": job_id, "mode": req.mode, "task": task}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job.")
    data = job.public()
    if job.status == "done":
        data["result_url"] = f"/api/result/{job_id}"
        # Compare's "before" side — advertised ONLY when the clip really exists,
        # so the front-end never offers a compare it can't play.
        if job.before_path and os.path.exists(job.before_path):
            data["before_url"] = f"/api/before/{job_id}"
    return data


@app.get("/api/result/{job_id}")
def result(job_id: str):
    job = manager.get(job_id)
    if not job or job.status != "done" or not job.result_path:
        raise HTTPException(404, "Result not ready.")
    return FileResponse(job.result_path, media_type="video/mp4",
                        filename="cleaned.mp4")


@app.get("/api/before/{job_id}")
def before(job_id: str):
    """The browser-safe H.264 'before' clip that matches the result's segment
    (see jobs._make_before) — the left side of the front-end Compare view."""
    job = manager.get(job_id)
    if (not job or job.status != "done" or not job.before_path
            or not os.path.exists(job.before_path)):
        raise HTTPException(404, "Before clip not available.")
    return FileResponse(job.before_path, media_type="video/mp4",
                        filename="before.mp4")


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
