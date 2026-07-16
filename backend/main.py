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
from jobs import JobManager, MAX_EXPORT_SECONDS, PREVIEW_SECONDS, MAX_REEL_OUTPUT_SECONDS
import watermark_remover as wr   # jobs.py puts the engine dir on sys.path on import
import accounts                  # users, magic-link auth, credit balances, packs
import admin_review              # owner QC panel; every route 404s unless WR_ADMIN_TOKEN is set
try:
    import stripe
except Exception:
    stripe = None

HERE = os.path.dirname(os.path.abspath(__file__))
STORAGE = os.path.join(HERE, "storage")
UPLOADS = os.path.join(STORAGE, "uploads")
os.makedirs(UPLOADS, exist_ok=True)

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "200"))
MAX_UPLOAD_SECONDS = MAX_EXPORT_SECONDS   # cleanup modes: upload == export, so 60s
# Reel creation samples from long source footage, so the reel flow accepts a
# bigger/longer upload — the *rendered* reel is still bounded by which part(s)
# the user selects (enforced at job submit against MAX_REEL_OUTPUT_SECONDS).
MAX_REEL_UPLOAD_MB      = int(os.environ.get("MAX_REEL_UPLOAD_MB", "2048"))   # 2 GB
MAX_REEL_UPLOAD_SECONDS = int(os.environ.get("MAX_REEL_UPLOAD_SECONDS", "900"))   # 15 min

# CPU-only pipelines (blur / reframe / captions) don't touch the GPU, so their
# uploads — and exports — can be far more generous than the inpaint tier.
# Captions especially: long talking videos are its natural use, and the free
# .srt on a long upload is cheap lead-gen (text out, no GPU, no big egress).
MAX_CPU_UPLOAD_MB      = int(os.environ.get("MAX_CPU_UPLOAD_MB", "500"))
MAX_CPU_UPLOAD_SECONDS = int(os.environ.get("MAX_CPU_UPLOAD_SECONDS", "300"))  # 5 min
MAX_CPU_EXPORT_SECONDS = int(os.environ.get("MAX_CPU_EXPORT_SECONDS",
                                            str(MAX_CPU_UPLOAD_SECONDS)))
_TIER_CAPS = {  # tier -> (seconds, MB)
    "gpu":  (MAX_UPLOAD_SECONDS, MAX_UPLOAD_MB),
    "cpu":  (MAX_CPU_UPLOAD_SECONDS, MAX_CPU_UPLOAD_MB),
    "reel": (MAX_REEL_UPLOAD_SECONDS, MAX_REEL_UPLOAD_MB),
}
_UPLOAD_TIER = {  # accepted `intent` values -> tier ('clean' kept for old clients)
    "clean": "gpu", "remove": "gpu", "erase": "gpu", "enhance": "gpu",
    "blur": "cpu", "reframe": "cpu", "captions": "cpu", "caption": "cpu", "cpu": "cpu",
    "reel": "reel",
}
_CPU_TASKS = ("blur", "reframe", "captions")
# NOTE: the reel *output* cap (MAX_REEL_OUTPUT_SECONDS) is enforced at job submit.

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

# Owner QC review panel (see backend/admin_review.py). DARK BY DEFAULT: with
# WR_ADMIN_TOKEN unset these routes all answer 404 and nothing is recorded.
# PRIVACY: do not enable in production until web/privacy.html discloses
# internal quality review — see the note at the top of admin_review.py.
admin_review.attach(manager, STORAGE)
app.include_router(admin_review.router)

@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    if not admin_review.enabled():
        raise HTTPException(404, "Not found.")
    p = os.path.join(HERE, "static", "admin.html")
    return HTMLResponse(open(p, encoding="utf-8").read())

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


@app.api_route("/api/health", methods=["GET", "HEAD"])
def health():
    # HEAD is allowed too: uptime monitors (UptimeRobot, etc.) probe with HEAD by
    # default — a GET-only route answers 405 and they wrongly flag the API "down".
    return {"ok": True, "preview_seconds": PREVIEW_SECONDS,
            "max_export_seconds": MAX_EXPORT_SECONDS,
            "max_upload_mb": MAX_UPLOAD_MB,   # cleanup-mode (remove/erase/etc.) cap; lets the client size-check before uploading
            "max_reel_upload_seconds": MAX_REEL_UPLOAD_SECONDS,
            "max_reel_upload_mb": MAX_REEL_UPLOAD_MB,
            "max_reel_output_seconds": MAX_REEL_OUTPUT_SECONDS,
            # per-mode upload tiers, so clients can show honest caps up front
            "tiers": {t: {"seconds": s, "mb": mb} for t, (s, mb) in _TIER_CAPS.items()},
            "cpu_tasks": list(_CPU_TASKS)}

