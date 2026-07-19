#!/usr/bin/env python3
"""
watermark_remover.py  —  adaptive watermark / overlay remover for video & images.

It figures out WHAT kind of mark it's facing and removes it the right way:

  * TILED / PERIODIC mark (e.g. a repeated "creatify"/"kapwing" stamp)
        -> finds the tiling lattice, REVERSE-BLENDS the whole semi-transparent
           layer out, then neural-inpaints the residue on flat areas.
  * CORNER LOGO / BUG (a fixed logo in one spot)
        -> masks that region and inpaints it.
  * USER-MARKED REGION (you paint over the overlay)
        -> inpaints exactly what you marked (+ reverse-blend if it's periodic).

Faces / detailed areas are protected by a per-frame "flatness" gate, so the
subject never gets the melted look generic removers produce. Audio is preserved.

Inpainting backend:
  * Uses LaMa (`simple-lama-inpainting`) if installed  -> best quality.
  * Falls back to OpenCV inpainting if not              -> runs anywhere.

CLI
---
  # auto-detect and clean a video:
  python watermark_remover.py in.mp4 out.mp4 --auto

  # clean only what you painted (white = remove) on a mask PNG:
  python watermark_remover.py in.mp4 out.mp4 --mask mask.png

  # clean a fixed box (x,y,w,h), multiple allowed with ';':
  python watermark_remover.py in.mp4 out.mp4 --boxes 40,30,180,80

  # fast preview of the first 4 seconds:
  python watermark_remover.py in.mp4 preview.mp4 --auto --preview 4

  # an image:
  python watermark_remover.py in.jpg out.png --mask mask.png

Options: --upscale 1080x1920  --engine auto|lama|classical  --no-sharpen
"""
import argparse, os, subprocess, sys, tempfile, shutil
import numpy as np
import cv2

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

# --------------------------------------------------------------------------- #
# ffmpeg discovery (PATH, else the pip 'imageio-ffmpeg' binary)
# --------------------------------------------------------------------------- #
_FFMPEG = None
def ffmpeg_bin():
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    _FFMPEG = shutil.which("ffmpeg")
    if not _FFMPEG:
        try:
            import imageio_ffmpeg
            _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            sys.exit("ffmpeg not found. Install it or run: pip install imageio-ffmpeg")
    return _FFMPEG


# --------------------------------------------------------------------------- #
# Inpainting backend
# --------------------------------------------------------------------------- #
class Inpainter:
    """Neural inpaint on a remote GPU (Modal) when WR_INPAINT_URL is set; else local
    LaMa if the deps are present; else OpenCV classical. Every path takes a BGR crop
    and mask01 (uint8 {0,1}, 1 = remove) and returns a BGR crop — so process_video,
    autotune and QC don't care which backend is live. If the remote GPU errors on a
    crop we quietly do that one crop classically, so a blip never kills a render."""
    def __init__(self, engine="auto"):
        self.kind = "classical"
        self.lama = None
        self.remote = None
        # Temporal (sequence) inpaint — ProPainter on Modal. Optional layer ON
        # TOP of the per-frame backend below: process_video prefers it for
        # chunked ROI fills; any failure falls back to the per-frame path.
        self.seq_url = os.environ.get("WR_INPAINT_SEQ_URL", "").strip() or None
        if self.seq_url:
            import requests
            self.seq_token = os.environ.get("WR_INPAINT_TOKEN", "")
            self.seq_timeout = float(os.environ.get("WR_INPAINT_SEQ_TIMEOUT", "300"))
            self._seq_sess = requests.Session()
            print("[engine] temporal inpaint = propainter (GPU)", flush=True)
        url = os.environ.get("WR_INPAINT_URL", "").strip()
        if url:
            import requests                      # only needed in remote mode
            self.remote = url
            self.remote_token = os.environ.get("WR_INPAINT_TOKEN", "")
            self.timeout = float(os.environ.get("WR_INPAINT_TIMEOUT", "120"))
            self._sess = requests.Session()
            self.kind = "modal"
            print("[engine] inpainting backend = modal (GPU)", flush=True)
            return
        if engine in ("auto", "lama"):
            try:
                from simple_lama_inpainting import SimpleLama
                import torch as _torch
                self._Image = __import__("PIL.Image", fromlist=["Image"])
                self.lama = SimpleLama(device=_torch.device("cpu"))
                self.kind = "lama"
            except Exception as e:
                if engine == "lama":
                    sys.exit(f"LaMa requested but unavailable: {e}\n"
                             "pip install simple-lama-inpainting pillow torch")
                # engine == "auto": don't swallow the reason — surface WHY LaMa
                # failed to load before silently falling back to classical.
                print(f"[engine] LaMa unavailable, falling back to classical: {e!r}",
                      flush=True)
        print(f"[engine] inpainting backend = {self.kind}", flush=True)

    def _classical(self, bgr, mask01):
        return cv2.inpaint(bgr, (mask01 * 255).astype(np.uint8), 4, cv2.INPAINT_TELEA)

    def _remote_batch(self, items):
        """items: list of (bgr, mask01) with non-empty masks -> list of inpainted
        BGR crops in the SAME order, in ONE Modal GPU call. Retries once."""
        import base64
        payload = []
        for bgr, m in items:
            oki, ib = cv2.imencode(".png", bgr)
            okm, mb = cv2.imencode(".png", (m * 255).astype(np.uint8))
            if not (oki and okm):
                raise RuntimeError("crop encode failed")
            payload.append({"image": base64.b64encode(ib).decode(),
                            "mask": base64.b64encode(mb).decode()})
        body = {"token": self.remote_token, "items": payload}
        last = None
        for _ in range(2):
            try:
                r = self._sess.post(self.remote, json=body, timeout=self.timeout)
                r.raise_for_status()
                outs = r.json()["results"]
                if len(outs) != len(items):
                    raise RuntimeError("result count mismatch")
                res = []
                for (bgr, _m), b64 in zip(items, outs):
                    arr = np.frombuffer(base64.b64decode(b64), np.uint8)
                    o = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if o is None:
                        raise RuntimeError("bad result png")
                    if o.shape[:2] != bgr.shape[:2]:
                        o = cv2.resize(o, (bgr.shape[1], bgr.shape[0]))
                    res.append(o)
                return res
            except Exception as e:
                last = e
        raise last

    def inpaint_sequence(self, frames, masks01):
        """TEMPORAL inpaint: a fixed region across consecutive frames, filled
        flow-consistently in ONE ProPainter GPU call. frames = BGR crops of the
        SAME size; masks01 = uint8 {0,1} per frame. Returns inpainted crops in
        order. RAISES on any failure — callers fall back to the per-frame path
        (LaMa/classical), so a render never hard-fails."""
        import base64
        if not self.seq_url:
            raise RuntimeError("no WR_INPAINT_SEQ_URL")
        fr_b64, mk_b64 = [], []
        for f, m in zip(frames, masks01):
            okf, fb = cv2.imencode(".jpg", f, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            okm, mb = cv2.imencode(".png", (m * 255).astype(np.uint8))
            if not (okf and okm):
                raise RuntimeError("sequence encode failed")
            fr_b64.append(base64.b64encode(fb).decode())
            mk_b64.append(base64.b64encode(mb).decode())
        body = {"token": self.seq_token, "frames": fr_b64, "masks": mk_b64}
        last = None
        for _ in range(2):                       # one retry, like the crop path
            try:
                r = self._seq_sess.post(self.seq_url, json=body,
                                        timeout=self.seq_timeout)
                r.raise_for_status()
                outs = r.json()["results"]
                if len(outs) != len(frames):
                    raise RuntimeError("result count mismatch")
                res = []
                for f, b64 in zip(frames, outs):
                    arr = np.frombuffer(base64.b64decode(b64), np.uint8)
                    o = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if o is None:
                        raise RuntimeError("bad result frame")
                    if o.shape[:2] != f.shape[:2]:
                        o = cv2.resize(o, (f.shape[1], f.shape[0]))
                    res.append(o)
                return res
            except Exception as e:
                last = e
        raise last

    def inpaint_batch(self, items):
        """Inpaint many (bgr, mask01) crops at once, returning BGR crops in order.
        Empty-mask crops pass through untouched. On the GPU backend this is ONE
        HTTP round-trip for the whole batch (the export speed-up); if the GPU
        errors, the whole batch falls back to classical for the affected crops."""
        if not items:
            return []
        todo = [i for i, (b, m) in enumerate(items) if m.max() > 0]
        out = [b for b, m in items]                     # default: pass through
        if not todo:
            return out
        if self.kind == "modal":
            try:
                got = self._remote_batch([items[i] for i in todo])
                for j, i in enumerate(todo):
                    out[i] = got[j]
                return out
            except Exception as e:
                if not getattr(self, "_warned", False):
                    print(f"[modal] batch inpaint failed ({e!r}); using classical "
                          f"for affected crops", flush=True)
                    self._warned = True
                for i in todo:
                    out[i] = self._classical(*items[i])
                return out
        # local lama / classical: no network to amortise, just do each in turn
        for i in todo:
            out[i] = self.inpaint(*items[i])
        return out

    def inpaint(self, bgr, mask01):
        if mask01.max() == 0:
            return bgr
        if self.kind == "modal":
            try:
                return self._remote_batch([(bgr, mask01)])[0]
            except Exception as e:
                if not getattr(self, "_warned", False):
                    print(f"[modal] inpaint failed ({e!r}); using classical for "
                          f"affected crops", flush=True)
                    self._warned = True
                return self._classical(bgr, mask01)
        if self.kind == "lama":
            Image = self._Image
            rgb = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            m = Image.fromarray((mask01 * 255).astype(np.uint8))
            res = np.array(self.lama(rgb, m))
            return cv2.cvtColor(res, cv2.COLOR_RGB2BGR)
        return self._classical(bgr, mask01)


# --------------------------------------------------------------------------- #
# Video I/O
# --------------------------------------------------------------------------- #
def probe(path):
    cap = cv2.VideoCapture(path)
    ok, f = cap.read()
    if not ok:
        sys.exit(f"Cannot read {path}")
    h, w = f.shape[:2]
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    return w, h, fps, n

def frames_iter(path, limit=None):
    cap = cv2.VideoCapture(path)
    i = 0
    while True:
        ok, f = cap.read()
        if not ok or (limit is not None and i >= limit):
            break
        yield f
        i += 1
    cap.release()

def _encoder_vf(upscale=None, sharpen=True, denoise=False, deblock=False):
    """Build the Encoder's ffmpeg -vf chain (split out so it's unit-testable).

    Halo guard: CAS is contrast-adaptive (safe up to ~0.8); classic unsharp is
    what rings on high-contrast edges, so its amount is capped low and reduced
    further after an upscale (lanczos + CAS already crispen the enlarged
    pixels). Denoise keeps a mild temporal term so fine moving detail isn't
    smeared, and deblock runs in 'weak' mode (kills blocking, keeps texture).
    """
    vf = []
    if deblock:
        vf.append("deblock=filter=weak:block=8")
    if denoise:
        vf.append("hqdn3d=1.5:1.2:3:3")
    if upscale:
        vf.append(f"scale={upscale[0]}:{upscale[1]}:flags=lanczos+accurate_rnd")
    s = 0.5 if sharpen is True else max(0.0, min(1.0, float(sharpen or 0.0)))
    if s > 0:
        vf.append(f"cas=strength={round(min(0.8, s), 2)}")
        amt = min(0.4, 0.5 * s) * (0.7 if upscale else 1.0)
        if amt >= 0.05:
            vf.append(f"unsharp=5:5:{round(amt, 2)}:5:5:0.0")
    return vf


class Encoder:
    """Streams raw BGR frames into ffmpeg/libx264 with an optional filter chain
    (in order): deblock -> hqdn3d denoise -> lanczos scale -> cas+unsharp.
    `sharpen` is a bool or a 0..1 strength (True == 0.5, the historic default).
    Sharpening is halo-guarded — see _encoder_vf."""
    def __init__(self, w, h, fps, raw, upscale=None, sharpen=True,
                 denoise=False, deblock=False):
        vf = _encoder_vf(upscale, sharpen, denoise, deblock)
        vf_arg = ["-vf", ",".join(vf)] if vf else []
        self.p = subprocess.Popen(
            [ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "rawvideo",
             "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
             *vf_arg, "-c:v", "libx264", "-crf", "16", "-preset", "medium",
             "-pix_fmt", "yuv420p", raw], stdin=subprocess.PIPE)
    def write(self, frame):
        self.p.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
    def close(self):
        self.p.stdin.close(); self.p.wait()

def mux_audio(video_only, src, out, audio=None):
    # -shortest caps the output at the video's length: previews render only the
    # first N seconds of video, and without it the COPIED audio track kept the
    # source's full duration — a 4 s free preview came back as a "20 s" file
    # (frozen last frame + the entire audio). Full exports are unaffected
    # (video and audio already have ~equal length).
    # `audio` (optional): replacement audio track (e.g. the denoised wav from
    # clean_audio_track) — re-encoded to AAC; None keeps `src`'s track as a
    # stream copy, exactly as before.
    a_in = audio or src
    acodec = ["-c:a", "aac", "-b:a", "192k"] if audio else ["-c:a", "copy"]
    r = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", video_only,
                        "-i", a_in, "-map", "0:v:0", "-map", "1:a:0?",
                        "-c:v", "copy", *acodec, "-shortest",
                        "-movflags", "+faststart", out])
    if r.returncode != 0:
        shutil.copy(video_only, out)


def transcribe_audio(src, tmpdir):
    """Extract mono 16 kHz audio and transcribe it on the Whisper GPU service
    (WR_WHISPER_URL). Returns (segments, language) with segments =
    [{"start", "end", "text"}, ...]. Raises on any failure — captions have no
    meaningful local fallback, so the job fails with a clear message instead."""
    import base64
    import requests
    url = os.environ.get("WR_WHISPER_URL", "").strip()
    if not url:
        raise RuntimeError("Captions aren't enabled on this server yet.")
    wav = os.path.join(tmpdir, "aud16k.wav")
    r = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", src,
                        "-vn", "-ac", "1", "-ar", "16000",
                        "-acodec", "pcm_s16le", wav])
    if r.returncode != 0 or not os.path.isfile(wav) or os.path.getsize(wav) < 8000:
        raise RuntimeError("This clip doesn't seem to have audible speech.")
    body = {"token": os.environ.get("WR_INPAINT_TOKEN", ""),
            "audio": base64.b64encode(open(wav, "rb").read()).decode()}
    timeout = float(os.environ.get("WR_WHISPER_TIMEOUT", "300"))
    last = None
    for _ in range(2):                       # one retry, like the GPU siblings
        try:
            resp = requests.post(url, json=body, timeout=timeout)
            resp.raise_for_status()
            d = resp.json()
            segs = [s for s in d.get("segments", [])
                    if (s.get("text") or "").strip()]
            return segs, d.get("language")
        except Exception as e:
            last = e
    raise RuntimeError(f"Transcription failed ({last!r}).")


