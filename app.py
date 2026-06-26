#!/usr/bin/env python3
"""
app.py — friendly web UI for the adaptive watermark remover (MVP demo).

Flow:  upload short video  ->  Auto-detect (or brush the area)  ->  Preview  ->  Export.

Run:
    pip install -r requirements.txt
    python app.py
then open the local URL it prints (add share=True for a public link).

This is the MVP front end for the product in PRODUCT_BRIEF.md. It calls the
engine in watermark_remover.py. With `simple-lama-inpainting` installed it uses
the neural model; otherwise it uses the classical fallback so the demo always runs.
"""
import os, tempfile, traceback
import numpy as np
import cv2
import gradio as gr

import watermark_remover as wr

MAX_SECONDS = 30                 # MVP limit
PREVIEW_SECONDS = 4

# one shared inpainter (loads the model once)
ENGINE = os.environ.get("WR_ENGINE", "auto")
_INP = None
def inpainter():
    global _INP
    if _INP is None:
        _INP = wr.Inpainter(ENGINE)
    return _INP


# --------------------------------------------------------------------------- #
def _rep_frame(video_path):
    cap = cv2.VideoCapture(video_path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, n // 2))
    ok, f = cap.read(); cap.release()
    if not ok:
        return None
    return cv2.cvtColor(f, cv2.COLOR_BGR2RGB)

def on_upload(video):
    """Load a representative frame into the editor and validate length."""
    if not video:
        return None, "Upload a short video to begin."
    w, h, fps, n = wr.probe(video)
    secs = n / max(fps, 1)
    note = f"Loaded {w}x{h}, {secs:.1f}s. "
    if secs > MAX_SECONDS + 0.5:
        note += f"⚠ This MVP handles up to {MAX_SECONDS}s — only the first {MAX_SECONDS}s will be used."
    else:
        note += "Brush over the watermark, or click Auto-detect."
    frame = _rep_frame(video)
    return gr.ImageEditor(value=frame), note

def _mask_from_editor(editor):
    """Extract painted strokes (any brush layer) -> uint8 {0,1} mask at frame size."""
    if not editor:
        return None
    layers = editor.get("layers") or []
    bg = editor.get("background")
    if bg is None:
        return None
    h, w = bg.shape[:2]
    m = np.zeros((h, w), np.uint8)
    for ly in layers:
        if ly is None:
            continue
        if ly.ndim == 3 and ly.shape[2] == 4:
            m |= (ly[..., 3] > 10).astype(np.uint8)
        else:
            g = ly[..., :3].sum(2) if ly.ndim == 3 else ly
            m |= (g > 10).astype(np.uint8)
    return m if m.any() else None

def on_autodetect(video):
    """Run engine detection and show what would be removed (red overlay)."""
    if not video:
        return None, "Upload a video first."
    try:
        info = wr.detect(video)
    except Exception as e:
        return None, f"Detection error: {e}"
    if info["type"] == "none" or info["mask"] is None:
        return None, "No clear watermark found automatically — please brush over it."
    frame = _rep_frame(video)
    h, w = frame.shape[:2]
    mask = cv2.resize(info["mask"], (w, h), interpolation=cv2.INTER_NEAREST)
    ov = frame.copy(); ov[mask > 0] = (255, 40, 40)
    blended = cv2.addWeighted(frame, 0.55, ov, 0.45, 0)
    return blended, f"Detected a **{info['type']}** watermark (shown in red). Click Preview."

def _build_mask(video, editor, w, h):
    """Painted mask if present, else auto-detected; returns (mask, info)."""
    info = dict(type="manual", mask=None, B=None, meanf=None, gain=0.0)
    m = _mask_from_editor(editor)
    if m is not None:
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
        det = wr.detect(video)                  # still grab periodic layer if tiled
        if det["type"] == "tiled":
            info = det; m = np.maximum(m, cv2.resize(det["mask"], (w, h), interpolation=cv2.INTER_NEAREST))
        return m, info
    info = wr.detect(video)
    if info["mask"] is None:
        return None, info
    return cv2.resize(info["mask"], (w, h), interpolation=cv2.INTER_NEAREST), info

def _process(video, editor, seconds, upscale, protect):
    w, h, fps, n = wr.probe(video)
    mask, info = _build_mask(video, editor, w, h)
    if mask is None:
        raise gr.Error("No watermark marked or detected. Brush over it and retry.")
    out = os.path.join(tempfile.mkdtemp(), "cleaned.mp4")
    up = (1080, 1920) if upscale and h >= w else (1920, 1080) if upscale else None
    wr.process_video(video, out, info, mask, inpainter(),
                     preview=seconds, upscale=up, sharpen=True, protect_subject=protect)
    return out

def on_preview(video, editor, upscale, protect):
    if not video:
        raise gr.Error("Upload a video first.")
    try:
        return _process(video, editor, PREVIEW_SECONDS, upscale, protect), \
               f"Preview of the first {PREVIEW_SECONDS}s ready. Happy? Click Export."
    except gr.Error:
        raise
    except Exception:
        raise gr.Error(traceback.format_exc().splitlines()[-1])

def on_export(video, editor, upscale, protect):
    if not video:
        raise gr.Error("Upload a video first.")
    try:
        out = _process(video, editor, min(MAX_SECONDS, 9999), upscale, protect)
        return out, out, "Done! Your cleaned video is ready to download."
    except gr.Error:
        raise
    except Exception:
        raise gr.Error(traceback.format_exc().splitlines()[-1])


# --------------------------------------------------------------------------- #
CSS = """
.gradio-container {max-width: 1000px !important}
#title {text-align:center}
footer {visibility:hidden}
"""

with gr.Blocks(css=CSS, title="CleanReel — Watermark Remover") as demo:
    gr.Markdown("# ✨ CleanReel — remove watermarks from your video", elem_id="title")
    gr.Markdown(
        "Upload a short clip **you own or have the rights to edit**, mark the overlay "
        "(or let us find it), preview, and export it clean. Faces and detail stay sharp.")

    with gr.Row():
        with gr.Column(scale=1):
            gr.Markdown("### 1 · Upload")
            video_in = gr.Video(label=f"Short video (≤{MAX_SECONDS}s)", sources=["upload"])
            owns = gr.Checkbox(label="I own or have the rights to edit this video", value=False)
            status = gr.Markdown("")
            gr.Markdown("### 2 · Mark the watermark")
            editor = gr.ImageEditor(label="Brush over the watermark, or use Auto-detect",
                                    type="numpy", brush=gr.Brush(colors=["#ff2828"], default_size=24),
                                    interactive=True)
            auto_btn = gr.Button("🔎 Auto-detect watermark", variant="secondary")
            with gr.Accordion("Advanced", open=False):
                upscale = gr.Checkbox(label="Upscale + sharpen to HD", value=True)
                protect = gr.Checkbox(label="Protect faces / detailed areas", value=True)
        with gr.Column(scale=1):
            gr.Markdown("### 3 · Preview & Export")
            preview_out = gr.Video(label="Preview / Result", interactive=False)
            with gr.Row():
                preview_btn = gr.Button("👁 Preview (first 4s)", variant="secondary")
                export_btn = gr.Button("⬇ Export full clean video", variant="primary")
            download = gr.File(label="Download", interactive=False)
            result_msg = gr.Markdown("")

    video_in.change(on_upload, video_in, [editor, status])
    auto_btn.click(on_autodetect, video_in, [editor, status])

    def _guard(owns_val):
        if not owns_val:
            raise gr.Error("Please confirm you own / have rights to edit this video.")
    preview_btn.click(_guard, owns, None).success(
        on_preview, [video_in, editor, upscale, protect], [preview_out, result_msg])
    export_btn.click(_guard, owns, None).success(
        on_export, [video_in, editor, upscale, protect], [preview_out, download, result_msg])

    gr.Markdown(
        "<sub>CleanReel is for content you own or are licensed to edit. "
        "Uploads are processed for your request only. Quality varies by watermark type — "
        "always check the free preview before exporting.</sub>")

if __name__ == "__main__":
    demo.launch(share=bool(os.environ.get("WR_SHARE")))
