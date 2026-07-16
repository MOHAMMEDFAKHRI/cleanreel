import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Moon, Sun } from 'lucide-react'
import Home from './screens/Home.jsx'
import Analyzing from './screens/Analyzing.jsx'
import MarkStub from './screens/MarkStub.jsx'
import { upload, wake, prewarm } from './api.js'

const MIN_ANALYZE_MS = 2000 // the analyzing beat always shows ≥2s (spec §2)

export default function App() {
  // screen state machine: home → analyzing → mark  (phases b–e extend this)
  const [screen, setScreen] = useState('home')
  const [dark, setDark] = useState(() => {
    const saved = localStorage.getItem('cr-theme')
    if (saved) return saved === 'dark'
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
  })
  const [toast, setToastMsg] = useState(null)
  const toastTimer = useRef(null)

  // upload state
  const [up, setUp] = useState(null)          // { name, pct } while uploading
  const [job, setJob] = useState(null)        // job-card hint for the analyzer
  const [video, setVideo] = useState(null)    // { fileId, width, height, seconds, url }

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    localStorage.setItem('cr-theme', dark ? 'dark' : 'light')
  }, [dark])

  useEffect(() => { wake() }, [])

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
    setVideo({ fileId: d.file_id, width: d.width, height: d.height, seconds: d.seconds, url: URL.createObjectURL(file) })
    setUp(null)
    setScreen('analyzing')
    setTimeout(() => setScreen('mark'), MIN_ANALYZE_MS)
  }, [showToast])

  const reset = useCallback(() => {
    if (video?.url) URL.revokeObjectURL(video.url)
    setVideo(null); setJob(null); setUp(null); setScreen('home')
  }, [video])

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
      {screen === 'mark' && <MarkStub video={video} job={job} onBack={reset} />}
      {toast && <div className="cr-toast" role="status">{toast}</div>}
    </div>
  )
}
