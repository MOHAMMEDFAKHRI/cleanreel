import React from 'react'
import { ChevronLeft } from 'lucide-react'

/** Shared header + CTA shell for the per-job decision screens (README §8). */
export default function JobShell({ title, sub, onBack, cta, ctaDisabled, onCta, hint, children }) {
  return (
    <>
      <div className="cr-backrow">
        <button className="cr-back" onClick={onBack} aria-label="Back"><ChevronLeft size={18} /></button>
        <div className="cr-dots"><i className="on" /><i /><i /></div>
      </div>
      <h1 className="cr-h1" style={{ fontSize: 22 }}>{title}</h1>
      <p className="cr-sub">{sub}</p>
      {children}
      <button className="cr-cta" disabled={ctaDisabled} onClick={onCta}>{cta}</button>
      {hint && <p className="cr-hint">{hint}</p>}
      <p className="cr-hint faint">Previewing confirms this is a video you own or are licensed to edit.</p>
    </>
  )
}
