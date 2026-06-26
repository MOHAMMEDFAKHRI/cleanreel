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
    """LaMa if available, else OpenCV. mask01: uint8 {0,1}, 1 = remove."""
    def __init__(self, engine="auto"):
        self.kind = "classical"
        self.lama = None
        if engine in ("auto", "lama"):
            try:
                from simple_lama_inpainting import SimpleLama
                self._Image = __import__("PIL.Image", fromlist=["Image"])
                self.lama = SimpleLama()
                self.kind = "lama"
            except Exception as e:
                if engine == "lama":
                    sys.exit(f"LaMa requested but unavailable: {e}\n"
                             "pip install simple-lama-inpainting pillow torch")
        print(f"[engine] inpainting backend = {self.kind}", flush=True)

    def inpaint(self, bgr, mask01):
        if mask01.max() == 0:
            return bgr
        if self.kind == "lama":
            Image = self._Image
            rgb = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            m = Image.fromarray((mask01 * 255).astype(np.uint8))
            res = np.array(self.lama(rgb, m))
            return cv2.cvtColor(res, cv2.COLOR_RGB2BGR)
        # classical fallback
        return cv2.inpaint(bgr, (mask01 * 255).astype(np.uint8), 4, cv2.INPAINT_TELEA)


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

class Encoder:
    def __init__(self, w, h, fps, raw, upscale=None, sharpen=True):
        vf = []
        if upscale:
            vf.append(f"scale={upscale[0]}:{upscale[1]}:flags=lanczos+accurate_rnd")
        if sharpen:
            vf += ["cas=strength=0.5", "unsharp=5:5:0.4:5:5:0.0"]
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

def mux_audio(video_only, src, out):
    r = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", video_only,
                        "-i", src, "-map", "0:v:0", "-map", "1:a:0?",
                        "-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart", out])
    if r.returncode != 0:
        shutil.copy(video_only, out)


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

def detect(path):
    """Return dict(type, mask, B, meanf, gain). type in tiled|logo|none."""
    w, h, fps, n = probe(path)
    segs = detect_scenes(path, n)
    acc = np.zeros((h, w, 3), np.float64); cnt = 0
    for f in frames_iter(path):
        acc += f; cnt += 1
    meanf = (acc / max(cnt, 1)).astype(np.float32)
    wm = _watermark_gradient_map(path, segs, h, w)

    lat = _find_lattice(wm)
    if lat and lat[2] > 0.10:                       # strong periodicity -> TILED
        B = _refine_and_extract(meanf, lat[0], lat[1])
        if B is not None:
            C = 245.0
            def hp(x):
                g = cv2.cvtColor(np.clip(x, 0, 255).astype(np.uint8),
                                 cv2.COLOR_BGR2GRAY).astype(np.float32)
                return g - cv2.GaussianBlur(g, (0, 0), 6)
            num = de = 0.0
            for i, f in enumerate(frames_iter(path)):
                if i % max(1, n // 6):
                    continue
                O = f.astype(np.float32); r = np.clip((C-O)/(C-meanf+1e-3), 0, 2.5)
                D = hp(O) - hp(O - B * r); num += float((hp(O)*D).sum()); de += float((D*D).sum())
            gain = float(np.clip(num / max(de, 1e-6), 0.5, 2.0))
            prom = np.maximum(B.mean(2) - cv2.GaussianBlur(B.mean(2), (0, 0), 9), 0)
            mask = (prom > np.percentile(prom, 63)).astype(np.uint8)
            mask = cv2.dilate(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)),
                              np.ones((3, 3), np.uint8))
            return dict(type="tiled", mask=mask, B=B, meanf=meanf, gain=gain)

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
                  sharpen=True, protect_subject=True, track=None):
    w, h, fps, n = probe(path)
    limit = int(preview * fps) if preview else None
    B = info.get("B"); meanf = info.get("meanf"); gain = info.get("gain", 0.0); C = 245.0
    KW = max(15, (min(w, h) // 40) | 1); TAU = 12.0
    mask_bin = (mask01 > 0).astype(np.uint8) if mask01 is not None else None
    tmp = tempfile.mkdtemp(); raw = os.path.join(tmp, "v.mp4")
    enc = Encoder(w, h, fps, raw, upscale, sharpen)
    for k, f in enumerate(frames_iter(path, limit)):
        # (1) reverse-blend the diffuse periodic layer out of the whole frame
        if B is not None:
            O = f.astype(np.float32); r = np.clip((C-O)/(C-meanf+1e-3), 0, 3.0)
            f = np.clip(O - gain * B * r, 0, 255).astype(np.uint8)
        # (2) build this frame's mask
        if track is not None:
            # MOVING / ANIMATED mark: follow it by template-matching each frame.
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            res = cv2.matchTemplate(g, track["template"], cv2.TM_CCOEFF_NORMED)
            _, _, _, loc = cv2.minMaxLoc(res)
            mr = track["mask_roi"]; th, tw = mr.shape
            nx, ny = int(loc[0]), int(loc[1])
            eff = np.zeros((h, w), np.uint8)
            y0, x0 = max(0, ny), max(0, nx); y1, x1 = min(h, ny + th), min(w, nx + tw)
            if y1 > y0 and x1 > x0:
                eff[y0:y1, x0:x1] = mr[y0 - ny:y1 - ny, x0 - nx:x1 - nx]
            eff = cv2.dilate(eff, np.ones((5, 5), np.uint8))
        else:
            # STATIC mark: gate to locally-flat pixels so the moving subject
            # (face/hands/clothes detail) is left untouched.
            eff = mask_bin
            if protect_subject:
                gg = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
                eff = mask_bin & (_flatness(gg, KW) < TAU).astype(np.uint8)
            eff = cv2.dilate(eff, np.ones((3, 3), np.uint8))
        # (3) inpaint (ROI-only when localized -> fast + sharp)
        f = _inpaint_smart(inp, f, eff)
        enc.write(f)
        if k % 25 == 0:
            print(f"  frame {k+1}/{limit or n}", flush=True)
    enc.close(); mux_audio(raw, path, out); shutil.rmtree(tmp, ignore_errors=True)
    print(f"done -> {out}")


def detect_image(path):
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

def _track_setup(path, boxes, ref=None):
    """Template + mask_roi from the first box at a reference frame (for --track)."""
    w, h, fps, n = probe(path)
    idx = int(ref * fps) if ref is not None else n // 2
    cap = cv2.VideoCapture(path); cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(n - 1, idx)))
    ok, f = cap.read(); cap.release()
    if not ok:
        sys.exit("Could not read the reference frame for --track.")
    x, y, bw, bh = boxes[0]
    g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
    return dict(template=g[y:y + bh, x:x + bw].copy(), mask_roi=np.ones((bh, bw), np.uint8))

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
