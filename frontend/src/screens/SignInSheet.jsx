import React, { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

/**
 * Sign-in bottom sheet (README §6). Steps:
 *  email  → "Almost there" + email input
 *  code   → 6-digit entry (the email also carries a magic link; code works here)
 *  saving → export progress (owned by App, shown here)
 *  credits→ out-of-credits message (401/402 fallback)
 */
export default function SignInSheet({ step, email, setEmail, onSendCode, onSubmitCode, onClose, savePct, error, busy, packs, onBuyPack }) {
  const [code, setCode] = useState('')
  const inputRef = useRef(null)
  useEffect(() => { inputRef.current?.focus() }, [step])

  return (
    <div className="cr-overlay" onClick={step !== 'saving' ? onClose : undefined}>
      <div className="cr-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="grab" />

        {step === 'email' && (
          <>
            <div className="row">
              <h3>Almost there</h3>
              <button className="close" onClick={onClose} aria-label="Close"><X size={16} /></button>
            </div>
            <p className="body">Sign in to save your video. Your first <b>2 exports are free</b> — no card needed.</p>
            <input
              ref={inputRef} type="email" inputMode="email" autoComplete="email"
              placeholder="you@example.com" value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && EMAIL_RE.test(email) && onSendCode()}
            />
            {error && <p className="err">{error}</p>}
            <button className="cr-cta" disabled={!EMAIL_RE.test(email) || busy} onClick={onSendCode}>
              {busy ? 'Sending…' : 'Email me a code'}
            </button>
            <p className="foot">No password. We only use your email for sign-in.</p>
          </>
        )}

        {step === 'code' && (
          <>
            <div className="row">
              <h3>Check your email</h3>
              <button className="close" onClick={onClose} aria-label="Close"><X size={16} /></button>
            </div>
            <p className="body">We sent a 6-digit code to <b>{email}</b>.</p>
            <input
              ref={inputRef} className="code" inputMode="numeric" pattern="[0-9]*" maxLength={6}
              placeholder="••••••" value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              onKeyDown={(e) => e.key === 'Enter' && code.length === 6 && onSubmitCode(code)}
            />
            {error && <p className="err">{error}</p>}
            <button className="cr-cta" disabled={code.length !== 6 || busy} onClick={() => onSubmitCode(code)}>
              {busy ? 'Checking…' : 'Sign in & save'}
            </button>
            <p className="foot">The email has a sign-in link too — either works.</p>
          </>
        )}

        {step === 'saving' && (
          <>
            <h3>Saving your full video…</h3>
            <p className="body">Every frame this time — hang tight.</p>
            <div className="cr-progress slim" style={{ marginTop: 14 }}>
              <i style={{ width: `${Math.round((savePct || 0) * 100)}%` }} />
            </div>
            <p className="foot">{Math.round((savePct || 0) * 100)}%</p>
          </>
        )}

        {step === 'credits' && (
          <>
            <div className="row">
              <h3>Out of export credits</h3>
              <button className="close" onClick={onClose} aria-label="Close"><X size={16} /></button>
            </div>
            <p className="body">One-time packs, no subscription. Credits never expire.</p>
            {packs === null && <p className="foot">Loading packs…</p>}
            {packs?.length === 0 && <p className="foot">Payments are being set up — check back soon.</p>}
            {packs?.length > 0 && (
              <div className="cr-packs">
                {packs.map(([id, p]) => (
                  <button key={id} className="cr-pack" onClick={() => onBuyPack(id)}>
                    <b>{p.label}</b><span>${(p.amount / 100).toFixed(2)}</span>
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