# ---- predictive GPU pre-warm -------------------------------------------------
# The LaMa / Enhance Modal apps scale to zero, so the FIRST preview after an idle
# spell pays ~15-20s of container cold-start (see gpu/modal_app.py). Rather than
# pay for an always-on warm GPU, the client calls /api/prewarm the instant a video
# is uploaded — a strong "a preview is coming" signal — so the container boots
# while the user is still brushing their mask / choosing settings, and the real
# Preview lands on an already-warm GPU. Fire-and-forget + a short per-app throttle
# keep the cost tied to genuine intent (a bounced upload just lets it scale back
# down after Modal's scaledown_window). No Modal-side change: we simply hit the
# existing inpaint/enhance endpoints with a throwaway 8x8 frame, which forces the
# container up and runs one trivial inference so the first real call is warm too.
_PREWARM_GAP = float(os.environ.get("WR_PREWARM_MIN_GAP", "25"))   # skip re-warm within N s (per app)
_PREWARM_TIMEOUT = float(os.environ.get("WR_PREWARM_TIMEOUT", "30"))
_prewarm_last = {}
_prewarm_lock = threading.Lock()
_WARM_IMG  = "iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAIAAADTED8xAAAB+0lEQVR42u3TQQ0AAAjEMMD4WeeNBloJS9ZJCr4aCTAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAATAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAbAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHAAGAAMAAYAAwABgADgAHgWutKA31OlVM6AAAAAElFTkSuQmCC"
_WARM_MASK = "iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAAAAAB5Gfe6AAAAg0lEQVR42u3SgQ0AQAQEQb7/nunCS8w0cLIRAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAGyT04O17KB3/QMEEEAAAQQQQAABBBBAAAEEEEAAAQQQQAABBBBAAAEEEEAAAQQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA+KEB2cABQAyZerMAAAAASUVORK5CYII="

def _prewarm_fire(kind, url, payload):
    t0 = time.time()
    try:
        import requests
        r = requests.post(url, json=payload, timeout=_PREWARM_TIMEOUT)
        print(f"[prewarm] {kind} warm done in {time.time()-t0:.1f}s (http {r.status_code})", flush=True)
    except Exception as e:
        # best-effort: a failed warm just means the preview pays the cold start, as before
        print(f"[prewarm] {kind} warm failed after {time.time()-t0:.1f}s ({e!r})", flush=True)

def _prewarm(kind, url_env, payload):
    url = os.environ.get(url_env, "").strip()
    if not url:
        return False                       # that GPU app isn't configured on this server
    now = time.time()
    with _prewarm_lock:
        if now - _prewarm_last.get(kind, 0.0) < _PREWARM_GAP:
            return False                   # already warm / warming — don't pile on GPU time
        _prewarm_last[kind] = now
    threading.Thread(target=_prewarm_fire, args=(kind, url, payload), daemon=True).start()
    return True

@app.post("/api/prewarm")
def prewarm(task: str = ""):
    """Best-effort GPU pre-warm ahead of a preview; called by the client on upload.
    Returns immediately — the Modal boot runs on a background thread. Only the task
    string is accepted (never a URL/token), so a client can't point warming anywhere
    but the server's own pre-configured Modal apps."""
    task = (task or "").strip().lower()
    tok = os.environ.get("WR_INPAINT_TOKEN", "")
    warmed = []
    if task in ("remove", "erase", ""):
        if _prewarm("lama", "WR_INPAINT_URL",
                    {"token": tok, "items": [{"image": _WARM_IMG, "mask": _WARM_MASK}]}):
            warmed.append("lama")
    if task == "enhance":
        if _prewarm("enhance", "WR_ENHANCE_URL",
                    {"token": tok, "items": [{"image": _WARM_IMG}], "scale": 2.0, "face_enhance": False}):
            warmed.append("enhance")
    if warmed:
        print(f"[prewarm] task={task or '(default)'} -> booting {'+'.join(warmed)}", flush=True)
    return {"ok": True, "warming": warmed}

