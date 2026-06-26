# Watermark Remover — Product & Technical Brief

*A web app that lets anyone upload a short video they own, mark the unwanted overlay, preview the cleaned result, and export it watermark-free — for free or minimal cost.*

> Working name ideas: **CleanReel**, **Unmark**, **ClearCut**, **Watermarkless**, **Plainframe**. (Pick later; "CleanReel" used as placeholder below.)

---

## 1. One-line pitch
Upload a short video → mark the watermark/overlay → AI removes it cleanly while keeping faces and detail sharp → download. Free for previews and small jobs; pay only for full HD video exports.

## 2. Who it's for & why
- **UGC creators / marketers / dropshippers** exporting from tools like Creatify, Kapwing, Canva, InVideo whose free tiers stamp a watermark.
- **Editors** who need to remove a stray logo, old subtitle burn-in, timecode, or bug from footage **they own or have the rights to edit**.
- The pull: existing removers are either low-quality (blur a box), expensive, or watermark the *output* again. CleanReel produces a genuinely clean result and a free preview before you pay anything.

## 3. The core idea (what makes it good)
A two-stage **adaptive** pipeline that figures out *what kind* of watermark it's facing and removes it the right way:

1. **Estimate & subtract** the consistent watermark layer (reverse-blend). Because most watermarks are *static* (same position every frame) and often *semi-transparent*, the real footage underneath can be largely **recovered**, not guessed.
2. **Neural inpaint** the residual (LaMa) only where needed, **gated away from faces/detailed areas** so the subject stays sharp and never gets the "melted" look generic removers produce.

The engine auto-detects the case and adapts:

| Detected type | How it's handled |
|---|---|
| **Tiled / periodic** (e.g. Creatify's repeated mark) | Find the tiling lattice → reverse-blend the whole layer → AI cleans residue on flat areas |
| **Corner logo / bug** | Mask the fixed region → AI inpaint |
| **User-marked region** (primary MVP path) | User paints the area → reverse-blend if periodic there, else AI inpaint |
| **Subtitle / text burn-in** | Treated as a marked region; AI inpaint per frame |

Proven on a real Creatify clip: went from fully watermarked → ~99% clean with the subject untouched.

## 4. MVP scope (v1) — stay focused
**One use case, done well:** short videos **up to 30 seconds**.

User flow:
1. **Upload** a short video (≤30s, ≤1080p) they own/have rights to.
2. **Mark** the unwanted overlay by painting over it on a representative frame (plus a one-click **Auto-detect** that proposes the region).
3. **Preview** — process a few seconds and show a before/after slider (fast, free).
4. **Export** — process the full clip and download the cleaned file (audio preserved).

Out of scope for v1 (later phases): images, long videos, batch, accounts/history, API.

## 5. UX principles (must feel effortless)
- Drag-and-drop upload; no signup to get a preview.
- "Mark it or let us find it" — manual brush **and** auto-detect; never force the user to understand the tech.
- Always show a **free preview before payment**. No surprises.
- Honest progress + time estimate; results download in one click; **never** re-watermark the output.
- Mobile-friendly, fast first paint, accessible.

## 6. Architecture — hybrid compute (cost-safe)
Prove the engine + UX first; scale infra only after demand.

- **Free / cheap tier:** previews and very short/low-res jobs run on cheap CPU workers (or in-browser later). Near-zero marginal cost → sustainable free tier.
- **Paid tier (credits):** full-length / HD video exports run on **server-side GPU** workers behind a **queue**, with per-job limits. GPU is rented on-demand and scaled to the queue, so cost tracks revenue.
- **Flow:** Frontend → API (upload, validate, job create) → object storage → queue → worker (CPU preview or GPU export) → result to storage → signed download URL. Webhooks/polling for progress.

```
[Browser] --upload--> [API/FastAPI] --> [Object storage (S3/R2)]
     |                     |                      |
   preview                 v                      v
   request           [Job queue] --> [CPU worker: preview]   (free)
                                 \-> [GPU worker: full export] (credits)
                                            |
                                      [Result] --> signed URL --> [Browser download]
```

## 7. Tech stack
- **Engine (the IP):** Python, OpenCV, NumPy; **LaMa** inpainting (`simple-lama-inpainting` / ONNX for portability); ffmpeg for I/O + audio passthrough; reverse-blend + lattice estimation (this repo's `watermark_remover.py`).
- **MVP web demo (this session):** **Gradio** app — fastest path to a real, shareable upload→mark→preview→export UI. Validates UX before investing in a custom front end.
- **Production (later):** Next.js (UI) + FastAPI (API) + Redis/RQ or Celery (queue) + S3/Cloudflare R2 (storage) + GPU workers (Runpod / Modal / Lambda / fly.io GPUs) + Stripe (credits). Auth via Clerk/Supabase.
- **Models served via** ONNX Runtime (CPU previews) and Torch+CUDA (GPU exports); model cached on workers.

## 8. Roadmap
- **Phase 0 (now):** adaptive engine + Gradio demo; validate quality & UX locally.
- **Phase 1:** deploy demo (HuggingFace Spaces / Render) for short videos; collect feedback; add auto-detect polish.
- **Phase 2:** accounts, credits (Stripe), GPU queue for full HD exports, job history.
- **Phase 3:** images, longer videos, batch, public API, browser-side previews (WebGPU) to cut costs further.

## 9. Pricing (illustrative)
- **Free:** unlimited previews; a few short exports/day at ≤720p with a small daily cap.
- **Credits / Pro:** HD (1080p) and longer exports; e.g. pennies per processed minute or a low monthly cap. Goal: cheapest credible option on the market.

## 10. Legal & ethical guardrails (non-negotiable)
- **Ownership gate:** users must confirm they **own or have the rights** to edit the uploaded video before processing. Clear ToS.
- The tool is for **your own content / licensed content** (removing your own free-tier export's mark, your old logo, stray text) — **not** for stripping third-party copyright/ownership marks. State this plainly.
- Privacy: auto-delete uploads/outputs after a short window; no training on user data without explicit opt-in; encrypted storage.
- Don't re-watermark or fingerprint outputs.

## 11. Success metrics
- Preview→export conversion; median time-to-clean; quality (manual rating / residual-watermark score); cost per export; D7 retention; organic search traffic for "remove watermark from video."

## 12. Known risks & mitigations
- **GPU cost spikes** → queue + per-job caps + credits gate; CPU/in-browser previews keep free tier cheap.
- **Hard cases** (moving/animated marks, opaque marks over detail) → set expectations, show preview first, offer manual brush refinement.
- **Abuse / IP misuse** → ownership attestation, ToS, rate limits, report flow.
- **Quality variance across videos** → adaptive detection + user region marking + preview-before-pay.
