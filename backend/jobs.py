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
import os, sys, time, uuid, threading, queue, traceback, tempfile, shutil
import numpy as np
import cv2
from dataclasses import dataclass, field, asdict

# import the engine (one folder up)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import watermark_remover as wr   # noqa: E402
import accounts                  # noqa: E402  (auto-refund of credits on failed exports)
import admin_review              # noqa: E402  (owner QC panel; inert unless WR_ADMIN_TOKEN is set)

PREVIEW_SECONDS = 4
MAX_EXPORT_SECONDS = 60          # single clip-length cap for this tier (upload == export)

# Storage janitor: uploads & results are transient working files. Anything older
# than STORAGE_TTL_HOURS is deleted. This number is a PROMISE made on
# web/privacy.html ("deleted within ~6 hours") — change both together.
STORAGE_TTL_HOURS = float(os.environ.get("STORAGE_TTL_HOURS", "6"))
STORAGE_SWEEP_SECONDS = int(os.environ.get("STORAGE_SWEEP_SECONDS", str(30 * 60)))

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


def _glyph_residual(path, cand_info, cand_mask, inp, B, protect, stdg=None,
                    k=4, limit=None):
    """Fraction of the KNOWN overlay structure (high-pass of B) that SURVIVES a
    candidate's cleaning, median over k sampled frames. 0 = stamp gone, 1 = all
    still there. Unlike each engine's own QC, this puts the reverse-blend and
    straight-inpaint candidates on the SAME scale, so a dual pick can't prefer
    a faded-but-legible ghost over a clean fill.

    The support is restricted to the STAMP'S OWN pixels (temporally static,
    std < 8): B's high-pass also contains legitimate static background
    structure (tile joints, kerb lines) that a GOOD inpaint faithfully
    reconstructs — measuring there would punish correct reconstruction.
    Occluding glyphs stay constant while passing shadows modulate everything
    else, so low-std-under-motion is exactly the stamp support."""
    m = cand_mask > 0
    if stdg is not None:
        m = m & (stdg < 8.0)
    if B is None or m.sum() < 8:
        return None
    bg = B.mean(2).astype(np.float32)
    u = (bg - cv2.GaussianBlur(bg, (0, 0), 6.0))[m]
    nu = float(np.linalg.norm(u))
    if nu < 1e-3:
        return None
    u /= nu
    rs = []
    for f in wr._sample_frames(path, k, limit):
        cleaned = wr._clean_frame_static(
            f.copy(), cand_info.get("B"), cand_info.get("meanf"),
            cand_info.get("gain", 0.0), cand_mask, inp, protect)
        before = abs(float((wr._hp_gray(f)[m] * u).sum()))
        after = abs(float((wr._hp_gray(cleaned)[m] * u).sum()))
        rs.append(after / (before + 1e-6))
    return float(np.median(rs)) if rs else None


@dataclass
class Job:
    id: str
    mode: str                      # 'preview' | 'export'
    status: str = "queued"         # queued | processing | done | error
    progress: float = 0.0          # 0..1
    message: str = "Queued"
    result_path: str | None = None
    before_path: str | None = None  # browser-safe H.264 "before" clip for Compare
    error: str | None = None
    qc: dict | None = None          # quality report: confidence, residual_reduction, damage
    created: float = field(default_factory=time.time)
    key: str | None = None          # dedup key (file_id|task|mode) — double-submit guard
    srt: str | None = None          # captions task: the .srt text (served via /api/srt)
    owner: str | None = None        # signed-in user's email (account page listing)
    task: str = "remove"            # which mode ran (account page listing)

    def public(self):
        d = asdict(self)
        for k in ("result_path", "before_path", "key", "srt", "owner"):
            d.pop(k, None)
        d["has_srt"] = bool(self.srt)
        return d


