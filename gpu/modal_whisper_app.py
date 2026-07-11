"""
modal_whisper_app.py — CleanReel's speech-to-captions microservice.

What it does
------------
Transcribes a clip's audio into timestamped segments (faster-whisper, "small"
model, ~460 MB, baked at build). The engine extracts mono 16 kHz audio, sends
it once per job, gets segments back, and renders them as burned-in captions
plus a downloadable .srt. Language is auto-detected.

Deploy
------
    deploy_whisper.bat   (or: py -3 -m modal deploy gpu/modal_whisper_app.py)
Prints the endpoint URL -> set on Render as WR_WHISPER_URL.
Auth reuses the shared token: Modal secret `cleanreel-inpaint` / Render
WR_INPAINT_TOKEN.

Wire format (matches watermark_remover.transcribe_audio)
    POST {url}
    body : {"token": <INPAINT_TOKEN>, "audio": <b64 wav mono 16k>,
            "language": null | "en" | ...}
    resp : {"language": "en", "duration": 12.3,
            "segments": [{"start": 0.0, "end": 2.4, "text": "..."}, ...]}
"""
import os
import base64

import modal

app = modal.App("cleanreel-whisper")

_MODEL = os.environ.get("WHISPER_MODEL", "small")
_ROOT = "/root/wmodels"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install("faster-whisper", "fastapi[standard]")
    # Bake the model at BUILD (and load-test it on CPU) so cold starts never
    # download and a broken snapshot fails the deploy, not a customer job.
    .run_commands(
        "python -c \"from faster_whisper import WhisperModel; "
        f"m = WhisperModel('{_MODEL}', device='cpu', compute_type='int8', "
        f"download_root='{_ROOT}'); print('whisper model baked OK')\""
    )
)

with image.imports():
    import io
    from faster_whisper import WhisperModel


@app.cls(
    gpu="T4",
    image=image,
    scaledown_window=120,
    secrets=[modal.Secret.from_name("cleanreel-inpaint")],
    timeout=300,
)
class Whisper:
    @modal.enter()
    def load(self):
        # GPU fp16 when available; quantized CPU as a resilient fallback so a
        # CUDA/cudnn hiccup degrades to "slower" instead of "broken".
        try:
            self.model = WhisperModel(_MODEL, device="cuda",
                                      compute_type="float16",
                                      download_root=_ROOT)
            self.device = "cuda"
        except Exception as e:
            print(f"[whisper] cuda load failed ({e!r}); using CPU int8", flush=True)
            self.model = WhisperModel(_MODEL, device="cpu",
                                      compute_type="int8",
                                      download_root=_ROOT)
            self.device = "cpu"

    @modal.fastapi_endpoint(method="POST")
    def transcribe(self, payload: dict):
        from fastapi import HTTPException
        token = os.environ.get("INPAINT_TOKEN", "")
        if not token or (payload or {}).get("token") != token:
            raise HTTPException(status_code=401, detail="unauthorized")
        audio_b64 = (payload or {}).get("audio", "")
        if not audio_b64 or len(audio_b64) > 40_000_000:   # ~30 MB decoded cap
            raise HTTPException(status_code=400, detail="bad audio payload")
        try:
            buf = io.BytesIO(base64.b64decode(audio_b64))
            segments, info = self.model.transcribe(
                buf,
                language=(payload or {}).get("language") or None,
                vad_filter=True,                # skip long silences
                beam_size=5,
            )
            out = [{"start": round(s.start, 3), "end": round(s.end, 3),
                    "text": s.text.strip()}
                   for s in segments if s.text and s.text.strip()]
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"transcribe failed: {e}")
        return {"language": info.language, "duration": round(info.duration, 3),
                "segments": out}
