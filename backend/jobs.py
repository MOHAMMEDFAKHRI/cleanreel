"""
jobs.py — minimal in-process job queue + the actual processing functions.

MVP design (single machine):
    * Jobs live in memory; one background worker thread pulls from a queue.
    * `preview` jobs are cheap (a few seconds) -> meant for the FREE tier.
    * `export` jobs run the full clip -> meant for the PAID tier (credits).

Production swap-in points are marked with  # PROD:
    * Replace this in-memory manager with Redis + RQ/Celery.
    * Run preview jobs on cheap CPU workers, export jobs on GPU workers.
    * Replace local storage with S3 / Cloudflare R2 (signed URLs).
"""
from __future__ import annotations
import os, sys, time, uuid, threading, queue, traceback
from dataclasses import dataclass, field, asdict

# import the engine (one folder up)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import watermark_remover as wr   # noqa: E402

PREVIEW_SECONDS = 4
MAX_EXPORT_SECONDS = 30          # MVP cap for the export tier

_INP = None
def _inpainter():
    """Load the inpainting model once per worker (LaMa if available else classical)."""
    global _INP
    if _INP is None:
        _INP = wr.Inpainter(os.environ.get("WR_ENGINE", "auto"))
    return _INP


@dataclass
class Job:
    id: str
    mode: str                      # 'preview' | 'export'
    status: str = "queued"         # queued | processing | done | error
    progress: float = 0.0          # 0..1
    message: str = "Queued"
    result_path: str | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)

    def public(self):
        d = asdict(self); d.pop("result_path", None); return d


class JobManager:
    def __init__(self, storage_dir: str):
        self.jobs: dict[str, Job] = {}
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.storage = storage_dir
        os.makedirs(os.path.join(storage_dir, "results"), exist_ok=True)
        self._t = threading.Thread(target=self._worker, daemon=True)
        self._t.start()

    # ---- public API ----
    def submit(self, mode: str, params: dict) -> str:
        job = Job(id=uuid.uuid4().hex, mode=mode)
        self.jobs[job.id] = job
        self.q.put((job.id, params))
        return job.id

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    # ---- worker ----
    def _worker(self):
        while True:
            job_id, params = self.q.get()
            job = self.jobs.get(job_id)
            if not job:
                continue
            try:
                job.status = "processing"; job.progress = 0.05
                job.message = "Analysing the watermark..."
                self._run(job, params)
                job.status = "done"; job.progress = 1.0; job.message = "Done"
            except Exception as e:
                job.status = "error"; job.error = str(e)
                job.message = "Failed"
                traceback.print_exc()
            finally:
                self.q.task_done()

    def _run(self, job: Job, params: dict):
        video = params["video_path"]
        w, h, fps, n = wr.probe(video)

        # Decide the mask: explicit boxes/painted mask, else auto-detect.
        info = dict(type="manual", mask=None, B=None, meanf=None, gain=0.0)
        if params.get("mask_path"):
            mask = wr.mask_from_painted(params["mask_path"], h, w)
            det = wr.detect(video)
            if det["type"] == "tiled":
                info = det; mask = (mask | det["mask"]).astype("uint8")
        elif params.get("boxes"):
            mask = wr.mask_from_boxes(params["boxes"], h, w)
        else:
            info = wr.detect(video)
            if info.get("mask") is None:
                raise RuntimeError("No watermark detected; mark the area and retry.")
            mask = info["mask"]
        job.progress = 0.25; job.message = f"Detected: {info['type']}. Cleaning..."

        seconds = PREVIEW_SECONDS if job.mode == "preview" else None
        up = (1080, 1920) if params.get("upscale") and h >= w else \
             (1920, 1080) if params.get("upscale") else None
        out = os.path.join(self.storage, "results", f"{job.id}.mp4")
        wr.process_video(video, out, info, mask, _inpainter(),
                         preview=seconds, upscale=up, sharpen=True,
                         protect_subject=params.get("protect", True))
        job.result_path = out
