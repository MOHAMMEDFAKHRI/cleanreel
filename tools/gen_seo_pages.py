#!/usr/bin/env python3
"""Generate CleanReel's SEO intent landing pages into web/.

One shared template + a per-page content dict = single source of truth.
Re-run after editing PAGES:  python tools/gen_seo_pages.py
Every page carries the rights-aware framing (owned/licensed content only,
auto-delete, free preview) and routes into the studio at /#tool.
"""
import html
import json
import os

SITE = "https://cleanreel.app"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

PAGES = {
    "remove-watermark-from-video": dict(
        title="Remove a Watermark from Video Online (Videos You Own) — CleanReel",
        h1="Remove a watermark from your video",
        desc="Remove your old watermarks from videos you own — AI inpainting fills the area "
             "naturally instead of blurring or cropping. Free preview, audio kept, uploads auto-delete.",
        intro="Re-exporting an old edit because a stale watermark is baked in? CleanReel's AI "
              "detects the mark, removes it with neural inpainting, and rebuilds the pixels "
              "underneath — no blur patch, no crop, audio untouched.",
        use_cases=["Your own free-tier exports from editing apps",
                   "An old channel watermark on footage you made",
                   "A client's mark on video they've licensed you to rework",
                   "Stray timestamps or camera overlays"],
        steps=["Upload a clip you own (up to 60s / 200 MB)",
               "Let auto-detect find the watermark — or brush over it",
               "Preview the cleaned result free",
               "Adjust the highlight and re-run until it's spotless",
               "Export the full video (1 credit) with audio intact"],
        faq=[("Can I remove any watermark?",
              "No — CleanReel is only for content you own or are licensed to edit, like your "
              "own exports or an old logo of yours. Removing someone else's watermark from "
              "their content may infringe their rights."),
             ("Will it leave a blur or smudge?",
              "No. Instead of blurring, neural inpainting reconstructs the background behind "
              "the mark, and a quality score tells you how clean the result is."),
             ("Does it work on moving watermarks?",
              "Marked regions can be tracked across frames. Semi-transparent and opaque marks "
              "use different removal strategies automatically.")],
        related=["remove-logo-from-video", "remove-text-from-video", "remove-object-from-video"]),

    "remove-logo-from-video": dict(
        title="Remove a Logo from Video Online (Rebrands & Old Marks) — CleanReel",
        h1="Remove an old logo from your video",
        desc="Rebranded? Erase your old logo from existing videos instead of re-editing them. "
             "AI inpainting, free preview, audio preserved, uploads auto-delete.",
        intro="A rebrand shouldn't mean re-editing years of content. CleanReel erases your old "
              "logo from finished videos and reconstructs what's behind it, so your library "
              "stays usable.",
        use_cases=["Old company or channel logo after a rebrand",
                   "A partner's expired sponsorship bug",
                   "Legacy corner bugs on footage you own",
                   "Client videos you're licensed to refresh"],
        steps=["Upload the video you own or are licensed to edit",
               "Brush over the logo (or let auto-detect find it)",
               "Preview the removal free",
               "Fine-tune the region if any trace remains",
               "Export the full clip — audio and quality preserved"],
        faq=[("Can I remove a competitor's logo from their video?",
              "No. CleanReel is for content you own or are licensed to edit — your own "
              "rebranded material, not other people's work."),
             ("What if the logo sits on a busy background?",
              "Neural inpainting handles textured backgrounds far better than blur tools, and "
              "the free preview lets you check before paying."),
             ("Does the whole video get re-compressed?",
              "The video is re-encoded at high quality (CRF 16) and your audio track is copied "
              "through untouched.")],
        related=["remove-watermark-from-video", "remove-object-from-video", "video-enhancer"]),

    "remove-text-from-video": dict(
        title="Remove Text, Subtitles & Timestamps from Video — CleanReel",
        h1="Remove burned-in text from your video",
        desc="Erase burned-in subtitles, timestamps, and captions from videos you own. AI fills "
             "the background naturally. Free preview, uploads auto-delete.",
        intro="Burned-in subtitles, timestamps from an old camera, captions in the wrong "
              "language — CleanReel removes baked-in text from footage you own and rebuilds "
              "the background behind it.",
        use_cases=["Hard-coded subtitles you need gone before re-captioning",
                   "Camera date/time stamps on family or archive footage",
                   "Old lower-thirds and titles on your own productions",
                   "Screen-recording overlays"],
        steps=["Upload your clip (60s / 200 MB max)",
               "Brush across the text region — or auto-detect it",
               "Preview the clean result free",
               "Re-run with an adjusted highlight if needed",
               "Export in full quality with original audio"],
        faq=[("Can it remove subtitles from a downloaded movie?",
              "No — only content you own or are licensed to edit. Typical legitimate uses are "
              "your own productions, archives, and licensed client work."),
             ("Text sits over a face — will the face survive?",
              "Faces and detailed areas are protected by the engine; removal is gated so the "
              "subject stays sharp."),
             ("Scrolling or moving text?",
              "Mark the full band it travels through, or use tracking for a moving region.")],
        related=["remove-watermark-from-video", "remove-object-from-video", "blur-face-in-video"]),

    "remove-object-from-video": dict(
        title="Remove Objects or People from Video Online — CleanReel",
        h1="Erase objects from your video",
        desc="Remove photobombers, trash, cables, or any distraction from videos you own. "
             "Neural inpainting with motion tracking. Free preview first.",
        intro="A stray trash can, a boom mic dipping into frame, a passer-by in your shot — "
              "brush over it and CleanReel erases it across the clip, tracking movement and "
              "rebuilding the background.",
        use_cases=["Passers-by in travel or real-estate footage",
                   "Equipment in shot: mics, stands, cables",
                   "Distracting objects in product or UGC videos",
                   "Accidental reflections or personal items"],
        steps=["Upload the video you own",
               "Brush over the object to erase",
               "Turn on tracking if it moves through the shot",
               "Preview free and refine the mask",
               "Export the cleaned full clip"],
        faq=[("Can it remove a person from someone else's video?",
              "CleanReel only accepts content you own or are licensed to edit — and consider "
              "privacy laws in your region when editing people out of footage."),
             ("How large an object can it handle?",
              "Small-to-medium regions inpaint best. Very large regions may show artifacts — "
              "the free preview and quality score show you before you pay."),
             ("Moving objects?",
              "Yes — enable tracking and the marked region follows the object across frames.")],
        related=["remove-watermark-from-video", "remove-text-from-video", "blur-face-in-video"]),

    "blur-face-in-video": dict(
        title="Blur Faces & License Plates in Video (Privacy) — CleanReel",
        h1="Blur faces &amp; plates in your video",
        desc="Auto-detect and blur every face or license plate across your video — for privacy, "
             "compliance, and safe sharing. Neural face detection, free preview.",
        intro="Publishing training footage, dashcam clips, or street interviews? CleanReel "
              "finds faces and plates with neural detection, tracks them across frames, and "
              "blurs or pixelates them steadily — no flicker.",
        use_cases=["Training and internal videos before wider sharing",
                   "Dashcam or incident footage with visible plates",
                   "Bystanders in vlogs and street footage",
                   "Compliance-driven redaction (GDPR-style requests)"],
        steps=["Upload your clip",
               "Pick targets: faces, plates, or both — or brush any custom region",
               "Choose blur or pixelate and set the strength",
               "Preview free — detection is tracked and smoothed",
               "Export the redacted full video"],
        faq=[("Will it catch every face?",
              "Detection is neural (profile and tilted faces included) and held across brief "
              "misses — and you can brush any missed spot manually, which gets blurred too."),
             ("Blur or pixelate — which is safer?",
              "Both are rendered at strengths designed to be unrecoverable; pixelate reads as "
              "more deliberate redaction in formal contexts."),
             ("Is the original kept on your servers?",
              "Uploads and results auto-delete within about 6 hours.")],
        related=["remove-object-from-video", "remove-text-from-video", "video-enhancer"]),

    "video-enhancer": dict(
        title="AI Video Enhancer — Sharpen, Denoise & Upscale Online — CleanReel",
        h1="Enhance &amp; upscale your video",
        desc="True neural enhancement: Real-ESRGAN restoration, face restore, denoise and 2× "
             "upscale for videos you own. Free preview before you pay.",
        intro="Old clips, compressed exports, low-light footage — CleanReel runs every frame "
              "through neural restoration (Real-ESRGAN, with face restore) to clean "
              "compression damage, recover detail, and optionally upscale 2×.",
        use_cases=["Old footage that needs a second life on social",
                   "Over-compressed exports and downloads of your own work",
                   "Low-light or noisy phone clips",
                   "Upscaling for larger displays or crisper reels"],
        steps=["Upload the video you own",
               "Pick 1× restore or 2× upscale",
               "Toggle denoise/deblock and sharpening",
               "Preview the result free",
               "Export the enhanced full clip (2 credits — it's GPU-heavy)"],
        faq=[("How is this different from a sharpen filter?",
              "Filters exaggerate existing pixels; neural restoration reconstructs detail — "
              "and faces get a dedicated restoration model."),
             ("Why does enhance cost 2 credits?",
              "Every frame runs through a GPU neural network — it's by far the heaviest "
              "operation we offer. Previews stay free."),
             ("Will faces look artificial?",
              "Face restore is tuned conservatively; check the free preview and dial sharpening "
              "down if you prefer a softer look.")],
        related=["remove-watermark-from-video", "blur-face-in-video", "remove-logo-from-video"]),
}

