# Going live — deployment guide

The backend **serves the website too** (the polished page in `backend/static/index.html`),
so deploying the one container puts the whole product online: marketing page + tool + API.

```
Browser ── https://your-domain ──> [Docker container]
                                     ├─ /            website (front end)
                                     ├─ /api/*        API (upload, jobs, result)
                                     └─ /robots.txt /sitemap.xml   (SEO)
```

## Fastest path (one container, free/low-cost tier)
Hosts that run a Docker container or a Python web service with a persistent process:
**Render, Railway, Fly.io** (all have small free/cheap tiers).

1. Put this `WatermarkRemover/` folder in a Git repo (GitHub).
2. On the host, create a **Web Service** from the repo using the included **`Dockerfile`**.
   - Port: **8000**. Health check path: `/api/health`.
   - Env: `SITE_URL=https://your-domain` (fixes SEO URLs), optional `WR_ENGINE=auto`.
3. Deploy. Open the URL — the site is live; `/docs` shows the API.

> First request downloads the LaMa model (~200 MB) onto the worker; it's cached after.
> CPU works (slower); for fast HD exports use a GPU host (below).

### Local Docker test
```
cd WatermarkRemover
docker build -t cleanreel .
docker run -p 8000:8000 -e SITE_URL=http://localhost:8000 cleanreel
# open http://localhost:8000
```

## Custom domain + SEO checklist
- Point your domain at the host; set `SITE_URL` to it.
- In `backend/static/index.html`, update the `canonical` + `og:url` to your domain.
- Submit `https://your-domain/sitemap.xml` in **Google Search Console**.
- Target keywords ("remove watermark from video", "free video watermark remover"); add a short blog/help section over time (this is where Next.js pays off later).

## Scaling path (when you have traffic)
1. **Split the front end** to a static host (Vercel / Netlify / Cloudflare Pages) for speed + CDN.
   In `index.html`, set `const API = "https://your-api-domain";` (CORS is already open).
   Migrate to **Next.js** here for multi-page SEO + a content/blog.
2. **GPU workers for exports** — move `jobs.py` from the in-process queue to **Redis + RQ/Celery**;
   run *preview* on cheap CPU, *export* on GPU (Runpod / Modal / Fly GPU), autoscaled to the queue.
   This is what keeps the free tier near-zero-cost while paid exports stay fast.
3. **Payments** — replace the in-memory credits with **Stripe** (checkout → webhook → credit balance) + real accounts (Clerk/Supabase).
4. **Storage** — local `storage/` → **S3 / Cloudflare R2** with signed URLs and auto-delete after N hours.
5. **Guardrails** — rate limits, max length/resolution per tier, ownership attestation logging, abuse reporting.

## Cost-control summary (matches the brief's hybrid model)
- Free tier: previews + short/low-res → cheap CPU (or in-browser later).
- Paid tier: full HD / longer → GPU queue, gated by credits + per-job limits.
- GPU rented on-demand and scaled to the queue, so spend tracks revenue.
