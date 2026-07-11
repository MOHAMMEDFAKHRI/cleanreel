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
        task="remove",
        demo="demo-remove-watermark",
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
        task="remove",
        demo="demo-remove-watermark",
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
        task="remove",
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
        task="erase",
        demo="demo-erase-object",
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
        task="blur",
        demo="demo-blur-faces",
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
        task="enhance",
        demo="demo-video-enhancer",
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

    "remove-subtitles-from-video": dict(
        task="remove",
        title="Remove Hard-Coded Subtitles from Video Online — CleanReel",
        h1="Remove burned-in subtitles from your video",
        desc="Strip hard-coded subtitles from videos you own so you can re-caption, translate, "
             "or repost clean. AI inpainting rebuilds the background. Free preview.",
        intro="Subtitles burned into the pixels can't be switched off — but they can be removed. "
              "CleanReel erases hard-coded subs from footage you own and reconstructs what's "
              "behind them, so you can re-caption in a new language or style.",
        use_cases=["Your own clips subtitled in the wrong language",
                   "Old caption styles you want to refresh",
                   "Re-captioning content for a new market",
                   "Cleaning subs off footage before a re-edit"],
        steps=["Upload the subtitled clip you own (up to 60s / 200 MB)",
               "Brush across the subtitle band — or auto-detect it",
               "Preview the clean result free",
               "Optional: switch to Captions mode to re-caption it automatically",
               "Export in full quality with original audio"],
        faq=[("Can I remove subtitles from a show or movie?",
              "No — CleanReel only accepts content you own or are licensed to edit, like your "
              "own productions or licensed client work."),
             ("What goes in place of the subtitles?",
              "Neural inpainting reconstructs the background behind the text — no blur bar, "
              "no crop, and a quality score shows how clean the result is."),
             ("Can CleanReel add new captions afterwards?",
              "Yes — the Captions mode transcribes the speech with AI and burns in fresh, "
              "styled captions, plus a free .srt download.")],
        related=["remove-text-from-video", "add-captions-to-video", "auto-subtitle-generator"]),

    "remove-person-from-video": dict(
        task="erase",
        demo="demo-erase-object",
        title="Remove a Person from Video Online (Videos You Own) — CleanReel",
        h1="Remove a person from your video",
        desc="Erase a photobomber or bystander from footage you own — brush, track, and let "
             "neural inpainting rebuild the scene. Free preview first.",
        intro="A stranger wandered through your shot, or someone asked to be edited out — "
              "brush over them and CleanReel erases them across the clip, tracking their "
              "movement and rebuilding the background behind them.",
        use_cases=["Photobombers in travel and event footage",
                   "Bystanders who asked not to appear",
                   "Crew members caught in the frame",
                   "Ex-members of a lineup in your own promo shots"],
        steps=["Upload the video you own",
               "Brush over the person to remove",
               "Tick tracking so the mask follows them",
               "Preview free and refine the mask",
               "Export the cleaned full clip"],
        faq=[("Is it legal to remove a person from a video?",
              "For your own footage it's generally your call — though privacy and publicity "
              "laws vary, and CleanReel only accepts content you own or are licensed to edit."),
             ("The person walks across the whole frame — will it work?",
              "Tracking follows the marked region, and temporal inpainting keeps the fill "
              "consistent between frames. Fast, erratic movement is harder — the free preview "
              "shows you before you pay."),
             ("What about just blurring them instead?",
              "Often the better choice — Privacy blur finds and blurs faces automatically, "
              "and reads as deliberate redaction rather than an edit.")],
        related=["remove-object-from-video", "blur-face-in-video", "remove-text-from-video"]),

    "blur-license-plate-in-video": dict(
        task="blur",
        demo="demo-blur-faces",
        title="Blur License Plates in Video Online (Dashcam & Street) — CleanReel",
        h1="Blur license plates in your video",
        desc="Auto-detect and blur license plates across dashcam, driving, and street footage "
             "you own. Tracked and steady, no flicker. Free preview.",
        intro="Posting dashcam or driving footage? CleanReel detects license plates, tracks "
              "them through the clip, and blurs or pixelates them steadily — so plates stay "
              "unreadable in every frame, not just the ones you checked.",
        use_cases=["Dashcam clips before posting or sharing",
                   "Incident footage sent to insurers or platforms",
                   "Street and car-spotting content",
                   "Real-estate and drone footage with parked cars"],
        steps=["Upload your clip",
               "Tick license plates (and faces, if people appear)",
               "Choose blur or pixelate and set the strength",
               "Preview free — detection is tracked and smoothed",
               "Export the redacted full video"],
        faq=[("Does it catch plates at an angle or in motion?",
              "Detection runs on every frame and is held across brief misses; anything it "
              "skips you can brush manually and it's blurred too."),
             ("Is a blurred plate really unreadable?",
              "Strengths are tuned to be unrecoverable — pixelate at high strength is the "
              "most conservative choice for compliance contexts."),
             ("Do you keep my footage?",
              "Uploads and results auto-delete within about 6 hours.")],
        related=["blur-face-in-video", "remove-object-from-video", "remove-person-from-video"]),

    "video-upscaler": dict(
        task="enhance",
        demo="demo-video-enhancer",
        title="AI Video Upscaler — 2× Resolution Online — CleanReel",
        h1="Upscale your video with AI",
        desc="Neurally upscale videos you own to 2× resolution — Real-ESRGAN restoration "
             "recovers detail instead of stretching pixels. Free preview.",
        intro="Stretching a small video just makes it blurry at a bigger size. CleanReel's "
              "upscaler runs every frame through Real-ESRGAN neural restoration, rebuilding "
              "texture and edges while doubling the resolution — faces get their own "
              "restoration model.",
        use_cases=["Old 480p/720p clips headed for modern feeds",
                   "Zoomed or cropped footage that lost resolution",
                   "Compressed downloads of your own published work",
                   "Phone clips destined for larger screens"],
        steps=["Upload the video you own",
               "Pick 2× upscale (or 1× restore-only)",
               "Toggle denoise/deblock for compressed sources",
               "Preview the result free",
               "Export the upscaled full clip (2 credits — it's GPU-heavy)"],
        faq=[("How is this different from resizing in an editor?",
              "Editors interpolate pixels — soft results at best. Neural upscaling "
              "reconstructs plausible detail learned from millions of videos."),
             ("What's the output size limit?",
              "Output is capped to a server-safe long side, so extremely large sources are "
              "restored at high quality rather than doubled further."),
             ("Why 2 credits?",
              "Every frame runs through a GPU network — the heaviest job we offer. The "
              "preview is still free.")],
        related=["video-enhancer", "remove-watermark-from-video", "resize-video-for-tiktok"]),

    "resize-video-for-tiktok": dict(
        task="reframe",
        demo="demo-reframe-vertical",
        title="Resize Video for TikTok — 9:16 Vertical with AI Tracking — CleanReel",
        h1="Resize your video for TikTok",
        desc="Turn landscape footage you own into 9:16 vertical for TikTok — an AI virtual "
             "camera keeps the subject centered. Free preview.",
        intro="A straight center-crop to 9:16 loses the action the moment your subject moves. "
              "CleanReel's smart crop tracks faces and motion with a smooth virtual camera, "
              "so your landscape clip becomes a vertical that stays on the subject.",
        use_cases=["Repurposing YouTube or event footage for TikTok",
                   "Turning interviews and talking heads vertical",
                   "Product and demo clips shot landscape",
                   "Sports and action moments where the subject moves"],
        steps=["Upload the landscape video you own",
               "Pick 9:16 vertical",
               "Smart crop tracks the subject — or tap the frame to pin the focus",
               "Preview the reframe free",
               "Export — then add captions in one more pass if you like"],
        faq=[("What if the whole frame matters?",
              "Choose Fit + blurred bars instead of smart crop — the full frame stays "
              "visible over cinematic blurred padding."),
             ("Can I control what the crop follows?",
              "Yes — tap the frame to pin the crop center, or leave it on auto face & "
              "motion tracking."),
             ("Does TikTok need captions too?",
              "They help a lot — CleanReel's Captions mode transcribes speech and burns in "
              "styled captions, with a free .srt download.")],
        related=["resize-video-for-instagram-reels", "add-captions-to-video", "video-upscaler"]),

    "resize-video-for-instagram-reels": dict(
        task="reframe",
        demo="demo-reframe-vertical",
        title="Resize Video for Instagram Reels — 9:16 & 4:5 with AI — CleanReel",
        h1="Resize your video for Instagram Reels",
        desc="Convert landscape footage you own to 9:16 Reels or 4:5 feed video — AI subject "
             "tracking keeps the framing right. Free preview.",
        intro="Reels wants 9:16, feed prefers 4:5, your footage is 16:9. CleanReel reframes "
              "to either shape with a smooth AI virtual camera that follows faces and motion, "
              "or fits the full frame over blurred bars.",
        use_cases=["Repurposing long-form video into Reels",
                   "4:5 feed versions of landscape promos",
                   "Client content deliverables in multiple aspect ratios",
                   "Square 1:1 versions for profile grids"],
        steps=["Upload the video you own",
               "Pick 9:16 (Reels), 4:5 (feed) or 1:1 (square)",
               "Smart crop follows the subject — or pin the focus with a tap",
               "Preview free",
               "Export, and caption it with the Captions mode if it has speech"],
        faq=[("Which ratio should I pick for Instagram?",
              "9:16 for Reels and Stories, 4:5 for feed posts, 1:1 for grids — you can "
              "export each from the same upload."),
             ("Will text or logos near the edges get cut?",
              "Smart crop centers on subjects, so edge graphics can crop out — use Fit + "
              "blurred bars when edge content must survive."),
             ("Is the export re-compressed?",
              "It's re-encoded at high quality (CRF 16) with your audio copied through.")],
        related=["resize-video-for-tiktok", "add-captions-to-video", "video-enhancer"]),

    "add-captions-to-video": dict(
        task="captions",
        title="Add Captions to Video Online — AI Transcribed & Burned In — CleanReel",
        h1="Add captions to your video",
        desc="AI transcribes the speech in your video and burns in clean, readable captions — "
             "plus a free .srt download. Language auto-detected. Free preview.",
        intro="Most viewers watch with the sound off — captions keep them watching. CleanReel "
              "transcribes your clip's speech with AI (language auto-detected), burns in "
              "bold, readable captions sized to your video, and gives you the transcript as "
              "a free .srt file.",
        use_cases=["Talking-head clips for TikTok, Reels and Shorts",
                   "Tutorials and how-tos watched on mute",
                   "Accessibility for deaf and hard-of-hearing viewers",
                   "Podcast clips and interview snippets"],
        steps=["Upload a clip with clear speech (up to 60s / 200 MB)",
               "Pick Captions mode — no marking needed",
               "Preview the burned-in captions free",
               "Download the .srt free, even from the preview",
               "Export the captioned full video (1 credit)"],
        faq=[("What languages does it support?",
              "The language is auto-detected — the AI model supports dozens of languages, "
              "and the free preview shows you the accuracy before you pay."),
             ("Can I get just the subtitle file?",
              "Yes — the .srt transcript is free on every captions job, including free "
              "previews. Only the burned-in export costs a credit."),
             ("What do the captions look like?",
              "Bold white text with a dark outline, bottom-centered and sized to your "
              "video — the style that survives every feed's compression.")],
        related=["auto-subtitle-generator", "burn-subtitles-into-video", "resize-video-for-tiktok"]),

    "auto-subtitle-generator": dict(
        task="captions",
        title="Auto Subtitle Generator — Free SRT from Video Online — CleanReel",
        h1="Generate subtitles for your video automatically",
        desc="Upload a video, get AI-generated subtitles: a free .srt file plus optional "
             "burned-in captions. Language auto-detected, timestamps included.",
        intro="Typing subtitles by hand is the worst job in video. CleanReel listens to your "
              "clip, generates accurately timestamped subtitles with AI speech recognition, "
              "and hands you the .srt for free — burn them in only if you want to.",
        use_cases=["SRT files for YouTube, Vimeo or LinkedIn uploads",
                   "Transcripts for editing or translation workflows",
                   "Subtitles for course and training videos",
                   "Quick captions for social clips"],
        steps=["Upload your video (up to 60s / 200 MB)",
               "Pick Captions mode",
               "Run a free preview — speech is transcribed with AI",
               "Download the .srt file — free",
               "Optionally export with the captions burned in (1 credit)"],
        faq=[("Is the SRT really free?",
              "Yes — every captions job includes the .srt download at no cost, even on free "
              "previews. Credits are only for the burned-in video export."),
             ("How accurate is the transcription?",
              "It uses the Whisper family of speech models — strong on clear speech, and the "
              "free preview lets you check your clip before paying anything."),
             ("Can I edit the subtitles?",
              "The .srt is a plain-text standard — open it in any editor or subtitle tool, "
              "tweak the text, and upload it wherever you publish.")],
        related=["add-captions-to-video", "free-srt-generator", "burn-subtitles-into-video"]),

    "burn-subtitles-into-video": dict(
        task="captions",
        title="Burn Subtitles into Video Online (Hardcode Captions) — CleanReel",
        h1="Burn subtitles into your video",
        desc="Hardcode captions into your video so they show everywhere — AI transcribes the "
             "speech and renders clean styled subtitles into the pixels. Free preview.",
        intro="Platform captions vanish when a clip is downloaded, embedded, or reposted — "
              "burned-in subtitles are part of the pixels and show everywhere. CleanReel "
              "transcribes your speech with AI and hardcodes clean, styled captions in one "
              "pass.",
        use_cases=["Clips reposted across platforms that drop caption tracks",
                   "Ads and promos where captions must always show",
                   "Event screens and displays with no subtitle support",
                   "Client deliverables that need captions baked in"],
        steps=["Upload your clip with speech",
               "Pick Captions mode — transcription is automatic",
               "Preview the burned-in result free",
               "Grab the free .srt too if you need the text version",
               "Export the hardcoded full video (1 credit)"],
        faq=[("Burned-in vs. an .srt file — which do I need?",
              "An .srt is a separate file platforms can show or hide; burned-in captions are "
              "rendered into the video itself and can never be turned off. CleanReel gives "
              "you both."),
             ("Will the captions survive compression?",
              "The style — bold white with a dark outline — is chosen to stay readable "
              "through every platform's re-encode."),
             ("Can I burn in my own edited subtitles?",
              "Not yet — today the captions come from the AI transcription of your clip's "
              "audio. Edited-SRT upload is on the roadmap.")],
        related=["add-captions-to-video", "auto-subtitle-generator", "remove-subtitles-from-video"]),

    "free-srt-generator": dict(
        task="captions",
        title="Free SRT Generator — AI Subtitles from Video — CleanReel",
        h1="Generate a free SRT file from your video",
        desc="Get an accurately timestamped .srt subtitle file from any video you own — free, "
             "AI-transcribed, language auto-detected. No credit needed.",
        intro="Need the subtitle file, not a new video? Upload your clip, run a free preview, "
              "and download the AI-generated .srt — accurately timestamped, language "
              "auto-detected, and genuinely free: the transcript never costs a credit.",
        use_cases=["SRT sidecar files for YouTube and Vimeo uploads",
                   "Transcripts for blog posts and show notes",
                   "Translation source files for multilingual subs",
                   "Caption files for platforms you publish on"],
        steps=["Upload a video with speech (up to 60s / 200 MB)",
               "Pick Captions mode and run a free preview",
               "AI transcribes the speech with timestamps",
               "Download the .srt — free, no credit used",
               "Optionally export the video with captions burned in (1 credit)"],
        faq=[("What's the catch — why is it free?",
              "The transcript is free because many people then want the burned-in version, "
              "which is the paid export. If you only need the .srt, it's yours."),
             ("Does it handle accents and background noise?",
              "The AI speech model is robust to accents; heavy background noise reduces "
              "accuracy — the free preview shows you exactly what you'll get."),
             ("What's an SRT file exactly?",
              "A plain-text subtitle standard: numbered lines with start/end timestamps. "
              "Nearly every video platform and player accepts it.")],
        related=["auto-subtitle-generator", "add-captions-to-video", "burn-subtitles-into-video"]),
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
    <a class="btn" href="/#tool={task}">Try free →</a>
  </header>

  <h1>{h1_html}</h1>
  <p>{intro}</p>
  <p><a class="btn" href="/#tool={task}">Open the studio — free preview</a></p>
{demo_html}

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

  <p style="margin-top:26px"><a class="btn" href="/#tool={task}">Clean up your video →</a></p>

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

