# CleanReel — Backend API (MVP)

FastAPI service that turns the engine into a web service with the **hybrid**
free-preview / paid-export model from `../PRODUCT_BRIEF.md`.

## Run
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
Open **http://127.0.0.1:8000/** for a built-in test UI, or hit the API directly.
(Uses `py -3.12` consistently is recommended on Windows: `py -3.12 -m uvicorn main:app --port 8000`.)

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET  | `/api/health` | liveness + limits |
| GET  | `/api/credits` | remaining export credits (stub) |
| POST | `/api/upload` | multipart video → `{file_id, width, height, seconds}` |
| GET  | `/api/reference/{fid}` | sharp still with the watermark highlighted (remove-mode canvas) |
| GET  | `/api/frame/{fid}` | sharp still, NO highlight (erase-mode canvas) |
| POST | `/api/autodetect/{fid}` | watermark auto-detect → PNG mask |
| POST | `/api/jobs` | `{file_id, mode:"preview"\|"export", task, owns_rights, ...}` → `{job_id}` |
| GET  | `/api/jobs/{id}` | `{status, progress, message, qc?, result_url?}` |
| GET  | `/api/result/{id}` | download the result mp4 |

- **preview** = free, first 4s. **export** = full clip; requires `owns_rights`, length ≤ 30s, and ≥1 credit — for every task.
- `task` selects the pipeline:
  - `remove` (default) — watermark removal; auto-detect runs if no `mask`/`boxes` are given. Extra: `mask` (base64 PNG), `boxes`, `protect`.
  - `erase` — inpaint ANY user-marked region (opaque, every frame). Requires `mask` or `boxes`; optional `track:true` follows a moving object (template from the `/api/frame` still).
  - `enhance` — no mask; re-encode through the enhancement chain. Extra: `scale` (1|2), `denoise` (bool, also drives deblock), `strength` (sharpen 0..1).
  - `reframe` — aspect conversion. Extra: `ratio` ("9:16"|"1:1"|"4:5"…), `fit` ("crop" = subject-tracked crop, "blur" = fit + blurred bars).
- `remove`/`erase` return a `qc` quality report; `enhance`/`reframe` do not (nothing to score).

## Environment variables (all optional, safe defaults)
| Var | Default | Meaning |
|---|---|---|
| `WR_ENGINE` | `auto` (`classical` in the Dockerfile) | inpainting backend; `auto` uses LaMa when installed (needs a 2 GB+ instance) |
| `WR_MAX_DIM` | `1366` | max output long side for remove / erase / reframe — oversized inputs are downscaled, never enlarged |
| `WR_ALLOW_UPSCALE` | unset | set `1` on a big instance to allow 1080p upscaling in remove/erase |
| `WR_ENHANCE_MAX` | `1600` | max OUTPUT long side for the enhance task (a 2× request is capped to this — memory guard) |
| `WR_REFRAME_SMOOTH` | `1.0` | reframe crop-path smoothing window, in seconds (bigger = calmer camera) |

## How the pieces map to the product
- `main.py` — HTTP API, validation, the **free-vs-paid gate** (length limit + credits).
- `jobs.py` — in-memory **queue + worker** calling the engine; `preview` cheap, `export` full.
- `static/index.html` — minimal browser test UI (upload → preview → export → download).

## Production swap-ins (marked `# PROD` in code)
- **Queue/workers:** replace the in-process queue with Redis + RQ/Celery; run *preview* on cheap CPU workers and *export* on **GPU** workers (autoscaled to the queue) — this is what keeps the free tier near-zero-cost.
- **Storage:** local `storage/` → S3 / Cloudflare R2 with signed URLs + auto-delete after N hours.
- **Auth + credits + payments:** real user accounts; credits backed by **Stripe**; the `X-User` header / `CREDITS` dict are stubs.
- **Limits/abuse:** rate limits, max resolution/length per tier, ownership attestation logging.
- **Front end:** swap the test page for a Next.js app calling these same endpoints.
