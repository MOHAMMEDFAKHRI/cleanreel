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
| POST | `/api/jobs` | `{file_id, mode:"preview"\|"export", owns_rights, boxes?, auto, upscale, protect}` → `{job_id}` |
| GET  | `/api/jobs/{id}` | `{status, progress, message, result_url?}` |
| GET  | `/api/result/{id}` | download cleaned mp4 |

- **preview** = free, first 4s. **export** = full clip; requires `owns_rights`, length ≤ 30s, and ≥1 credit.
- Auto-detect runs if no `boxes` are given; otherwise the boxes define the region.

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