def _ts_srt(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def segments_to_srt(segs):
    """Standard .srt text from whisper segments."""
    return "\n".join(
        f"{i + 1}\n{_ts_srt(float(s['start']))} --> {_ts_srt(float(s['end']))}\n"
        f"{str(s['text']).strip()}\n"
        for i, s in enumerate(segs))


def _ts_ass(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


# Caption style presets (CLE-33). fs/mv are fractions of frame height;
# border 1 = outline+shadow, 3 = opaque box behind the text (uses BackColour).
CAPTION_STYLES = {
    "clean":   dict(fs=0.045, bold=1, out_div=12, border=1, mv=0.060,
                    back="&H7F000000"),
    "bold":    dict(fs=0.058, bold=1, out_div=9,  border=3, mv=0.070,
                    back="&H55000000"),
    "minimal": dict(fs=0.034, bold=0, out_div=16, border=1, mv=0.045,
                    back="&H7F000000"),
}


CAPTION_SIZES = {"s": 0.8, "m": 1.0, "l": 1.28}
CAPTION_COLORS = {"white": "&H00FFFFFF", "yellow": "&H0000FFFF",
                  "green": "&H0000FF7F", "pink": "&H00B469FF"}


def caption_video(path, out, segs, preview=None, progress_cb=None, style="clean",
                  pos="bottom", size="m", color="white"):
    """Burn styled captions into the clip (ASS subtitles rendered by ffmpeg's
    libass). One ffmpeg pass — no frame loop.
    style: clean | bold | minimal   pos: bottom | middle
    size: s | m | l                 color: white | yellow | green | pink"""
    st = CAPTION_STYLES.get(str(style).lower(), CAPTION_STYLES["clean"])
    mult = CAPTION_SIZES.get(str(size).lower(), 1.0)
    col = CAPTION_COLORS.get(str(color).lower(), CAPTION_COLORS["white"])
    align = 5 if str(pos).lower() == "middle" else 2   # ASS: 5=mid-center, 2=bottom-center
    w, h, fps, n = probe(path)
    fs = max(16, int(h * st["fs"] * mult))
    mv = max(18, int(h * st["mv"]))
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
        "BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV\n"
        f"Style: Cap,DejaVu Sans,{fs},{col},&H00000000,{st['back']},"
        f"{st['bold']},{st['border']},{max(2, fs // st['out_div'])},1,{align},"
        f"40,40,{mv}\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n")
    lines = []
    for s in segs:
        st, en = float(s["start"]), float(s["end"])
        if preview and st >= preview:
            break
        if preview:
            en = min(en, float(preview))
        txt = (str(s["text"]).strip().replace("\\", "")
               .replace("{", "(").replace("}", ")").replace("\n", " "))
        if txt:
            lines.append(f"Dialogue: 0,{_ts_ass(st)},{_ts_ass(en)},Cap,{txt}")
    if not lines:
        raise RuntimeError("No speech found in the previewed part of the clip.")
    tmp = tempfile.mkdtemp()
    try:
        ass = os.path.join(tmp, "subs.ass")
        with open(ass, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(lines) + "\n")
        dur = ["-t", str(preview)] if preview else []
        r = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error",
                            "-i", path, *dur, "-vf", f"ass={ass}",
                            "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                            "-movflags", "+faststart", out])
        if (r.returncode != 0 or not os.path.isfile(out)
                or os.path.getsize(out) < 1024):
            raise RuntimeError("Caption render failed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if progress_cb:
        progress_cb(1, 1)
    print(f"done -> {out}")


def trim_video(src, out, start=None, end=None):
    """Frame-accurate trim (re-encode, CRF 16). start/end in seconds; either
    may be None. Raises on failure or a resulting clip under 0.5s."""
    args = [ffmpeg_bin(), "-y", "-loglevel", "error", "-i", src]
    if start:
        args += ["-ss", f"{max(0.0, float(start)):.3f}"]
    if end:
        args += ["-to", f"{float(end):.3f}"]
    args += ["-c:v", "libx264", "-crf", "16", "-preset", "fast",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", out]
    r = subprocess.run(args)
    if r.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) < 4096:
        raise RuntimeError("Trim failed — check the start/end times.")


def _endcard_lines(text, max_chars=26):
    """Word-wrap CTA text to at most 2 centred lines."""
    words = str(text).split()
    lines, cur = [], ""
    for wd in words:
        if len(cur) + len(wd) + 1 <= max_chars or not cur:
            cur = (cur + " " + wd).strip()
        else:
            lines.append(cur); cur = wd
        if len(lines) == 2:
            break
    if cur and len(lines) < 2:
        lines.append(cur)
    return lines[:2]


def _font_file():
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.isfile(p):
            return p
    return None


ENDCARD_THEMES = {           # (gradient c0, c1, text color)
    "dark":   ("0x0b0e1a", "0x2a1f5e", "white"),
    "light":  ("0xf3f4fa", "0xd7dbf2", "0x14172b"),
    "accent": ("0x4a63d8", "0x8a5cf0", "white"),
}


def make_endcard(w, h, fps, text, out, secs=2.5, theme="dark"):
    """Gradient CTA card with the user's text centred, faded in, with silent
    audio (so concat keeps consistent streams). Text is written via drawtext
    textfile= — no filter-escaping pitfalls. theme: dark | light | accent."""
    c0, c1, tcol = ENDCARD_THEMES.get(str(theme).lower(), ENDCARD_THEMES["dark"])
    secs = max(1.0, min(5.0, float(secs or 2.5)))
    lines = _endcard_lines(text)
    if not lines:
        raise RuntimeError("End-card text is empty.")
    # Size to the frame, then clamp so the LONGEST line fits inside ~92% of the
    # width (DejaVu Sans Bold advance ≈ 0.62 em): fs <= 0.92*w / (0.62*len).
    longest = max(len(ln) for ln in lines)
    fs = max(20, min(int(min(w, h) * 0.075), int(w * 1.48 / max(1, longest))))
    font = _font_file()
    tmp = tempfile.mkdtemp()
    try:
        draws = []
        for i, ln in enumerate(lines):
            tf = os.path.join(tmp, f"l{i}.txt")
            with open(tf, "w", encoding="utf-8") as f:
                f.write(ln)
            ypos = f"(h-text_h)/2" if len(lines) == 1 else \
                   f"(h/2)-text_h{'-%d' % int(fs*0.15) if i == 0 else '+%d' % int(fs*0.95)}"
            d = (f"drawtext=textfile='{tf}':fontcolor={tcol}:fontsize={fs}"
                 f":x=(w-text_w)/2:y={ypos}")
            if font:
                d += f":fontfile='{font}'"
            draws.append(d)
        vf = ",".join(draws) + f",fade=t=in:st=0:d=0.4,format=yuv420p"
        r = subprocess.run([
            ffmpeg_bin(), "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"gradients=s={w}x{h}:c0={c0}:c1={c1}:"
                  f"x0=0:y0=0:x1={w}:y1={h}:speed=0.00001:"
                  f"duration={secs}:rate={fps:.3f}",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={secs}",
            "-vf", vf, "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k", "-shortest", out])
        if r.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) < 2048:
            raise RuntimeError("End-card render failed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def concat_videos(a, b, out):
    """Concatenate two clips of the SAME dimensions (re-encode; audio
    resampled to a common rate so mismatched sources still join cleanly)."""
    r = subprocess.run([
        ffmpeg_bin(), "-y", "-loglevel", "error", "-i", a, "-i", b,
        "-filter_complex",
        "[0:a]aresample=48000[a0];[1:a]aresample=48000[a1];"
        "[0:v][a0][1:v][a1]concat=n=2:v=1:a=1[v][a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-crf", "17", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", out])
    if r.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) < 4096:
        raise RuntimeError("Joining the end card failed.")


def concat_many(paths, out):
    """Concatenate N clips of the SAME dimensions into one file (re-encode; audio
    resampled to a common rate). A single input is just copied. Used to stitch
    several trimmed segments into one reel. Robust to silent sources: if the
    audio-aware join fails (e.g. a clip has no audio track), it retries video-only."""
    paths = [p for p in paths if p]
    if not paths:
        raise RuntimeError("No clips to join.")
    if len(paths) == 1:
        shutil.copyfile(paths[0], out)
        return
    n = len(paths)

    def _run(with_audio):
        args = [ffmpeg_bin(), "-y", "-loglevel", "error"]
        for p in paths:
            args += ["-i", p]
        if with_audio:
            fc = "".join(f"[{i}:a]aresample=48000[a{i}];" for i in range(n))
            fc += "".join(f"[{i}:v][a{i}]" for i in range(n))
            fc += f"concat=n={n}:v=1:a=1[v][a]"
            maps = ["-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        else:
            fc = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
            maps = ["-map", "[v]"]
        args += ["-filter_complex", fc] + maps + [
            "-c:v", "libx264", "-crf", "17", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
        return subprocess.run(args).returncode

    rc = _run(True)
    if rc != 0 or not os.path.isfile(out) or os.path.getsize(out) < 4096:
        _run(False)                       # a source had no audio — join video only
    if not os.path.isfile(out) or os.path.getsize(out) < 4096:
        raise RuntimeError("Joining your selected parts failed.")


def rotate_video(src, out, mode):
    """Rotate the whole clip a fixed amount (applied before reframing in the reel
    pipeline). mode: 'left' (90° CCW), 'right' (90° CW), '180'. Uses
    -noautorotate so the transpose acts on the raw frames predictably, then
    clears the rotation metadata so players don't re-rotate. Audio is re-encoded
    to AAC for a clean hand-off to the next stage."""
    m = str(mode).lower()
    if m in ("right", "cw"):
        vf = "transpose=1"
    elif m in ("left", "ccw"):
        vf = "transpose=2"
    elif m in ("180", "flip"):
        vf = "transpose=1,transpose=1"
    else:
        raise RuntimeError(f"Unknown rotate mode: {mode}")
    r = subprocess.run([
        ffmpeg_bin(), "-y", "-loglevel", "error", "-noautorotate", "-i", src,
        "-vf", vf, "-c:v", "libx264", "-crf", "17", "-preset", "fast",
        "-pix_fmt", "yuv420p", "-metadata:s:v:0", "rotate=0",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])
    if r.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) < 4096:
        raise RuntimeError("Rotating the video failed.")


def _deepfilter_bin():
    """Path to the deep-filter CLI (DeepFilterNet's MIT/Apache-licensed Rust
    binary, fetched at Docker build time) or None if unavailable."""
    p = os.environ.get("WR_DEEPFILTER_BIN", "/usr/local/bin/deep-filter")
    if os.path.isfile(p) and os.access(p, os.X_OK):
        return p
    return shutil.which("deep-filter")


def clean_audio_track(src, tmpdir):
    """Extract `src`'s audio and denoise it (hiss/wind/hum) with DeepFilterNet.
    Returns the cleaned wav path, or None on no-audio / missing binary / any
    failure — callers keep the original audio. Strictly best-effort."""
    try:
        bin_ = _deepfilter_bin()
        if not bin_:
            print("[audio] deep-filter binary not found — keeping original audio",
                  flush=True)
            return None
        wav = os.path.join(tmpdir, "orig.wav")
        r = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", src,
                            "-vn", "-acodec", "pcm_s16le", "-ar", "48000", wav])
        if r.returncode != 0 or not os.path.isfile(wav) or os.path.getsize(wav) < 1024:
            return None                      # no (usable) audio track
        outdir = os.path.join(tmpdir, "df")
        os.makedirs(outdir, exist_ok=True)
        r = subprocess.run([bin_, wav, "-o", outdir], timeout=600,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            return None
        cleaned = os.path.join(outdir, "orig.wav")
        if not os.path.isfile(cleaned):     # tolerate output-name differences
            wavs = [os.path.join(outdir, n) for n in os.listdir(outdir)
                    if n.endswith(".wav")]
            cleaned = max(wavs, key=os.path.getmtime) if wavs else None
        if cleaned and os.path.getsize(cleaned) > 1024:
            return cleaned
        return None
    except Exception as e:
        print(f"[audio] clean failed ({e!r}) — keeping original audio", flush=True)
        return None


def make_before_clip(src, out, preview=None, max_dim=720):
    """Browser-safe H.264 copy of the ORIGINAL's first `preview` seconds (whole
    clip if None) — the "before" side of the front-end Compare view. Sources
    are often codecs browsers can't decode (HEVC from iPhones, OpenCV's mp4v,
    .mov/.avi/.mkv), so the raw upload can't be shown directly; this re-encode
    through the standard Encoder (libx264 + yuv420p) always plays. No filters
    are applied — no sharpen/denoise — only an optional downscale to `max_dim`
    on the long side (aspect kept, even dims) so the pass stays cheap and
    memory-safe. Audio is muxed exactly like the result's (mux_audio), so the
    before/after containers line up frame for frame in the compare slider."""
    w, h, fps, n = probe(src)
    limit = int(preview * fps) if preview else None
    target = None
    if max_dim and max(w, h) > max_dim:
        s = max_dim / float(max(w, h))
        target = (_even(w * s), _even(h * s))
    tmp = tempfile.mkdtemp(); raw = os.path.join(tmp, "v.mp4")
    try:
        enc = Encoder(w, h, fps, raw, upscale=target, sharpen=False)
        for f in frames_iter(src, limit):
            enc.write(f)
        enc.close()
        mux_audio(raw, src, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
def detect_scenes(path, n):
    prev, diffs = None, []
    for f in frames_iter(path):
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            diffs.append(float(np.mean(np.abs(g - prev))))
        prev = g
    if not diffs:
        return [(0, n)]
    d = np.array(diffs); thr = d.mean() + 3.5 * d.std()
    cuts, merged = [i + 1 for i in range(len(d)) if d[i] > thr and d[i] > 10], []
    for c in cuts:
        if not merged or c - merged[-1] > 8:
            merged.append(c)
    b = [0] + merged + [n]
    segs = [(b[i], b[i + 1]) for i in range(len(b) - 1) if b[i + 1] - b[i] >= 12]
    return segs or [(0, n)]

def _watermark_gradient_map(path, segs, h, w):
    """Per-scene mean gradient MIN — isolates a static mark from changing content."""
    sx = [None] * len(segs); sy = [None] * len(segs); cnt = [0] * len(segs)
    def seg_of(i):
        for k, (a, b) in enumerate(segs):
            if a <= i < b:
                return k
    for i, f in enumerate(frames_iter(path)):
        k = seg_of(i)
        if k is None:
            continue
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, 3); gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, 3)
        if sx[k] is None:
            sx[k] = np.zeros((h, w), np.float32); sy[k] = np.zeros((h, w), np.float32)
        sx[k] += gx; sy[k] += gy; cnt[k] += 1
    mags = [np.sqrt((sx[k]/cnt[k])**2 + (sy[k]/cnt[k])**2) for k in range(len(segs)) if cnt[k]]
    return np.minimum.reduce(mags) if mags else np.zeros((h, w), np.float32)

def _find_lattice(wm):
    """Return (v1, v2, strength) of the tiling lattice via autocorrelation, or None."""
    h, w = wm.shape
    hp = wm - cv2.GaussianBlur(wm, (0, 0), 8); hp -= hp.mean()
    ac = np.fft.fftshift(np.fft.ifft2(np.abs(np.fft.fft2(hp)) ** 2).real)
    cy, cx = h // 2, w // 2
    yy, xx = np.ogrid[:h, :w]; r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    center = float(ac[cy, cx]) + 1e-9
    ac[r < 0.03 * max(h, w)] = -1e18
    dil = cv2.dilate(ac.astype(np.float32), np.ones((9, 9), np.uint8))
    pk = (ac >= dil) & (ac > np.percentile(ac, 99.0))
    ys, xs = np.where(pk)
    cand = sorted(((int(y-cy), int(x-cx), float(ac[y, x])) for y, x in zip(ys, xs)
                   if 0.04*max(h, w) < np.hypot(y-cy, x-cx) < 0.55*max(h, w)),
                  key=lambda t: -t[2])
    if len(cand) < 2:
        return None
    strength = cand[0][2] / center           # peak height relative to autocorr peak
    v1 = np.array(cand[0][:2]); v2 = None
    for dy, dx, _ in cand[1:]:
        if abs(v1[0]*dx - v1[1]*dy) > 0.15 * np.hypot(*v1) * np.hypot(dy, dx):
            v2 = np.array([dy, dx]); break
    if v2 is None:
        return None
    return v1, v2, strength

def _refine_and_extract(meanf, v1, v2):
    """Sub-pixel lattice refine + Moisan periodic FFT extraction -> clean layer B."""
    h, w = meanf.shape[:2]; cy, cx = h // 2, w // 2
    yy, xx = np.ogrid[:h, :w]
    A0 = np.array([[v1[0], v2[0]], [v1[1], v2[1]]], float); Bk0 = np.linalg.inv(A0).T
    Rg = (meanf - cv2.boxFilter(meanf, -1, (91, 91))).mean(2)
    win = np.outer(np.hanning(h), np.hanning(w))
    mag = np.abs(np.fft.fftshift(np.fft.fft2(Rg * win)))
    det = mag.copy(); det[np.sqrt((yy-cy)**2 + (xx-cx)**2) < 4] = 0
    dil = cv2.dilate(det.astype(np.float32), np.ones((9, 9), np.uint8))
    pk = (det >= dil) & (det > np.percentile(det, 99.6))
    M = np.array([Bk0[:, 0], Bk0[:, 1]]).T; obs = []
    for y, x in zip(*np.where(pk)):
        if not (1 <= y < h-1 and 1 <= x < w-1):
            continue
        lx, cc, rx = (np.log(mag[y, x-1]+1), np.log(mag[y, x]+1), np.log(mag[y, x+1]+1))
        ddx = 0.5*(lx-rx)/(lx-2*cc+rx+1e-9)
        uy, dv = np.log(mag[y-1, x]+1), np.log(mag[y+1, x]+1)
        ddy = 0.5*(uy-dv)/(uy-2*cc+dv+1e-9)
        fy = (y+np.clip(ddy, -1, 1)-cy)/h; fx = (x+np.clip(ddx, -1, 1)-cx)/w
        mn = np.linalg.solve(M, [fy, fx]); mnr = np.round(mn)
        if np.all(np.abs(mn-mnr) < 0.25) and not np.all(mnr == 0):
            obs.append((mnr[0], mnr[1], fy, fx))
    if len(obs) < 4:
        return None
    obs = np.array(obs); Mmn = obs[:, :2]
    sY = np.linalg.lstsq(Mmn, obs[:, 2], rcond=None)[0]
    sX = np.linalg.lstsq(Mmn, obs[:, 3], rcond=None)[0]
    b1 = np.array([sY[0], sX[0]]); b2 = np.array([sY[1], sX[1]])
    def moi(u):
        v = np.zeros_like(u)
        v[0, :] += u[-1, :]-u[0, :]; v[-1, :] += u[0, :]-u[-1, :]
        v[:, 0] += u[:, -1]-u[:, 0]; v[:, -1] += u[:, 0]-u[:, -1]
        fxx = np.fft.fftfreq(w).reshape(1, w); fyy = np.fft.fftfreq(h).reshape(h, 1)
        den = (2*np.cos(2*np.pi*fxx)+2*np.cos(2*np.pi*fyy)-4); den[0, 0] = 1
        Vf = np.fft.fft2(v); Vf[0, 0] = 0
        return u - np.real(np.fft.ifft2(Vf/den))
    mask_l = np.zeros((h, w), np.float32)
    for m in range(-12, 13):
        for nn in range(-12, 13):
            if m == 0 and nn == 0:
                continue
            ff = m*b1 + nn*b2; iy = cy+ff[0]*h; ix = cx+ff[1]*w
            if 0 <= iy < h and 0 <= ix < w:
                cv2.circle(mask_l, (int(round(ix)), int(round(iy))), 2, 1.0, -1)
    R = meanf - cv2.boxFilter(meanf, -1, (91, 91))
    B = np.zeros_like(R)
    for c in range(3):
        Fc = np.fft.fftshift(np.fft.fft2(moi(R[:, :, c]))) * mask_l
        B[:, :, c] = np.real(np.fft.ifft2(np.fft.ifftshift(Fc)))
    return B

def _calibrate_gain(path, B, meanf, n):
    """Pick the reverse-blend strength that best flattens the residual high-freq."""
    C = 245.0
    def hp(x):
        g = cv2.cvtColor(np.clip(x, 0, 255).astype(np.uint8),
                         cv2.COLOR_BGR2GRAY).astype(np.float32)
        return g - cv2.GaussianBlur(g, (0, 0), 6)
    num = de = 0.0
    for i, f in enumerate(frames_iter(path)):
        if i % max(1, n // 6):
            continue
        O = f.astype(np.float32); r = np.clip((C - O) / (C - meanf + 1e-3), 0, 2.5)
        D = hp(O) - hp(O - B * r); num += float((hp(O) * D).sum()); de += float((D * D).sum())
    return float(np.clip(num / max(de, 1e-6), 0.5, 2.0))


def _detect_soft_overlay(meanf, std_gray=None):
    """Find a STATIC, SEMI-TRANSPARENT logo/wordmark (e.g. a stock-site stamp
    like 'shutterstock' across the middle of the frame).

    Such a mark is NOT periodic (so the lattice path misses it) and is NOT a
    compact opaque bug. We localise it by the fact that, in the temporal mean,
    its structure is a 2-D consistent overlay: it has edge energy in BOTH the
    x and y directions (text), which separates it from 1-D edges like a horizon
    (y-energy only) and from moving content (which averages out of the mean).
    Returns (mask, B) for the reverse-blend path, or None.
    """
    h, w = meanf.shape[:2]
    gmean = cv2.cvtColor(np.clip(meanf, 0, 255).astype(np.uint8),
                         cv2.COLOR_BGR2GRAY).astype(np.float32)
    mhp = gmean - cv2.GaussianBlur(gmean, (0, 0), 9)          # consistent overlay structure
    gx = cv2.Sobel(mhp, cv2.CV_32F, 1, 0, 3)
    gy = cv2.Sobel(mhp, cv2.CV_32F, 0, 1, 3)
    Ex = cv2.boxFilter(gx * gx, -1, (15, 15))
    Ey = cv2.boxFilter(gy * gy, -1, (15, 15))
    tex = np.sqrt(np.minimum(Ex, Ey))                        # 2-D (text) energy, not 1-D edges
    hi = float(np.percentile(tex, 99.7)); med = float(np.percentile(tex, 50)) + 1e-6
    if hi < 6.0 or hi / med < 8.0:                           # no distinct consistent overlay
        return None
    m = (tex > np.percentile(tex, 98.3)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 31), np.uint8))
    nlab, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    keep = np.zeros_like(m)
    for k in range(1, nlab):
        x, y, bw, bh, area = stats[k]
        if area < 0.0015 * m.size or bw > 0.85 * w:          # too small / full-width (horizon)
            continue
        if std_gray is not None and float(std_gray[lab == k].mean()) < 3.0:
            continue                                         # baked-in/opaque -> not reverse-blend
        keep[lab == k] = 1
    if keep.sum() == 0:
        return None
    mask = cv2.dilate(keep, np.ones((7, 7), np.uint8))
    cov = float(mask.mean())
    if not (0.002 < cov < 0.25):
        return None
    B = (meanf - cv2.GaussianBlur(meanf, (0, 0), 9)) * mask[..., None]
    return mask, B


def _layer_from_mean(meanf, iters=2):
    """CLE-45: estimate the overlay layer straight from the temporal mean —
    B = meanf − inpaint(meanf over the mark) — with one support-refinement pass.
    Makes NO lattice assumption, so offset/rhombic tile grids (most real
    watermarks) work as well as rectangular ones. Returns (B, support_mask)."""
    mgray = cv2.cvtColor(np.clip(meanf, 0, 255).astype(np.uint8),
                         cv2.COLOR_BGR2GRAY).astype(np.float32)
    prom = np.maximum(mgray - cv2.GaussianBlur(mgray, (0, 0), 9), 0)
    m = cv2.dilate((prom > np.percentile(prom, 90)).astype(np.uint8),
                   np.ones((3, 3), np.uint8))
    B = None
    for _ in range(max(1, iters)):
        bg = cv2.inpaint(np.clip(meanf, 0, 255).astype(np.uint8), m, 4,
                         cv2.INPAINT_NS).astype(np.float32)
        B = np.clip(meanf - bg, 0, None)
        lay = B.mean(2)
        pos = lay[lay > 0.5]
        thr = max(2.0, float(np.percentile(pos, 25))) if pos.size else 2.0
        m = cv2.dilate((lay > thr).astype(np.uint8), np.ones((3, 3), np.uint8))
    return B, m


def _calibrate_gain_pc(path, B, meanf, n):
    """Per-channel variant of _calibrate_gain (real overlays are rarely
    channel-uniform). Returns a (3,) vector — broadcasts everywhere the scalar
    gain did (`gain * B * r`)."""
    C = 245.0
    def hp(x):
        return x - cv2.GaussianBlur(x, (0, 0), 6)
    num = np.zeros(3); de = np.zeros(3)
    for i, f in enumerate(frames_iter(path)):
        if i % max(1, n // 6):
            continue
        O = f.astype(np.float32); r = np.clip((C - O) / (C - meanf + 1e-3), 0, 2.5)
        for c in range(3):
            D = hp(O[..., c]) - hp(O[..., c] - B[..., c] * r[..., c])
            num[c] += float((hp(O[..., c]) * D).sum()); de[c] += float((D * D).sum())
    return np.clip(num / np.maximum(de, 1e-6), 0.5, 2.0)


def _score_layer(path, B, region, meanf, n):
    """How well does subtracting B·r flatten the mark on 2 sampled frames?
    `region` must be the SAME pixels for every candidate (use the union of
    candidate masks) or the scores aren't comparable. Lower mean residual
    high-pass energy = better candidate."""
    C = 245.0
    def hp(x):
        g = cv2.cvtColor(np.clip(x, 0, 255).astype(np.uint8),
                         cv2.COLOR_BGR2GRAY).astype(np.float32)
        return g - cv2.GaussianBlur(g, (0, 0), 6)
    total, cnt = 0.0, 0
    cap = cv2.VideoCapture(path)
    try:
        for idx in (n // 3, (2 * n) // 3):
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, idx))
            ok, f = cap.read()
            if not ok:
                continue
            O = f.astype(np.float32)
            r = np.clip((C - O) / (C - meanf + 1e-3), 0, 3.0)
            v = hp(O - B * r)[region > 0]
            total += float((v ** 2).sum()); cnt += v.size
    finally:
        cap.release()
    return total / max(cnt, 1)


def _lattice_motion_support(wm, lat, std_gray):
    """CLE-60: verify a detected lattice is a real OVERLAY, not periodic
    *content* (wall art, fabric print). A genuine tiled watermark is stamped
    over everything — its temporally-consistent structure repeats at the
    lattice period on MOVING regions too. Content periodicity only repeats
    where that content sits (static background). We correlate the consistency
    map with its lattice-shifted self and ask whether the periodic-evidence
    support extends onto moving areas at a sane fraction of its overall
    density. Clips with almost no motion can't be judged -> pass through.
    Measured: real tiled clip ratio 0.85, content-periodicity analog 0.04."""
    v1, v2 = lat[0], lat[1]
    hp = wm - cv2.GaussianBlur(wm, (0, 0), 8)
    sup = None
    for v in (v1, v2):
        sh = np.roll(hp, (int(round(v[0])), int(round(v[1]))), axis=(0, 1))
        num = cv2.boxFilter(hp * sh, -1, (31, 31))
        den = np.sqrt(np.clip(cv2.boxFilter(hp * hp, -1, (31, 31)) *
                              cv2.boxFilter(sh * sh, -1, (31, 31)), 0, None)) + 1e-6
        c = num / den
        sup = c if sup is None else np.maximum(sup, c)
    per = sup > 0.4
    mov = std_gray > max(8.0, float(np.percentile(std_gray, 80)))
    if float(mov.mean()) < 0.02 or float(per.mean()) < 1e-4:
        return True                                  # not enough motion to judge
    ratio = float(per[mov].mean()) / max(float(per.mean()), 1e-6)
    if ratio < 0.15:
        print(f"[engine] tiled rejected: periodic structure only on static "
              f"content (motion-support ratio {ratio:.2f}) -> not an overlay",
              flush=True)
        return False
    return True


def detect(path):
    """Return dict(type, mask, B, meanf, gain). type in tiled|logo-soft|logo|none."""
    w, h, fps, n = probe(path)
    segs = detect_scenes(path, n)
    acc = np.zeros((h, w, 3), np.float64); cnt = 0
    sg = np.zeros((h, w), np.float64); sg2 = np.zeros((h, w), np.float64)
    for f in frames_iter(path):
        acc += f; cnt += 1
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float64)
        sg += g; sg2 += g * g
    meanf = (acc / max(cnt, 1)).astype(np.float32)
    mg = sg / max(cnt, 1)
    std_gray = np.sqrt(np.clip(sg2 / max(cnt, 1) - mg * mg, 0, None)).astype(np.float32)
    wm = _watermark_gradient_map(path, segs, h, w)

    lat = _find_lattice(wm)
    if lat and lat[2] > 0.10 and _lattice_motion_support(wm, lat, std_gray):
        # strong periodicity confirmed as an OVERLAY (CLE-60 gate) -> TILED
        # CLE-45: two candidate overlay layers, keep whichever measurably
        # flattens the mark better. The lattice tile-average bleeds moving
        # background into B (and fails outright on offset/rhombic grids —
        # eval: 15-17 dB vs 26 dB for the inpainted-mean layer), so the
        # mean-layer estimate is usually the winner; the score keeps us honest.
        cands = []
        B_lat = _refine_and_extract(meanf, lat[0], lat[1])
        if B_lat is not None:
            prom = np.maximum(B_lat.mean(2) - cv2.GaussianBlur(B_lat.mean(2), (0, 0), 9), 0)
            m_lat = (prom > np.percentile(prom, 63)).astype(np.uint8)
            m_lat = cv2.dilate(cv2.morphologyEx(m_lat, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)),
                               np.ones((3, 3), np.uint8))
            cands.append(("lattice", B_lat, m_lat))
        B_mean, m_mean = _layer_from_mean(meanf)
        if B_mean is not None and m_mean.any():
            cands.append(("meanlayer", B_mean,
                          cv2.dilate(m_mean, np.ones((3, 3), np.uint8))))
        if cands:
            union = cands[0][2].copy()
            for _, _, m in cands[1:]:
                union |= m
            best = min(cands, key=lambda c: _score_layer(path, c[1], union, meanf, n))
            name, B, mask = best
            gain = _calibrate_gain_pc(path, B, meanf, n)
            print(f"[engine] tiled layer estimator = {name} "
                  f"(gain {np.round(gain, 2).tolist()})", flush=True)
            return dict(type="tiled", mask=mask, B=B, meanf=meanf, gain=gain)

    # SEMI-TRANSPARENT static logo / wordmark (e.g. a stock-site stamp) -> reverse-blend
    soft = _detect_soft_overlay(meanf, std_gray)
    if soft is not None:
        mask, B = soft
        gain = _calibrate_gain(path, B, meanf, n)
        return dict(type="logo-soft", mask=mask, B=B, meanf=meanf, gain=gain)

    # not tiled: look for a compact static high-gradient blob (corner logo / bug)
    wn = cv2.GaussianBlur(wm, (0, 0), 2)
    th = (wn > max(np.percentile(wn, 99.0), 6.0)).astype(np.uint8)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cov = th.mean()
    if 0.0008 < cov < 0.15:                         # localized -> LOGO
        m = cv2.dilate(th, np.ones((9, 9), np.uint8))
        return dict(type="logo", mask=m, B=None, meanf=meanf, gain=0.0)
    return dict(type="none", mask=None, B=None, meanf=meanf, gain=0.0)


