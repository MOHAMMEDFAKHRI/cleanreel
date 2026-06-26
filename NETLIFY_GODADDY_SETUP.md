# Go live with GitHub + Netlify + GoDaddy

Your stack maps like this:

| Your tool | Hosts | What it runs |
|---|---|---|
| **Netlify** | the website (front end) | static files in `web/` |
| **GoDaddy** | your domain | DNS → points to Netlify |
| **GitHub** | the code | both front end + backend, auto-deploys on push |
| **Render** (new, free) | the API | the FastAPI + AI backend — *Netlify can't run this* |

**Why a second host?** Netlify serves static sites only. The watermark removal needs a
long-running Python server with ffmpeg + the AI model — that runs on **Render** (or Railway/Fly).
The Netlify site simply calls the Render API.

```
Visitor ─> your-domain (GoDaddy DNS) ─> Netlify (web/ static site)
                                           └─ fetch() ─> Render (FastAPI API + AI)
```

---

## Step 1 — Push the code to GitHub
From the `WatermarkRemover` folder:
```bash
git init
git add .
git commit -m "CleanReel: engine + API + web"
git branch -M main
git remote add origin https://github.com/<you>/cleanreel.git    # create this empty repo on github.com first
git push -u origin main
```
(`.gitignore` already excludes test media, caches and `storage/`.)

## Step 2 — Deploy the API to Render (free)
1. https://render.com → **New → Web Service** → connect your GitHub repo.
2. Settings:
   - **Runtime: Docker** (it auto-finds the `Dockerfile`).
   - **Root Directory:** leave blank (repo root) — the Dockerfile is there.
   - **Health Check Path:** `/api/health`
   - **Environment:** add `WR_ENGINE=auto` (and later `SITE_URL=https://your-domain.com`).
3. Create the service. When it's live, copy its URL, e.g. **`https://cleanreel-api.onrender.com`**.
   - First request downloads the AI model (~200 MB); it caches after.
   - ⚠ Render's *free* instance is small (and sleeps when idle). It's fine to validate with short
     clips. For real use, pick a small paid instance (more RAM) or a GPU host (Railway/Fly/Runpod).

## Step 3 — Point the website at your API
Edit **`web/config.js`**:
```js
window.API_BASE = "https://cleanreel-api.onrender.com";   // your Render URL
```
Commit + push (`git commit -am "set API url" && git push`).

## Step 4 — Deploy the website to Netlify
1. https://app.netlify.com → **Add new site → Import an existing project** → pick the GitHub repo.
2. Netlify reads `netlify.toml` automatically: **Publish directory = `web`**, no build command. Deploy.
3. You'll get `https://<random>.netlify.app` — open it and test upload → preview.

## Step 5 — Connect your GoDaddy domain
In **Netlify → Site → Domain management → Add a domain** → enter your domain. Then either:

- **Easiest — use Netlify DNS:** Netlify gives you 4 nameservers. In **GoDaddy → your domain →
  Nameservers → Change → Enter my own nameservers**, paste Netlify's. Netlify then handles DNS + HTTPS.
- **Or keep GoDaddy DNS:** in **GoDaddy → DNS**, add:
  - `A` record `@` → `75.2.60.5` (Netlify's load balancer)
  - `CNAME` record `www` → `<your-site>.netlify.app`

DNS takes minutes–hours to propagate; Netlify auto-issues a free HTTPS certificate.

## Step 6 — Finalize SEO
In `web/index.html` set `canonical` + `og:url` to `https://your-domain.com/`; in `web/robots.txt`
and `web/sitemap.xml` replace `YOUR-DOMAIN.com`. Commit + push (Netlify redeploys).
Then add the site in **Google Search Console** and submit `https://your-domain.com/sitemap.xml`.

## Updating later
Just `git push` — Netlify redeploys the site and Render redeploys the API automatically.

---
### Want me to walk it on your screen?
I can guide you click-by-click through Render, Netlify and GoDaddy (you do the logins and any
password/payment steps yourself — I won't touch credentials). Say the word and I'll start the walkthrough.
