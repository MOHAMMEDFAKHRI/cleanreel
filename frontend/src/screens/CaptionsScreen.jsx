import React from 'react'
import JobShell from './JobShell.jsx'

const LOOKS = [
  { id: 'bold',    title: 'BIG & BOLD',       note: 'TikTok style',      cls: 'lookBold',  params: { cap_style: 'bold', cap_size: 'l', cap_color: 'white' } },
  { id: 'clean',   title: 'Clean bottom line', note: 'Subtitle style',   cls: 'lookClean', params: { cap_style: 'clean', cap_size: 'm', cap_color: 'white' } },
  { id: 'karaoke', title: 'Karaoke pop',       note: 'Bold + yellow',    cls: 'lookPop',   params: { cap_style: 'bold', cap_size: 'l', cap_color: 'yellow' } },
]

/**
 * 3d Captions — style pick. The words are transcribed during the render;
 * the tap-to-fix transcript editor is its own issue (needs a transcribe-first
 * API). opts = { look }
 */
export default function CaptionsScreen({ opts, setOpts, onBack, onPreview }) {
  return (
    <JobShell
      title="Pick a look"
      sub="We listen to the audio and write the words for you — 98% of them are usually right."
      onBack={onBack} cta="Preview the captions — free" onCta={onPreview}
      hint="You get the .srt file too, free — use it anywhere."
    >
      <div className="cr-looks">
        {LOOKS.map(l => (
          <button key={l.id} className={'cr-look ' + l.cls + (opts.look === l.id ? ' on' : '')} onClick={() => setOpts({ ...opts, look: l.id })}>
            <span className={'cap ' + l.cls}>{l.id === 'clean' ? 'like this' : l.id === 'karaoke' ? 'like THIS' : 'LIKE THIS'}</span>
            <b>{l.title}</b><span className="note">{l.note}</span>
          </button>
        ))}
      </div>
    </JobShell>
  )
}
export { LOOKS }
