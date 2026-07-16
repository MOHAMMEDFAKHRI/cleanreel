import React from 'react'
import { Check } from 'lucide-react'

/** Done screen (README §7). */
export default function Done({ downloadUrl, credits, onAgain }) {
  return (
    <div className="cr-center">
      <div className="cr-donecheck"><Check size={36} strokeWidth={3} /></div>
      <h2 style={{ fontSize: 24 }}>Saved — it’s yours</h2>
      <p className="sub">
        Full quality, audio kept, no watermark of ours — ever.
        The upload deletes itself in 6 hours.
      </p>
      {credits != null && (
        <span className="cr-creditpill">
          {credits} export credit{credits === 1 ? '' : 's'} left
        </span>
      )}
      <a className="cr-cta" style={{ textAlign: 'center', textDecoration: 'none', width: '100%' }}
         href={downloadUrl} download="cleaned.mp4">
        Download video
      </a>
      <button className="cr-cta ghost" style={{ width: '100%' }} onClick={onAgain}>
        Clean another video
      </button>
    </div>
  )
}
