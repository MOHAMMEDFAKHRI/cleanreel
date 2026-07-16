import React from 'react'
import JobShell from './JobShell.jsx'

const THEMES = [
  { id: 'dark',   bg: 'linear-gradient(160deg,#2a2f52,#101322)', ink: '#fff' },
  { id: 'light',  bg: 'linear-gradient(160deg,#f4f5f9,#dfe3f2)', ink: '#171a2b' },
  { id: 'accent', bg: 'linear-gradient(160deg,#5b63e8,#8a5cf0)', ink: '#fff' },
]

/** Reel wizard screen 2 — write the end card (live 9:16 mini preview). */
export default function EndCardScreen({ opts, setOpts, onBack, onNext }) {
  const set = (k, v) => setOpts({ ...opts, [k]: v })
  const theme = THEMES.find(t => t.id === opts.cardTheme) || THEMES[0]
  return (
    <JobShell
      title="Write your end card"
      sub="It shows for 2.5 seconds after the clip — where should people go next?"
      onBack={onBack}
      cta="Looks good — build my reel" ctaDisabled={!opts.cta.trim()} onCta={onNext}
      hint="Keep it under one breath."
    >
      <div className="cr-cardpreview" style={{ background: theme.bg, color: theme.ink }}>
        <span>{opts.cta.trim() || 'Follow @yourhandle'}</span>
        <i className="mono">end · 2.5s</i>
      </div>
      <input
        className="cr-ctainput" maxLength={80} placeholder="Follow @yourhandle for more"
        value={opts.cta} onChange={(e) => set('cta', e.target.value)} autoFocus
      />
      <div className="cr-counter">{opts.cta.length}/80</div>
      <div className="cr-swatches">
        {THEMES.map(t => (
          <button key={t.id} className={'cr-swatch' + (opts.cardTheme === t.id ? ' on' : '')}
                  style={{ background: t.bg }} aria-label={t.id}
                  onClick={() => set('cardTheme', t.id)} />
        ))}
      </div>
    </JobShell>
  )
}
