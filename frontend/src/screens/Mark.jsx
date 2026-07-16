import React, { useMemo, useRef, useState } from 'react'
import { ChevronLeft, Crosshair, X } from 'lucide-react'
import { frameUrl } from '../api.js'

/**
 * Mark screen (README §3): the whole frame is tappable; detected overlays are
 * pre-selected; no brush/box/tracking controls exist. bboxes arrive in
 * canvas-still coordinates, and the still is rendered at its natural aspect,
 * so percentage positioning maps taps exactly.
 */
export default function Mark({ video, regions, selected, setSelected, onAddRegion, onMergeSpots, onBack, onPreview, showToast }) {
  const frameRef = useRef(null)
  const dragRef = useRef(null)                 // {x0,y0,moved} in content coords
  const [dragBox, setDragBox] = useState(null) // live rubber-band rect
  const [coachSeen, setCoachSeen] = useState(false)
  const [ripple, setRipple] = useState(null)   // {x,y,key} in % of frame
  const [lastAdd, setLastAdd] = useState(null) // region that drove the title change

  const W = video.width, H = video.height
  const marks = useMemo(() => regions.filter(r => r.kind === 'watermark' || r.kind === 'logo'), [regions])
  const selCount = selected.size

  const title = lastAdd
    ? (lastAdd.moving ? 'Got it — tracking that too' : 'Got it — that too')
    : marks.length > 1 ? 'We found watermarks'
    : marks.length === 1 ? (marks[0].kind === 'logo' ? 'We found a logo' : 'We found a watermark')
    : 'Tap what you want gone'
  const sub = selCount
    ? 'Tap anything else to add it, or preview the fix below.'
    : marks.length ? 'It’s already selected — tap anything else you want gone.'
    : 'We didn’t spot a mark on our own — tap the thing itself and we’ll target it.'

  const toggle = (r) => {
    const next = new Set(selected)
    if (next.has(r.id)) {
      next.delete(r.id); setLastAdd(null); showToast('Unselected')
    } else {
      next.add(r.id); setLastAdd(r); setCoachSeen(true)
      if (r.moving) showToast('Got it — it moves, so we’ll follow it')
    }
    setSelected(next)
  }

  const toContent = (e) => {
    const rect = frameRef.current.getBoundingClientRect()
    const px = (e.clientX - rect.left) / rect.width
    const py = (e.clientY - rect.top) / rect.height
    return { px, py, x: px * W, y: py * H }
  }

  const addSpot = (bx, by, bw, bh) => {
    // merge with any overlapping existing spots → ONE region, one chip
    const overlaps = regions.filter(r => r.kind === 'spot' &&
      bx < r.bbox[0] + r.bbox[2] && bx + bw > r.bbox[0] &&
      by < r.bbox[1] + r.bbox[3] && by + bh > r.bbox[1])
    let x0 = bx, y0 = by, x1 = bx + bw, y1 = by + bh
    for (const r of overlaps) {
      x0 = Math.min(x0, r.bbox[0]); y0 = Math.min(y0, r.bbox[1])
      x1 = Math.max(x1, r.bbox[0] + r.bbox[2]); y1 = Math.max(y1, r.bbox[1] + r.bbox[3])
    }
    const n = regions.filter(r => r.kind === 'spot').length - overlaps.length + 1
    const region = { id: `tap-${Date.now()}`, kind: 'spot',
                     label: `Marked spot${n > 1 ? ' ' + n : ''}`,
                     bbox: [Math.round(x0), Math.round(y0), Math.round(x1 - x0), Math.round(y1 - y0)],
                     confidence: null, moving: false, preselected: false }
    onMergeSpots(overlaps.map(r => r.id), region)
    setCoachSeen(true)
    showToast(overlaps.length ? 'Grew that spot' : 'Marked — we’ll clean that spot')
  }

  const onPointerDown = (e) => {
    if (!frameRef.current) return
    e.currentTarget.setPointerCapture?.(e.pointerId)
    const c = toContent(e)
    dragRef.current = { ...c, moved: false }
  }
  const onPointerMove = (e) => {
    const d = dragRef.current
    if (!d) return
    const c = toContent(e)
    if (Math.hypot(c.x - d.x, c.y - d.y) > 0.02 * Math.min(W, H)) d.moved = true
    if (d.moved) setDragBox({ x0: Math.min(d.x, c.x), y0: Math.min(d.y, c.y),
                              x1: Math.max(d.x, c.x), y1: Math.max(d.y, c.y) })
  }
  const onPointerUp = (e) => {
    const d = dragRef.current
    dragRef.current = null
    if (!d) return
    if (d.moved && dragBox) {
      setDragBox(null)
      const minSide = 0.05 * Math.min(W, H)
      const bw = Math.max(minSide, dragBox.x1 - dragBox.x0)
      const bh = Math.max(minSide, dragBox.y1 - dragBox.y0)
      addSpot(Math.max(0, Math.min(W - bw, dragBox.x0)),
              Math.max(0, Math.min(H - bh, dragBox.y0)), bw, bh)
      return
    }
    setDragBox(null)
    onFrameClick(e)
  }

  const onFrameClick = (e) => {
    const el = frameRef.current
    if (!el) return
    const { px, py, x, y } = toContent(e)
    // smallest region containing the point wins (nested boxes)
    const hit = regions
      .filter(r => !r.whole)   // the whole-frame pattern is chip-managed — taps pass through
      .filter(r => x >= r.bbox[0] && x <= r.bbox[0] + r.bbox[2] && y >= r.bbox[1] && y <= r.bbox[1] + r.bbox[3])
      .sort((a, b) => a.bbox[2] * a.bbox[3] - b.bbox[2] * b.bbox[3])[0]
    if (hit) { toggle(hit); return }
    // nothing detected here — the user knows better: mark the spot manually.
    // 'spot' regions flow to the render as boxes → the GPU inpaint handles them.
    const side = Math.round(0.16 * Math.min(W, H))
    addSpot(Math.max(0, Math.min(W - side, Math.round(x - side / 2))),
            Math.max(0, Math.min(H - side, Math.round(y - side / 2))), side, side)
    setRipple({ x: px * 100, y: py * 100, key: Date.now() })
  }

  const ctaLabel = selCount === 0 ? 'Select something to remove'
    : selCount === 1 ? 'Preview the fix — free'
    : selCount === 2 ? 'Preview both fixes — free'
    : 'Preview all fixes — free'

  return (
    <>
      <div className="cr-backrow">
        <button className="cr-back" onClick={onBack} aria-label="Back"><ChevronLeft size={18} /></button>
        <div className="cr-dots"><i className="on" /><i /><i /></div>
      </div>
      <h1 className="cr-h1" style={{ fontSize: 22 }}>{title}</h1>
      <p className="cr-sub">{sub}</p>

      <div className="cr-markframe">
        <div
          ref={frameRef} className="cr-markinner"
          onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
          style={{ touchAction: 'none' }}
        >
        <img src={frameUrl(video.fileId)} alt="" draggable={false} />
        {regions.map(r => {
          const on = selected.has(r.id)
          if (r.whole) {
            return on ? (
              <div key={r.id} className="cr-wholeframe">
                <span className="cr-dettag" style={{ top: 8, bottom: 'auto' }}>✓ pattern selected — everywhere</span>
              </div>
            ) : null
          }
          const [x, y, w, h] = r.bbox
          const style = {
            left: `${(x / W) * 100}%`, top: `${(y / H) * 100}%`,
            width: `${(w / W) * 100}%`, height: `${(h / H) * 100}%`,
          }
          const cls = 'cr-region' + (on ? (r.preselected ? ' det' : ' sel') : '')
          return (
            <div key={r.id} className={cls} style={style}>
              {on && r.preselected && <span className="cr-dettag">✓ selected for you</span>}
              {on && !r.preselected && r.moving && (
                <span className="cr-movetag"><Crosshair size={10} /> moving — we’ll follow it</span>
              )}
            </div>
          )
        })}
        {dragBox && (
          <div className="cr-region sel" style={{
            left: `${(dragBox.x0 / W) * 100}%`, top: `${(dragBox.y0 / H) * 100}%`,
            width: `${((dragBox.x1 - dragBox.x0) / W) * 100}%`, height: `${((dragBox.y1 - dragBox.y0) / H) * 100}%`,
          }} />
        )}
        {ripple && <span key={ripple.key} className="cr-ripple" style={{ left: `${ripple.x}%`, top: `${ripple.y}%` }} />}
        {!coachSeen && regions.length > 0 && <span className="cr-coach">tap anything you want gone</span>}
        </div>
      </div>

      <div className="cr-chips">
        {regions.filter(r => selected.has(r.id)).map(r => (
          <button key={r.id} className="cr-chip" onClick={() => toggle(r)}>
            {r.label} <X size={12} strokeWidth={2.6} />
          </button>
        ))}
        <span className="cr-chip ghost">+ tap to add more</span>
      </div>

      <button className="cr-cta" disabled={selCount === 0} onClick={() => onPreview()}>
        {ctaLabel}
      </button>
      <p className="cr-hint">
        Tap anything you want gone — or drag a box over it. Tapped wrong? Tap it again.
      </p>
      <p className="cr-hint faint">
        Previewing confirms this is a video you own or are licensed to edit.
      </p>
    </>
  )
}
