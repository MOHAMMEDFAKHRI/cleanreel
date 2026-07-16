import React, { useRef, useState } from 'react'

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

/** "See it work" — real frames, drag to compare (ported from the classic homepage). */
export default function Demos({ onTry }) {
  return (
    <>
      <div className="cr-label" style={{ marginTop: 26 }}>See it work</div>
      <p className="cr-sub" style={{ marginTop: 0, marginBottom: 10, fontSize: 12.5 }}>
        Real frames, real results — drag any slider to compare.
      </p>
      <div className="cr-demogrid">
        {DEMOS.map(d => <DemoCard key={d.job} d={d} onTry={() => onTry(d.job)} />)}
      </div>
    </>
  )
}