# Demo clip metadata (assets in web/demos/: {slug}.mp4 + .webm + -poster.jpg).
# blur-faces and video-enhancer text mirrors the hand-made live pages verbatim.
DEMOS = {
    "demo-remove-watermark": dict(
        name="How to remove a watermark from a video",
        desc="Upload a video you own, auto-detect the watermark, preview free, and "
             "export the cleaned clip with CleanReel.",
        aria="removing a watermark from a video with CleanReel",
        caption="Real capture: auto-detect finds the mark and neural inpainting "
                "rebuilds the background — sweep the before / after.",
        duration="PT24S"),
    "demo-erase-object": dict(
        name="How to erase an object from a video",
        desc="Brush over an object in a video you own, preview the AI removal free, "
             "and export the cleaned clip with CleanReel.",
        aria="erasing an object from a video with CleanReel",
        caption="Real capture: brush the object, tracking follows it through the "
                "shot, and the fill stays clean — sweep the before / after.",
        duration="PT27S"),
    "demo-blur-faces": dict(
        name="How to blur faces in a video",
        desc="Upload a video you own, auto-detect faces and plates, preview free, "
             "and export the blurred clip with CleanReel.",
        aria="auto-detecting and blurring faces in a video with CleanReel",
        caption="Real capture: detection finds every face, then the tracked blur "
                "holds steady through the before / after.",
        duration="PT27S"),
    "demo-video-enhancer": dict(
        name="How to enhance and upscale a video",
        desc="Upload a soft or compressed video you own, run neural restoration "
             "with 2x upscale, preview free, and export with CleanReel.",
        aria="enhancing and upscaling a compressed video with CleanReel",
        caption="Real capture: a 360p compressed clip through neural restore + 2× "
                "upscale — sweep the before / after.",
        duration="PT26S"),
    "demo-reframe-vertical": dict(
        name="How to reframe a horizontal video to vertical",
        desc="Upload a video you own, let the subject tracker follow the action, "
             "and export a 9:16 vertical crop with CleanReel.",
        aria="reframing a horizontal video to vertical with CleanReel",
        caption="Real capture: the subject tracker keeps the crop centered as the "
                "clip reframes to 9:16.",
        duration="PT26S"),
}