# --------------------------------------------------------------------------- #
# Interactive helpers (canvas reference frame + user-painted mask)
# --------------------------------------------------------------------------- #
def sharpest_frame(path, limit=400, with_index=False):
    """Return the sharpest frame (max Laplacian variance) — a good canvas still.
    with_index=True also returns the frame index (used as tracking reference)."""
    best = None; bestv = -1.0; besti = 0
    for i, f in enumerate(frames_iter(path, limit)):
        v = float(cv2.Laplacian(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
        if v > bestv:
            bestv = v; best = f.copy(); besti = i
    return (best, besti) if with_index else best


def mean_and_std(path):
    """One pass: temporal mean frame (BGR float32) + per-pixel gray std (float32)."""
    w, h, fps, n = probe(path)
    acc = np.zeros((h, w, 3), np.float64); cnt = 0
    sg = np.zeros((h, w), np.float64); sg2 = np.zeros((h, w), np.float64)
    for f in frames_iter(path):
        acc += f; cnt += 1
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float64); sg += g; sg2 += g * g
    meanf = (acc / max(cnt, 1)).astype(np.float32)
    mg = sg / max(cnt, 1)
    std_gray = np.sqrt(np.clip(sg2 / max(cnt, 1) - mg * mg, 0, None)).astype(np.float32)
    return meanf, std_gray


def reference_image(path, meanf=None):
    """A sharp still with any STATIC semi-transparent mark highlighted (yellow
    glow), so the user can SEE exactly where to brush. Returns BGR uint8."""
    if meanf is None:
        meanf, _ = mean_and_std(path)
    gm = cv2.cvtColor(np.clip(meanf, 0, 255).astype(np.uint8),
                      cv2.COLOR_BGR2GRAY).astype(np.float32)
    mhp = gm - cv2.GaussianBlur(gm, (0, 0), 9)
    gx = cv2.Sobel(mhp, cv2.CV_32F, 1, 0, 3); gy = cv2.Sobel(mhp, cv2.CV_32F, 0, 1, 3)
    tex = np.sqrt(np.minimum(cv2.boxFilter(gx * gx, -1, (15, 15)),
                             cv2.boxFilter(gy * gy, -1, (15, 15))))
    thr = np.percentile(tex, 98.5); hi = np.percentile(tex, 99.9)
    t = np.clip((tex - thr) / (hi - thr + 1e-6), 0, 1)       # only the mark glows
    t = cv2.GaussianBlur(t, (0, 0), 2)
    base = sharpest_frame(path)
    if base is None:
        base = np.clip(meanf, 0, 255).astype(np.uint8)
    out = base.astype(np.float32)
    tint = np.zeros_like(out); tint[..., 1] = 255; tint[..., 2] = 255   # yellow highlight
    a = t[..., None] * 0.75
    out = out * (1 - a) + tint * a
    return np.clip(out, 0, 255).astype(np.uint8)


def info_from_user_mask(path, mask01, meanf=None, std_gray=None):
    """Build processing info for a user-painted mask (white = remove).
    Semi-transparent area (content shows through) -> reverse-blend + inpaint.
    Opaque area (no content shows through) -> straight inpaint."""
    w, h, fps, n = probe(path)
    if meanf is None or std_gray is None:
        meanf, std_gray = mean_and_std(path)
    mask = (mask01 > 0).astype(np.uint8)
    if mask.sum() == 0:
        return dict(type="manual", mask=mask, B=None, meanf=meanf, gain=0.0)
    # Opacity on the mask CORE (eroded to drop the sloppy brush margin that often
    # spills onto a MOVING background), via a robust median. The old mean over the
    # whole mask misread an opaque logo as see-through whenever the brush caught
    # motion at its edge -> reverse-blend + subject-gate then left the logo behind
    # (same failure class as CLE-25, here on the watermark-remove path).
    core = cv2.erode(mask, np.ones((5, 5), np.uint8))
    core_std = std_gray[core > 0] if int(core.sum()) >= 16 else std_gray[mask > 0]
    if float(np.median(core_std)) < 3.0:                     # opaque -> inpaint only
        return dict(type="manual", mask=mask, B=None, meanf=meanf, gain=0.0)
    B = (meanf - cv2.GaussianBlur(meanf, (0, 0), 9)) * mask[..., None]
    gain = _calibrate_gain(path, B, meanf, n)
    return dict(type="logo-soft", mask=mask, B=B, meanf=meanf, gain=gain)


# --------------------------------------------------------------------------- #
# Masks from user input
# --------------------------------------------------------------------------- #
def mask_from_painted(png_path, h, w):
    m = cv2.imread(png_path, cv2.IMREAD_GRAYSCALE)
    if m is None:
        sys.exit(f"Cannot read mask {png_path}")
    if m.shape != (h, w):
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
    return (m > 127).astype(np.uint8)

def mask_from_boxes(boxes, h, w):
    m = np.zeros((h, w), np.uint8)
    for (x, y, bw, bh) in boxes:
        m[max(0, y):y+bh, max(0, x):x+bw] = 1
    return m


# --------------------------------------------------------------------------- #
# Enhance & reframe (repurposing tasks — no watermark logic involved)
# --------------------------------------------------------------------------- #
def _even(v):
    """Nearest-not-above even int >= 2 (libx264 + yuv420p need even dims)."""
    v = int(round(v))
    return max(2, v - (v & 1))


def _enhance_video_neural(path, out, url, scale=1.0, preview=None, max_dim=None,
                          progress_cb=None):
    """TRUE neural enhance on the Modal GPU: Real-ESRGAN restore/upscale, with
    optional GFPGAN face restore (WR_ENHANCE_FACE, default on). Frames travel
    in chunks of WR_ENHANCE_BATCH as JPEG-95; the Encoder does the final fit to
    max_dim with its ffmpeg filters OFF (the network already restored detail —
    re-sharpening would halo it). Raises on any failure so enhance_video can
    fall back to the classical chain. Audio is preserved via mux_audio."""
    import base64
    import requests
    token = os.environ.get("WR_INPAINT_TOKEN", "")
    batch = max(1, int(os.environ.get("WR_ENHANCE_BATCH", "6")))
    timeout = float(os.environ.get("WR_ENHANCE_TIMEOUT", "300"))
    face = os.environ.get("WR_ENHANCE_FACE", "1").lower() not in ("0", "false", "")
    netscale = 2.0 if float(scale) >= 1.5 else 1.0
    w, h, fps, n = probe(path)
    limit = int(preview * fps) if preview else None
    ow, oh = w * float(scale), h * float(scale)      # requested output size
    if max_dim and max(ow, oh) > max_dim:
        s = max_dim / max(ow, oh)
        ow, oh = ow * s, oh * s
    ow, oh = _even(ow), _even(oh)
    # Sizing rule — quality-neutral by construction: never feed the network
    # FEWER pixels than the output holds (that would swap real source detail
    # for hallucinated detail), and never more than the output can use (the
    # encoder would just downscale the excess away — pure wasted GPU time):
    #   * source covers the output -> pre-scale frames to the output size and
    #     run the network as pure RESTORATION (netscale 1). The max_dim cap
    #     already bounds what the customer receives, so nothing they get back
    #     is degraded.
    #   * genuine enlargement (source smaller than output) -> send the source
    #     untouched and let the network really upscale it (netscale 2).
    if w * h >= ow * oh:
        sw, sh, netscale = ow, oh, 1.0
    else:
        sw, sh = w, h
    sess = requests.Session()

    def send(chunk):
        items = []
        for f in chunk:
            ok, jb = cv2.imencode(".jpg", f, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            if not ok:
                raise RuntimeError("frame encode failed")
            items.append({"image": base64.b64encode(jb).decode()})
        body = {"token": token, "scale": netscale, "face_enhance": face,
                "items": items}
        last = None
        for _ in range(2):                            # one retry, like inpaint
            try:
                r = sess.post(url, json=body, timeout=timeout)
                r.raise_for_status()
                outs = r.json()["results"]
                if len(outs) != len(chunk):
                    raise RuntimeError("result count mismatch")
                res = []
                for b64 in outs:
                    o = cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8),
                                     cv2.IMREAD_COLOR)
                    if o is None:
                        raise RuntimeError("bad result frame")
                    res.append(o)
                return res
            except Exception as e:
                last = e
        raise last

    print("[engine] enhance backend = modal (GPU, Real-ESRGAN"
          + ("+GFPGAN)" if face else ")"), flush=True)
    # WR_ENHANCE_PARALLEL chunks are kept in flight at once (default 3): Modal
    # auto-scales a container per concurrent request, so wall-clock drops ~3x
    # at the SAME total GPU cost. Results are written strictly in submission
    # order, so the output video is identical to the sequential path.
    workers = max(1, int(os.environ.get("WR_ENHANCE_PARALLEL", "3")))
    from collections import deque
    from concurrent.futures import ThreadPoolExecutor
    tmp = tempfile.mkdtemp(); raw = os.path.join(tmp, "v.mp4")
    enc = None
    total = limit or n
    written = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        pending = deque()                             # futures, submission order

        def write_chunk(res):
            nonlocal enc, written
            for o in res:
                if enc is None:                       # dims from the first result
                    rh, rw = o.shape[:2]
                    target = (ow, oh) if (ow, oh) != (rw, rh) else None
                    enc = Encoder(rw, rh, fps, raw, upscale=target, sharpen=False)
                enc.write(o)
                written += 1
                if progress_cb and total and written % 5 == 0:
                    progress_cb(written, total)
            if written % 50 < len(res):
                print(f"  frame {written}/{total or '?'}", flush=True)

        buf = []
        for f in frames_iter(path, limit):
            if (f.shape[1], f.shape[0]) != (sw, sh):  # shrink BEFORE buffering
                f = cv2.resize(f, (sw, sh), interpolation=cv2.INTER_AREA)
            buf.append(f)
            if len(buf) >= batch:
                pending.append(pool.submit(send, buf)); buf = []
                while len(pending) >= workers:        # bounded lookahead
                    write_chunk(pending.popleft().result())
        if buf:
            pending.append(pool.submit(send, buf))
        while pending:
            write_chunk(pending.popleft().result())
        if enc is None:
            raise RuntimeError("no frames")
        enc.close(); enc = None
        mux_audio(raw, path, out)
    finally:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        if enc is not None:
            try:
                enc.close()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"done -> {out}")


