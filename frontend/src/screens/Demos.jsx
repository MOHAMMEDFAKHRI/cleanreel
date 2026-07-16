import React, { useRef, useState } from 'react'
import { ChevronLeft } from 'lucide-react'

const DEMOS = [
  { job: 'remove',  title: 'Take a mark off', line: 'Auto-detected, neurally rebuilt', img: 'remove',
    altA: 'Frame after watermark removal', altB: 'Frame before, with tiled watermark' },
  { job: 'erase',   title: 'Erase something', line: 'Objects, stickers, clutter — gone', img: 'erase',
    altA: 'Frame after — the balloon is gone, clean sky remains', altB: 'Frame before, two hot-air balloons' },
  { job: 'enhance', title: 'Make it sharper', line: 'Neural restore + 2× upscale', img: 'enhance',
    altA: 'Frame after neural enhancement', altB: 'Frame before, compressed and soft' },
  { job: 'reframe', title: 'Fit it for TikTok', line: 'Subject kept centered in 9:16', img: 'reframe',
    altA: 'Clip reframed to a subject-centered vertical crop', altB: 'Original landscape clip' },
  { job: 'blur',    title: 'Hide faces', line: 'Tracked steady, even in motion', img: 'blur',
    altA: 'Street scene after all faces were blurred', altB: 'Street scene before, faces visible' },
  { job: 'caption', title: 'Add captions', line: 'Speech → styled captions + free .srt', img: 'captions',
    altA: 'Frame after, spoken words burned in as captions', altB: 'Frame before, no captions' },
]

function DemoCard({ d, onTry }) {
  const [pct, setPct] = useState(55)
  const ref = useRef(null)
  const drag = useRef(false)
  const move = (clientX) => {
    const r = ref.current?.getBoundingClientRect()
    if (r) setPct(Math.min(94, Math.max(6, ((clientX - r.left) / r.width) * 100)))
  }
  return (
    <div className="cr-demo">
      <b>{d.title}</b>
      <div
        ref={ref} className="ba"
        onPointerDown={(e) => { drag.current = true; e.currentTarget.setPointerCapture(e.pointerId); move(e.clientX) }}
        onPointerMove={(e) => drag.current && move(e.clientX)}
        onPointerUp={() => { drag.current = false }}
      >
        <img src={`/demos/ba/${d.img}-after.jpg`} alt={d.altA} loading="lazy" draggable={false} />
        <img
          src={`/demos/ba/${d.img}-before.jpg`} alt={d.altB} loading="lazy" draggable={false}
          className="before" style={{ clipPath: `inset(0 ${100 - pct}% 0 0)` }}
        />
        <i className="line" style={{ left: `${pct}%` }} />
        <s className="tag tl">Before</s><s className="tag tr">After</s>
      </div>
      <p>{d.line} · <button onClick={onTry}>try it →</button></p>
    </div>
  )
}

/**
 * "See it work" — its own pull-over page (slides over home). Deep-linkable via
 * #demos; browser back closes it.
 */
export default function Demos({ onTry, onClose }) {
  return (
    <div className="cr-demopage">
      <div className="inner">
        <div className="cr-backrow">
          <button className="cr-back" onClick={onClose} aria-label="Back"><ChevronLeft size={18} /></button>
          <div style={{ flex: 1 }} />
        </div>
        <h1 className="cr-h1" style={{ fontSize: 24 }}>See it work</h1>
        <p className="cr-sub">Real frames, real results — drag any slider to compare before and after.</p>
        <div className="cr-demogrid page">
          {DEMOS.map(d => <DemoCard key={d.job} d={d} onTry={() => onTry(d.job)} />)}
        </div>
        <button className="cr-cta" onClick={onClose} style={{ marginTop: 20 }}>
          Your clip next — fix it free
        </button>
      </div>
    </div>
  )
}