CSS = """
:root{--bg:#0b0e1a;--card:#151a33;--line:#252b4d;--ink:#eef1fb;--mut:#9aa2c0;
--acc:#7c5cff;--acc2:#b45cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.65 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:0 20px}
header{display:flex;justify-content:space-between;align-items:center;padding:20px 0}
.logo{font-weight:800;font-size:20px;color:var(--ink);text-decoration:none}
.logo span{background:linear-gradient(90deg,var(--acc),var(--acc2));-webkit-background-clip:text;background-clip:text;color:transparent}
h1{font-size:clamp(28px,4.5vw,42px);line-height:1.15;margin:26px 0 12px;font-weight:800}
h1 em{font-style:normal;background:linear-gradient(90deg,var(--acc),var(--acc2));-webkit-background-clip:text;background-clip:text;color:transparent}
h2{font-size:22px;margin:34px 0 10px}
p,li{color:var(--mut)} a{color:#b7a6ff}
.btn{background:linear-gradient(90deg,var(--acc),var(--acc2));color:#fff;border:0;border-radius:12px;
padding:13px 22px;font-weight:700;font-size:16px;cursor:pointer;display:inline-block;text-decoration:none}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 22px;margin:14px 0}
.trust{border-left:3px solid var(--acc);border-radius:0}
ol li,ul li{margin:6px 0}
details{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin:8px 0}
summary{cursor:pointer;color:var(--ink);font-weight:600}
footer{color:#6b7396;font-size:13px;padding:34px 0;border-top:1px solid var(--line);margin-top:40px}
.rel a{margin-right:14px}
"""

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<meta name="description" content="{desc}"/>
<link rel="canonical" href="{site}/{slug}"/>
<meta name="robots" content="index,follow"/>
<meta property="og:type" content="website"/>
<meta property="og:title" content="{title}"/>
<meta property="og:description" content="{desc}"/>
<meta property="og:url" content="{site}/{slug}"/>
<script type="application/ld+json">{faq_schema}</script>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <header>
    <a class="logo" href="/">✨ Clean<span>Reel</span></a>
    <a class="btn" href="/#tool">Try free →</a>
  </header>

  <h1>{h1_html}</h1>
  <p>{intro}</p>
  <p><a class="btn" href="/#tool">Open the studio — free preview</a></p>

  <div class="card trust"><p><b style="color:var(--ink)">For content you own.</b>
  Only upload videos you own or are <a href="/terms.html">licensed to edit</a>.
  Uploads auto-delete within ~6 hours, results are never re-watermarked, and you
  see a free preview before paying anything.</p></div>

  <h2>Good uses</h2>
  <ul>{use_cases}</ul>

  <h2>How it works</h2>
  <ol>{steps}</ol>

  <h2>Why CleanReel instead of cropping or blurring?</h2>
  <p>Crops lose framing and blur boxes draw the eye. CleanReel reconstructs the
  background with neural inpainting and shows a quality score, so the edit is
  invisible — and previews are free, so a result that isn't clean costs nothing.</p>

  <h2>FAQ</h2>
  {faq_html}

  <p style="margin-top:26px"><a class="btn" href="/#tool">Clean up your video →</a></p>

  <p class="rel"><b style="color:var(--ink)">Related tools:</b> {related}</p>

  <footer>
    CleanReel is for content you own or are licensed to edit. Uploads are processed
    only for your request and auto-deleted within about 6 hours.
    <br/>© CleanReel · GREATER ADELAIDE SALES PTY LTD
    <br/><a href="/terms.html">Terms</a> · <a href="/privacy.html">Privacy</a> ·
    <a href="/refunds.html">Refunds</a> · <a href="mailto:support@cleanreel.app">Contact</a> ·
    <a href="mailto:abuse@cleanreel.app">Report abuse</a>
  </footer>