def enhance_video(path, out, scale=1.0, denoise=True, sharpen=0.6, deblock=None,
                  preview=None, max_dim=None, progress_cb=None):
    """ENHANCE entry point. When WR_ENHANCE_URL is set, runs TRUE neural
    enhance (Real-ESRGAN + optional GFPGAN on the Modal GPU); on any failure —
    or with the var unset — falls back to the classical ffmpeg chain below
    (lanczos upscale, hqdn3d denoise, deblock, halo-guarded cas+unsharp), so
    an enhance job never hard-fails.
      scale     output scale factor (1.0 or 2.0 from the UI)
      sharpen   0..1 strength (0 = off; classical chain only)
      deblock   None -> follow `denoise`  (classical chain only)
      max_dim   cap on the OUTPUT long side (memory guard; None = uncapped)
    """
    url = os.environ.get("WR_ENHANCE_URL", "").strip()
    if url:
        try:
            return _enhance_video_neural(path, out, url, scale=scale,
                                         preview=preview, max_dim=max_dim,
                                         progress_cb=progress_cb)
        except Exception as e:
            print(f"[modal] enhance failed ({e!r}); falling back to the "
                  "classical chain", flush=True)
    w, h, fps, n = probe(path)
    limit = int(preview * fps) if preview else None
    if deblock is None:
        deblock = bool(denoise)
    tw, th = w * float(scale), h * float(scale)
    if max_dim and max(tw, th) > max_dim:
        s = max_dim / max(tw, th)
        tw, th = tw * s, th * s
    tw, th = _even(tw), _even(th)
    target = (tw, th) if (tw, th) != (w, h) else None
    tmp = tempfile.mkdtemp(); raw = os.path.join(tmp, "v.mp4")
    enc = Encoder(w, h, fps, raw, upscale=target, sharpen=sharpen,
                  denoise=denoise, deblock=deblock)
    total = limit or n
    for k, f in enumerate(frames_iter(path, limit)):
        enc.write(f)
        if progress_cb and total and k % 10 == 0:
            progress_cb(k + 1, total)
        if k % 50 == 0:
            print(f"  frame {k+1}/{total or '?'}", flush=True)
    enc.close(); mux_audio(raw, path, out); shutil.rmtree(tmp, ignore_errors=True)
    print(f"done -> {out}")


