import React, { useMemo, useRef, useState } from 'react'
import { ChevronLeft, Crosshair, X } from 'lucide-react'
import { frameUrl } from '../api.js'

/**
 * Mark screen (README §3): the whole frame is tappable; detected overlays are
 * pre-selected; no brush/box/tracking controls exist. bboxes arrive in
 * canvas-still coordinates, and the still is rendered at its natural aspect,
 * so percentage positioning maps taps exactly.
 */
export default function Mark({ video, regions, selected, setSelected, onBack, onPreview, showToast }) {
  const frameRef = useRef(null)
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

  const onFrameClick = (e) => {
    const el = frameRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const px = (e.clientX - rect.left) / rect.width
    const py = (e.clientY - rect.top) / rect.height
    const x = px * W, y = py * H
    // smallest region containing the point wins (nested boxes)
    const hit = regions
      .filter(r => x >= r.bbox[0] && x <= r.bbox[0] + r.bbox[2] && y >= r.bbox[1] && y <= r.bbox[1] + r.bbox[3])
      .sort((a, b) => a.bbox[2] * a.bbox[3] - b.bbox[2] * b.bbox[3])[0]
    if (hit) { toggle(hit); return }
    setRipple({ x: px * 100, y: py * 100, key: Date.now() })
    showToast('Nothing removable there — tap the thing itself')
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

      <div
        ref={frameRef} className="cr-markframe"
        style={{ aspectRatio: `${W} / ${H}` }}
        onClick={onFrameClick}
      >
        <img src={frameUrl(video.fileId)} alt="" draggable={false} />
        {regions.map(r => {
          const on = selected.has(r.id)
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
        {ripple && <span key={ripple.key} className="cr-ripple" style={{ left: `${ripple.x}%`, top: `${ripple.y}%` }} />}
        {!coachSeen && regions.length > 0 && <span className="cr-coach">tap anything you want gone</span>}
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
        Only selected things are touched. Faces stay sharp. Tapped wrong? Tap it again.
      </p>
      <p className="cr-hint faint">
        Previewing confirms this is a video you own or are licensed to edit.
      </p>
    </>
  )
}
