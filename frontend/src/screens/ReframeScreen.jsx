import React, { useRef } from 'react'
import { Crosshair } from 'lucide-react'
import JobShell from './JobShell.jsx'
import { frameUrl } from '../api.js'

const DESTS = [
  { id: '9:16', label: 'TikTok / Reels', note: '9:16', w: 9, h: 16 },
  { id: '1:1',  label: 'Square',         note: '1:1',  w: 1, h: 1 },
  { id: '4:5',  label: 'Portrait',       note: '4:5',  w: 4, h: 5 },
]

/**
 * 3b Reframe — "Where is this going?": destination cards, live crop-region
 * overlay with dimmed surround, smart-crop vs blurred-bars, tap-to-pin focus.
 * opts = { ratio, fit, focus:[x,y]|null }
 */
export default function ReframeScreen({ video, opts, setOpts, onBack, onPreview }) {
  const frameRef = useRef(null)
  const set = (k, v) => setOpts({ ...opts, [k]: v })

  // crop-region geometry (as % of the source frame)
  const [rw, rh] = opts.ratio.split(':').map(Number)
  const srcAR = video.width / video.height, dstAR = rw / rh
  const cropW = dstAR <= srcAR ? (dstAR / srcAR) * 100 : 100
  const cropH = dstAR <= srcAR ? 100 : (srcAR / dstAR) * 100
  const fx = opts.focus ? opts.focus[0] : 0.5
  const fy = opts.focus ? opts.focus[1] : 0.5
  const left = Math.min(100 - cropW, Math.max(0, fx * 100 - cropW / 2))
  const top = Math.min(100 - cropH, Math.max(0, fy * 100 - cropH / 2))

  const pin = (e) => {
    const r = frameRef.current.getBoundingClientRect()
    set('focus', [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height])
  }

  return (
    <JobShell
      title="Where is this going?"
      sub="We follow the subject automatically — you just pick the shape."
      onBack={onBack} cta="Preview the reframe — free" onCta={onPreview}
      hint="Tap the frame to pin the focus somewhere else."
    >
      <div className="cr-dests">
        {DESTS.map(d => (
          <button key={d.id} className={'cr-dest' + (opts.ratio === d.id ? ' on' : '')} onClick={() => set('ratio', d.id)}>
            <i style={{ aspectRatio: `${d.w} / ${d.h}` }} />
            <b>{d.label}</b><span>{d.note}</span>
          </button>
        ))}
      </div>

      <div className="cr-markframe">
        <div ref={frameRef} className="cr-markinner" onClick={pin} style={{ cursor: 'crosshair' }}>
          <img src={frameUrl(video.fileId)} alt="" draggable={false} />
          {opts.fit === 'crop' && (
            <div className="cr-croprect" style={{ left: `${left}%`, top: `${top}%`, width: `${cropW}%`, height: `${cropH}%` }}>
              <span className="cr-croptag">{opts.focus ? 'focus pinned' : 'follows the subject'}</span>
            </div>
          )}
        </div>
      </div>

      <div className="cr-fillcards">
        <button className={'cr-fill' + (opts.fit === 'crop' ? ' on' : '')} onClick={() => set('fit', 'crop')}>
          <Crosshair size={18} strokeWidth={2} />
          <b>Smart crop</b><span>Recommended — fills the screen</span>
        </button>
        <button className={'cr-fill' + (opts.fit === 'blur' ? ' on' : '')} onClick={() => set('fit', 'blur')}>
          <i className="barsIcon" />
          <b>Show everything</b><span>Adds soft blurred bars</span>
        </button>
      </div>
    </JobShell>
  )
}
