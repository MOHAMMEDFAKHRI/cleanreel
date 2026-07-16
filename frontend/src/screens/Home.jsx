import React, { useRef, useState } from 'react'
import { ArrowUp, Droplet, Eraser, Sparkles, Smartphone, EyeOff, MessageSquare, Clapperboard } from 'lucide-react'
import Demos from './Demos.jsx'
import { caps, tierOf } from '../api.js'

function capLine(hint) {
  const c = caps[tierOf(hint)]
  const t = c.seconds >= 120 ? `Up to ${Math.round(c.seconds / 60)} min` : `Up to ${c.seconds}s`
  return `${t} for this job`
}

const JOBS = [
  { id: 'remove',  Icon: Droplet,       title: 'Take a mark off my video', desc: 'Logos, watermarks, text' },
  { id: 'erase',   Icon: Eraser,        title: 'Erase something in the scene', desc: 'People, objects, clutter' },
  { id: 'enhance', Icon: Sparkles,      title: 'Make it sharper', desc: 'Fix blur & low quality' },
  { id: 'reframe', Icon: Smartphone,    title: 'Fit it for TikTok / Reels', desc: 'Auto-crop to vertical' },
  { id: 'blur',    Icon: EyeOff,        title: 'Hide faces or plates', desc: 'Privacy blur' },
  { id: 'caption', Icon: MessageSquare, title: 'Add captions', desc: 'Auto subtitles' },
]

export default function Home({ uploading, onFile, hint, onHint }) {
  const inputRef = useRef(null)
  const [drag, setDrag] = useState(false)
  const [demosOpen, setDemosOpen] = useState(() => window.location.hash === '#demos')

  // pull-over page semantics: #demos in the URL, browser back closes it
  React.useEffect(() => {
    const onPop = () => setDemosOpen(window.location.hash === '#demos')
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  const openDemos = () => { history.pushState(null, '', '#demos'); setDemosOpen(true) }
  const closeDemos = () => { if (window.location.hash === '#demos') history.replaceState(null, '', window.location.pathname); setDemosOpen(false) }

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
        ) : (
          <>
            <div className="tile"><ArrowUp size={24} strokeWidth={2.4} /></div>
            <div className="t1">Add your video</div>
            <div className="t2">{capLine(hint)} · deleted after 6 hours</div>
          </>
        )}
      </button>

      <div className="cr-label">Or pick a job</div>
      <button className="cr-reelcard" onClick={() => !uploading && pick('reel')}>
        <Clapperboard size={22} strokeWidth={2} />
        <span className="txt"><b>Make a Reel</b><i>Cut, caption & crop — takes up to 15 min of footage</i></span>
      </button>
      <div className="cr-jobs">
        {JOBS.map(({ id, Icon, title, desc }) => (
          <button key={id} className="cr-job" onClick={() => !uploading && pick(id)}>
            <Icon size={19} strokeWidth={2} />
            <span className="jt">{title}</span>
            <span className="jd">{desc}</span>
          </button>
        ))}
      </div>

      <button className="cr-demopull" onClick={() => openDemos()}>
        <span><b>See it work</b><i>Real before / after results from every job</i></span>
        <em>→</em>
      </button>

      {demosOpen && (
        <Demos
          onClose={() => history.back()}
          onTry={(job) => { closeDemos(); if (!uploading) pick(job) }}
        />
      )}

      <footer className="cr-foot">
        For videos you own or are licensed to edit · free preview on everything
        <br /><a href="/studio.html" style={{ color: 'inherit' }}>Prefer the classic studio?</a>
      </footer>
    </>
  )
}
