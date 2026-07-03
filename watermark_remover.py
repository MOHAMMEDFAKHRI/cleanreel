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
    """Streams raw BGR frames into ffmpeg/libx264 with an optional filter chain
    (in order): deblock -> hqdn3d denoise -> lanczos scale -> cas+unsharp.
    `sharpen` is a bool or a 0..1 strength (True == 0.5, the historic default)."""
    def __init__(self, w, h, fps, raw, upscale=None, sharpen=True,
                 denoise=False, deblock=False):
        vf = []
        if deblock:
            vf.append("deblock=filter=strong:block=8")
        if denoise:
            vf.append("hqdn3d=1.5:1.5:4:4")
        if upscale:
            vf.append(f"scale={upscale[0]}:{upscale[1]}:flags=lanczos+accurate_rnd")
        s = 0.5 if sharpen is True else max(0.0, min(1.0, float(sharpen or 0.0)))
        if s > 0:
            vf += [f"cas=strength={round(s, 2)}", f"unsharp=5:5:{round(0.8 * s, 2)}:5:5:0.0"]
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
    if lat and lat[2] > 0.10:                       # strong periodicity -> TILED
        B = _refine_and_extract(meanf, lat[0], lat[1])
        if B is not None:
            gain = _calibrate_gain(path, B, meanf, n)
            prom = np.maximum(B.mean(2) - cv2.GaussianBlur(B.mean(2), (0, 0), 9), 0)
            mask = (prom > np.percentile(prom, 63)).astype(np.uint8)
            mask = cv2.dilate(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)),
                              np.ones((3, 3), np.uint8))
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
    if float(std_gray[mask > 0].mean()) < 3.0:               # opaque -> inpaint only
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


def enhance_video(path, out, scale=1.0, denoise=True, sharpen=0.6, deblock=None,
                  preview=None, max_dim=None, progress_cb=None):
    """Quality pass through the Encoder's ffmpeg chain: optional lanczos upscale,
    temporal denoise (hqdn3d), deblocking, and adaptive sharpening (cas+unsharp).
    No mask, no inpainting; audio is preserved.
      scale     output scale factor (1.0 or 2.0 from the UI)
      sharpen   0..1 strength (0 = off)
      deblock   None -> follow `denoise`
      max_dim   cap on the OUTPUT long side (memory guard; None = uncapped)
    """
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
    Returns (idxs, cxs, cys, cuts, n_seen) in SOURCE pixel coords."""
    w, h, fps, n = probe(path)
    step = max(1, int(round(fps / sample_hz)))
    sw = min(max_w, w); sh = max(2, int(round(h * sw / w)))
    kx, ky = w / sw, h / sh
    face = _face_cascade()
    ys_g, xs_g = np.mgrid[0:sh, 0:sw].astype(np.float32)
    idxs, cxs, cys, difs = [], [], [], []
    prev = None; n_seen = 0
    for i, f in enumerate(frames_iter(path, limit)):
        n_seen += 1
        if i % step:
            continue
        small = cv2.resize(f, (sw, sh), interpolation=cv2.INTER_AREA)
        g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gf = g.astype(np.float32)
        difs.append(float(np.mean(cv2.absdiff(g, prev[1]))) if prev is not None else 0.0)
        cx = cy = None
        if face is not None:
            det = face.detectMultiScale(g, 1.15, 4,
                                        minSize=(max(16, sh // 10), max(16, sh // 10)))
            if len(det):
                a = np.array([bw * bh for (x, y, bw, bh) in det], np.float32)
                cx = float(sum((x + bw / 2) * ar for (x, y, bw, bh), ar in zip(det, a)) / a.sum())
                cy = float(sum((y + bh / 2) * ar for (x, y, bw, bh), ar in zip(det, a)) / a.sum())
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
    virtual camera never whips. Cuts are allowed to jump. Returns float32[n]."""
    n_frames = max(1, int(n_frames))
    xs = np.interp(np.arange(n_frames), idxs, vals).astype(np.float32)
    sigma = max(1.0, smooth_sec * fps / 2.0)
    r = max(1, int(3 * sigma))
    ker = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2); ker /= ker.sum()
    bounds = [0] + sorted(c for c in set(cuts) if 0 < c < n_frames) + [n_frames]
    out = xs.copy()
    vmax = max(2.0, 0.012 * dim)                 # px/frame pan-speed ceiling
    for a, b in zip(bounds[:-1], bounds[1:]):
        seg = xs[a:b]
        if len(seg) >= 3:
            seg = np.convolve(np.pad(seg, r, mode="edge"), ker, mode="valid").astype(np.float32)
        cur = float(seg[0])
        for i in range(len(seg)):
            cur += float(np.clip(float(seg[i]) - cur, -vmax, vmax))
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
                  smooth_sec=None, progress_cb=None):
    """Convert a video to a new aspect ratio.
      fit='crop'  a smoothly tracked crop window keeps the subject (faces >
                  motion > detail) centered; per-shot smoothing kills jitter.
      fit='blur'  scale-to-fit over blurred, darkened bars (nothing cropped) —
                  the fallback when the subject is too wide to crop cleanly.
    Audio is preserved. max_dim caps the OUTPUT long side (memory guard).
    smooth_sec: crop-path smoothing window (default: env WR_REFRAME_SMOOTH or 1.0)."""
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
                  sharpen=True, protect_subject=True, track=None, progress_cb=None):
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
        if progress_cb and (limit or n) and k % 10 == 0:
            progress_cb(k + 1, limit or n)
        if k % 25 == 0:
            print(f"  frame {k+1}/{limit or n}", flush=True)
    enc.close(); mux_audio(raw, path, out); shutil.rmtree(tmp, ignore_errors=True)
    print(f"done -> {out}")