def demo_block(demo):
    """The gradient-border demo card + VideoObject schema (matches the style of
    the hand-made live pages). Empty string when a page has no demo."""
    if not demo:
        return ""
    d = DEMOS[demo]
    schema = json.dumps({
        "@context": "https://schema.org", "@type": "VideoObject",
        "name": d["name"], "description": d["desc"],
        "thumbnailUrl": f"{SITE}/demos/{demo}-poster.jpg",
        "contentUrl": f"{SITE}/demos/{demo}.mp4",
        # Full ISO-8601 with timezone — a bare date made Search Console warn
        # about ambiguous uploadDate values (CLE-27).
        "uploadDate": "2026-07-09T12:00:00+09:30",
        "duration": d["duration"]}, ensure_ascii=False)
    return (
        f'<script type="application/ld+json">{schema}</script>\n'
        f'  <div class="card" style="padding:10px;border:2px solid transparent;border-radius:16px;'
        f'background:linear-gradient(var(--card),var(--card)) padding-box,'
        f'linear-gradient(90deg,var(--acc),var(--acc2)) border-box;'
        f'box-shadow:0 6px 28px rgba(124,92,255,.28)">\n'
        f'    <video autoplay muted loop playsinline\n'
        f'           poster="/demos/{demo}-poster.jpg"\n'
        f'           style="width:100%;height:auto;display:block;border-radius:10px"\n'
        f'           aria-label="Demo: {html.escape(d["aria"])}">\n'
        f'      <source src="/demos/{demo}.webm" type="video/webm"/>\n'
        f'      <source src="/demos/{demo}.mp4" type="video/mp4"/>\n'
        f'    </video>\n'
        f'    <p style="margin:10px 6px 2px;font-size:13px">{html.escape(d["caption"])}</p>\n'
        f'  </div>')


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
        task=p["task"], demo_html=demo_block(p.get("demo")),
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
