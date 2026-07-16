import React from 'react'
import { ChevronLeft } from 'lucide-react'
import Toggle from './Toggle.jsx'
import { LOOKS } from './CaptionsScreen.jsx'

/**
 * Fine-tune (subset of the prototype's editor): trim + captions + clean audio.
 * Music / filters / speed need new backend surface (and music = licensing) —
 * deferred per the issue's "split new product surface into own issues".
 */
export default function FineTune({ video, opts, setOpts, onBack, onRebuild }) {
  const set = (k, v) => setOpts({ ...opts, [k]: v })
  const dur = video.seconds || 0
  const t0 = opts.trimStart ?? 0
  const t1 = opts.trimEnd ?? dur
  const span = Math.max(0.5, t1 - t0)

  return (
    <>
      <div className="cr-backrow">
        <button className="cr-back" onClick={onBack} aria-label="Back"><ChevronLeft size={18} /></button>
        <div className="cr-dots"><i className="on" /><i className="on" /><i /></div>
      </div>
      <h1 className="cr-h1" style={{ fontSize: 22 }}>Fine-tune your reel</h1>
      <p className="cr-sub">{span.toFixed(1)}s selected · starts at {t0.toFixed(1)}s</p>

      <div className="cr-plancard" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
        <b style={{ fontSize: 13.5 }}>Trim</b>
        <label className="cr-trimlabel">Starts at {t0.toFixed(1)}s
          <input type="range" min="0" max={dur} step="0.1" value={t0}
                 onChange={(e) => set('trimStart', Math.min(Number(e.target.value), t1 - 0.5))} />
        </label>
        <label className="cr-trimlabel">Ends at {t1.toFixed(1)}s
          <input type="range" min="0" max={dur} step="0.1" value={t1}
                 onChange={(e) => set('trimEnd', Math.max(Number(e.target.value), t0 + 0.5))} />
        </label>
      </div>

      <div className="cr-plan" style={{ marginTop: 9 }}>
        <div className="cr-plancard" onClick={() => set('captions', !opts.captions)}>
          <div className="txt"><b>Captions</b><span>{opts.captions ? LOOKS.find(l => l.id === opts.look)?.title : 'Off'}</span></div>
          <Toggle on={opts.captions} onChange={(v) => set('captions', v)} />
        </div>
        <div className="cr-plancard" onClick={() => set('cleanAudio', !opts.cleanAudio)}>
          <div className="txt"><b>Clean up the audio</b><span>Free — removes hiss & background hum</span></div>
          <Toggle on={opts.cleanAudio} onChange={(v) => set('cleanAudio', v)} />
        </div>
      </div>

      <button className="cr-cta" onClick={onRebuild}>Update my reel — free</button>
      <p className="cr-hint">Music, filters and speed are coming — they need new engine work.</p>
    </>
  )
}
