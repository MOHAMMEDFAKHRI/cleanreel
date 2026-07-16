# CleanReel redesign frontend (CLE-44)

Vite + React SPA implementing the "2a" guided-flow redesign
(spec: `ARVYNN/Cleanreel app redesign.zip` → design_handoff_cleanreel_redesign/).

- Ships at **cleanreel.app/new/** while the old UI holds the fort at `/`.
- Built output goes to `web/new/` and **is committed** (Netlify has no build step).
- Talks to the same Render API; `window.API_BASE` comes from the site-root `/config.js`.

## Workflow
```
cd frontend
npm install        # once
npm run build      # writes web/new/  → then push as usual (fix_and_push.bat)
npm run dev        # local dev server (uses fallback API base)
```

## Phases (from CLE-44)
- (a) tokens + home/upload/analyze shell  ← this
- (b) mark screen: tap-to-select on remove/erase (needs region-metadata API)
- (c) per-job decision screens  (d) reel wizard + fine-tune  (e) dark-mode QA

Design tokens live in `src/tokens.css` (light + dark via `[data-theme]`).
