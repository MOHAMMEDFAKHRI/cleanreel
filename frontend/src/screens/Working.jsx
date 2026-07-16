import React, { useEffect, useRef, useState } from 'react'
import { Check } from 'lucide-react'

const AT = [0.12, 0.45, 0.75, 0.94]

/** Working screen (README §4): plain-language checklist driven by REAL job progress. */
export default function Working({ pct, title, steps }) {
  const STEPS = (steps || []).map((label, i) => ({ at: AT[i], label }))
  // rolling ETA from observed progress rate
  const [eta, setEta] = useState(null)
  const hist = useRef([])
  useEffect(() => {
    const now = Date.now() / 1000
    hist.current.push([now, pct])
    hist.current = hist.current.filter(([t]) => now - t < 20)
    const [t0, p0] = hist.current[0]
    if (pct > p0 + 0.01 && now > t0 + 1) {
      const rate = (pct - p0) / (now - t0)
      setEta(Math.max(1, Math.round((1 - pct) / rate)))
    }
  }, [pct])

  return (
    <div className="cr-center" style={{ justifyContent: 'flex-start', paddingTop: 30 }}>
      <div className="cr-eyebrow">Working on it</div>
      <h2 style={{ fontSize: 22 }}>{title}</h2>
      <p className="sub">
        {eta != null ? `About ${eta} seconds left — this preview is free.` : 'This preview is free.'}
      </p>
      <div className="cr-workframe">
        <div className="cr-scanline" />
        <span className="mono">{Math.round(pct * 100)}%</span>
      </div>
      <ul className="cr-steps">
        {STEPS.map((s, i) => {
          const state = pct >= s.at ? 'done' : (i === 0 || pct >= STEPS[i - 1].at) ? 'active' : 'pending'
          return (
            <li key={s.label} className={state}>
              {state === 'done' ? <span className="dot done"><Check size={11} strokeWidth={3} /></span>
                : state === 'active' ? <span className="dot spin" />
                : <span className="dot" />}
              {s.label}
            </li>
          )
        })}
      </ul>
      <div className="cr-progress slim"><i style={{ width: `${Math.round(pct * 100)}%` }} /></div>
      <p className="sub" style={{ fontSize: 12 }}>
        Faces are protected the whole way — they’re never repainted.
      </p>
    </div>
  )
}
