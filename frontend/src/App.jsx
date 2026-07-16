import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Moon, Sun } from 'lucide-react'
import Home from './screens/Home.jsx'
import Analyzing from './screens/Analyzing.jsx'
import Mark from './screens/Mark.jsx'
import Working from './screens/Working.jsx'
import PreviewScreen from './screens/PreviewScreen.jsx'
import SignInSheet from './screens/SignInSheet.jsx'
import Done from './screens/Done.jsx'
import { upload, wake, prewarm, getRegions, createJob, jobStatus, absUrl } from './api.js'
import { loadAuth, saveAuth, authHeaders, requestCode, submitCode, me } from './auth.js'
import { initAnalytics, track, identify } from './analytics.js'

const MIN_ANALYZE_MS = 1500

export default function App() {
  // screens: home → analyzing → mark → working → preview → done
  // sheet (over preview): null | email | code | saving | credits
  const [screen, setScreen] = useState('home')
  const [sheet, setSheet] = useState(null)
  const [dark, setDark] = useState(() => {
    const saved = localStorage.getItem('cr-theme')
    if (saved) return saved === 'dark'
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
  })
  const [toast, setToastMsg] = useState(null)
  const toastTimer = useRef(null)

  const [auth, setAuth] = useState(loadAuth)
  const [up, setUp] = useState(null)
  const [job, setJob] = useState(null)
  const [video, setVideo] = useState(null)
  const [regions, setRegions] = useState([])
  const [selected, setSelected] = useState(new Set())
  const [workPct, setWorkPct] = useState(0)
  const [preview, setPreview] = useState(null)
  const [savePct, setSavePct] = useState(0)
  const [done, setDone] = useState(null)           // { downloadUrl, credits }
  const [email, setEmail] = useState(auth?.email || '')
  const [sheetErr, setSheetErr] = useState(null)
  const [sheetBusy, setSheetBusy] = useState(false)
  const pollRef = useRef(null)

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    localStorage.setItem('cr-theme', dark ? 'dark' : 'light')
  }, [dark])

  useEffect(() => {
    wake(); initAnalytics(loadAuth()?.email)
    return () => clearInterval(pollRef.current)
  }, [])

  const showToast = useCallback((msg) => {
    setToastMsg(msg)
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToastMsg(null), 2400)
  }, [])

  const setAuthBoth = useCallback((a) => { setAuth(a); saveAuth(a); if (a?.email) identify(a.email) }, [])

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
    if (!res) { setUp(null); track('upload_failed', { reason: 'network' }); showToast('Could not reach the server — try again'); return }
    if (!res.ok) { setUp(null); track('upload_failed', { reason: 'rejected', code: res.status }); showToast(res.data?.detail || 'Upload failed'); return }
    const d = res.data
    track('upload_ok', { seconds: d.seconds, w: d.width, h: d.height })
    prewarm(jobHint === 'erase' ? 'erase' : jobHint === 'enhance' ? 'enhance' : 'remove')
    setVideo({ fileId: d.file_id, width: d.width, height: d.height, seconds: d.seconds })
    setUp(null); setPreview(null); setDone(null)
    setScreen('analyzing')

    const t0 = Date.now()
    const resp = await getRegions(d.file_id)
    const wait = Math.max(0, MIN_ANALYZE_MS - (Date.now() - t0))
    setTimeout(() => {
      const regs = resp?.regions || []
      track('regions_found', { n: regs.length, preselected: regs.filter(r => r.preselected).length, type: resp?.watermark_type })
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

  /** Shared job runner for preview + export. */
  const runJob = useCallback(async (mode) => {
    const chosen = regions.filter(r => selected.has(r.id))
    if (!chosen.length || !video) return
    const anyMark = chosen.some(r => r.kind === 'watermark' || r.kind === 'logo')
    const extra = chosen.filter(r => !(r.kind === 'watermark' || r.kind === 'logo'))
    const body = {
      file_id: video.fileId, mode, task: 'remove',
      owns_rights: true, auto: anyMark,
      boxes: extra.length ? extra.map(r => r.bbox) : null,
    }
    const headers = mode === 'export' ? authHeaders(auth) : {}
    const r = await fetch((window.API_BASE || 'https://cleanreel.onrender.com') + '/api/jobs', {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify(body),
    })
    return { ok: r.ok, status: r.status, data: await r.json().catch(() => null) }
  }, [regions, selected, video, auth])

  const startPreview = useCallback(async () => {
    track('preview_click', { selections: selected.size })
    setWorkPct(0.02); setScreen('working')
    const res = await runJob('preview')
    if (!res?.ok || !res.data?.job_id) {
      setScreen('mark'); showToast(res?.data?.detail || 'Could not start the preview — try again')
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
          track('job_done', { mode: 'preview' })
          setPreview({ resultUrl: absUrl(s.result_url), beforeUrl: absUrl(s.before_url), confidence: s.qc?.confidence ?? null })
          setScreen('preview')
        } else if (s.status === 'failed') {
          clearInterval(pollRef.current)
          track('job_error', { mode: 'preview' })
          setScreen('mark'); showToast(s.message || 'That render failed — try again')
        }
      } catch { /* transient */ }
    }, 1500)
  }, [runJob, selected, showToast])

  const startExport = useCallback(async () => {
    setSheet('saving'); setSavePct(0.02)
    const res = await runJob('export')
    if (!res?.ok) {
      if (res?.status === 401) { setAuthBoth(null); setSheetErr(null); setSheet('email'); track('export_blocked_signin'); return }
      if (res?.status === 402) { setSheet('credits'); track('export_blocked_credits'); return }
      setSheet(null); showToast(res?.data?.detail || 'Could not start the export — try again')
      return
    }
    track('export_submitted', { selections: selected.size })
    const id = res.data.job_id
    clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const s = await jobStatus(id)
        setSavePct(Math.max(0.02, s.progress || 0))
        if (s.status === 'done') {
          clearInterval(pollRef.current)
          track('job_done', { mode: 'export' })
          const info = await me(auth).catch(() => null)
          if (info && !info.expired) setAuthBoth({ ...auth, email: info.email, credits: info.credits })
          setSheet(null)
          setDone({ downloadUrl: absUrl(s.result_url), credits: info && !info.expired ? info.credits : null })
          setScreen('done')
        } else if (s.status === 'failed') {
          clearInterval(pollRef.current)
          track('job_error', { mode: 'export' })
          setSheet(null); showToast(s.message || 'The export failed — credits were refunded')
        }
      } catch { /* transient */ }
    }, 1500)
  }, [runJob, auth, selected, setAuthBoth, showToast])

  const onSave = useCallback(() => {
    track('export_click', { signed_in: !!auth?.session })
    if (auth?.session) startExport()
    else { setSheetErr(null); setSheet('email') }
  }, [auth, startExport])

  const onSendCode = useCallback(async () => {
    setSheetBusy(true); setSheetErr(null)
    const r = await requestCode(email.trim()).catch(() => null)
    setSheetBusy(false)
    if (!r) { setSheetErr('Network hiccup — try again.'); return }
    if (!r.ok) { setSheetErr(r.data?.detail || 'Could not send the code.'); return }
    setSheet('code')
  }, [email])

  const onSubmitCode = useCallback(async (code) => {
    setSheetBusy(true); setSheetErr(null)
    const r = await submitCode(email.trim(), code).catch(() => null)
    setSheetBusy(false)
    if (!r) { setSheetErr('Network hiccup — try again.'); return }
    if (!r.ok) { setSheetErr(r.data?.detail || 'That code didn’t match — check the email.'); return }
    track('signin_code')
    setAuthBoth({ session: r.data.session, email: r.data.email, credits: r.data.credits })
    startExport()
  }, [email, setAuthBoth, startExport])

  const reset = useCallback(() => {
    clearInterval(pollRef.current)
    setVideo(null); setJob(null); setUp(null)
    setRegions([]); setSelected(new Set()); setPreview(null); setDone(null); setSheet(null)
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
            <div className="cr-avatar" aria-hidden>{(auth?.email || 'M')[0].toUpperCase()}</div>
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
          preview={preview} video={video} selectedLabels={selectedLabels}
          onBack={() => setScreen('mark')} onSave={onSave}
        />
      )}
      {screen === 'done' && done && (
        <Done downloadUrl={done.downloadUrl} credits={done.credits} onAgain={reset} />
      )}
      {sheet && (
        <SignInSheet
          step={sheet} email={email} setEmail={setEmail}
          onSendCode={onSendCode} onSubmitCode={onSubmitCode}
          onClose={() => sheet !== 'saving' && setSheet(null)}
          savePct={savePct} error={sheetErr} busy={sheetBusy}
        />
      )}
      {toast && <div className="cr-toast" role="status">{toast}</div>}
    </div>
  )
}
