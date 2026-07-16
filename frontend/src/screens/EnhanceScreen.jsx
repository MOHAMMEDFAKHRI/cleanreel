import React from 'react'
import JobShell from './JobShell.jsx'
import Toggle from './Toggle.jsx'
import { frameUrl } from '../api.js'

/**
 * 3a Enhance — "Here's what we'd improve": a recommended plan as toggle
 * cards, not settings. opts = { denoise, sharpen, strength, upscale }.
 */
export default function EnhanceScreen({ video, opts, setOpts, onBack, onPreview }) {
  const set = (k, v) => setOpts({ ...opts, [k]: v })
  const is720ish = Math.min(video.width, video.height) <= 800

  return (
    <JobShell
      title="Here’s what we’d improve"
      sub="It looks a little soft and has some compression noise — here’s our plan."
      onBack={onBack} cta="Preview the improvement — free" onCta={onPreview}
      hint="Faces stay natural — nothing gets the plastic look. Exports use 2 credits."
    >
      <div className="cr-splitdemo">
        <div className="half soft" style={{ backgroundImage: `url(${frameUrl(video.fileId)})` }}><span>SOFT</span></div>
        <div className="half sharp" style={{ backgroundImage: `url(${frameUrl(video.fileId)})` }}><span>SHARPENED</span></div>
      </div>

      <div className="cr-plan">
        <div className={'cr-plancard' + (opts.denoise ? ' rec' : '')} onClick={() => set('denoise', !opts.denoise)}>
          <div className="txt">
            <b>Clean up compression noise</b>
            <span>Smooths blockiness without smearing detail</span>
          </div>
          <Toggle on={opts.denoise} onChange={(v) => set('denoise', v)} />
        </div>

        <div className={'cr-plancard' + (opts.sharpen ? ' rec' : '')} onClick={() => set('sharpen', !opts.sharpen)}>
          <div className="txt">
            <b>Sharpen it</b>
            <span>How much: {Math.round(opts.strength * 100)}%</span>
            {opts.sharpen && (
              <input
                type="range" min="0" max="100" value={Math.round(opts.strength * 100)}
                onClick={(e) => e.stopPropagation()}
                onChange={(e) => set('strength', Number(e.target.value) / 100)}
              />
            )}
          </div>
          <Toggle on={opts.sharpen} onChange={(v) => set('sharpen', v)} />
        </div>

        <div className={'cr-plancard' + (opts.upscale ? ' rec' : '')} onClick={() => set('upscale', !opts.upscale)}>
          <div className="txt">
            <b>Make it 2× bigger</b>
            <span>{is720ish ? `${Math.min(video.width, video.height)}p → ${Math.min(video.width, video.height) * 2}p · takes a bit longer` : 'Already high-res — usually not needed'}</span>
          </div>
          <Toggle on={opts.upscale} onChange={(v) => set('upscale', v)} />
        </div>
      </div>
    </JobShell>
  )
}
