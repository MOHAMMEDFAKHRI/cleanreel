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
import numpy as np
import cv2
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


def _overlay_consistency(path, info, k=6, limit=None):
    """How consistently the static overlay structure (temporal-mean high-pass)
    shows up in individual frames, inside the mask. Median |cosine| over k
    sampled frames: ~0.9 = a see-through overlay sits there in EVERY frame
    (reverse-blend it out); ~0.1 = the mean structure is just a moving
    object's smear (straight inpaint is right)."""
    m = info["mask"] > 0
    if info.get("B") is None or m.sum() < 8:
        return 0.0
    bg = info["B"].mean(2).astype(np.float32)
    u = (bg - cv2.GaussianBlur(bg, (0, 0), 6.0))[m]
    nu = float(np.linalg.norm(u))
    if nu < 1e-3:
        return 0.0
    u /= nu
    cs = []
    for f in wr._sample_frames(path, k, limit):
        hp = wr._hp_gray(f)[m]
        cs.append(abs(float((hp * u).sum())) / (float(np.linalg.norm(hp)) + 1e-6))
    return float(np.median(cs)) if cs else 0.0


@dataclass
class Job:
    id: str
    mode: str                      # 'preview' | 'export'
    status: str = "queued"         # queued | processing | done | error
    progress: float = 0.0          # 0..1
    message: str = "Queued"
    result_path: str | None = None
    error: str | None = None
    qc: dict | None = None          # quality report: confidence, residual_reduction, damage
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
                job.message = "Starting..."
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
        task = params.get("task", "remove")     # remove | erase | enhance | reframe | blur
        seconds = PREVIEW_SECONDS if job.mode == "preview" else None
        out = os.path.join(self.storage, "results", f"{job.id}.mp4")

        def render_progress(done, total):
            if total:
                job.progress = min(0.97, 0.25 + 0.72 * done / float(total))

        # ---------- ENHANCE: pure quality pass (no mask, no detection) ----------
        if task == "enhance":
            scale = float(params.get("scale", 1.0) or 1.0)
            job.progress = 0.15
            job.message = ("Enhancing — denoise, 2× upscale, halo-safe sharpen..."
                           if scale >= 1.5 else
                           "Enhancing — denoise + halo-safe sharpen...")
            # Own output cap: 2x of a large source would blow the memory budget.
            # WR_ENHANCE_MAX = max OUTPUT long side for enhance (default 1600).
            max_out = int(os.environ.get("WR_ENHANCE_MAX", "1600"))
            wr.enhance_video(video, out,
                             scale=scale,
                             denoise=bool(params.get("denoise", True)),
                             sharpen=float(params.get("strength", 0.6)),
                             preview=seconds, max_dim=max_out,
                             progress_cb=render_progress)
            job.result_path = out
            return

        # ---------- REFRAME: aspect conversion with subject tracking ----------
        if task == "reframe":
            ratio = params.get("ratio", "9:16"); fit = params.get("fit", "crop")
            focus = params.get("focus")          # (x, y) normalized, or None
            job.progress = 0.15
            if fit != "crop":
                job.message = f"Reframing to {ratio} (blurred fill)..."
            elif focus:
                job.message = f"Reframing to {ratio} around your focus point..."
            else:
                job.message = f"Reframing to {ratio} (smart crop, auto-tracking)..."
            wr.reframe_video(video, out, ratio=ratio, fit=fit, preview=seconds,
                             max_dim=int(os.environ.get("WR_MAX_DIM", "1366")),
                             focus=focus, progress_cb=render_progress)
            job.result_path = out
            return

        # ---------- PRIVACY BLUR: auto faces/plates + optional manual regions ----------
        if task == "blur":
            tg = params.get("targets")           # None = default; [] = explicit none
            targets = [t for t in (["face"] if tg is None else tg)
                       if t in ("face", "plate")]
            style = params.get("style") or "blur"
            strength = max(0.0, min(1.0, float(params.get("strength", 0.6))))
            w, h, fps, n = wr.probe(video)
            mask = None
            if params.get("mask_path"):
                mask = wr.mask_from_painted(params["mask_path"], h, w)
            elif params.get("boxes"):
                mask = wr.mask_from_boxes(params["boxes"], h, w)
            if mask is not None and mask.sum() == 0:
                mask = None
            if not targets and mask is None:
                raise RuntimeError("Pick faces and/or license plates to blur — "
                                   "or brush a region first.")
            names = " + ".join("faces" if t == "face" else "license plates"
                               for t in targets)
            job.progress = 0.15
            job.message = (f"Finding and blurring {names}..." if targets
                           else "Blurring your marked region...")
            # No watermark analysis needed — blur is detection + obscuring only.
            wr.blur_video(video, out, targets=tuple(targets), style=style,
                          strength=strength, mask01=mask,
                          track=bool(params.get("track")) and mask is not None,
                          track_ref=params.get("track_ref"), preview=seconds,
                          max_dim=int(os.environ.get("WR_MAX_DIM", "1366")),
                          progress_cb=render_progress)
            job.result_path = out
            return

        w, h, fps, n = wr.probe(video)
        trk = None

        if task == "erase":
            # ---------- ERASE: user-mask removal of ANY object ----------
            job.message = "Preparing the erase..."
            if params.get("mask_path"):
                mask = wr.mask_from_painted(params["mask_path"], h, w)
            elif params.get("boxes"):
                mask = wr.mask_from_boxes(params["boxes"], h, w)
            else:
                raise RuntimeError("Brush over what you want erased, then retry.")
            if mask.sum() == 0:
                raise RuntimeError("The erase mask is empty — brush over the object and retry.")
            protect = False
            if params.get("track"):
                # MOVING object: opaque inpaint, template-matched each frame
                # (mark once -> follow). Reverse-blend needs a static region.
                info = dict(type="erase", mask=mask, B=None, meanf=None, gain=0.0)
                trk = wr._track_setup(video, ref=params.get("track_ref"), mask01=mask)
                job.message = "Tracking the object..."
                # QC scoring assumes a static region; skip it for tracked erases.
            else:
                # SMART ENGINE CHOICE: classify the marked region by its temporal
                # behaviour instead of always assuming it's opaque (that forced
                # straight inpaint and scored ~49% on see-through bands).
                # Semi-transparent (content shows through -> high temporal std)
                # -> reverse-blend it out like a watermark; opaque (low std)
                # -> straight inpaint, as before.
                job.message = "Analysing the marked region..."
                qc_limit = int(seconds * fps) if seconds else int(min(n, 8 * fps))
                meanf = params.get("meanf"); stdg = params.get("std_gray")
                if meanf is None or stdg is None:
                    meanf, stdg = wr.mean_and_std(video)
                # Opacity test on the mask CORE (median of temporal std): a
                # sloppy brush margin over a moving background must not make a
                # solid logo look "semi-transparent".
                core = cv2.erode(mask, np.ones((13, 13), np.uint8))
                if core.sum() < 16:
                    core = mask
                if float(np.median(stdg[core > 0])) < 3.0:
                    info = dict(type="erase", mask=mask, B=None, meanf=None, gain=0.0)
                else:
                    info = wr.info_from_user_mask(video, mask, meanf, stdg)
                    mask = info["mask"]
                soft = info.get("B") is not None
                dual = False
                if soft:
                    # Guard: high std alone can also mean a MOVING opaque object
                    # brushed without tracking. A true see-through overlay's
                    # structure recurs in EVERY frame; check that consistency.
                    c = _overlay_consistency(video, info, k=6, limit=qc_limit)
                    if c < 0.18:
                        soft = False                     # clearly a moving object
                    elif c < 0.32:
                        dual = True                      # ambiguous: let QC decide
                if not soft:
                    info = dict(type="erase", mask=mask, B=None, meanf=None, gain=0.0)
                protect = soft   # reverse-blend keeps the detail under the band
                job.message = ("Scoring both erase engines to pick the best..." if dual
                               else "See-through overlay found — reverse-blending it out..."
                               if soft else "Solid object — filling the region in...")
                try:
                    if dual:
                        # score BOTH engines on sampled frames, keep the winner
                        opq = dict(type="erase", mask=mask, B=None, meanf=None, gain=0.0)
                        i1, m1, q1 = wr.autotune(video, opq, mask, _inpainter(),
                                                 protect=False, k=4, limit=qc_limit)
                        i2, m2, q2 = wr.autotune(video, info, mask, _inpainter(),
                                                 protect=True, k=4, limit=qc_limit)
                        if q2["confidence"] > q1["confidence"]:
                            info, mask, qc, soft = i2, m2, q2, True
                        else:
                            info, mask, qc, soft = i1, m1, q1, False
                        protect = soft
                    else:
                        info, mask, qc = wr.autotune(video, info, mask, _inpainter(),
                                                     protect=protect, k=4, limit=qc_limit)
                    job.qc = qc
                    conf = int(round(qc.get("confidence", 0) * 100))
                    kind = "see-through overlay" if soft else "solid object"
                    job.message = f"Erase quality {conf}% ({kind}). Rendering..."
                except Exception as e:
                    print("[qc] skipped:", e)   # QC must never block delivery
            job.progress = 0.25
        else:
            # ---------- REMOVE (default): the watermark pipeline ----------
            job.message = "Analysing the watermark..."
            protect = params.get("protect", True)
            info = dict(type="manual", mask=None, B=None, meanf=None, gain=0.0)
            if params.get("mask_path"):
                user_mask = wr.mask_from_painted(params["mask_path"], h, w)
                info = wr.info_from_user_mask(video, user_mask,
                                              params.get("meanf"), params.get("std_gray"))
                mask = info["mask"]
            elif params.get("boxes"):
                mask = wr.mask_from_boxes(params["boxes"], h, w)
            else:
                info = wr.detect(video)
                if info.get("mask") is None:
                    raise RuntimeError("No watermark detected; mark the area and retry.")
                mask = info["mask"]
            job.progress = 0.25; job.message = f"Detected: {info['type']}. Cleaning..."

            # Quality control: iterate on sampled frames to pick the best reverse-
            # blend strength + mask, then render the full clip once with those params.
            try:
                qc_limit = int(seconds * fps) if seconds else int(min(n, 8 * fps))
                tuned_info, tuned_mask, qc = wr.autotune(
                    video, info, mask, _inpainter(),
                    protect=protect, k=4, limit=qc_limit)
                info, mask = tuned_info, tuned_mask
                job.qc = qc
                conf = int(round(qc.get("confidence", 0) * 100))
                if qc.get("ok"):
                    job.message = f"Quality {conf}%. Rendering..."
                else:
                    job.message = (f"Quality {conf}% — for a spotless result, mark the "
                                   f"watermark on the canvas and retry. Rendering best pass...")
            except Exception as e:
                # QC must never block delivery; fall back to the un-tuned params.
                print("[qc] skipped:", e)

        # Resolution policy — keep the whole container within the memory budget.
        # Upscaling source to 1080p once OOM-killed the old 512 MB box, so by
        # default we NEVER enlarge; we only downscale oversized inputs. On a
        # larger instance, set WR_ALLOW_UPSCALE=1 (and/or raise WR_MAX_DIM).
        max_dim = int(os.environ.get("WR_MAX_DIM", "1366"))
        long_side = max(w, h)
        if os.environ.get("WR_ALLOW_UPSCALE") == "1" and params.get("upscale"):
            up = (1080, 1920) if h >= w else (1920, 1080)
        elif long_side > max_dim:
            s = max_dim / float(long_side)
            up = (max(2, int(round(w * s)) // 2 * 2),      # even dims for yuv420p
                  max(2, int(round(h * s)) // 2 * 2))
        else:
            up = None
        wr.process_video(video, out, info, mask, _inpainter(),
                         preview=seconds, upscale=up, sharpen=True,
                         protect_subject=protect, track=trk,
                         progress_cb=render_progress)
        job.result_path = out