# --------------------------------------------------------------------------- #
# Quality control: score the clean, auto-tune, and report confidence
# --------------------------------------------------------------------------- #
def _hp_gray(bgr, sigma=6.0):
    g = cv2.cvtColor(np.clip(bgr, 0, 255).astype(np.uint8),
                     cv2.COLOR_BGR2GRAY).astype(np.float32)
    return g - cv2.GaussianBlur(g, (0, 0), sigma)

def _clean_frame_static(f, B, meanf, gain, mask_bin, inp, protect=True):
    """Clean ONE frame with the same static-mark logic as process_video (reverse-
    blend the diffuse layer, gate to flat pixels to protect the subject, ROI inpaint)."""
    h, w = f.shape[:2]
    KW = max(15, (min(w, h) // 40) | 1); TAU = 12.0; C = 245.0
    if B is not None and meanf is not None:
        O = f.astype(np.float32); r = np.clip((C - O) / (C - meanf + 1e-3), 0, 3.0)
        f = np.clip(O - gain * B * r, 0, 255).astype(np.uint8)
    eff = mask_bin
    if eff is not None and protect:
        gg = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        eff = mask_bin & (_flatness(gg, KW) < TAU).astype(np.uint8)
    if eff is not None:
        eff = cv2.dilate(eff, np.ones((3, 3), np.uint8))
    return _inpaint_smart(inp, f, eff)

def _sample_frames(path, k=4, limit=None):
    """Up to k frames spread evenly across the clip (or the first `limit` frames)."""
    frames = list(frames_iter(path, limit))
    if not frames:
        return []
    if len(frames) <= k:
        return frames
    idx = np.linspace(0, len(frames) - 1, k).round().astype(int)
    return [frames[i] for i in idx]

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

def quality_report(path, info, mask_bin, inp, gain=None, protect=True, k=4, limit=None):
    """Clean k sampled frames with the given params and average the QC scores.
    Returns dict(residual_reduction, damage, confidence, samples)."""
    B = info.get("B"); meanf = info.get("meanf")
    if gain is None:
        gain = info.get("gain", 0.0)
    frames = _sample_frames(path, k, limit)
    if not frames:
        return dict(residual_reduction=0.0, damage=1.0, confidence=0.0, samples=0)
    rr, dm = [], []
    for f in frames:
        cleaned = _clean_frame_static(f.copy(), B, meanf, gain, mask_bin, inp, protect)
        r, d = _score_clean(f, cleaned, mask_bin, B)
        rr.append(r); dm.append(d)
    resid = float(np.mean(rr)); damage = float(np.mean(dm))
    confidence = float(np.clip(resid, 0, 1) * (1.0 - np.clip(damage, 0, 1)))
    return dict(residual_reduction=round(resid, 3), damage=round(damage, 3),
                confidence=round(confidence, 3), samples=len(frames))

def autotune(path, info, mask_bin, inp, protect=True, k=4, limit=None):
    """Iteratively refine: try a small grid of reverse-blend strengths and mask
    dilations on sampled frames, keep the best-scoring combination. Returns
    (best_info, best_mask, qc_report). This is the automated 'reiterate on
    anomaly' step — done on samples so we render the full clip only once."""
    B = info.get("B")
    base_gain = float(info.get("gain", 0.0))
    # gain only matters for reverse-blend; skip that axis when B is None
    gain_mults = [1.0, 1.4] if B is not None else [1.0]
    dilations = [0, 4]
    best = None
    for gm in gain_mults:
        for dl in dilations:
            m = mask_bin if dl == 0 else cv2.dilate(mask_bin, np.ones((dl * 2 + 1,) * 2, np.uint8))
            g = base_gain * gm
            qc = quality_report(path, info, m, inp, gain=g, protect=protect, k=k, limit=limit)
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

def _track_setup(path, boxes=None, ref=None, mask01=None):
    """Template + mask_roi for a MOVING mark/object (--track / tracked erase).
    Either boxes[0] gives the region, or a painted mask01 does — then the box is
    its bounding rect and mask_roi keeps the painted shape, so only what the
    user brushed is inpainted as it moves. ref = reference time in seconds
    (default: middle of the clip)."""
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
    return dict(template=g[y:y + bh, x:x + bw].copy(), mask_roi=roi[:bh, :bw])

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
