import React, { useCallback, useRef, useState } from 'react'
import { Check, ChevronLeft } from 'lucide-react'

/** Preview screen (README §5): draggable before/after slider over the real clips. */
export default function PreviewScreen({ preview, video, selectedLabels, onBack, onSave }) {
  const [pct, setPct] = useState(55)
  const frameRef = useRef(null)
  const dragging = useRef(false)

  const move = useCallback((clientX) => {
    const el = frameRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    setPct(Math.min(94, Math.max(6, ((clientX - r.left) / r.width) * 100)))
  }, [])

  // only brag about a confidence worth bragging about
  const confPct = preview.confidence != null ? Math.round(preview.confidence * 100) : null
  const many = selectedLabels.length > 1
  const badge = (many ? 'Both removed' : 'Removed') + (confPct >= 50 ? ` · looks ${confPct}% clean` : '')

  return (
    <>
      <div className="cr-backrow">
        <button className="cr-back" onClick={onBack} aria-label="Back"><ChevronLeft size={18} /></button>
        <div className="cr-dots"><i className="on" /><i className="on" /><i /></div>
      </div>
      <div className="cr-okbadge"><Check size={13} strokeWidth={3} /> {badge}</div>
      <h1 className="cr-h1" style={{ fontSize: 21 }}>Here’s your free preview</h1>
      <p className="cr-sub">Drag the line to compare before and after.</p>

      <div
        ref={frameRef} className="cr-compare"
        style={video ? { aspectRatio: `${video.width} / ${video.height}` } : undefined}
        onPointerDown={(e) => { dragging.current = true; e.currentTarget.setPointerCapture(e.pointerId); move(e.clientX) }}
        onPointerMove={(e) => dragging.current && move(e.clientX)}
        onPointerUp={() => { dragging.current = false }}
      >
        <video className="after" src={preview.resultUrl} autoPlay muted loop playsInline />
        {preview.beforeUrl && (
          <video
            className="before" src={preview.beforeUrl} autoPlay muted loop playsInline
            style={{ clipPath: `inset(0 ${100 - pct}% 0 0)` }}
          />
        )}
        {preview.beforeUrl && <span className="divider" style={{ left: `${pct}%` }} />}
        <span className="tag tl">BEFORE</span>
        <span className="tag tr">AFTER</span>
        {preview.beforeUrl && (
          <span className="handle" style={{ left: `${pct}%` }}>⟷</span>
        )}
      </div>

      <div className="cr-resultchips">
        {[...new Set(selectedLabels.map(l => l.replace(/ \d+$/, '')))].map(l => (
          <span key={l}><Check size={12} strokeWidth={3} /> {l} gone</span>
        ))}
        <span><Check size={12} strokeWidth={3} /> Audio kept</span>
      </div>

      <button className="cr-cta" onClick={onSave}>Save the full video</button>
      <button className="cr-cta ghost" onClick={onBack}>Missed a spot — go back</button>
    </>
  )
}
