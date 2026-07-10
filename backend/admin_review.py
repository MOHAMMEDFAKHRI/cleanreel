"""
admin_review.py — owner-only QC review panel. DARK BY DEFAULT.

Everything in this module is gated on the WR_ADMIN_TOKEN env var. While it is
unset (the default), the /admin page and every /api/admin/* route return 404,
nothing is recorded, and no media is retained — production behaves exactly as
before this file existed.

PRIVACY GATE (owner decision pending, 10 Jul 2026): enabling retention keeps
user uploads/results PAST the ~6h window promised on web/privacy.html. Before
setting WR_ADMIN_TOKEN + WR_QC_RETAIN_HOURS in production, add a disclosure
line to web/privacy.html, e.g.:
    "A small sample of processed clips may be reviewed internally to improve
     output quality; retained copies are deleted within {WR_QC_RETAIN_HOURS}h."

Env vars:
    WR_ADMIN_TOKEN        long random string; unset = whole feature off (404s)
    WR_QC_RETAIN_HOURS    keep QC copies this long (default 72; 0 = no copies,
                          panel then only sees metadata + still-live files)
    WR_QC_RETAIN_CONF     retain media when qc confidence is below this
                          (default 0.75)
    WR_QC_RETAIN_MODE     'preview' (default) = never retain paid exports;
                          'all' = exports too
    WR_QC_RETAIN_ALL      retain EVERY preview, not just flagged ones (default
                          '1' since 10 Jul 2026, owner decision; '0' = flagged
                          only). Routine previews keep only the small rendered
                          before/after pair; flagged jobs also keep the
                          original upload.
    WR_QC_RETAIN_MAX      max retained jobs; oldest evicted first (default 200)
"""
from __future__ import annotations
import os, json, time, shutil, hmac
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import FileResponse

ADMIN_TOKEN   = os.environ.get("WR_ADMIN_TOKEN", "").strip()
RETAIN_HOURS  = float(os.environ.get("WR_QC_RETAIN_HOURS", "72"))
RETAIN_CONF   = float(os.environ.get("WR_QC_RETAIN_CONF", "0.75"))
RETAIN_MODE   = os.environ.get("WR_QC_RETAIN_MODE", "preview").lower()
RETAIN_ALL    = os.environ.get("WR_QC_RETAIN_ALL", "1") == "1"
RETAIN_MAX    = int(os.environ.get("WR_QC_RETAIN_MAX", "200"))

_manager = None          # set by attach() from main.py at startup
_storage = None

router = APIRouter()


def enabled() -> bool:
    return bool(ADMIN_TOKEN)


def attach(manager, storage_dir: str):
    """main.py hands us the live JobManager + storage root at startup."""
    global _manager, _storage
    _manager, _storage = manager, storage_dir
    if enabled():
        os.makedirs(_retained_root(), exist_ok=True)


def _admin_dir() -> str:
    return os.path.join(_storage, "admin")

def _retained_root() -> str:
    return os.path.join(_admin_dir(), "retained")

def _log_path() -> str:
    return os.path.join(_admin_dir(), "jobs.jsonl")


def _check(token: str | None):
    """404 (not 401/403) when disabled or wrong token: the panel should be
    indistinguishable from not existing to anyone probing the API."""
    if not enabled() or not token or not hmac.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(404, "Not found.")


# --------------------------------------------------------------------------- #
# Recording + retention — called by jobs.JobManager._worker, strictly
# best-effort: any exception is swallowed there and must never touch a job.
# --------------------------------------------------------------------------- #
def _is_flagged(job, params) -> bool:
    """A job worth a close look: errored, low QC confidence, or a remove/erase
    where the scorer bailed. Other tasks (enhance, reframe, blur) never emit
    QC, so a missing report there is normal, not a flag."""
    if job.status == "error":
        return True
    qc = job.qc or {}
    if qc:
        return float(qc.get("confidence", 0.0)) < RETAIN_CONF
    return params.get("task", "remove") in ("remove", "erase")


def _should_retain(job, params) -> bool:
    if RETAIN_HOURS <= 0:
        return False
    if job.mode == "export" and RETAIN_MODE != "all":
        return False               # paid customers' full videos stay untouched
    return RETAIN_ALL or _is_flagged(job, params)


