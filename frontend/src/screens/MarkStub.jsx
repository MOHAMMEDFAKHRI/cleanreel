import React from 'react'
import { ChevronLeft } from 'lucide-react'

/**
 * Phase (b) placeholder — the real mark screen (tap-to-select, detection
 * badges, chips) lands next. This proves the shell: header pattern, step
 * dots, 4/5 frame with the actual uploaded clip.
 */
export default function MarkStub({ video, job, onBack }) {
  return (
    <>
      <div className="cr-backrow">
        <button className="cr-back" onClick={onBack} aria-label="Back"><ChevronLeft size={18} /></button>
        <div className="cr-dots"><i className="on" /><i /><i /></div>
      </div>
      <h1 className="cr-h1" style={{ fontSize: 22 }}>Upload looks good</h1>
      <p className="cr-sub">
        {video ? `${video.width}×${video.height} · ${video.seconds}s` : ''}{job ? ` · job: ${job}` : ''}
      </p>
      <div className="cr-stub-frame">
        {video?.url && <video src={video.url + '#t=0.01'} muted playsInline preload="auto" />}
      </div>
      <div className="cr-note">
        Tap-to-select lands in the next build (phase b). Your clip is uploaded and
        the GPU is pre-warming — nothing is lost by going back.
      </div>
      <button className="cr-cta ghost" onClick={onBack}>Back to start</button>
    </>
  )
}
