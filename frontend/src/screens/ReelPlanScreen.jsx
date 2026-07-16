import React from 'react'
import JobShell from './JobShell.jsx'
import Toggle from './Toggle.jsx'
import { LOOKS } from './CaptionsScreen.jsx'

/**
 * Reel wizard screen 1 — "Here's your reel plan" (README 4a).
 * AI-proposed plan as toggle cards. The prototype's hook-card-at-start needs
 * new backend (cards only concat at the END today) — the end card IS the
 * supported card, so the writer screen edits that.
 */
export default function ReelPlanScreen({ opts, setOpts, onBack, onNext }) {
  const set = (k, v) => setOpts({ ...opts, [k]: v })
  return (
    <JobShell
      title="Here’s your reel plan"
      sub="We cut a reel from your footage — long clips welcome. Flip anything you don’t want."
      onBack={onBack}
      cta={opts.endCard ? 'Next — write your end card' : 'Build my reel preview — free'}
      onCta={onNext}
      hint="Preview is free. Saving a reel uses 2 credits — it runs the whole pipeline."
    >
      <div className="cr-plan">
        <div className={'cr-plancard' + (opts.crop ? ' rec' : '')} onClick={() => set('crop', !opts.crop)}>
          <div className="txt"><b>Crop to 9:16</b><span>Vertical, follows the subject</span></div>
          <Toggle on={opts.crop} onChange={(v) => set('crop', v)} />
        </div>

        <div className={'cr-plancard' + (opts.captions ? ' rec' : '')} onClick={() => set('captions', !opts.captions)}>
          <div className="txt">
            <b>Captions — {LOOKS.find(l => l.id === opts.look)?.title}</b>
            <span>Auto-written from the audio</span>
            {opts.captions && (
              <div className="cr-segs" style={{ marginTop: 8 }} onClick={(e) => e.stopPropagation()}>
                {LOOKS.map(l => (
                  <button key={l.id} className={opts.look === l.id ? 'on' : ''} onClick={() => set('look', l.id)}>
                    {l.id === 'bold' ? 'Bold' : l.id === 'clean' ? 'Clean' : 'Pop'}
                  </button>
                ))}
              </div>
            )}
          </div>
          <Toggle on={opts.captions} onChange={(v) => set('captions', v)} />
        </div>

        <div className={'cr-plancard' + (opts.endCard ? ' rec' : '')} onClick={() => set('endCard', !opts.endCard)}>
          <div className="txt"><b>End card with your @handle</b><span>2.5s card after the clip — your call to action</span></div>
          <Toggle on={opts.endCard} onChange={(v) => set('endCard', v)} />
        </div>
      </div>
    </JobShell>
  )
}