_FACE_CASCADE = None
def _face_cascade():
    """The Haar face detector bundled with OpenCV, or None if unavailable."""
    global _FACE_CASCADE
    if _FACE_CASCADE is None:
        try:
            c = cv2.CascadeClassifier(
                os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
            _FACE_CASCADE = c if not c.empty() else False
        except Exception:
            _FACE_CASCADE = False
    return _FACE_CASCADE or None


# YuNet — neural face detector (tiny ONNX, ships via the Dockerfile which fetches
# it from the official OpenCV zoo at build time). Preferred over the Haar
# cascades for reframe subject-tracking and privacy blur: it handles profile,
# tilted and partially-occluded faces that Haar misses. Everything falls back
# to Haar automatically if the model file / OpenCV support is absent.
_YUNET_PATH = os.environ.get(
    "WR_YUNET_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "models", "face_detection_yunet_2023mar.onnx"))
_YUNET = None

def _yunet():
    """cv2.FaceDetectorYN (YuNet) instance, or None -> caller uses Haar."""
    global _YUNET
    if _YUNET is None:
        try:
            if os.path.isfile(_YUNET_PATH) and hasattr(cv2, "FaceDetectorYN_create"):
                _YUNET = cv2.FaceDetectorYN_create(
                    _YUNET_PATH, "", (320, 320),
                    score_threshold=0.6, nms_threshold=0.3, top_k=200)
                print("[engine] face detector = YuNet (neural)")
            else:
                _YUNET = False
                print("[engine] face detector = Haar (YuNet model not found)")
        except Exception as e:
            _YUNET = False
            print(f"[engine] face detector = Haar (YuNet unavailable: {e})")
    return _YUNET or None

def _yunet_detect(det, bgr, min_side=0):
    """YuNet detections on one BGR image -> [(x, y, w, h), ...] int boxes."""
    h, w = bgr.shape[:2]
    det.setInputSize((w, h))
    faces = det.detect(bgr)[1]
    if faces is None:
        return []
    out = []
    for f in faces:
        x, y, bw, bh = (int(round(float(v))) for v in f[:4])
        if bw > 0 and bh > 0 and min(bw, bh) >= min_side:
            out.append((max(0, x), max(0, y), bw, bh))
    return out


def parse_ratio(ratio):
    """'9:16' -> 0.5625 (w/h). Raises ValueError on nonsense."""
    try:
        a, b = str(ratio).replace("x", ":").split(":")
        r = float(a) / float(b)
    except Exception:
        raise ValueError(f"Bad aspect ratio {ratio!r}; use e.g. '9:16', '1:1', '4:5'.")
    if not (0.1 <= r <= 10.0):
        raise ValueError(f"Aspect ratio {ratio!r} is out of range.")
    return r


def _interest_track(path, limit=None, sample_hz=6.0, max_w=384):
    """CPU-light subject locator: on sampled, downscaled frames find the
    'interesting' center — faces (area-weighted) > motion energy > detail
    (gradient) — plus shot cuts from big luma jumps between samples.
    Faces get MEMORY: once seen, their spot is held for ~1.2 s of Haar
    flicker (and lightly EMA-damped) so busy backgrounds can't yank the crop
    off a face; the memory resets on hard cuts.
    Returns (idxs, cxs, cys, cuts, n_seen) in SOURCE pixel coords."""
    w, h, fps, n = probe(path)
    step = max(1, int(round(fps / sample_hz)))
    sw = min(max_w, w); sh = max(2, int(round(h * sw / w)))
    kx, ky = w / sw, h / sh
    yn = _yunet()
    face = _face_cascade() if yn is None else None   # Haar only as fallback
    ys_g, xs_g = np.mgrid[0:sh, 0:sw].astype(np.float32)
    idxs, cxs, cys, difs = [], [], [], []
    prev = None; n_seen = 0
    face_c = None; face_hold = 0                 # face memory: (cx,cy) + samples left
    HOLD = max(2, int(round(sample_hz * 1.2)))   # bridge ~1.2 s of missed detections
    for i, f in enumerate(frames_iter(path, limit)):
        n_seen += 1
        if i % step:
            continue
        small = cv2.resize(f, (sw, sh), interpolation=cv2.INTER_AREA)
        g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gf = g.astype(np.float32)
        dif = float(np.mean(cv2.absdiff(g, prev[1]))) if prev is not None else 0.0
        difs.append(dif)
        if dif > 40.0:                           # hard cut -> forget the old face spot
            face_c = None; face_hold = 0
        cx = cy = None
        if yn is not None or face is not None:
            if yn is not None:                   # neural (YuNet) — BGR input
                det = _yunet_detect(yn, small, min_side=max(12, sh // 14))
            else:                                # classical fallback (Haar)
                det = face.detectMultiScale(g, 1.15, 4,
                                            minSize=(max(16, sh // 10), max(16, sh // 10)))
            if len(det):
                a = np.array([bw * bh for (x, y, bw, bh) in det], np.float32)
                cx = float(sum((x + bw / 2) * ar for (x, y, bw, bh), ar in zip(det, a)) / a.sum())
                cy = float(sum((y + bh / 2) * ar for (x, y, bw, bh), ar in zip(det, a)) / a.sum())
                if face_c is not None:           # damp Haar box jitter between samples
                    cx = 0.65 * cx + 0.35 * face_c[0]
                    cy = 0.65 * cy + 0.35 * face_c[1]
                face_c = (cx, cy); face_hold = HOLD
            elif face_c is not None and face_hold > 0:
                face_hold -= 1                   # briefly lost: hold the face's spot
                cx, cy = face_c                  # (motion/detail must not yank away)
        if cx is None and prev is not None:
            d = cv2.GaussianBlur(cv2.absdiff(gf, prev[0]), (0, 0), 3)
            e = d * d; tot = float(e.sum())
            if tot > 1e3 and float(e.max()) > 25.0:          # real motion, not noise
                cx = float((e * xs_g).sum() / tot); cy = float((e * ys_g).sum() / tot)
        if cx is None:                                        # static scene -> detail
            e = (cv2.Sobel(gf, cv2.CV_32F, 1, 0, 3) ** 2 +
                 cv2.Sobel(gf, cv2.CV_32F, 0, 1, 3) ** 2)
            tot = float(e.sum()) + 1e-6
            cx = float((e * xs_g).sum() / tot); cy = float((e * ys_g).sum() / tot)
        idxs.append(i); cxs.append(cx * kx); cys.append(cy * ky)
        prev = (gf, g)
    if not idxs:
        idxs, cxs, cys, difs = [0], [w / 2.0], [h / 2.0], [0.0]
    d = np.array(difs)
    thr = max(18.0, float(d.mean() + 3.5 * d.std()))
    cuts = [int(idxs[j]) for j in range(1, len(idxs)) if d[j] > thr]
    return (np.array(idxs), np.array(cxs, np.float32),
            np.array(cys, np.float32), cuts, n_seen)


def _smooth_path(idxs, vals, cuts, n_frames, fps, smooth_sec=1.0, dim=1920):
    """Per-frame smooth center track: interpolate the samples, gaussian-smooth
    within each shot (window ~ smooth_sec), then clamp the pan speed so the
    virtual camera never whips. A small deadband (~0.8% of the frame) makes
    the camera HOLD STILL through sub-pixel target wiggle instead of drifting.
    Cuts are allowed to jump. Returns float32[n]."""
    n_frames = max(1, int(n_frames))
    xs = np.interp(np.arange(n_frames), idxs, vals).astype(np.float32)
    sigma = max(1.0, smooth_sec * fps / 2.0)
    r = max(1, int(3 * sigma))
    ker = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2); ker /= ker.sum()
    bounds = [0] + sorted(c for c in set(cuts) if 0 < c < n_frames) + [n_frames]
    out = xs.copy()
    vmax = max(2.0, 0.010 * dim)                 # px/frame pan-speed ceiling
    dead = 0.008 * dim                           # ignore tiny target wiggle
    for a, b in zip(bounds[:-1], bounds[1:]):
        seg = xs[a:b]
        if len(seg) >= 3:
            seg = np.convolve(np.pad(seg, r, mode="edge"), ker, mode="valid").astype(np.float32)
        cur = float(seg[0])
        for i in range(len(seg)):
            err = float(seg[i]) - cur
            if abs(err) > dead:                  # outside the deadband: chase calmly
                cur += float(np.clip(err - np.copysign(dead, err), -vmax, vmax))
            out[a + i] = cur
    return out


def _reframe_blur(path, out, a_t, w, h, fps, n, limit, max_dim, progress_cb):
    """Scale-to-fit over a blurred, darkened cover background (no cropping)."""
    if a_t < (w / h):
        oh, ow = h, h * a_t                      # keep source height
    else:
        ow, oh = w, w / a_t                      # keep source width
    if max_dim and max(ow, oh) > max_dim:
        s = max_dim / max(ow, oh); ow, oh = ow * s, oh * s
    ow, oh = _even(ow), _even(oh)
    s_fit = min(ow / w, oh / h)
    fw, fh = max(2, int(round(w * s_fit))), max(2, int(round(h * s_fit)))
    fx, fy = (ow - fw) // 2, (oh - fh) // 2
    s_cov = max(ow / w, oh / h)
    bw_, bh_ = int(np.ceil(w * s_cov)), int(np.ceil(h * s_cov))
    bx, by = (bw_ - ow) // 2, (bh_ - oh) // 2
    tmp = tempfile.mkdtemp(); raw = os.path.join(tmp, "v.mp4")
    enc = Encoder(ow, oh, fps, raw, upscale=None, sharpen=False)
    total = limit or n
    for k, f in enumerate(frames_iter(path, limit)):
        bg = cv2.resize(f, (bw_, bh_), interpolation=cv2.INTER_AREA)[by:by + oh, bx:bx + ow]
        sm = cv2.resize(bg, (max(2, ow // 8), max(2, oh // 8)), interpolation=cv2.INTER_AREA)
        sm = cv2.GaussianBlur(sm, (0, 0), 6)
        canvas = cv2.resize(sm, (ow, oh), interpolation=cv2.INTER_LINEAR)
        canvas = (canvas.astype(np.float32) * 0.55).astype(np.uint8)     # darken bars
        canvas[fy:fy + fh, fx:fx + fw] = cv2.resize(f, (fw, fh),
                                                    interpolation=cv2.INTER_AREA)
        enc.write(canvas)
        if progress_cb and total and k % 10 == 0:
            progress_cb(k + 1, total)
        if k % 50 == 0:
            print(f"  frame {k+1}/{total or '?'}", flush=True)
    enc.close(); mux_audio(raw, path, out); shutil.rmtree(tmp, ignore_errors=True)
    print(f"done -> {out}")


def reframe_video(path, out, ratio="9:16", fit="crop", preview=None, max_dim=None,
                  smooth_sec=None, focus=None, progress_cb=None):
    """Convert a video to a new aspect ratio.
      fit='crop'  a smoothly tracked crop window keeps the subject (faces >
                  motion > detail) centered; per-shot smoothing kills jitter.
      fit='blur'  scale-to-fit over blurred, darkened bars (nothing cropped) —
                  the fallback when the subject is too wide to crop cleanly.
    Audio is preserved. max_dim caps the OUTPUT long side (memory guard).
    smooth_sec: crop-path smoothing window (default: env WR_REFRAME_SMOOTH or 1.0).
    focus: optional (x, y) in NORMALIZED 0..1 source coords — pins the crop
    center on that point (rock steady, no auto-tracking). None = auto-track."""
    a_t = parse_ratio(ratio)
    w, h, fps, n = probe(path)
    limit = int(preview * fps) if preview else None
    if smooth_sec is None:
        smooth_sec = float(os.environ.get("WR_REFRAME_SMOOTH", "1.0"))
    if fit not in ("crop", "blur"):
        raise ValueError("fit must be 'crop' or 'blur'.")
    if fit == "blur":
        return _reframe_blur(path, out, a_t, w, h, fps, n, limit, max_dim, progress_cb)

    # crop-window size in source pixels
    if a_t < (w / h):                            # narrower target -> crop width
        ch = h - (h & 1); cw = min(_even(ch * a_t), w - (w & 1))
    else:                                        # wider/equal target -> crop height
        cw = w - (w & 1); ch = min(_even(cw / a_t), h - (h & 1))
    axis_x = cw < w                              # which axis actually moves

    if focus is not None:
        # user-pinned focus point: a fixed crop centred on it (clamped inside
        # the frame) — no tracking pass needed, the camera never moves.
        fx = min(max(float(focus[0]), 0.0), 1.0) * w
        fy = min(max(float(focus[1]), 0.0), 1.0) * h
        n_seen = int(limit if limit else (n if n > 0 else 1))
        p0 = (int(np.clip(round(fx - cw / 2), 0, w - cw)) if axis_x
              else int(np.clip(round(fy - ch / 2), 0, h - ch)))
        pos = np.full(max(1, n_seen), p0, int)
    else:
        idxs, cxs, cys, cuts, n_seen = _interest_track(path, limit)
        track = _smooth_path(idxs, cxs if axis_x else cys, cuts, n_seen, fps,
                             smooth_sec, dim=(w if axis_x else h))
        if axis_x:
            pos = np.clip(np.round(track - cw / 2), 0, w - cw).astype(int)
        else:
            pos = np.clip(np.round(track - ch / 2), 0, h - ch).astype(int)

    ow, oh = cw, ch
    if max_dim and max(ow, oh) > max_dim:
        s = max_dim / max(ow, oh); ow, oh = _even(ow * s), _even(oh * s)
    target = (ow, oh) if (ow, oh) != (cw, ch) else None

    tmp = tempfile.mkdtemp(); raw = os.path.join(tmp, "v.mp4")
    enc = Encoder(cw, ch, fps, raw, upscale=target, sharpen=False)
    x0, y0 = (w - cw) // 2, (h - ch) // 2        # the fixed axis stays centered
    for k, f in enumerate(frames_iter(path, limit)):
        p = int(pos[min(k, len(pos) - 1)])
        crop = f[y0:y0 + ch, p:p + cw] if axis_x else f[p:p + ch, x0:x0 + cw]
        enc.write(crop)
        if progress_cb and n_seen and k % 10 == 0:
            progress_cb(k + 1, n_seen)
        if k % 50 == 0:
            print(f"  frame {k+1}/{n_seen}", flush=True)
    enc.close(); mux_audio(raw, path, out); shutil.rmtree(tmp, ignore_errors=True)
    print(f"done -> {out}")


# --------------------------------------------------------------------------- #
# Privacy blur (auto face / license-plate blurring + manual user regions)
# --------------------------------------------------------------------------- #
_PRIVACY_CASCADES: dict = {}
def _privacy_cascade(name):
    """Load (and cache) a Haar cascade bundled with OpenCV, or None."""
    if name not in _PRIVACY_CASCADES:
        try:
            c = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, name))
            _PRIVACY_CASCADES[name] = c if not c.empty() else None
        except Exception:
            _PRIVACY_CASCADES[name] = None
    return _PRIVACY_CASCADES[name]

# target -> (cascade file, scaleFactor, minNeighbors, also_run_mirrored)
# 'plate' uses the Russian-plate cascade as a GENERIC plate detector — it fires
# on most landscape plates but is approximate (labelled "beta" in the UI).
PRIVACY_TARGETS = {
    "face":  [("haarcascade_frontalface_default.xml", 1.10, 4, False),
              ("haarcascade_profileface.xml",         1.10, 4, True)],
    "plate": [("haarcascade_russian_plate_number.xml", 1.08, 4, False)],
}

def _detect_boxes(gray, targets):
    """Haar detections for the given targets on one grayscale frame.
    Returns [(x, y, w, h), ...] in `gray` pixel coords."""
    gh, gw = gray.shape[:2]
    out = []
    for t in targets:
        for name, sf, mn, mirror in PRIVACY_TARGETS.get(t, ()):
            c = _privacy_cascade(name)
            if c is None:
                continue
            ms = (max(18, gh // 14),) * 2 if t == "face" else (0, 0)
            passes = [gray, cv2.flip(gray, 1)] if mirror else [gray]
            for pi, img in enumerate(passes):
                det = c.detectMultiScale(img, sf, mn, minSize=ms)
                for (x, y, bw, bh) in det:
                    if pi:                       # mirrored pass -> unmirror the box
                        x = gw - int(x) - int(bw)
                    # CLE-57: the plate cascade fires on ANY text-like texture
                    # (printed labels, captions). Real plates are short, wide
                    # and a modest fraction of the frame — gate to that shape.
                    if t == "plate":
                        ar = bw / max(float(bh), 1e-6)
                        if not (1.8 <= ar <= 6.5): continue
                        if not (0.03 * gw <= bw <= 0.35 * gw): continue
                        if bh > 0.12 * gh: continue
                    out.append((int(x), int(y), int(bw), int(bh)))
    return out


def detect_privacy_boxes(frame, targets):
    """Privacy detections for ONE BGR frame, in FULL frame coords (floats).
    Detection runs on a downscaled copy for speed; plates get a bit more
    resolution because the plate cascade's window is comparatively large.
    Faces use the neural YuNet detector when available (profile/tilted/partly
    covered faces that Haar misses); plates and the Haar fallback share the
    classical path."""
    h, w = frame.shape[:2]
    dw = min(720 if "plate" in targets else 560, w)
    dh = max(2, int(round(h * dw / w)))
    small = frame if dw == w else cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_AREA)
    kx, ky = w / float(dw), h / float(dh)
    boxes = []
    classical = list(targets)
    yn = _yunet()
    if "face" in classical and yn is not None:
        boxes += _yunet_detect(yn, small, min_side=max(10, dh // 22))
        classical.remove("face")
    if classical:
        g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        boxes += _detect_boxes(g, classical)
    return [(x * kx, y * ky, bw * kx, bh * ky) for (x, y, bw, bh) in boxes]


class _RegionTracker:
    """Temporal smoothing for per-frame privacy detections so the blur is
    STABLE instead of flickery: each detection is matched to a live track by
    center distance, track geometry is EMA-damped, a track is HELD for `hold`
    frames after its detector drops out (Haar flickers; faces turn), and the
    reported boxes are expanded by `expand` so edges/ears stay covered."""
    def __init__(self, hold=12, ema=0.35, expand=0.18, min_hits=3):
        # min_hits (CLE-57): a track must be re-detected in this many frames
        # before it starts blurring — one-frame false positives (texture,
        # printed text) never fire, while real faces confirm in ~0.1s.
        self.tracks = []                 # dicts: cx, cy, w, h, ttl, hits
        self.hold = max(1, int(hold)); self.ema = float(ema); self.expand = float(expand)
        self.min_hits = max(1, int(min_hits))

    def update(self, det):
        fresh = [[x + bw / 2.0, y + bh / 2.0, float(bw), float(bh)]
                 for (x, y, bw, bh) in det]
        used = [False] * len(fresh)
        for t in self.tracks:
            best, bd = None, None
            for j, f in enumerate(fresh):
                if used[j]:
                    continue
                d = float(np.hypot(f[0] - t["cx"], f[1] - t["cy"]))
                if d < 0.75 * max(t["w"], t["h"], f[2], f[3]) and (bd is None or d < bd):
                    best, bd = j, d
            if best is not None:
                f = fresh[best]; used[best] = True; a = self.ema
                t["cx"] += a * (f[0] - t["cx"]); t["cy"] += a * (f[1] - t["cy"])
                t["w"] += a * (f[2] - t["w"]);   t["h"] += a * (f[3] - t["h"])
                t["ttl"] = self.hold
                t["hits"] = min(t.get("hits", 1) + 1, self.min_hits)
            else:
                t["ttl"] -= 1                    # briefly lost: keep blurring its spot
                if t.get("hits", 1) < self.min_hits:
                    t["ttl"] = 0                 # unconfirmed + lost -> drop instantly
        self.tracks = [t for t in self.tracks if t["ttl"] > 0]
        for j, f in enumerate(fresh):
            if not used[j]:
                self.tracks.append(dict(cx=f[0], cy=f[1], w=f[2], h=f[3],
                                        ttl=self.hold, hits=1))
        return self.boxes()

    def boxes(self):
        out = []
        for t in self.tracks:
            if t.get("hits", 1) < self.min_hits:   # probation: not blurred yet
                continue
            bw = t["w"] * (1.0 + self.expand); bh = t["h"] * (1.0 + self.expand)
            out.append((t["cx"] - bw / 2.0, t["cy"] - bh / 2.0, bw, bh))
        return out


def _blur_patch(roi, style="blur", strength=0.6):
    """A fully-obscured copy of an ROI. 'blur' = very strong Gaussian done as
    downscale -> blur -> upscale (fast at ANY region size, radius scales with
    region size and strength); 'pixelate' = mosaic. Floors guarantee the
    region is genuinely unrecognizable even at the lowest strength."""
    h, w = roi.shape[:2]
    s = min(1.0, max(0.15, float(strength)))
    if style == "pixelate":
        cell = max(4, int(round(min(h, w) * (0.09 + 0.16 * s))))
        sw, sh = max(1, w // cell), max(1, h // cell)
        small = cv2.resize(roi, (sw, sh), interpolation=cv2.INTER_AREA)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    k = max(3, int(round(min(h, w) * (0.06 + 0.14 * s))))
    small = cv2.resize(roi, (max(1, w // k), max(1, h // k)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 2.0)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _obscure_boxes(frame, boxes, style, strength):
    """Obscure rectangular regions in-place (boxes may be floats; clamped)."""
    h, w = frame.shape[:2]
    for (x, y, bw, bh) in boxes:
        x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
        x1, y1 = min(w, int(round(x + bw))), min(h, int(round(y + bh)))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        frame[y0:y1, x0:x1] = _blur_patch(frame[y0:y1, x0:x1], style, strength)


def _obscure_masked(frame, mask01, style, strength, rect=None):
    """Obscure an arbitrary painted region in-place (only inside its bounding
    rect, so a small brushed spot doesn't cost a full-frame blur)."""
    if rect is None:
        ys, xs = np.where(mask01 > 0)
        if len(ys) == 0:
            return
        rect = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    x0, y0, x1, y1 = rect
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    roi = frame[y0:y1, x0:x1]
    patch = _blur_patch(roi, style, strength)
    m = mask01[y0:y1, x0:x1] > 0
    roi[m] = patch[m]


def blur_video(path, out, targets=("face",), style="blur", strength=0.6,
               mask01=None, boxes=None, track=False, track_ref=None,
               preview=None, max_dim=None, progress_cb=None):
    """Privacy blur: auto-detect faces and/or license plates in EVERY frame and
    obscure them, plus (optionally) user-marked regions. Detections are
    tracked & smoothed (_RegionTracker) so the blur holds steady through Haar
    flicker and head turns. Audio is preserved.
      targets   subset of {'face', 'plate'} to auto-detect (may be empty when
                a manual mask/boxes region is given)
      style     'blur' (strong Gaussian) | 'pixelate' (mosaic)
      strength  0..1 — how coarse the obscuring is (floored to stay opaque)
      mask01 / boxes   user-marked extra region(s), unioned with the auto ones
      track     True = the marked region MOVES; follow it by template matching
                (mark once -> follow), track_ref = reference time in seconds
      preview   only the first N seconds;  max_dim caps the OUTPUT long side
    Raises RuntimeError (friendly message) when there is nothing to blur."""
    w, h, fps, n = probe(path)
    limit = int(preview * fps) if preview else None
    targets = tuple(t for t in (targets or ()) if t in PRIVACY_TARGETS)
    if style not in ("blur", "pixelate"):
        style = "blur"

    man_mask = None
    if mask01 is not None and np.asarray(mask01).max() > 0:
        man_mask = (np.asarray(mask01) > 0).astype(np.uint8)
        if man_mask.shape != (h, w):
            man_mask = cv2.resize(man_mask, (w, h), interpolation=cv2.INTER_NEAREST)
    elif boxes:
        man_mask = mask_from_boxes(boxes, h, w)
    if man_mask is not None and man_mask.max() == 0:
        man_mask = None
    if not targets and man_mask is None:
        raise RuntimeError("Nothing to blur — pick faces/plates, or brush a region.")

    trk = None; man_rect = None
    if man_mask is not None:
        if track:
            trk = _track_setup(path, ref=track_ref, mask01=man_mask)
        else:
            ys, xs = np.where(man_mask > 0)
            man_rect = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    if targets and man_mask is None:
        # Quick pre-scan (one frame at a time — memory-safe): if the detectors
        # see nothing anywhere, say so instead of rendering an untouched copy.
        step = max(1, int(limit or n or 240) // 12)
        found = False
        for i, f in enumerate(frames_iter(path, limit)):
            if i % step:
                continue
            if detect_privacy_boxes(f, targets):
                found = True; break
        if not found:
            names = " or ".join("faces" if t == "face" else "license plates" for t in targets)
            raise RuntimeError(f"No {names} found — brush the area to blur, "
                               "or pick a different target.")

    ow, oh = w, h
    if max_dim and max(w, h) > max_dim:
        sc = max_dim / float(max(w, h)); ow, oh = _even(w * sc), _even(h * sc)
    target = (ow, oh) if (ow, oh) != (w, h) else None

    tracker = _RegionTracker(hold=max(4, int(round(fps * 0.5)))) if targets else None
    tmp = tempfile.mkdtemp(); raw = os.path.join(tmp, "v.mp4")
    enc = Encoder(w, h, fps, raw, upscale=target, sharpen=False)
    total = limit or n
    hidden_frames = 0                    # CLE-57: honest reporting to the UI
    for k, f in enumerate(frames_iter(path, limit)):
        if tracker is not None:
            bx = tracker.update(detect_privacy_boxes(f, targets))
            if bx:
                hidden_frames += 1
            _obscure_boxes(f, bx, style, strength)
        if man_mask is not None:
            if trk is not None:
                # moving manual region: tracked (multi-scale, gated). Privacy
                # fails CLOSED — when the tracker loses the region it keeps
                # obscuring the last confident spot instead of exposing it.
                eff, rect = _track_mask(trk, f, on_lost="hold")
                if eff is not None:
                    _obscure_masked(f, eff, style, strength, rect=rect)
                    hidden_frames += 1
            else:
                _obscure_masked(f, man_mask, style, strength, rect=man_rect)
                hidden_frames += 1
        enc.write(f)
        if progress_cb and total and k % 10 == 0:
            progress_cb(k + 1, total)
        if k % 50 == 0:
            print(f"  frame {k+1}/{total or '?'}", flush=True)
    enc.close(); mux_audio(raw, path, out); shutil.rmtree(tmp, ignore_errors=True)
    print(f"done -> {out}  (hidden on {hidden_frames}/{total or '?'} frames)")
    return {"hidden_frames": int(hidden_frames), "frames": int(total or 0)}


# --------------------------------------------------------------------------- #
# Core processing
# --------------------------------------------------------------------------- #
def _flatness(gray, kw):
    mu = cv2.boxFilter(gray, -1, (kw, kw))
    return np.sqrt(np.clip(cv2.boxFilter(gray*gray, -1, (kw, kw)) - mu*mu, 0, None))

def _inpaint_smart(inp, frame, mask01, full_thresh=0.45, pad=24):
    """Inpaint only the mask's bounding box (much faster + sharper) unless the mask
    is dense/tiled (> full_thresh of the frame) — then inpaint the whole frame."""
    if mask01 is None or mask01.max() == 0:
        return frame
    if float(mask01.mean()) > full_thresh:
        return inp.inpaint(frame, mask01)
    ys, xs = np.where(mask01 > 0)
    y0, y1 = max(0, int(ys.min()) - pad), min(frame.shape[0], int(ys.max()) + pad + 1)
    x0, x1 = max(0, int(xs.min()) - pad), min(frame.shape[1], int(xs.max()) + pad + 1)
    out = frame.copy()
    out[y0:y1, x0:x1] = inp.inpaint(frame[y0:y1, x0:x1], mask01[y0:y1, x0:x1])
    return out

def _inpaint_plan(frame, mask01, full_thresh=0.45, pad=24):
    """Same ROI logic as _inpaint_smart, but PLAN ONLY — describe what to inpaint
    without doing it, so a whole chunk of frames can be inpainted in one batched
    GPU call. Returns:
        None                              -> nothing to inpaint
        ("full", frame, mask01)           -> dense mask, inpaint the whole frame
        ((y0, y1, x0, x1), crop, cropmask)-> localized, inpaint just the ROI."""
    if mask01 is None or mask01.max() == 0:
        return None
    if float(mask01.mean()) > full_thresh:
        return ("full", frame, mask01)
    ys, xs = np.where(mask01 > 0)
    y0, y1 = max(0, int(ys.min()) - pad), min(frame.shape[0], int(ys.max()) + pad + 1)
    x0, x1 = max(0, int(xs.min()) - pad), min(frame.shape[1], int(xs.max()) + pad + 1)
    return ((y0, y1, x0, x1), frame[y0:y1, x0:x1], mask01[y0:y1, x0:x1])

def process_image(path, out, mask01, inp):
    img = cv2.imread(path)
    if img is None:
        sys.exit(f"Cannot read {path}")
    h, w = img.shape[:2]
    if mask01.shape != (h, w):
        mask01 = cv2.resize(mask01, (w, h), interpolation=cv2.INTER_NEAREST)
    res = inp.inpaint(img, cv2.dilate(mask01, np.ones((3, 3), np.uint8)))
    cv2.imwrite(out, res)
    print(f"done -> {out}")

def process_video(path, out, info, mask01, inp, preview=None, upscale=None,
                  sharpen=True, protect_subject=True, track=None, progress_cb=None,
                  shield_faces=False):
    w, h, fps, n = probe(path)
    limit = int(preview * fps) if preview else None
    B = info.get("B"); meanf = info.get("meanf"); gain = info.get("gain", 0.0); C = 245.0
    KW = max(15, (min(w, h) // 40) | 1); TAU = 12.0
    mask_bin = (mask01 > 0).astype(np.uint8) if mask01 is not None else None
    tmp = tempfile.mkdtemp(); raw = os.path.join(tmp, "v.mp4")
    enc = Encoder(w, h, fps, raw, upscale, sharpen)
    total = limit or n
    # Batch size: on the GPU (modal) backend we send a whole chunk of frames' crops
    # in ONE request — that's the export speed-up (fewer round-trips). The local
    # classical/lama backends have no network to amortise, so they stream one frame
    # at a time (chunk = 1) to keep memory flat.
    if getattr(inp, "seq_url", None):
        # temporal (ProPainter) mode wants LONGER chunks: more frames = more
        # real pixels to propagate from = better, more consistent fills
        chunk = int(os.environ.get("WR_INPAINT_SEQ_BATCH", "16"))
    elif getattr(inp, "kind", "") == "modal":
        chunk = int(os.environ.get("WR_INPAINT_BATCH", "12"))
    else:
        chunk = 1
    buf = []            # list of [frame, plan]  (plan from _inpaint_plan)
    written = 0

    def _flush():
        nonlocal written
        idxs = [i for i, (fr, pl) in enumerate(buf) if pl is not None]
        # TEMPORAL path (ProPainter): union the chunk's ROIs into ONE fixed
        # region and fill it flow-consistently across all frames — kills the
        # per-frame shimmer. Needs >=2 localized ROIs; "full"-frame plans and
        # any failure fall through to the per-frame path below.
        # STATIC-SCENE GUARD (CLE-31): flow-based temporal inpainting needs
        # motion to pull real pixels from. On a (near-)static chunk ProPainter
        # has nothing to propagate and hallucinates a low-res checkered fill —
        # far worse than per-frame LaMa. Cheap test: if the first and last
        # frames of the chunk are near-identical, skip the temporal path.
        _static_chunk = False
        if idxs and len(idxs) >= 2:
            fa, fb = buf[idxs[0]][0], buf[idxs[-1]][0]
            _static_chunk = float(
                np.mean(cv2.absdiff(fa[::4, ::4], fb[::4, ::4]))) < 1.0
        if (idxs and getattr(inp, "seq_url", None) and len(idxs) >= 2
                and not _static_chunk
                and getattr(inp, "_seq_fails", 0) < 2
                and all(buf[i][1][0] != "full" for i in idxs)):
            y0 = min(buf[i][1][0][0] for i in idxs)
            y1 = max(buf[i][1][0][1] for i in idxs)
            x0 = min(buf[i][1][0][2] for i in idxs)
            x1 = max(buf[i][1][0][3] for i in idxs)
            # union ~whole frame -> bandwidth/VRAM blow-up; use per-frame path
            if (y1 - y0) * (x1 - x0) <= 0.6 * h * w:
                try:
                    frames = [buf[i][0][y0:y1, x0:x1] for i in idxs]
                    masks = []
                    for i in idxs:
                        ry0, ry1, rx0, rx1 = buf[i][1][0]
                        m = np.zeros((y1 - y0, x1 - x0), np.uint8)
                        m[ry0 - y0:ry1 - y0, rx0 - x0:rx1 - x0] = buf[i][1][2]
                        masks.append(m)
                    done = inp.inpaint_sequence(frames, masks)
                    for j, i in enumerate(idxs):
                        fr = buf[i][0].copy()
                        fr[y0:y1, x0:x1] = done[j]
                        buf[i][0] = fr
                    inp._seq_fails = 0           # healthy again
                    idxs = []                    # handled — skip per-frame path
                except Exception as e:
                    inp._seq_fails = getattr(inp, "_seq_fails", 0) + 1
                    if not getattr(inp, "_seq_warned", False):
                        print(f"[propainter] sequence inpaint failed ({e!r}); "
                              "using per-frame backend", flush=True)
                        inp._seq_warned = True
        if idxs:
            crops = [(buf[i][1][1], buf[i][1][2]) for i in idxs]     # (crop, cropmask)
            done = inp.inpaint_batch(crops)                          # one GPU call
            for j, i in enumerate(idxs):
                roi = buf[i][1][0]; res = done[j]
                if roi == "full":
                    buf[i][0] = res
                else:
                    y0, y1, x0, x1 = roi
                    fr = buf[i][0].copy(); fr[y0:y1, x0:x1] = res; buf[i][0] = fr
        for fr, _pl in buf:
            enc.write(fr); written += 1
            if progress_cb and total and written % 10 == 0:
                progress_cb(written, total)
            if written % 25 == 0:
                print(f"  frame {written}/{total}", flush=True)
        buf.clear()

    _fs_cache = [None]; _fidx = -1                     # CLE-55 face-shield cache
    for f in frames_iter(path, limit):
        _fidx += 1
        # (1) reverse-blend the diffuse periodic layer out of the whole frame
        if B is not None:
            O = f.astype(np.float32); r = np.clip((C-O)/(C-meanf+1e-3), 0, 3.0)
            f = np.clip(O - gain * B * r, 0, 255).astype(np.uint8)
        # (2) build this frame's mask (the tracker is stateful, so this MUST stay in
        #     frame order — only the inpaint is deferred/batched, never the tracking)
        if track is not None:
            # MOVING / ANIMATED mark: follow it (multi-scale, gated). eff=None when
            # the tracker has no trustworthy location: leave the frame untouched.
            eff, _rect = _track_mask(track, f, on_lost="skip")
        else:
            # STATIC mark: when reverse-blend (B) is removing a SEMI-TRANSPARENT
            # watermark globally, gate the residual inpaint to locally-flat pixels
            # so a moving subject (face/hands/clothes detail) under the mark stays
            # untouched. But for an OPAQUE / inpaint-only removal (B is None) nothing
            # else removes the mark — gating to flat pixels would leave the
            # (high-detail) logo/text behind, so inpaint the FULL marked region.
            eff = mask_bin
            if protect_subject and eff is not None and B is not None:
                gg = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
                eff = mask_bin & (_flatness(gg, KW) < TAU).astype(np.uint8)
            if eff is not None and shield_faces:
                # CLE-55: faces are never repainted by the remove-inpaint.
                # Re-detect every few frames (faces move slowly vs fps; the
                # expanded boxes cover the gap between refreshes).
                if _fidx % 6 == 0:
                    _fs_cache[0] = _face_shield(f)
                if _fs_cache[0] is not None:
                    eff = eff & (1 - _fs_cache[0])
            if eff is not None:
                eff = cv2.dilate(eff, np.ones((3, 3), np.uint8))
        # (3) plan the inpaint; a whole chunk's crops go to the GPU together
        buf.append([f, _inpaint_plan(f, eff)])
        if len(buf) >= chunk:
            _flush()
    if buf:
        _flush()
    enc.close(); mux_audio(raw, path, out); shutil.rmtree(tmp, ignore_errors=True)
    print(f"done -> {out}")


# --------------------------------------------------------------------------- #
# Quality control: score the clean, auto-tune, and report confidence
# --------------------------------------------------------------------------- #
def _hp_gray(bgr, sigma=6.0):
    g = cv2.cvtColor(np.clip(bgr, 0, 255).astype(np.uint8),
                     cv2.COLOR_BGR2GRAY).astype(np.float32)
    return g - cv2.GaussianBlur(g, (0, 0), sigma)

def _face_shield(frame, expand=0.25):
    """CLE-55: uint8 mask of detected faces (boxes expanded by `expand`) — the
    region the remove-inpaint must NEVER repaint. None when no faces."""
    h, w = frame.shape[:2]
    boxes = detect_privacy_boxes(frame, ["face"])
    if not boxes:
        return None
    m = np.zeros((h, w), np.uint8)
    for (x, y, bw, bh) in boxes:
        px, py = bw * expand, bh * expand
        x0 = max(0, int(x - px)); y0 = max(0, int(y - py))
        x1 = min(w, int(x + bw + px)); y1 = min(h, int(y + bh + py))
        m[y0:y1, x0:x1] = 1
    return m


def _clean_frame_static(f, B, meanf, gain, mask_bin, inp, protect=True,
                        shield_faces=False):
    """Clean ONE frame with the same static-mark logic as process_video (reverse-
    blend the diffuse layer, gate to flat pixels to protect the subject, ROI inpaint)."""
    h, w = f.shape[:2]
    KW = max(15, (min(w, h) // 40) | 1); TAU = 12.0; C = 245.0
    if B is not None and meanf is not None:
        O = f.astype(np.float32); r = np.clip((C - O) / (C - meanf + 1e-3), 0, 3.0)
        f = np.clip(O - gain * B * r, 0, 255).astype(np.uint8)
    eff = mask_bin
    if eff is not None and protect and B is not None:   # gate only when reverse-blending; opaque -> full inpaint
        gg = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        eff = mask_bin & (_flatness(gg, KW) < TAU).astype(np.uint8)
    if eff is not None and shield_faces:                 # CLE-55: never repaint faces
        fs = _face_shield(f)
        if fs is not None:
            eff = eff & (1 - fs)
    if eff is not None:
        eff = cv2.dilate(eff, np.ones((3, 3), np.uint8))
    return _inpaint_smart(inp, f, eff)

def _sample_frames(path, k=4, limit=None):
    """Up to k frames spread evenly across the clip (or the first `limit`
    frames) — STREAMED: only the k chosen frames are ever held in memory.
    (`list(frames_iter(...))` used to buffer ~120 full-res frames per QC
    call, ~750 MB at 1080p; back-to-back QC passes OOM-killed the 2 GB box.)"""
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    if limit is not None:
        n = min(n, int(limit))
    if n <= 0:
        # container reports no frame count: fall back to the first k frames
        return [f for f in frames_iter(path, k)]
    idx = set(np.linspace(0, n - 1, min(k, n)).round().astype(int).tolist())
    out = []
    for i, f in enumerate(frames_iter(path, limit)):
        if i in idx:
            out.append(f)
            if len(out) >= len(idx):
                break
    return out

def _score_clean(orig, cleaned, mask_bin, B):
    """Return (residual_reduction, damage) for one before/after frame pair.
      residual_reduction: 1 - leftover watermark structure / original (higher=better)
      damage:             over-smoothing + colour shift inside the region (lower=better)
    """
    m = mask_bin > 0
    if m.sum() < 8:
        return 1.0, 0.0
    ho, hc = _hp_gray(orig), _hp_gray(cleaned)
    if B is not None:
        # Project the frame's high-pass onto the KNOWN watermark structure inside
        # the mask. If the projection shrank, the mark is gone.
        bg = B.mean(2).astype(np.float32)
        bhp = (bg - cv2.GaussianBlur(bg, (0, 0), 6.0))[m]
        nb = float(np.linalg.norm(bhp))
        e_o = float(np.linalg.norm(ho[m])) + 1e-6
        u = bhp / (nb + 1e-6)
        before = abs(float((ho[m] * u).sum()))
        after = abs(float((hc[m] * u).sum()))
        # Guard: if the watermark structure carries ~no energy here, or explains
        # almost none of the region's texture, we can't claim a removal — score it
        # low rather than dividing ~0/~0 into a false "perfect".
        if nb < 1e-3 or (before / e_o) < 0.05:
            resid_red = 0.0
        else:
            resid_red = 1.0 - after / (before + 1e-6)
    else:
        # Inpaint-only (opaque logo / manual): a good fill makes interior texture
        # resemble the surrounding ring — neither a flat hole nor a bright seam.
        ring = (cv2.dilate(mask_bin, np.ones((15, 15), np.uint8)) > 0) & (~m)
        e_in = float(np.sqrt((hc[m] ** 2).mean()))
        e_rg = float(np.sqrt((hc[ring] ** 2).mean())) if ring.sum() else e_in
        ratio = e_in / (e_rg + 1e-6)
        resid_red = 1.0 - min(1.0, abs(ratio - 1.0))
    # damage: interior flatter than its ring (lost detail) + mean colour shift
    ring = (cv2.dilate(mask_bin, np.ones((15, 15), np.uint8)) > 0) & (~m)
    cg = cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY).astype(np.float32)
    tex_in = float(cg[m].std())
    tex_rg = float(cg[ring].std()) if ring.sum() else tex_in
    oversmooth = np.clip((tex_rg - tex_in) / (tex_rg + 1e-6) - 0.4, 0, 1)
    dcol = 0.0
    if ring.sum():
        dcol = min(1.0, abs(float(cg[m].mean()) - float(cg[ring].mean())) / 40.0)
    damage = float(np.clip(0.7 * oversmooth + 0.3 * dcol, 0, 1))
    return float(np.clip(resid_red, -1, 1)), damage

def quality_report(path, info, mask_bin, inp, gain=None, protect=True, k=4, limit=None,
                   shield_faces=False):
    """Clean k sampled frames with the given params and average the QC scores.
    Returns dict(residual_reduction, damage, confidence, samples, ...).

    CLE-56: confidence used to reward ONLY in-mask flattening — repainting a
    face *improved* the score, and ghost marks outside the tuned mask never
    counted. Two extra multiplicative penalties fix that:
      face_damage   mean |before-after| inside detected faces (faces should be
                    ~untouched by remove; only the subtractive un-blend and
                    codec noise may move them a few grey levels)
      residue_kept  the B-structure projection measured over the WHOLE frame,
                    not just the mask — surviving ghost bands keep it high"""
    B = info.get("B"); meanf = info.get("meanf")
    if gain is None:
        gain = info.get("gain", 0.0)
    frames = _sample_frames(path, k, limit)
    if not frames:
        return dict(residual_reduction=0.0, damage=1.0, confidence=0.0, samples=0)
    rr, dm, fd, rk = [], [], [], []
    for f in frames:
        cleaned = _clean_frame_static(f.copy(), B, meanf, gain, mask_bin, inp,
                                      protect, shield_faces=shield_faces)
        r, d = _score_clean(f, cleaned, mask_bin, B)
        rr.append(r); dm.append(d)
        fs = _face_shield(f, expand=0.05)
        if fs is not None and fs.sum() > 64:
            m = fs > 0
            fd.append(float(np.abs(cleaned.astype(np.float32) - f.astype(np.float32))[m].mean()))
        if B is not None:
            bg = B.mean(2).astype(np.float32)
            bhp = bg - cv2.GaussianBlur(bg, (0, 0), 6.0)
            sup = np.abs(bhp) > 1.0                      # wherever B has structure
            if sup.sum() > 64:
                u = bhp[sup] / (float(np.linalg.norm(bhp[sup])) + 1e-6)
                before = abs(float((_hp_gray(f)[sup] * u).sum()))
                after = abs(float((_hp_gray(cleaned)[sup] * u).sum()))
                if before > 1e-3:
                    rk.append(min(1.0, after / before))
    resid = float(np.mean(rr)); damage = float(np.mean(dm))
    face_damage = float(np.mean(fd)) if fd else 0.0
    residue_kept = float(np.mean(rk)) if rk else 0.0
    # ~5 grey levels of face movement is legitimate (un-blend); 18+ = repaint
    p_face = float(np.clip((face_damage - 5.0) / 13.0, 0, 1))
    # keeping >15% of the frame-wide mark structure starts costing confidence
    p_resid = float(np.clip((residue_kept - 0.15) / 0.5, 0, 1))
    confidence = float(np.clip(resid, 0, 1) * (1.0 - np.clip(damage, 0, 1))
                       * (1.0 - 0.8 * p_face) * (1.0 - 0.6 * p_resid))
    return dict(residual_reduction=round(resid, 3), damage=round(damage, 3),
                face_damage=round(face_damage, 2), residue_kept=round(residue_kept, 3),
                confidence=round(confidence, 3), samples=len(frames))

def autotune(path, info, mask_bin, inp, protect=True, k=4, limit=None,
             shield_faces=False):
    """Iteratively refine: try a small grid of reverse-blend strengths and mask
    dilations on sampled frames, keep the best-scoring combination. Returns
    (best_info, best_mask, qc_report). This is the automated 'reiterate on
    anomaly' step — done on samples so we render the full clip only once."""
    B = info.get("B")
    # gain may be a scalar (legacy) or a (3,) per-channel vector (CLE-45) —
    # np.asarray keeps the auto-tune's gain-multiplier axis working for both.
    base_gain = np.asarray(info.get("gain", 0.0), dtype=np.float32)
    # gain only matters for reverse-blend; skip that axis when B is None
    gain_mults = [1.0, 1.4] if B is not None else [1.0]
    dilations = [0, 4]
    best = None
    for gm in gain_mults:
        for dl in dilations:
            m = mask_bin if dl == 0 else cv2.dilate(mask_bin, np.ones((dl * 2 + 1,) * 2, np.uint8))
            g = base_gain * gm
            qc = quality_report(path, info, m, inp, gain=g, protect=protect, k=k,
                                limit=limit, shield_faces=shield_faces)
            cand = (qc["confidence"], qc["residual_reduction"], -qc["damage"])
            if best is None or cand > best[0]:
                best = (cand, g, m, qc)
    _, g, m, qc = best
    out_info = dict(info); out_info["gain"] = g
    qc = dict(qc); qc["engine"] = getattr(inp, "kind", "classical")
    qc["ok"] = bool(qc["confidence"] >= 0.5 and qc["residual_reduction"] >= 0.4)
    return out_info, m, qc


def detect_image(path):  # noqa: E302  (QC section above)
    """Single-image tiled-watermark auto-detect -> mask (or None)."""
    img = cv2.imread(path)
    if img is None:
        return None
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    wm = np.sqrt(cv2.Sobel(g, cv2.CV_32F, 1, 0, 3) ** 2 + cv2.Sobel(g, cv2.CV_32F, 0, 1, 3) ** 2)
    lat = _find_lattice(wm)
    if not lat or lat[2] < 0.10:
        return None
    B = _refine_and_extract(img.astype(np.float32), lat[0], lat[1])
    if B is None:
        return None
    prom = np.maximum(B.mean(2) - cv2.GaussianBlur(B.mean(2), (0, 0), 9), 0)
    m = (prom > np.percentile(prom, 63)).astype(np.uint8)
    return cv2.dilate(cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)),
                      np.ones((3, 3), np.uint8))

# --------------------------------------------------------------------------- #
# Moving-mark tracking (erase / blur "Moving object")
#
# v2 tracker: the old loop matched ONE fixed-size gray template per frame and
# ALWAYS inpainted wherever the global NCC peak landed. On a handheld object
# that tilts/zooms (a product box near a face), the peak stays deceptively
# high (0.7+) at the WRONG place once the pose drifts — measured on a real
# clip it hit the true region on only ~6% of frames while confidently
# inpainting other content. v2 fixes the two failure modes separately:
#   * finding: multi-scale matching, a local search window around the last
#     confirmed position (continuity), and a strict global re-acquire after a
#     loss (small-context scan verified by the big-context template);
#   * trusting: every candidate must pass an edge-energy veto (flat walls
#     can't win), a structure "leash" vs. the reference template, and a
#     dominant-hue color gate derived from the marked patch — if a frame has
#     no trustworthy location the tracker ABSTAINS (erase leaves the frame
#     untouched; blur holds the last spot) instead of damaging other content.
# WR_TRACK_V2=0 restores the legacy behavior; WR_TRACK_PAD tunes how much the
# tracked mask is grown (fraction of its size) so small drift still covers.
# --------------------------------------------------------------------------- #
def _blurf(g):
    return cv2.GaussianBlur(g.astype(np.float32), (0, 0), 1.5)

def _sobf(g):
    g = g.astype(np.float32)
    return cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0, 3),
                         cv2.Sobel(g, cv2.CV_32F, 0, 1, 3))

def _nccs(a, b):
    """Plain NCC of two equal-size float patches (no sliding)."""
    a = a - a.mean(); b = b - b.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 1e-6 else 0.0

def _hue_sig(bgr):
    """Dominant-hue signature of a patch: (hue_lo, frac) — the modal hue window
    (±12°, wrapping) among saturated pixels and how much of the patch it covers.
    None when the patch is too gray to color-gate on."""
    if bgr.size < 48:
        return None
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    H = hsv[..., 0].astype(np.int32); S = hsv[..., 1]
    sel = S >= 30
    if sel.mean() < 0.05:
        return None
    hist = np.bincount(H[sel].ravel(), minlength=180).astype(np.float32)
    ext = np.concatenate([hist[-12:], hist, hist[:12]])
    sm = np.convolve(ext, np.ones(25, np.float32), mode="valid")
    lo = (int(np.argmax(sm)) - 12) % 180
    frac = float((((H - lo) % 180) <= 24)[sel].mean() * sel.mean())
    return (lo, frac)

def _hue_frac(bgr, lo):
    """Fraction of a patch's pixels that are saturated AND inside the ±12° hue
    window starting at `lo` (the signature window from _hue_sig)."""
    if bgr.size < 48:
        return 0.0
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    H = hsv[..., 0].astype(np.int32); S = hsv[..., 1]
    return float(((S >= 30) & (((H - lo) % 180) <= 24)).mean())


def _track_setup(path, boxes=None, ref=None, mask01=None):
    """Template + mask_roi for a MOVING mark/object (--track / tracked erase).
    Either boxes[0] gives the region, or a painted mask01 does — then the box is
    its bounding rect and mask_roi keeps the painted shape, so only what the
    user brushed is inpainted as it moves. ref = reference time in seconds
    (default: middle of the clip). Besides the legacy template/mask_roi keys,
    the dict carries the v2 tracker state (see the section comment above)."""
    w, h, fps, n = probe(path)
    roi = None
    if boxes:
        x, y, bw, bh = boxes[0]
    elif mask01 is not None and mask01.max() > 0:
        m = (mask01 > 0).astype(np.uint8)
        x, y, bw, bh = cv2.boundingRect(m)
        roi = m[y:y + bh, x:x + bw].copy()
    else:
        raise RuntimeError("Tracking needs a box or a painted mask for the mark.")
    x = max(0, min(w - 2, int(x))); y = max(0, min(h - 2, int(y)))
    bw = max(2, min(w - x, int(bw))); bh = max(2, min(h - y, int(bh)))
    idx = int(ref * fps) if ref is not None else n // 2
    cap = cv2.VideoCapture(path); cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(n - 1, idx)))
    ok, f = cap.read(); cap.release()
    if not ok:
        raise RuntimeError("Could not read the reference frame for tracking.")
    g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
    if roi is None:
        roi = np.ones((bh, bw), np.uint8)
    trk = dict(template=g[y:y + bh, x:x + bw].copy(), mask_roi=roi[:bh, :bw])
    if os.environ.get("WR_TRACK_V2", "1") == "0":
        return trk                                   # legacy tracker requested
    # ---- v2 state: context templates around the mark + gates ----
    ex, ey = int(round(bw * 0.50)), int(round(bh * 1.8))     # BIG context (precise)
    cx0, cy0 = max(0, x - ex), max(0, y - ey)
    cx1, cy1 = min(w, x + bw + ex), min(h, y + bh + ey)
    T0b = _blurf(g)[cy0:cy1, cx0:cx1].copy()
    T0s = _sobf(g)[cy0:cy1, cx0:cx1].copy()
    exs, eys = int(round(bw * 0.35)), int(round(bh * 0.9))   # SMALL context (re-acquire)
    sx0, sy0 = max(0, x - exs), max(0, y - eys)
    sx1, sy1 = min(w, x + bw + exs), min(h, y + bh + eys)
    T1s = _sobf(g)[sy0:sy1, sx0:sx1].copy()
    df = max(1, int(round(max(w, h) / 640.0)))               # re-acquire scan res
    T1s_p = (cv2.resize(T1s, (max(8, (sx1 - sx0) // df), max(6, (sy1 - sy0) // df)),
                        interpolation=cv2.INTER_AREA) if df > 1 else T1s)
    sig = _hue_sig(f[y:y + bh, x:x + bw])
    if sig is not None and sig[1] < 0.35:
        sig = None                                   # not colorful enough to gate on
    trk.update(v2=True, W=w, H=h, fps=fps, df=df, box=(x, y, bw, bh),
               off=(x - cx0, y - cy0), T0b=T0b, T0s=T0s, T1s_p=T1s_p,
               off1=(x - sx0, y - sy0), s1shape=(sy1 - sy0, sx1 - sx0),
               E0=float(np.sqrt((T0s ** 2).mean())), sig=sig,
               cx=(cx0 + cx1) / 2.0, cy=(cy0 + cy1) / 2.0, s=1.0, miss=0,
               hold=max(6, int(round(fps * 0.75))), seen=False,
               keep=0.50, reacq=0.50, leash=0.15)
    return trk


def _track_user_box(trk, cx, cy, s):
    """Map the tracked BIG-context center/scale back to the USER's box."""
    bt_h, bt_w = trk["T0b"].shape
    x, y, bw, bh = trk["box"]
    return ((cx - bt_w * s / 2.0) + trk["off"][0] * s,
            (cy - bt_h * s / 2.0) + trk["off"][1] * s, bw * s, bh * s)


def _track_col_ok(trk, frame, ub):
    """Color gate: the candidate user-box must keep a sane share of the marked
    patch's dominant hue. Always passes for gray/colorless marks."""
    if trk["sig"] is None:
        return True
    x, y, w, h = [int(round(v)) for v in ub]
    x0, y0 = max(0, x), max(0, y)
    roi = frame[y0:y0 + max(2, h), x0:x0 + max(2, w)]
    if roi.size < 48:
        return False
    return _hue_frac(roi, trk["sig"][0]) >= max(0.10, 0.15 * trk["sig"][1])


def _track_locate(trk, frame):
    """v2 per-frame locator -> (x, y, w, h, score, active) for the USER box.
    active=0 means the tracker has no trustworthy location this frame."""
    W, H, df = trk["W"], trk["H"], trk["df"]
    T0b, T0s = trk["T0b"], trk["T0s"]
    bt_h, bt_w = T0b.shape
    fg = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    best = None
    if trk["seen"]:                                  # local search (continuity)
        gb = _blurf(fg)
        s = trk["s"]; tw, th = bt_w * s, bt_h * s
        rad = max(24, 0.7 * max(tw, th)) * (1 + 0.5 * min(trk["miss"], 6))
        wx0, wy0 = int(max(0, trk["cx"] - tw / 2 - rad)), int(max(0, trk["cy"] - th / 2 - rad))
        wx1, wy1 = int(min(W, trk["cx"] + tw / 2 + rad)), int(min(H, trk["cy"] + th / 2 + rad))
        win = gb[wy0:wy1, wx0:wx1]
        if win.shape[0] >= 12 and win.shape[1] >= 14:
            for sm in (0.94, 1.0, 1.06):             # multi-scale (zoom happens)
                ss = float(np.clip(s * sm, 0.15, 4.0))
                tws, ths = int(round(bt_w * ss)), int(round(bt_h * ss))
                if tws < 10 or ths < 7 or tws >= win.shape[1] or ths >= win.shape[0]:
                    continue
                t = cv2.resize(T0b, (tws, ths), interpolation=cv2.INTER_AREA)
                r = cv2.matchTemplate(win, t, cv2.TM_CCOEFF_NORMED)
                _, mx, _, loc = cv2.minMaxLoc(r)
                if best is None or mx > best[0]:
                    best = (float(mx), wx0 + loc[0], wy0 + loc[1], tws, ths, ss)
    Sg = None
    def _gate(cand):
        nonlocal Sg
        sc, px, py, tws, ths, ss = cand
        if Sg is None:
            Sg = _sobf(fg)
        crop = Sg[max(0, py):py + ths, max(0, px):px + tws]
        if crop.size == 0:
            return False
        if float(np.sqrt((crop ** 2).mean())) < 0.20 * trk["E0"] * min(1.0, ss):
            return False                             # edge veto: flat can't win
        t = cv2.resize(T0s, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_AREA)
        if _nccs(crop, t) < trk["leash"]:
            return False                             # structure leash vs reference
        return _track_col_ok(trk, frame, _track_user_box(trk, px + tws / 2.0,
                                                         py + ths / 2.0, ss))
    good = best is not None and best[0] >= trk["keep"] and _gate(best)
    if not good:                                     # global re-acquire (strict)
        smallg = (cv2.resize(fg, (W // df, H // df), interpolation=cv2.INTER_AREA)
                  if df > 1 else fg)
        Ssp = _sobf(smallg); ra = None
        s1h, s1w = trk["s1shape"]
        for s in (0.25, 0.32, 0.4, 0.5, 0.63, 0.8, 1.0, 1.2, 1.45):
            tws, ths = int(round(s1w * s / df)), int(round(s1h * s / df))
            if tws < 10 or ths < 7 or tws >= Ssp.shape[1] or ths >= Ssp.shape[0]:
                continue
            t = cv2.resize(trk["T1s_p"], (tws, ths), interpolation=cv2.INTER_AREA)
            r = cv2.matchTemplate(Ssp, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(r)
            if ra is None or mx > ra[0]:
                ra = (float(mx), loc[0] * df, loc[1] * df, s)
        if ra is not None and ra[0] >= trk["reacq"]:
            sc1, px1, py1, s = ra                    # small-ctx hit -> big-ctx box
            ux, uy = px1 + trk["off1"][0] * s, py1 + trk["off1"][1] * s
            cand = (sc1, int(round(ux - trk["off"][0] * s)),
                    int(round(uy - trk["off"][1] * s)),
                    int(round(bt_w * s)), int(round(bt_h * s)), s)
            if Sg is None:
                Sg = _sobf(fg)
            crop = Sg[max(0, cand[2]):cand[2] + cand[4], max(0, cand[1]):cand[1] + cand[3]]
            vok = (crop.size > 0 and crop.shape[0] >= 6 and crop.shape[1] >= 8 and
                   _nccs(crop, cv2.resize(T0s, (crop.shape[1], crop.shape[0]),
                                          interpolation=cv2.INTER_AREA)) >= 0.28)
            if vok and _gate(cand):
                best, good = cand, True
    if good:
        sc, px, py, tws, ths, ss = best
        trk["cx"], trk["cy"] = px + tws / 2.0, py + ths / 2.0
        trk["s"] = 0.85 * trk["s"] + 0.15 * ss if trk["seen"] else ss
        trk["miss"] = 0; trk["seen"] = True
        return _track_user_box(trk, trk["cx"], trk["cy"], trk["s"]) + (sc, 1)
    trk["miss"] += 1
    ub = _track_user_box(trk, trk["cx"], trk["cy"], trk["s"])
    active = 1 if (trk["seen"] and trk["miss"] <= trk["hold"]
                   and _track_col_ok(trk, frame, ub)) else 0
    return ub + (0.0, active)


def _track_mask(trk, frame, on_lost="skip"):
    """Per-frame effective mask for a tracked mark -> (mask01 or None, rect).
    v2: locate + scale the painted mask_roi to the tracked size, grow it by
    WR_TRACK_PAD (default 11%) so small drift still covers the mark. When the
    tracker abstains: erase SKIPS the frame (inpainting a guessed spot damages
    other content), blur HOLDS the last confident spot (privacy fails closed).
    Legacy (WR_TRACK_V2=0): the original single-scale argmax behavior."""
    h, w = frame.shape[:2]
    mr = trk["mask_roi"]
    if not trk.get("v2"):
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        res = cv2.matchTemplate(g, trk["template"], cv2.TM_CCOEFF_NORMED)
        _, _, _, loc = cv2.minMaxLoc(res)
        th_, tw_ = mr.shape
        nx, ny = int(loc[0]), int(loc[1])
        y0, x0 = max(0, ny), max(0, nx)
        y1, x1 = min(h, ny + th_), min(w, nx + tw_)
        if y1 <= y0 or x1 <= x0:
            return None, None
        eff = np.zeros((h, w), np.uint8)
        eff[y0:y1, x0:x1] = mr[y0 - ny:y1 - ny, x0 - nx:x1 - nx]
        eff = cv2.dilate(eff, np.ones((5, 5), np.uint8))
        return eff, (x0, y0, x1, y1)
    x, y, bw, bh, score, active = _track_locate(trk, frame)
    if not active:
        if on_lost != "hold" or not trk["seen"]:
            return None, None                        # abstain: do no harm
    bw_i, bh_i = max(2, int(round(bw))), max(2, int(round(bh)))
    nx, ny = int(round(x)), int(round(y))
    roi = cv2.resize(mr, (bw_i, bh_i), interpolation=cv2.INTER_NEAREST)
    pad = max(2, int(round(float(os.environ.get("WR_TRACK_PAD", "0.11"))
                           * max(bw_i, bh_i))))
    k = 2 * pad + 1
    # grow the CANVAS first, then dilate — dilating alone can't enlarge an
    # all-ones box mask, it would only end up shifted by the pad offset.
    roi = cv2.copyMakeBorder(roi, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    roi = cv2.dilate(roi, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    ny -= pad; nx -= pad                             # padding grew the roi
    th_, tw_ = roi.shape
    y0, x0 = max(0, ny), max(0, nx)
    y1, x1 = min(h, ny + th_), min(w, nx + tw_)
    if y1 <= y0 or x1 <= x0:
        return None, None
    eff = np.zeros((h, w), np.uint8)
    eff[y0:y1, x0:x1] = roi[y0 - ny:y1 - ny, x0 - nx:x1 - nx]
    return eff, (x0, y0, x1, y1)


def marked_region_motion(path, mask01, ref=None, limit=None, k=7):
    """Does the marked content MOVE across the clip? Cheap probe for the erase
    path: sample k frames over the first `limit` frames and test whether the
    reference patch is still at (about) its original spot — an NCC 'stay'
    score robust to ±8 px of handheld camera wobble, plus a dominant-hue
    'color stay'. Returns (moving, stats). Colorless marks only flip on a
    clear appearance collapse, so a static logo never gets hijacked."""
    m = (np.asarray(mask01) > 0).astype(np.uint8)
    if m.max() == 0:
        return False, dict(reason="empty mask")
    x, y, bw, bh = cv2.boundingRect(m)
    w, h, fps, n = probe(path)
    if limit:
        n = min(n, int(limit)) if n else int(limit)
    if n < k + 1 or bw < 12 or bh < 10:
        return False, dict(reason="too short/small")
    idx = int(ref * fps) if ref is not None else n // 2
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, idx))
    ok, reff = cap.read()
    if not ok:
        cap.release()
        return False, dict(reason="no ref frame")
    T = _blurf(cv2.cvtColor(reff, cv2.COLOR_BGR2GRAY))[y:y + bh, x:x + bw].copy()
    sig = _hue_sig(reff[y:y + bh, x:x + bw])
    if sig is not None and sig[1] < 0.35:
        sig = None
    wob = 8
    wx0, wy0 = max(0, x - wob), max(0, y - wob)
    wx1, wy1 = min(w, x + bw + wob), min(h, y + bh + wob)
    stays, cols = [], []
    for i in np.linspace(0, n - 1, k).round().astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if not ok:
            continue
        win = _blurf(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))[wy0:wy1, wx0:wx1]
        if win.shape[0] < T.shape[0] or win.shape[1] < T.shape[1]:
            continue
        r = cv2.matchTemplate(win, T, cv2.TM_CCOEFF_NORMED)
        stays.append(float(cv2.minMaxLoc(r)[1]))
        if sig is not None:
            cols.append(_hue_frac(f[y:y + bh, x:x + bw], sig[0]))
    cap.release()
    if not stays:
        return False, dict(reason="no samples")
    stay = float(np.median(stays))
    colstay = float(np.median(cols)) if cols else None
    moving = ((colstay is not None and colstay < 0.35 * sig[1] and stay < 0.55)
              or stay < 0.22
              or (colstay is None and stay < 0.32))
    return bool(moving), dict(stay=round(stay, 2),
                              colstay=None if colstay is None else round(colstay, 2))

def clean(input_path, output_path, inp, mask=None, boxes=None, auto=True, track=False,
          preview=None, upscale=None, sharpen=True, protect=True, ref=None):
    """Unified entry point for ONE file (image or video)."""
    if os.path.splitext(input_path)[1].lower() in IMAGE_EXTS:
        img = cv2.imread(input_path)
        if img is None:
            sys.exit(f"Cannot read {input_path}")
        h, w = img.shape[:2]
        if mask:
            m = mask_from_painted(mask, h, w)
        elif boxes:
            m = mask_from_boxes(boxes, h, w)
        elif auto:
            m = detect_image(input_path)
            if m is None:
                sys.exit("No watermark auto-detected in image; use --mask or --boxes.")
        else:
            sys.exit("For images, provide --mask or --boxes.")
        process_image(input_path, output_path, m, inp)
        return

    w, h, fps, n = probe(input_path)
    info = dict(type="manual", mask=None, B=None, meanf=None, gain=0.0)
    trk = None
    if track:
        if not boxes:
            sys.exit("--track needs --boxes marking the mark in a reference frame.")
        trk = _track_setup(input_path, boxes, ref)
        m = mask_from_boxes(boxes, h, w)
        print("[mode] tracking a moving/animated mark")
    elif mask:
        m = mask_from_painted(mask, h, w)
        if auto:
            det = detect(input_path)
            if det["type"] == "tiled":
                info = det; m = np.maximum(m, det["mask"])
    elif boxes:
        m = mask_from_boxes(boxes, h, w)
    else:
        info = detect(input_path)
        if info["type"] == "none" or info["mask"] is None:
            sys.exit("Could not auto-detect a watermark. Re-run with --mask / --boxes / --track.")
        m = info["mask"]; print(f"[detect] watermark type = {info['type']}")

    up = tuple(int(v) for v in upscale.lower().split("x")) if upscale else None
    process_video(input_path, output_path, info, m, inp, preview=preview, upscale=up,
                  sharpen=sharpen, protect_subject=protect, track=trk)


def main():
    import glob
    ap = argparse.ArgumentParser(description="Adaptive watermark/overlay remover.")
    ap.add_argument("input", help="video/image file (or a folder/glob with --batch)")
    ap.add_argument("output", help="output file (or an output folder with --batch)")
    ap.add_argument("--mask", help="PNG mask, white = remove")
    ap.add_argument("--boxes", help="x,y,w,h boxes separated by ';'")
    ap.add_argument("--auto", action="store_true", help="auto-detect (also augments --mask)")
    ap.add_argument("--track", action="store_true", help="follow a MOVING/animated mark (needs --boxes)")
    ap.add_argument("--batch", action="store_true", help="input=folder/glob, output=folder")
    ap.add_argument("--preview", type=float, help="only process the first N seconds")
    ap.add_argument("--ref", type=float, help="reference time (s) for the --track template")
    ap.add_argument("--upscale", help="e.g. 1080x1920")
    ap.add_argument("--engine", choices=["auto", "lama", "classical"], default="auto")
    ap.add_argument("--no-sharpen", action="store_true")
    ap.add_argument("--no-protect", action="store_true",
                    help="disable subject protection (inpaint the whole mask)")
    args = ap.parse_args()
    boxes = [tuple(int(v) for v in b.split(",")) for b in args.boxes.split(";")] if args.boxes else None
    auto = args.auto or not (args.mask or boxes or args.track)
    inp = Inpainter(args.engine)
    kw = dict(mask=args.mask, boxes=boxes, auto=auto, track=args.track, preview=args.preview,
              upscale=args.upscale, sharpen=not args.no_sharpen, protect=not args.no_protect, ref=args.ref)

    if args.batch:
        vid = (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi")
        src = glob.glob(os.path.join(args.input, "*")) if os.path.isdir(args.input) else glob.glob(args.input)
        files = sorted(f for f in src if os.path.splitext(f)[1].lower() in IMAGE_EXTS
                       or f.lower().endswith(vid))
        if not files:
            sys.exit(f"No media files found in {args.input}")
        os.makedirs(args.output, exist_ok=True)
        for fp in files:
            base, ext = os.path.splitext(os.path.basename(fp))
            outp = os.path.join(args.output, f"{base}_clean{ext}")
            print(f"\n=== {fp} ===")
            try:
                clean(fp, outp, inp, **kw)
            except SystemExit as e:
                print("skipped:", e)
            except Exception as e:
                print("FAILED:", e)
        return

    clean(args.input, args.output, inp, **kw)


if __name__ == "__main__":
    main()