class EmailReq(BaseModel):
    email: str

class TokenReq(BaseModel):
    token: str

class CodeReq(BaseModel):
    email: str
    code: str

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

@app.post("/api/auth/code")
def auth_code(req: CodeReq, request: Request):
    """Exchange the 6-digit code from the sign-in email for a session — keeps
    sign-in in the tab that holds the upload (in-app-browser fix). Attempts are
    rate-limited hard: a 6-digit space must not be brute-forceable."""
    rate_limit(request, "code", int(os.environ.get("RATE_CODE_PER_HOUR", "15")))
    email = (req.email or "").strip().lower()
    if "@" not in email or len(email) > 200:
        raise HTTPException(400, "Please enter a valid email address.")
    if not accounts.verify_login_code(email, req.code):
        raise HTTPException(400, "That code is invalid or has expired — "
                                 "request a fresh sign-in email.")
    credits = accounts.ensure_user(email)
    session = accounts.sign_token(email, "session", ttl=60 * 60 * 24 * 30)
    return {"email": email, "credits": credits, "session": session}

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


@app.get("/api/file/{file_id}")
def file_exists(file_id: str):
    """Cheap liveness check for an upload: FILES is in-memory, so a deploy or
    restart forgets uploads — the front end uses this to decide whether
    'already uploaded' is still true before skipping a re-upload."""
    return {"ok": file_id in FILES}


