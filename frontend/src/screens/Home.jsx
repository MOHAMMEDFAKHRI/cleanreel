import React, { useRef, useState } from 'react'
import { ArrowUp, Droplet, Eraser, Sparkles, Smartphone, EyeOff, MessageSquare } from 'lucide-react'

const JOBS = [
  { id: 'remove',  Icon: Droplet,       title: 'Take a mark off my video', desc: 'Logos, watermarks, text' },
  { id: 'erase',   Icon: Eraser,        title: 'Erase something in the scene', desc: 'People, objects, clutter' },
  { id: 'enhance', Icon: Sparkles,      title: 'Make it sharper', desc: 'Fix blur & low quality' },
  { id: 'reframe', Icon: Smartphone,    title: 'Fit it for TikTok / Reels', desc: 'Auto-crop to vertical' },
  { id: 'blur',    Icon: EyeOff,        title: 'Hide faces or plates', desc: 'Privacy blur' },
  { id: 'caption', Icon: MessageSquare, title: 'Add captions', desc: 'Auto subtitles' },
]

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
          </>
        )}
      </button>

      <div className="cr-label">Or pick a job</div>
      <div className="cr-jobs">
        {JOBS.map(({ id, Icon, title, desc }) => (
          <button key={id} className="cr-job" onClick={() => !uploading && pick(id)}>
            <Icon size={19} strokeWidth={2} />
            <span className="jt">{title}</span>
            <span className="jd">{desc}</span>
          </button>
        ))}
      </div>

      <footer className="cr-foot">
        For videos you own or are licensed to edit · free preview on everything
        <br /><a href="/studio.html" style={{ color: 'inherit' }}>Prefer the classic studio?</a>
      </footer>
    </>
  )
}
