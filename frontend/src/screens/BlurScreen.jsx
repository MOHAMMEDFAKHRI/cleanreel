import React, { useMemo } from 'react'
import JobShell from './JobShell.jsx'
import { frameUrl } from '../api.js'

/**
 * 3c Privacy blur — everything found is HIDDEN BY DEFAULT; chips flip a whole
 * class visible (per-region keep-visible needs backend support — split issue).
 * opts = { faces:bool, plates:bool, style:'blur'|'pixelate' }
 */
export default function BlurScreen({ video, regions, opts, setOpts, onBack, onPreview }) {
  const set = (k, v) => setOpts({ ...opts, [k]: v })
  const faces = useMemo(() => regions.filter(r => r.kind === 'face'), [regions])
  const plates = useMemo(() => regions.filter(r => r.kind === 'plate'), [regions])
  const W = video.width, H = video.height
  const n = faces.length + plates.length

  const title = n
    ? `We found ${[faces.length && `${faces.length} face${faces.length > 1 ? 's' : ''}`, plates.length && `${plates.length} plate${plates.length > 1 ? 's' : ''}`].filter(Boolean).join(' & ')}`
    : 'Nothing to hide on this frame'
  const anyOn = (opts.faces && faces.length > 0) || (opts.plates && plates.length > 0)

  return (
    <JobShell
      title={title}
      sub={n ? 'Everything is hidden by default — tap a chip to keep it visible.' : 'The blur still runs on every frame — faces are found as they appear.'}
      onBack={onBack} cta="Preview the blur — free" ctaDisabled={!anyOn && n > 0} onCta={onPreview}
      hint="Blur follows people even when they move."
    >
      <div className="cr-markframe">
        <div className="cr-markinner">
          <img src={frameUrl(video.fileId)} alt="" draggable={false} />
          {faces.map(r => {
            const [x, y, w, h] = r.bbox
            return (
              <div key={r.id} className={'cr-blurspot round' + (opts.faces ? ' on' : '')}
                   style={{ left: `${x / W * 100}%`, top: `${y / H * 100}%`, width: `${w / W * 100}%`, height: `${h / H * 100}%` }}>
                {opts.faces && <span className="cr-croptag">hidden · follows them</span>}
              </div>
            )
          })}
          {plates.map(r => {
            const [x, y, w, h] = r.bbox
            return (
              <div key={r.id} className={'cr-blurspot' + (opts.plates ? ' on' : '')}
                   style={{ left: `${x / W * 100}%`, top: `${y / H * 100}%`, width: `${w / W * 100}%`, height: `${h / H * 100}%` }}>
                {opts.plates && <span className="cr-croptag">hidden</span>}
              </div>
            )
          })}
        </div>
      </div>

      <div className="cr-chips">
        {faces.length > 0 && (
          <button className={'cr-chip' + (opts.faces ? '' : ' off')} onClick={() => set('faces', !opts.faces)}>
            {faces.length > 1 ? `Faces ×${faces.length}` : 'Face'} · {opts.faces ? 'hidden' : 'visible'}
          </button>
        )}
        {plates.length > 0 && (
          <button className={'cr-chip' + (opts.plates ? '' : ' off')} onClick={() => set('plates', !opts.plates)}>
            {plates.length > 1 ? `Plates ×${plates.length}` : 'Plate'} · {opts.plates ? 'hidden' : 'visible'}
          </button>
        )}
      </div>

      <div className="cr-segs">
        <button className={opts.style === 'blur' ? 'on' : ''} onClick={() => set('style', 'blur')}>Soft blur</button>
        <button className={opts.style === 'pixelate' ? 'on' : ''} onClick={() => set('style', 'pixelate')}>Pixelate</button>
      </div>
    </JobShell>
  )
}