</div>
</body>
</html>
"""

NICE = {s: p["h1"].replace("&amp;", "&") for s, p in PAGES.items()}


def build(slug, p):
    faq_schema = json.dumps({
        "@context": "https://schema.org", "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": q,
                        "acceptedAnswer": {"@type": "Answer", "text": a}}
                       for q, a in p["faq"]]}, ensure_ascii=False)
    faq_html = "\n  ".join(
        f"<details><summary>{html.escape(q)}</summary><p>{html.escape(a)}</p></details>"
        for q, a in p["faq"])
    related = " ".join(f'<a href="/{r}">{html.escape(NICE[r])}</a>' for r in p["related"])
    return TEMPLATE.format(
        title=html.escape(p["title"]), desc=html.escape(p["desc"]), site=SITE, slug=slug,
        faq_schema=faq_schema, css=CSS,
        h1_html=p["h1"].replace("your", "<em>your</em>", 1),
        intro=html.escape(p["intro"]),
        use_cases="".join(f"<li>{html.escape(u)}</li>" for u in p["use_cases"]),
        steps="".join(f"<li>{html.escape(s)}</li>" for s in p["steps"]),
        faq_html=faq_html, related=related)


def main():
    for slug, p in PAGES.items():
        path = os.path.join(OUT, slug + ".html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(build(slug, p))
        print("wrote", path)
    # refresh sitemap.xml with the homepage + all generated pages
    urls = [f"{SITE}/"] + [f"{SITE}/{s}" for s in PAGES]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for i, u in enumerate(urls):
        pr = "1.0" if i == 0 else "0.8"
        sm.append(f"  <url><loc>{u}</loc><changefreq>weekly</changefreq>"
                  f"<priority>{pr}</priority></url>")
    sm.append("</urlset>\n")
    with open(os.path.join(OUT, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write("\n".join(sm))
    print("wrote sitemap.xml with", len(urls), "urls")


if __name__ == "__main__":
    main()
