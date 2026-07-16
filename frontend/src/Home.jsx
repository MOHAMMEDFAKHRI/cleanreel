import React, { useRef, useState } from 'react'
import { ArrowUp, Droplet, Eraser, Sparkles, Smartphone, EyeOff, MessageSquare, Clapperboard } from 'lucide-react'

const JOBS = [
  { id: 'remove',  Icon: Droplet,       title: 'Take a mark off my video', desc: 'Logos, watermarks, text' },
  { id: 'erase',   Icon: Eraser,        title: 'Erase something in the scene', desc: 'People, objects, clutter' },
  { id: 'enhance', Icon: Sparkles,      title: 'Make it sharper', desc: 'Fix blur & low quality' },
  { id: 'reframe', Icon: Smartphone,    title: 'Fit it for TikTok / Reels', desc: 'Auto-crop to vertical' },
  { id: 'blur',    Icon: EyeOff,        title: 'Hide faces or plates', desc: 'Privacy blur' },
  { id: 'caption', Icon: MessageSquare, title: 'Add captions', desc: 'Auto subtitles' },
]

export default function Home({ uploading, onFile, hint }) {
  const inputRef = useRef(null)
  const jobRef = useRef(null)
  const [drag, setDrag] = useState(false)

  const pick = (jobHint) => {
    jobRef.current = jobHint || null
    inputRef.current?.click()
  }

  return (
    <>
      <h1 className="cr-h1">What are we fixing today?</h1>
      <p className="cr-sub">Drop a clip in — we’ll spot the problems for you.</p>

      <input
        ref={inputRef} type="file" accept="video/*" hidden
        onChange={(e) => { onFile(e.target.files?.[0], jobRef.current); e.target.value = '' }}
      />

      <button
        className={'cr-drop' + (drag ? ' dragover' : '')}
        onClick={() => !uploading && pick(hint || null)}
        onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); if (!uploading) onFile(e.dataTransfer.files?.[0], hint || null) }}
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
            <div className="t2">Up to 60s · deleted after 6 hours</div>
          </>
        )}
      </button>

      <div className="cr-label">Or pick a job</div>
      <button className="cr-reelcard" onClick={() => !uploading && pick('reel')}>
        <Clapperboard size={22} strokeWidth={2} />
        <span className="txt"><b>Make a Reel</b><i>Cut, caption & crop — one pass, ready to post</i></span>
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

      <footer className="cr-foot">
        For videos you own or are licensed to edit · free preview on everything
        <br /><a href="/studio.html" style={{ color: 'inherit' }}>Prefer the classic studio?</a>
      </footer>
    </>
  )
}
