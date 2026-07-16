import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Moon, Sun } from 'lucide-react'
import Home from './screens/Home.jsx'
import Analyzing from './screens/Analyzing.jsx'
import Mark from './screens/Mark.jsx'
import Working from './screens/Working.jsx'
import PreviewScreen from './screens/PreviewScreen.jsx'
import { upload, wake, prewarm, getRegions, createJob, jobStatus, absUrl } from './api.js'

const MIN_ANALYZE_MS = 1500   // the analyzing beat never flashes shorter than this

export default function App() {
  // state machine (README): home → analyzing → mark → working → preview
  const [screen, setScreen] = useState('home')
  const [dark, setDark] = useState(() => {
    const saved = localStorage.getItem('cr-theme')
    if (saved) return saved === 'dark'
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
  })
  const [toast, setToastMsg] = useState(null)
  const toastTimer = useRef(null)

  const [up, setUp] = useState(null)                 // { name, pct } while uploading
  const [job, setJob] = useState(null)               // analyzer hint from the job card
  const [video, setVideo] = useState(null)           // { fileId, width, height, seconds }
  const [regions, setRegions] = useState([])
  const [selected, setSelected] = useState(new Set())
  const [workPct, setWorkPct] = useState(0)
  const [preview, setPreview] = useState(null)       // { resultUrl, beforeUrl, confidence }
  const pollRef = useRef(null)

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    localStorage.setItem('cr-theme', dark ? 'dark' : 'light')
  }, [dark])

  useEffect(() => { wake(); return () => clearInterval(pollRef.current) }, [])

  const showToast = useCallback((msg) => {
    setToastMsg(msg)
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToastMsg(null), 2400)
  }, [])

  const startUpload = useCallback(async (file, jobHint) => {
    if (!file) return
    if (!file.type.startsWith('video/')) { showToast('That doesn’t look like a video'); return }
    setJob(jobHint || null)
    setUp({ name: file.name, pct: 0 })
    let res = null
    for (let attempt = 0; attempt < 2 && !res; attempt++) {
      try {
        res = await upload(file, { onProgress: (pct) => setUp({ name: file.name, pct }) })
      } catch {
        if (attempt === 0) { showToast('Hiccup while uploading — retrying…'); setUp({ name: file.name, pct: 0 }) }
      }
    }
    if (!res) { setUp(null); showToast('Could not reach the server — try again'); return }
    if (!res.ok) { setUp(null); showToast(res.data?.detail || 'Upload failed'); return }
    const d = res.data
    prewarm(jobHint === 'erase' ? 'erase' : jobHint === 'enhance' ? 'enhance' : 'remove')
    const vid = { fileId: d.file_id, width: d.width, height: d.height, seconds: d.seconds }
    setVideo(vid); setUp(null); setPreview(null)
    setScreen('analyzing')

    // real analysis: regions + a floor so the beat reads as intentional
    const t0 = Date.now()
    const resp = await getRegions(d.file_id)
    const wait = Math.max(0, MIN_ANALYZE_MS - (Date.now() - t0))
    setTimeout(() => {
      const regs = resp?.regions || []
      setRegions(regs)
      setSelected(new Set(regs.filter(r => r.preselected).map(r => r.id)))
      setScreen('mark')
      const found = regs.filter(r => r.preselected)
      if (found.length) {
        showToast(found.length > 1
          ? `Found ${found.length} marks — already selected for you`
          : `Found a ${found[0].kind === 'logo' ? 'logo' : 'watermark'} — already selected for you`)
      }
    }, wait)
  }, [showToast])

  const startPreview = useCallback(async () => {
    const chosen = regions.filter(r => selected.has(r.id))
    if (!chosen.length || !video) return
    const anyMark = chosen.some(r => r.kind === 'watermark' || r.kind === 'logo')
    const extra = chosen.filter(r => !(r.kind === 'watermark' || r.kind === 'logo'))
    setWorkPct(0.02); setScreen('working')
    const res = await createJob({
      file_id: video.fileId, mode: 'preview', task: 'remove',
      owns_rights: true, auto: anyMark,
      boxes: extra.length ? extra.map(r => r.bbox) : null,
    })
    if (!res.ok || !res.data?.job_id) {
      setScreen('mark'); showToast(res.data?.detail || 'Could not start the preview — try again')
      return
    }
    const id = res.data.job_id
    clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const s = await jobStatus(id)
        setWorkPct(Math.max(0.02, s.progress || 0))
        if (s.status === 'done') {
          clearInterval(pollRef.current)
          setPreview({
            resultUrl: absUrl(s.result_url), beforeUrl: absUrl(s.before_url),
            confidence: s.qc?.confidence ?? null,
          })
          setScreen('preview')
        } else if (s.status === 'failed') {
          clearInterval(pollRef.current)
          setScreen('mark'); showToast(s.message || 'That render failed — try again')
        }
      } catch { /* transient poll error — keep going */ }
    }, 1500)
  }, [regions, selected, video, showToast])

  const reset = useCallback(() => {
    clearInterval(pollRef.current)
    setVideo(null); setJob(null); setUp(null)
    setRegions([]); setSelected(new Set()); setPreview(null)
    setScreen('home')
  }, [])

  const selectedLabels = regions.filter(r => selected.has(r.id)).map(r => r.label)

  return (
    <div className="cr-app">
      {screen === 'home' && (
        <>
          <header className="cr-head">
            <div className="cr-logo">Clean<em>Reel</em></div>
            <div className="spacer" />
            <button className="cr-iconbtn" aria-label="Toggle dark mode" onClick={() => setDark(d => !d)}>
              {dark ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <div className="cr-avatar" aria-hidden>M</div>
          </header>
          <Home uploading={up} onFile={startUpload} />
        </>
      )}
      {screen === 'analyzing' && <Analyzing />}
      {screen === 'mark' && video && (
        <Mark
          video={video} regions={regions} selected={selected} setSelected={setSelected}
          onBack={reset} onPreview={startPreview} showToast={showToast}
        />
      )}
      {screen === 'working' && <Working pct={workPct} />}
      {screen === 'preview' && preview && (
        <PreviewScreen
          preview={preview} selectedLabels={selectedLabels}
          onBack={() => setScreen('mark')}
          onSave={() => showToast('Sign-in & save lands in the next build — preview is yours to watch')}
        />
      )}
      {toast && <div className="cr-toast" role="status">{toast}</div>}
    </div>
  )
}