def record_job(job, params):
    """Append a metadata line for every finished/errored job and, when the
    retention rules say so, copy the media into the retained store."""
    if not enabled() or not _storage:
        return
    os.makedirs(_admin_dir(), exist_ok=True)
    retained = False
    if _should_retain(job, params):
        # Disk guard: with retain-all on, only FLAGGED jobs keep the original
        # upload (can be up to 200 MB); routine previews keep just the small
        # rendered before/after pair — which is what a quality review needs.
        retained = _retain_media(job, params,
                                 include_input=_is_flagged(job, params))
    rec = {
        "id": job.id, "mode": job.mode, "task": params.get("task", "remove"),
        "status": job.status, "created": job.created, "finished": time.time(),
        "qc": job.qc, "error": job.error, "message": job.message,
        "clean_audio": bool(params.get("clean_audio")),
        "retained": retained,
    }
    with open(_log_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _retain_media(job, params, include_input: bool = True) -> bool:
    dst = os.path.join(_retained_root(), job.id)
    try:
        os.makedirs(dst, exist_ok=True)
        src_in = params.get("video_path") if include_input else None
        if src_in and os.path.isfile(src_in):
            shutil.copy2(src_in, os.path.join(
                dst, "input" + (os.path.splitext(src_in)[1] or ".mp4")))
        if job.result_path and os.path.isfile(job.result_path):
            shutil.copy2(job.result_path, os.path.join(dst, "result.mp4"))
        if job.before_path and os.path.isfile(job.before_path):
            shutil.copy2(job.before_path, os.path.join(dst, "before.mp4"))
        with open(os.path.join(dst, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"id": job.id, "mode": job.mode,
                       "task": params.get("task", "remove"),
                       "status": job.status, "qc": job.qc, "error": job.error,
                       "retained_at": time.time()}, f)
        print(f"[qcretain] kept media for job {job.id} "
              f"(status={job.status}, conf="
              f"{(job.qc or {}).get('confidence', 'n/a')})", flush=True)
        return True
    except Exception as e:
        print(f"[qcretain] copy failed for {job.id}: {e}", flush=True)
        shutil.rmtree(dst, ignore_errors=True)
        return False


def sweep():
    """TTL + count-cap eviction for the retained store. Called from the
    existing storage janitor loop; best-effort like everything there."""
    if not enabled() or not _storage or not os.path.isdir(_retained_root()):
        return
    root = _retained_root()
    dirs = []
    for name in os.listdir(root):
        p = os.path.join(root, name)
        if os.path.isdir(p):
            dirs.append((os.path.getmtime(p), p))
    dirs.sort()                                   # oldest first
    cutoff = time.time() - RETAIN_HOURS * 3600
    removed = 0
    for mtime, p in dirs:
        if mtime < cutoff or (len(dirs) - removed) > RETAIN_MAX:
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
    if removed:
        print(f"[qcretain] swept {removed} retained job(s)", flush=True)


# --------------------------------------------------------------------------- #
# Admin API
# --------------------------------------------------------------------------- #
@router.get("/api/admin/jobs")
def admin_jobs(x_admin_token: str | None = Header(default=None)):
    _check(x_admin_token)
    retained_ids = set()
    if os.path.isdir(_retained_root()):
        retained_ids = {d for d in os.listdir(_retained_root())
                        if os.path.isdir(os.path.join(_retained_root(), d))}
    history: list[dict] = []
    if os.path.isfile(_log_path()):
        with open(_log_path(), encoding="utf-8") as f:
            lines = f.readlines()[-300:]
        for ln in lines:
            try:
                r = json.loads(ln)
                r["retained"] = r["id"] in retained_ids   # eviction-aware
                history.append(r)
            except Exception:
                pass
    history.reverse()                                     # newest first
    seen = {r["id"] for r in history}
    live = []
    if _manager:
        for j in list(_manager.jobs.values()):
            live.append({
                "id": j.id, "mode": j.mode, "task": "?", "status": j.status,
                "created": j.created, "qc": j.qc, "error": j.error,
                "message": j.message, "retained": j.id in retained_ids,
                "live": True,
                "has_result": bool(j.result_path and os.path.exists(j.result_path)),
                "has_before": bool(j.before_path and os.path.exists(j.before_path)),
                "in_history": j.id in seen,
            })
        live.sort(key=lambda r: r["created"], reverse=True)
    return {"live": live, "history": history,
            "retain_hours": RETAIN_HOURS, "retain_conf": RETAIN_CONF}


@router.get("/api/admin/media/{job_id}/{kind}")
def admin_media(job_id: str, kind: str,
                x_admin_token: str | None = Header(default=None)):
    _check(x_admin_token)
    if kind not in ("input", "result", "before"):
        raise HTTPException(404, "Not found.")
    # Retained copy first (survives the 6h janitor + restarts) …
    d = os.path.join(_retained_root(), job_id)
    if os.path.isdir(d):
        if kind == "input":
            for name in os.listdir(d):
                if name.startswith("input"):
                    return FileResponse(os.path.join(d, name))
        else:
            p = os.path.join(d, f"{kind}.mp4")
            if os.path.isfile(p):
                return FileResponse(p, media_type="video/mp4")
    # … then whatever is still live inside the normal 6h window.
    job = _manager.get(job_id) if _manager else None
    if job:
        p = job.result_path if kind == "result" else \
            job.before_path if kind == "before" else None
        if p and os.path.isfile(p):
            return FileResponse(p, media_type="video/mp4")
    raise HTTPException(404, "Media not available (expired or never retained).")
