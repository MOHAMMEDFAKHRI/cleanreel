# CleanReel — Adaptive Watermark Remover (MVP)

Remove watermarks/overlays from a short video you own: upload → mark the area
(or auto-detect) → preview → export clean. Faces and detail stay sharp; audio is kept.

See **PRODUCT_BRIEF.md** for the full product + technical plan.

## What's here
| File | Purpose |
|---|---|
| `watermark_remover.py` | The adaptive engine (CLI + importable). Auto-detects tiled vs corner-logo, or takes a marked region; reverse-blend + neural inpaint, classical fallback; video + image. |
| `app.py` | Friendly Gradio web UI (upload → mark → preview → export). |
| `requirements.txt` | Dependencies. |
| `PRODUCT_BRIEF.md` | Vision, MVP scope, hybrid-compute architecture, roadmap, pricing, legal. |

## Run the web app (locally)
```bash
pip install -r requirements.txt
python app.py
# open the printed http://127.0.0.1:7860  (set WR_SHARE=1 for a public link)
```
First neural run downloads the LaMa model (~200 MB). No NVIDIA GPU? It still works
on CPU (slower), or omit torch/simple-lama to use the classical fallback.

## Use the engine directly (CLI)
```bash
# auto-detect & clean
python watermark_remover.py in.mp4 out.mp4 --auto

# clean a painted mask (white = remove) or a fixed box
python watermark_remover.py in.mp4 out.mp4 --mask mask.png
python watermark_remover.py in.mp4 out.mp4 --boxes 560,40,150,90

# fast 4-second preview, and HD upscale
python watermark_remover.py in.mp4 prev.mp4 --auto --preview 4
python watermark_remover.py in.mp4 out.mp4 --auto --upscale 1080x1920

# an image (auto-detects a tiled mark, or pass --mask/--boxes)
python watermark_remover.py in.jpg out.png --auto

# MOVING / animated mark: mark it once with a box, the engine tracks & removes it
python watermark_remover.py in.mp4 out.mp4 --track --boxes 194,292,120,60
#   (--ref 1.5  picks the second at which your box is drawn; default = middle frame)

# BATCH a whole folder (or glob) -> writes *_clean files into an output folder
python watermark_remover.py inputs_folder outputs_folder --batch --auto
```
`--engine auto|lama|classical`, `--no-sharpen`, `--no-protect`, `--preview N`, `--upscale WxH` also available.

### v2 capabilities
- **Tiled / corner-logo / user-region** — auto-adapts (as above).
- **Moving / animated marks** — `--track` follows the mark across frames (template tracking) and removes it.
- **Images** — `--auto` now detects tiled marks in a single image too.
- **Batch** — `--batch` processes a folder/glob.
- **Faster + sharper** — inpainting now runs only on the mark's bounding box (ROI) for localized marks, not the whole frame.

## How it adapts
1. **Detect** — finds a tiling lattice (→ *tiled* mark) or a compact static blob (→ *corner logo*); you can also paint the region yourself.
2. **Reverse-blend** — for semi-transparent/tiled marks, subtracts the whole watermark layer so the real footage is recovered, not guessed.
3. **Neural inpaint (LaMa)** — cleans the residue, **gated to flat areas** so faces/hands/clothes detail are never touched.
4. **Finish** — optional HD upscale + light sharpen, original audio muxed back.

## Next steps toward the website (from the brief)
- Wrap the engine in a FastAPI job API; previews on CPU (free), full exports on a GPU queue (credits).
- Next.js front end; Stripe credits; S3/R2 storage with auto-delete; ownership attestation + ToS.
- Deploy the demo first (e.g. HuggingFace Spaces / Render) to validate, then scale infra on demand.

## Please use responsibly
For content **you own or are licensed to edit** (your own free-tier exports, your old logo,
stray text/timecode). Not for stripping others' ownership/copyright marks.