class JobManager:
    def __init__(self, storage_dir: str):
        self.jobs: dict[str, Job] = {}
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.storage = storage_dir
        os.makedirs(os.path.join(storage_dir, "results"), exist_ok=True)
        self._t = threading.Thread(target=self._worker, daemon=True)
        self._t.start()
        self._jt = threading.Thread(target=self._janitor, daemon=True)
        self._jt.start()

    # ---- public API ----
    def submit(self, mode: str, params: dict, key: str | None = None,
               owner: str | None = None) -> str:
        job = Job(id=uuid.uuid4().hex, mode=mode, key=key, owner=owner,
                  task=params.get("task", "remove"))
        self.jobs[job.id] = job
        self.q.put((job.id, params))
        return job.id

    def for_owner(self, email: str, ttl_hours: float) -> list:
        """The signed-in user's recent jobs, newest first, still inside the
        storage TTL. In-memory: a service restart clears the list (results
        themselves live on disk until the janitor's TTL sweep)."""
        cutoff = time.time() - ttl_hours * 3600
        mine = [j for j in self.jobs.values()
                if j.owner == email and j.created >= cutoff]
        return sorted(mine, key=lambda j: -j.created)

    def has_active(self, key: str) -> bool:
        """True if an identical job (same dedup key) is queued or processing —
        the server-side guard against accidental double-submits."""
        return any(j.key == key and j.status in ("queued", "processing")
                   for j in self.jobs.values())

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def pending(self) -> int:
        """Backpressure gauge: jobs waiting in the queue, plus the one the
        worker is currently rendering (if any). create_job refuses new work
        with a 429 when this gets deep."""
        n = self.q.qsize()
        if any(j.status == "processing" for j in list(self.jobs.values())):
            n += 1
        return n

    # ---- storage janitor ----
    def _janitor(self):
        """Best-effort sweeper: every STORAGE_SWEEP_SECONDS, delete files under
        storage/uploads and storage/results whose mtime is older than
        STORAGE_TTL_HOURS (the retention window promised on web/privacy.html).
        Strictly best-effort — any error is logged and retried next sweep;
        it must never crash the app."""
        while True:
            try:
                cutoff = time.time() - STORAGE_TTL_HOURS * 3600
                removed = 0
                for sub in ("uploads", "results"):
                    d = os.path.join(self.storage, sub)
                    if not os.path.isdir(d):
                        continue
                    for name in os.listdir(d):
                        p = os.path.join(d, name)
                        try:
                            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                                os.remove(p)
                                removed += 1
                        except OSError:
                            pass          # locked / already gone — next sweep gets it
                if removed:
                    print(f"[janitor] removed {removed} stored file(s) older than "
                          f"{STORAGE_TTL_HOURS:g}h", flush=True)
                admin_review.sweep()   # retained-store TTL/cap; no-op when disabled
            except Exception as e:
                print("[janitor] sweep skipped:", e, flush=True)
            time.sleep(STORAGE_SWEEP_SECONDS)

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
                self._clean_audio(job, params)   # opt-in add-on; best-effort
                self._make_before(job, params)   # best-effort; never fails the job
                job.status = "done"; job.progress = 1.0; job.message = "Done"
                # Paid exports only (refund_on_fail == the payer's email):
                # tell them it's ready — long exports mean closed tabs.
                # send_export_email is internally best-effort, but guard anyway
                # so a notify bug can never flip a finished job to "error".
                try:
                    if params.get("refund_on_fail"):
                        accounts.send_export_email(
                            params["refund_on_fail"], job_id,
                            params.get("task", "export"), ok=True,
                            ttl_hours=STORAGE_TTL_HOURS)
                except Exception:
                    pass
            except Exception as e:
                job.status = "error"; job.error = str(e)
                job.message = "Failed"
                traceback.print_exc()
                # Paid export failed -> automatically give the credit back.
                # create_job deducted it up-front and stamped the payer's email
                # into params["refund_on_fail"] (exports only). The worker
                # handles each job exactly once and a successful job never
                # reaches this branch, so it cannot double-credit.
                # Known gap: a hard process restart mid-job (deploy, OOM kill)
                # never reaches this line either — those rare cases are
                # handled manually via support/SQL.
                email = params.get("refund_on_fail")
                if email:
                    try:
                        n = max(1, int(params.get("refund_credits", 1)))
                        bal = accounts.add_credits(email, n)
                        print(f"[refund] export job {job_id} failed -> returned "
                              f"{n} credit(s) to {email} (balance now {bal})", flush=True)
                        accounts.send_export_email(
                            email, job_id, params.get("task", "export"),
                            ok=False, credits_refunded=n,
                            ttl_hours=STORAGE_TTL_HOURS)
                    except Exception as rerr:
                        print(f"[refund] FAILED to refund {email} for job "
                              f"{job_id}: {rerr}", flush=True)
            finally:
                # Owner QC panel: log the finished/errored job and retain flagged
                # media. Inert unless WR_ADMIN_TOKEN is set; never touches the job.
                try:
                    admin_review.record_job(job, params)
                except Exception as aerr:
                    print("[qcretain] record skipped:", aerr, flush=True)
                self.q.task_done()

    def _clean_audio(self, job: Job, params: dict):
        """Opt-in 'clean audio' add-on: denoise the finished result's audio
        track (DeepFilterNet CLI) and remux in place. Strictly best-effort —
        any failure leaves the result exactly as rendered, original audio."""
        if not params.get("clean_audio") or not job.result_path:
            return
        if not os.path.isfile(job.result_path):
            return
        job.message = "Cleaning audio..."
        tmp = tempfile.mkdtemp()
        # Stage the remuxed file NEXT TO the result (same filesystem): the
        # storage dir is a separate mount from /tmp on Render, and os.replace
        # cannot cross devices (EXDEV, "Invalid cross-device link").
        swapped = job.result_path + ".audio.tmp.mp4"
        try:
            cleaned = wr.clean_audio_track(job.result_path, tmp)
            if not cleaned:
                return
            wr.mux_audio(job.result_path, job.result_path, swapped, audio=cleaned)
            if os.path.isfile(swapped) and os.path.getsize(swapped) > 0:
                os.replace(swapped, job.result_path)
                print(f"[audio] cleaned audio track for job "
                      f"{getattr(job, 'id', '?')}", flush=True)
        except Exception as e:
            print(f"[audio] skipped ({e!r})", flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                if os.path.isfile(swapped):
                    os.remove(swapped)       # never leave a stray staging file
            except OSError:
                pass

    def _make_before(self, job: Job, params: dict):
        """Emit a browser-safe H.264 '{job_id}_before.mp4' covering the SAME
        segment the result covers (the preview seconds, or the whole clip on
        export), so the front-end Compare view can always play the "before"
        side — the raw upload is often a codec browsers can't decode (HEVC,
        mp4v, .mov/.avi/.mkv). Runs for EVERY task, streams one frame at a
        time (memory-safe), and is strictly best-effort: any failure —
        including probe()'s SystemExit — only skips the clip, never the job."""
        try:
            if not (job.result_path and os.path.exists(job.result_path)):
                return
            job.message = "Preparing the before/after compare..."
            seconds = PREVIEW_SECONDS if job.mode == "preview" else None
            bpath = os.path.join(self.storage, "results", f"{job.id}_before.mp4")
            wr.make_before_clip(params["video_path"], bpath, preview=seconds,
                                max_dim=int(os.environ.get("WR_BEFORE_MAX", "720")))
            if os.path.exists(bpath) and os.path.getsize(bpath) > 0:
                job.before_path = bpath
        except (Exception, SystemExit) as e:
            print("[before] clip skipped:", e)

    def _run(self, job: Job, params: dict):
        video = params["video_path"]
        task = params.get("task", "remove")     # remove | erase | enhance | reframe | blur | captions
        seconds = PREVIEW_SECONDS if job.mode == "preview" else None
        out = os.path.join(self.storage, "results", f"{job.id}.mp4")

        def render_progress(done, total):
            if total:
                job.progress = min(0.97, 0.25 + 0.72 * done / float(total))

        # ---------- ENHANCE: pure quality pass (no mask, no detection) ----------
        if task == "enhance":
            scale = float(params.get("scale", 1.0) or 1.0)
            job.progress = 0.15
            if os.environ.get("WR_ENHANCE_URL", "").strip():
                job.message = ("Enhancing — neural restore + 2× upscale (Real-ESRGAN)..."
                               if scale >= 1.5 else
                               "Enhancing — neural restore (Real-ESRGAN)...")
            else:
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

        # ---------- CAPTIONS: transcribe speech, burn styled subtitles ----------
        if task == "captions":
            job.progress = 0.15
            job.message = "Transcribing speech (AI)..."
            tmpd = tempfile.mkdtemp()
            try:
                segs, lang = wr.transcribe_audio(video, tmpd)
            finally:
                shutil.rmtree(tmpd, ignore_errors=True)
            if not segs:
                raise RuntimeError("No speech detected — nothing to caption.")
            job.srt = wr.segments_to_srt(segs)
            job.progress = 0.55
            job.message = f"Rendering captions ({len(segs)} lines)..."
            wr.caption_video(video, out, segs, preview=seconds,
                             progress_cb=render_progress)
            job.result_path = out
            return

        # ---------- REEL: chained pipeline — trim -> reframe -> captions -> CTA ----------
        if task == "reel":
            ratio = params.get("ratio", "9:16"); fit = params.get("fit", "crop")
            cap_style = params.get("cap_style", "clean")
            cta = (params.get("cta") or "").strip()
            t0, t1 = params.get("trim_start"), params.get("trim_end")
            tmpd = tempfile.mkdtemp()
            try:
                stage = video
                # 1) optional trim (frame-accurate)
                if t0 or t1:
                    job.progress = 0.06
                    job.message = "Reel — trimming your clip..."
                    trimmed = os.path.join(tmpd, "trim.mp4")
                    wr.trim_video(stage, trimmed, start=t0, end=t1)
                    stage = trimmed
                # 2) reframe (smart crop / blurred fill)
                job.progress = 0.12
                job.message = f"Reel 1/3 — reframing to {ratio}..."
                ref_out = os.path.join(tmpd, "reframed.mp4")
                wr.reframe_video(stage, ref_out, ratio=ratio, fit=fit,
                                 preview=seconds,
                                 max_dim=int(os.environ.get("WR_MAX_DIM", "1366")),
                                 focus=params.get("focus"),
                                 progress_cb=lambda d, t:
                                     setattr(job, "progress",
                                             min(0.5, 0.12 + 0.38 * d / max(1, t))))
                # 3) captions (skips gracefully when there's no speech —
                #    a music-only reel must not fail the whole pipeline)
                cap_out = ref_out
                if params.get("captions", True):
                    job.progress = 0.52
                    job.message = "Reel 2/3 — transcribing speech (AI)..."
                    segs = []
                    try:
                        segs, _lang = wr.transcribe_audio(ref_out, tmpd)
                    except Exception as e:
                        print(f"[reel] captions skipped: {e}", flush=True)
                    if segs:
                        job.srt = wr.segments_to_srt(segs)
                        job.progress = 0.62
                        job.message = f"Reel 2/3 — burning captions ({len(segs)} lines)..."
                        cap_out = os.path.join(tmpd, "captioned.mp4")
                        wr.caption_video(ref_out, cap_out, segs, preview=None,
                                         style=cap_style,
                                         pos=params.get("cap_pos", "bottom"),
                                         size=params.get("cap_size", "m"),
                                         color=params.get("cap_color", "white"))
                    else:
                        job.message = "Reel 2/3 — no speech found, skipping captions..."
                # 4) optional CTA end card
                if cta:
                    job.progress = 0.85
                    job.message = "Reel 3/3 — adding your end card..."
                    w2, h2, fps2, _n2 = wr.probe(cap_out)
                    card = os.path.join(tmpd, "card.mp4")
                    wr.make_endcard(w2, h2, fps2, cta, card,
                                    secs=float(params.get("card_secs", 2.5)),
                                    theme=params.get("card_theme", "dark"))
                    wr.concat_videos(cap_out, card, out)
                else:
                    shutil.copyfile(cap_out, out)   # copy, not move (EXDEV)
                job.result_path = out
                return
            finally:
                shutil.rmtree(tmpd, ignore_errors=True)

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
            forced = bool(params.get("track"))
            moving = False
            if not forced and os.environ.get("WR_AUTOTRACK", "1") != "0":
                # AUTO-TRACK: the reported #1 erase failure was a moving object
                # marked WITHOUT ticking "Moving object" — the static mask then
                # inpaints a spot the object has already left, so the object
                # survives in almost every frame. Probe whether the marked
                # content actually stays put; if it clearly moves, track it
                # automatically (the checkbox still forces tracking on).
                try:
                    probe_limit = int(seconds * fps) if seconds else int(min(n, 8 * fps))
                    moving, mstats = wr.marked_region_motion(
                        video, mask, ref=params.get("track_ref"), limit=probe_limit)
                    if moving:
                        print(f"[autotrack] marked region moves {mstats} -> tracking",
                              flush=True)
                except Exception as e:
                    moving = False
                    print("[autotrack] probe skipped:", e)
            if forced or moving:
                # MOVING object: opaque inpaint, tracked each frame
                # (mark once -> follow). Reverse-blend needs a static region.
                info = dict(type="erase", mask=mask, B=None, meanf=None, gain=0.0)
                trk = wr._track_setup(video, ref=params.get("track_ref"), mask01=mask)
                job.message = ("Tracking the object..." if forced else
                               "The marked object moves — tracking it across the video...")
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
                stds = stdg[core > 0]
                # "Static" here is looser than the median test below: opaque
                # stamp glyphs never let the background through, but codec
                # noise still gives them a temporal std of ~3-8 (measured on a
                # CRF-27 clip: glyph median 3.2, p90 8.1, moving bg ~32).
                frac_static = float((stds < 8.0).mean()) if stds.size else 0.0
                if float(np.median(stds)) < 3.0:
                    info = dict(type="erase", mask=mask, B=None, meanf=None, gain=0.0)
                else:
                    info = wr.info_from_user_mask(video, mask, meanf, stdg)
                    mask = info["mask"]
                soft = info.get("B") is not None
                dual = False
                dual_bias = 0.0   # extra confidence soft must beat to win a dual
                if soft:
                    # Guard: high std alone can also mean a MOVING opaque object
                    # brushed without tracking. A true see-through overlay's
                    # structure recurs in EVERY frame; check that consistency.
                    c = _overlay_consistency(video, info, k=6, limit=qc_limit)
                    if c < 0.18:
                        soft = False                     # clearly a moving object
                    elif c < 0.32:
                        dual = True                      # ambiguous: let QC decide
                    elif frac_static >= 0.06:   # generous-brush margins dilute
                        # the static fraction (a hand-drawn box is rarely tight
                        # around the stamp), so trigger the dual scoring early —
                        # a false trigger only costs a few seconds of scoring.
                        # OPAQUE STAMP over a moving background (e.g. a burned-in
                        # camcorder timestamp): the glyph pixels are temporally
                        # STATIC while the background between them moves, so the
                        # median-std test above reads "semi-transparent" and the
                        # consistency check passes (the stamp recurs in every
                        # frame). But reverse-blend cannot recover pixels an
                        # opaque stamp fully covers — it leaves ghost text.
                        # A meaningful static fraction inside the core is the
                        # tell: score BOTH engines and keep the winner — and
                        # since the residual metric is known to overrate
                        # reverse-blend on ghost text, soft must win CLEARLY.
                        dual = True
                        dual_bias = 0.05
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
                        # The two engines' confidences are NOT on a common scale
                        # (soft = projection shrinkage, opaque = fill naturalness),
                        # so a faded-but-legible ghost can outscore a clean fill.
                        # Decisive tie-break: how much of the KNOWN overlay
                        # structure survives each candidate's cleaning (0 = gone).
                        g1 = g2 = None
                        try:
                            g1 = _glyph_residual(video, i1, m1, _inpainter(),
                                                 info["B"], protect=False,
                                                 stdg=stdg, k=4, limit=qc_limit)
                            g2 = _glyph_residual(video, i2, m2, _inpainter(),
                                                 info["B"], protect=True,
                                                 stdg=stdg, k=4, limit=qc_limit)
                            print(f"[dual] glyph residual opaque={g1:.3f} "
                                  f"soft={g2:.3f}", flush=True)
                        except Exception as e:
                            print("[dual] glyph residual skipped:", e, flush=True)
                        if g1 is not None and g2 is not None and abs(g1 - g2) > 0.08:
                            pick_soft = g2 < g1
                        else:
                            pick_soft = q2["confidence"] > q1["confidence"] + dual_bias
                        if pick_soft:
                            info, mask, qc, soft = i2, m2, q2, True
                        else:
                            if g1 is not None:
                                # q1's fill-naturalness score underrates a clean
                                # erase on busy backgrounds; fold in the direct
                                # "is the stamp gone" evidence so the user isn't
                                # told a clean result may be dirty.
                                q1 = dict(q1); q1["glyph_residual"] = round(g1, 3)
                                rr = round(1.0 - min(1.0, g1), 3)
                                q1["residual_reduction"] = max(q1["residual_reduction"], rr)
                                q1["confidence"] = max(q1["confidence"],
                                                       round(rr * (1.0 - q1["damage"]), 3))
                                q1["ok"] = bool(q1["confidence"] >= 0.5
                                                and q1["residual_reduction"] >= 0.4)
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
