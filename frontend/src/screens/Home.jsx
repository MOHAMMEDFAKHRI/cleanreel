import React, { useRef, useState } from 'react'
import { ArrowUp } from 'lucide-react'
import { caps, tierOf } from '../api.js'

function capLine(hint) {
  const c = caps[tierOf(hint)]
  const t = c.seconds >= 120 ? `Up to ${Math.round(c.seconds / 60)} min` : `Up to ${c.seconds}s`
  return `${t} for this job`
}

export default function Home({ uploading, error, onFile, hint, onHint, onRetry }) {
  const inputRef = useRef(null)
  const [drag, setDrag] = useState(false)

  // hint lives in App state (single source of truth) — set it BEFORE the
  // picker opens so no dispatch order can drop it
  const pick = (jobHint) => {
    onHint(jobHint || null)
    inputRef.current?.click()
  }

  return (
    <>
      <h1 className="cr-h1">What are we fixing today?</h1>
      <p className="cr-sub">Drop a clip in — we’ll spot the problems for you.</p>

      <input
        ref={inputRef} type="file" accept="video/*" hidden
        onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = '' }}
      />

      <button
        className={'cr-drop' + (drag ? ' dragover' : '')}
        onClick={() => !uploading && pick(hint)}
        onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); if (!uploading) onFile(e.dataTransfer.files?.[0]) }}
        disabled={!!uploading}
      >
        {uploading ? (
          <>
            <div className="fname">{uploading.name}</div>
            <div className="cr-progress"><i style={{ width: uploading.pct + '%' }} /></div>
            <div className="t2">{uploading.pct}% · never shared, auto-deletes</div>
          </>
        ) : error ? (
          <>
            <div className="tile err">!</div>
            <div className="t1">That didn’t work</div>
            <div className="t2 err">{error.message}</div>
            {error.options?.map(o => (
              <span key={o.job} className="cr-retry"
                    onClick={(e) => { e.stopPropagation(); onRetry(o.job) }}>
                {o.label} →
              </span>
            ))}
            <div className="t2">…or tap to try another clip</div>
          </>
        ) : (
          <>
            <div className="tile"><ArrowUp size={24} strokeWidth={2.4} /></div>
            <div className="t1">Add your video</div>
            <div className="t2">{capLine(hint)} · deleted after 6 hours</div>
          </>
        )}
      </button>

      <footer className="cr-foot">
        For videos you own or are licensed to edit · free preview on everything
        <br /><a href="/studio.html" style={{ color: 'inherit' }}>Prefer the classic studio?</a>
      </footer>
    </>
  )
}