@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...), intent: str = "clean"):
    rate_limit(request, "upload", RATE_UPLOAD_PER_HOUR)
    # Per-mode upload tiers (intent = task name or tier):
    #   gpu  (remove/erase/enhance) — every frame hits the GPU, keep it tight
    #   cpu  (blur/reframe/captions) — ffmpeg/YuNet/whisper only, be generous
    #   reel — samples from long source footage, biggest caps
    tier = _UPLOAD_TIER.get((intent or "").strip().lower(), "gpu")
    max_secs, max_mb = _TIER_CAPS[tier]
    reel = tier == "reel"
    ext = os.path.splitext(file.filename or "")[1].lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"):
        raise HTTPException(400, "Unsupported video format.")
    fid = uuid.uuid4().hex
    path = os.path.join(UPLOADS, fid + ext)
    size = 0
    with open(path, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > max_mb << 20:
                f.close(); os.remove(path)
                raise HTTPException(413, f"File too large (>{max_mb} MB).")
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
    if seconds > max_secs:
        os.remove(path)
        tail = "for reels" if reel else "for this job"
        raise HTTPException(413, f"That clip is {seconds:.0f}s — the limit is {max_secs}s per video {tail}. Trim it and try again.")
    FILES[fid] = {"path": path, "w": w, "h": h, "seconds": seconds, "fps": fps,
                  "intent": "reel" if reel else "clean", "tier": tier}
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


def _canvas_limit(meta) -> int:
    """Frames the mark-up canvas may be picked from: the sharpest frame INSIDE
    the free-preview span (was: first 400 frames). A user marks the object on
    this still — if the still sat at, say, t=8 s while the preview renders only
    the first 4 s, the marked spot may show content the preview never contains
    and the erase looks like it did nothing. Exports include these frames too,
    so anchoring the canvas to the preview span is safe for both modes."""
    fps = meta.get("fps") or 24.0
    return max(1, min(400, int(PREVIEW_SECONDS * max(fps, 1e-6))))


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
        f, idx = wr.sharpest_frame(meta["path"], limit=_canvas_limit(meta),
                                   with_index=True)
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
        f, idx = wr.sharpest_frame(meta["path"], limit=_canvas_limit(meta),
                                   with_index=True)
        if f is not None:
            meta["_sharp_t"] = idx / max(meta.get("fps") or 24.0, 1e-6)
    if f is None:
        raise HTTPException(400, "Could not read a frame from that video.")
    boxes = [[int(round(v)) for v in b] for b in wr.detect_privacy_boxes(f, tgs)]
    return {"boxes": boxes, "targets": tgs}


# --------------------------------------------------------------------------- #
# CLE-44 phase (b): region metadata for the guided mark screen's tap-to-select.
# One call returns EVERY candidate the user could tap, each with a
# plain-language label, a moving flag, and whether the analyzer pre-selected
# it — the front end never has to know detector internals.
# --------------------------------------------------------------------------- #
_REGION_TARGETS = ("marks", "face", "plate")     # 'marks' = static overlays


def _frame_at(path, idx):
    """One decoded BGR frame by index (None on failure)."""
    cap = cv2.VideoCapture(path)
    try:
        if idx > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, f = cap.read()
        return f if ok else None
    finally:
        cap.release()


def _pad_bbox(x, y, bw, bh, W, H, pad=0.05):
    """Slightly padded int bbox, clamped to the frame — a friendlier tap
    target than the raw detector rectangle."""
    px = max(2, int(round(bw * pad))); py = max(2, int(round(bh * pad)))
    x0 = max(0, int(round(x)) - px); y0 = max(0, int(round(y)) - py)
    x1 = min(W, int(round(x + bw)) + px); y1 = min(H, int(round(y + bh)) + py)
    return [x0, y0, max(1, x1 - x0), max(1, y1 - y0)]


def _mark_regions(meta):
    """Static-overlay detections (watermark / logo) as regions. Reuses the
    same cached detection the render pipeline uses, so what gets pre-selected
    here is exactly what a remove job would target."""
    det = meta.get("_det")
    if det is None:
        det = wr.detect(meta["path"]); meta["_det"] = det
    t = det.get("type", "none"); m = det.get("mask")
    if t == "none" or m is None or not (m > 0).any():
        return [], t
    W, H = meta["w"], meta["h"]
    m8 = (m > 0).astype(np.uint8)
    kind, label, conf = {
        "tiled":     ("watermark", "Watermark pattern", 0.92),
        "logo-soft": ("watermark", "Watermark",         0.85),
        "logo":      ("logo",      "Logo",              0.75),
    }[t]
    if t == "tiled":                     # lattice covers the frame -> one region
        ys, xs = np.nonzero(m8)
        bbox = _pad_bbox(xs.min(), ys.min(),
                         xs.max() - xs.min() + 1, ys.max() - ys.min() + 1, W, H)
        return [dict(id="mark-0", kind=kind, label=label, bbox=bbox,
                     confidence=conf, moving=False, preselected=True)], t
    # compact overlays: merge nearby blobs, keep the biggest few
    merged = cv2.dilate(m8, np.ones((15, 15), np.uint8))
    nlab, lab, stats, _ = cv2.connectedComponentsWithStats(merged)
    comps = [(stats[k][4], stats[k][:4]) for k in range(1, nlab)
             if stats[k][4] >= 0.0004 * m8.size]
    comps.sort(key=lambda c: -c[0])
    out = []
    for i, (_, (x, y, bw, bh)) in enumerate(comps[:6]):
        out.append(dict(id=f"mark-{i}", kind=kind,
                        label=label if len(comps) == 1 else f"{label} {i + 1}",
                        bbox=_pad_bbox(x, y, bw, bh, W, H),
                        confidence=conf, moving=False, preselected=True))
    return out, t


def _privacy_regions(meta, tgs):
    """Face/plate regions anchored to the SAME canvas still the mark screen
    shows (so bboxes line up with what the user sees). Two extra sampled
    frames across the preview span decide the `moving` flag for real instead
    of assuming it."""
    W, H = meta["w"], meta["h"]
    cache = meta.get("_frame")
    if cache is not None:
        f0 = cv2.imdecode(np.frombuffer(cache, np.uint8), cv2.IMREAD_COLOR)
    else:
        f0, idx = wr.sharpest_frame(meta["path"], limit=_canvas_limit(meta),
                                    with_index=True)
        if f0 is not None:
            meta["_sharp_t"] = idx / max(meta.get("fps") or 24.0, 1e-6)
    if f0 is None:
        return []
    lim = _canvas_limit(meta)
    laters = [f for f in (_frame_at(meta["path"], lim // 2),
                          _frame_at(meta["path"], max(lim - 1, 1))) if f is not None]
    diag = float(np.hypot(W, H)); out = []
    names = {"face": "Face", "plate": "Plate"}
    for tg in tgs:
        base = wr.detect_privacy_boxes(f0, [tg])
        if not base:
            continue
        later_boxes = [wr.detect_privacy_boxes(f, [tg]) for f in laters]
        for i, (x, y, bw, bh) in enumerate(base):
            cx, cy = x + bw / 2.0, y + bh / 2.0
            moving = False
            for boxes in later_boxes:    # nearest same-kind detection later on
                best = None
                for (X, Y, BW, BH) in boxes:
                    d = float(np.hypot(X + BW / 2.0 - cx, Y + BH / 2.0 - cy))
                    if best is None or d < best:
                        best = d
                if best is not None and best > 0.025 * diag:
                    moving = True; break
            out.append(dict(
                id=f"{tg}-{i}", kind=tg,
                label=names[tg] if len(base) == 1 else f"{names[tg]} {i + 1}",
                bbox=_pad_bbox(x, y, bw, bh, W, H),
                confidence=0.8 if tg == "face" else 0.6,
                moving=moving, preselected=False))
    return out


@app.post("/api/regions/{fid}")
def regions(fid: str, targets: str = "marks,face,plate"):
    """Everything tappable on the mark screen, in canvas-still coordinates:
    [{id, kind, label, bbox:[x,y,w,h], confidence, moving, preselected}].
    kinds: watermark | logo | face | plate. Static overlays are pre-selected
    (the analyzer found the problem); faces/plates are offered, not chosen.
    Cached per (file, targets) — repeat calls are free."""
    meta = FILES.get(fid)
    if not meta:
        raise HTTPException(404, "Unknown file_id (upload first).")
    tgs = [t.strip().lower() for t in (targets or "").split(",") if t.strip()]
    if not tgs or any(t not in _REGION_TARGETS for t in tgs):
        raise HTTPException(400, "targets must be a comma list from "
                                 "'marks' / 'face' / 'plate'.")
    key = ",".join(sorted(tgs))
    cached = meta.setdefault("_regions", {}).get(key)
    if cached is not None:
        return cached
    regs, wm_type = ([], "none")
    if "marks" in tgs:
        regs, wm_type = _mark_regions(meta)
    regs += _privacy_regions(meta, [t for t in tgs if t in ("face", "plate")])
    resp = {"regions": regs, "watermark_type": wm_type,
            "frame": {"w": meta["w"], "h": meta["h"],
                      "t": round(float(meta.get("_sharp_t") or 0.0), 3)}}
    meta["_regions"][key] = resp
    return resp


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
    clean_audio: bool = False             # any task: denoise audio (DeepFilterNet)
    cap_style: str | None = None          # reel: caption preset 'clean' | 'bold' | 'minimal'
    cap_pos: str | None = None            # reel: 'bottom' | 'middle'
    cap_size: str | None = None           # reel: 's' | 'm' | 'l'
    cap_color: str | None = None          # reel: 'white' | 'yellow' | 'green' | 'pink'
    card_theme: str | None = None         # reel: 'dark' | 'light' | 'accent'
    card_secs: float | None = None        # reel: end-card duration 1..5s
    cta: str | None = None                # reel: end-card text (<= 80 chars)
    trim_start: float | None = None       # reel: trim in (seconds) — legacy single range
    trim_end: float | None = None         # reel: trim out (seconds) — legacy single range
    segments: list[list[float]] | None = None  # reel: [[start,end], ...] ordered parts; overrides trim_*
    captions: bool = True                 # reel: burn captions (auto-skips if no speech)
    rotate: str | None = None             # reel: 'auto' | 'left' | 'right' | '180'


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
    reel_segments = None        # reel: normalized [(start, end), ...] of selected parts
    reel_out_seconds = None     # reel: total rendered length (sum of parts / trimmed span)
    if task not in ("remove", "erase", "enhance", "reframe", "blur", "captions", "reel"):
        raise HTTPException(400, "task must be remove | erase | enhance | reframe | blur | captions | reel.")
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
    if task in ("reframe", "reel"):
        try:
            wr.parse_ratio(req.ratio)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if req.fit not in ("crop", "blur"):
            raise HTTPException(400, "fit must be 'crop' or 'blur'.")
        if req.focus is not None and len(req.focus) != 2:
            raise HTTPException(400, "focus must be [x, y] with 0..1 values.")
    if task == "reel":
        if (req.cap_style or "clean").lower() not in ("clean", "bold", "minimal"):
            raise HTTPException(400, "cap_style must be clean | bold | minimal.")
        if (req.cap_pos or "bottom").lower() not in ("bottom", "middle"):
            raise HTTPException(400, "cap_pos must be bottom | middle.")
        if (req.cap_size or "m").lower() not in ("s", "m", "l"):
            raise HTTPException(400, "cap_size must be s | m | l.")
        if (req.cap_color or "white").lower() not in ("white", "yellow", "green", "pink"):
            raise HTTPException(400, "cap_color must be white | yellow | green | pink.")
        if (req.card_theme or "dark").lower() not in ("dark", "light", "accent"):
            raise HTTPException(400, "card_theme must be dark | light | accent.")
        if (req.rotate or "auto").lower() not in ("auto", "none", "left", "right", "180"):
            raise HTTPException(400, "rotate must be auto | left | right | 180.")
        # Which part(s) of the source become the reel. `segments` (multi-part,
        # trim-anywhere) wins; otherwise fall back to the legacy single trim range.
        src_secs = float(meta.get("seconds") or 0.0)
        if req.segments:
            segs = []
            for pair in req.segments:
                if not pair or len(pair) != 2:
                    raise HTTPException(400, "Each segment must be [start, end] in seconds.")
                s = max(0.0, float(pair[0])); e = float(pair[1])
                if src_secs:
                    s = min(s, src_secs); e = min(e, src_secs)
                if e <= s + 0.3:
                    raise HTTPException(400, "Each selected part must be at least 0.3s long.")
                segs.append((round(s, 3), round(e, 3)))
            if not segs:
                raise HTTPException(400, "Add at least one part to your reel.")
            if len(segs) > 20:
                raise HTTPException(400, "Too many parts — keep it under 20.")
            reel_segments = segs
            reel_out_seconds = sum(e - s for s, e in segs)
        else:
            t0 = max(0.0, float(req.trim_start or 0.0))
            t1 = float(req.trim_end) if req.trim_end else None
            if t1 is not None and t1 <= t0 + 0.5:
                raise HTTPException(400, "trim_end must be at least 0.5s after trim_start.")
            end = t1 if t1 is not None else (src_secs or None)
            reel_out_seconds = (end - t0) if end is not None else None
        # NB: the total-length cap is enforced on EXPORT only (below). Previews
        # always render just the first few seconds, so length must not block them.

    # Backpressure: one worker renders one job at a time, so a deep queue means
    # a silent multi-hour wait. Refuse early instead — and BEFORE any credit is
    # deducted, so a rejected request never costs the customer anything.
    if manager.pending() >= MAX_QUEUE_DEPTH:
        raise HTTPException(429, "We're at capacity right now — please try again "
                                 "in a few minutes.")

    # Double-submit guard: the same file + task + mode while an identical job
    # is still queued/processing is almost always an accidental second click.
    # Rejected BEFORE the credit deduction below, so a duplicate export can
    # never double-charge.
    dup_key = f"{req.file_id}|{task}|{req.mode}"
    if manager.has_active(dup_key):
        raise HTTPException(409, "That job is already queued or running — hang "
                                 "tight, no need to submit it again.")

    refund_email = None
    # Neural enhance sends every full frame through the GPU (Real-ESRGAN +
    # GFPGAN) — by far the heaviest export — so it costs 2 credits; everything
    # else stays 1. use_credits is all-or-nothing: an insufficient balance
    # deducts nothing.
    # Reel chains two GPU-backed stages (reframe + whisper captions), so it
    # also costs 2 — same logic as enhance: heavy pipeline, honest price.
    export_cost = 2 if task in ("enhance", "reel") else 1
    if req.mode == "export":                 # preview stays free & anonymous
        if task == "reel":
            # Reels are capped by the *selected* footage, not the source length.
            if reel_out_seconds is not None and reel_out_seconds > MAX_REEL_OUTPUT_SECONDS + 0.5:
                raise HTTPException(413, f"Reel export limited to {MAX_REEL_OUTPUT_SECONDS}s of selected footage in this tier.")
        else:
            export_cap = MAX_CPU_EXPORT_SECONDS if task in _CPU_TASKS else MAX_EXPORT_SECONDS
            if meta["seconds"] > export_cap:
                raise HTTPException(413, f"Export limited to {export_cap}s for this job.")
        email = current_user(authorization)
        if not email:
            raise HTTPException(401, "Please sign in to export.")
        if not accounts.use_credits(email, export_cost):
            raise HTTPException(402, (f"This export uses {export_cost} credits — you don't have "
                                      "enough. Buy a pack to export the full video."
                                      if export_cost > 1 else
                                      "Out of export credits. Buy a pack to export the full video."))
        refund_email = email                 # paid: worker refunds these credits on failure

    params = {
        "video_path": meta["path"], "task": task,
        "boxes": [tuple(b) for b in req.boxes] if req.boxes else None,
        "upscale": req.upscale, "protect": req.protect,
        "clean_audio": bool(req.clean_audio),
    }
    if refund_email:
        # If the render fails, jobs.JobManager._worker returns these credits.
        params["refund_on_fail"] = refund_email
        params["refund_credits"] = export_cost
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
    elif task == "captions":
        # style passthrough for the standalone captions task (the guided UI's
        # "Pick a look" — previously only the reel pipeline read these).
        params["cap_style"] = (req.cap_style or "clean").lower()
        params["cap_pos"] = (req.cap_pos or "bottom").lower()
        params["cap_size"] = (req.cap_size or "m").lower()
        params["cap_color"] = (req.cap_color or "white").lower()
    elif task == "reel":
        params["ratio"] = req.ratio
        params["fit"] = req.fit
        if req.focus is not None:
            params["focus"] = (max(0.0, min(1.0, float(req.focus[0]))),
                               max(0.0, min(1.0, float(req.focus[1]))))
        params["cap_style"] = (req.cap_style or "clean").lower()
        params["cap_pos"] = (req.cap_pos or "bottom").lower()
        params["cap_size"] = (req.cap_size or "m").lower()
        params["cap_color"] = (req.cap_color or "white").lower()
        params["card_theme"] = (req.card_theme or "dark").lower()
        params["card_secs"] = max(1.0, min(5.0, float(req.card_secs or 2.5)))
        params["captions"] = bool(req.captions)
        rot = (req.rotate or "auto").lower()
        if rot in ("left", "right", "180"):
            params["rotate"] = rot
        # CTA: strip control characters, hard cap at 80 chars (2 lines on card)
        cta = "".join(ch for ch in (req.cta or "") if ch.isprintable())[:80].strip()
        if cta:
            params["cta"] = cta
        # Prefer the normalized multi-part selection; keep single-range for back-compat.
        if reel_segments is not None:
            params["segments"] = reel_segments
        else:
            if req.trim_start:
                params["trim_start"] = max(0.0, float(req.trim_start))
            if req.trim_end:
                params["trim_end"] = float(req.trim_end)
    # Attach the signed-in user (export always; preview too when signed in) so
    # the account page can list their recent work.
    owner = refund_email or current_user(authorization)
    job_id = manager.submit(req.mode, params, key=dup_key, owner=owner)
    print(f"[job] mode={req.mode} task={task} id={job_id}", flush=True)
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
        if getattr(job, "srt", None):
            data["srt_url"] = f"/api/srt/{job_id}"
    return data


@app.get("/api/my/jobs")
def my_jobs(authorization: str | None = Header(default=None)):
    """The signed-in user's jobs from the last STORAGE_TTL_HOURS (account
    page). In-memory listing: a server restart clears it, results on disk
    still honour the TTL. Never exposes another user's work."""
    email = current_user(authorization)
    if not email:
        raise HTTPException(401, "Sign in to see your recent work.")
    from jobs import STORAGE_TTL_HOURS
    items = []
    for j in manager.for_owner(email, STORAGE_TTL_HOURS):
        d = {"id": j.id, "task": j.task, "mode": j.mode, "status": j.status,
             "created": j.created,
             "expires_at": j.created + STORAGE_TTL_HOURS * 3600,
             "message": j.message if j.status != "done" else None}
        if j.status == "done":
            if j.result_path and os.path.exists(j.result_path):
                d["result_url"] = f"/api/result/{j.id}"
            else:
                d["status"] = "expired"
            if getattr(j, "srt", None):
                d["srt_url"] = f"/api/srt/{j.id}"
            if j.qc:
                d["confidence"] = j.qc.get("confidence")
        items.append(d)
    return {"jobs": items, "ttl_hours": STORAGE_TTL_HOURS}


@app.get("/api/srt/{job_id}")
def srt_file(job_id: str):
    """The captions task's transcript as a downloadable .srt (free — the SRT
    is lead-gen; the burned-in export is what costs a credit)."""
    job = manager.get(job_id)
    if not job or not getattr(job, "srt", None):
        raise HTTPException(404, "No captions for this job.")
    return PlainTextResponse(job.srt, media_type="application/x-subrip",
                             headers={"Content-Disposition":
                                      'attachment; filename="captions.srt"'})


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
