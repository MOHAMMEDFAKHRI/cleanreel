import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Moon, Sun } from 'lucide-react'
import Home from './screens/Home.jsx'
import Analyzing from './screens/Analyzing.jsx'
import Mark from './screens/Mark.jsx'
import Working from './screens/Working.jsx'
import PreviewScreen from './screens/PreviewScreen.jsx'
import SignInSheet from './screens/SignInSheet.jsx'
import Done from './screens/Done.jsx'
import EnhanceScreen from './screens/EnhanceScreen.jsx'
import ReframeScreen from './screens/ReframeScreen.jsx'
import BlurScreen from './screens/BlurScreen.jsx'
import CaptionsScreen, { LOOKS } from './screens/CaptionsScreen.jsx'
import ReelPlanScreen from './screens/ReelPlanScreen.jsx'
import EndCardScreen from './screens/EndCardScreen.jsx'
import FineTune from './screens/FineTune.jsx'
import { TASK_META } from './taskMeta.js'
import { upload, wake, prewarm, getRegions, createJob, jobStatus, absUrl, caps, tierOf } from './api.js'
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
  const [upErr, setUpErr] = useState(null)   // sticky rejection: { message, options: [{job,label}] }
  const lastFileRef = useRef(null)           // the rejected File — kept for one-tap retry on a bigger tier
  const [job, setJob] = useState(null)
  const [video, setVideo] = useState(null)
  const jobRef = useRef(null)
  const [regions, setRegions] = useState([])
  const [selected, setSelected] = useState(new Set())
  const [workPct, setWorkPct] = useState(0)
  const [preview, setPreview] = useState(null)
  const [savePct, setSavePct] = useState(0)
  const [done, setDone] = useState(null)           // { downloadUrl, credits }
  const [enhanceOpts, setEnhanceOpts] = useState({ denoise: true, sharpen: true, strength: 0.6, upscale: false })
  const [reframeOpts, setReframeOpts] = useState({ ratio: '9:16', fit: 'crop', focus: null })
  const [blurOpts, setBlurOpts] = useState({ faces: true, plates: true, style: 'blur' })
  const [capOpts, setCapOpts] = useState({ look: 'bold' })
  const [reelOpts, setReelOpts] = useState({ crop: true, captions: true, look: 'bold', endCard: false, cta: '', cardTheme: 'dark', trimStart: null, trimEnd: null, cleanAudio: false })
  const [packs, setPacks] = useState(null)
  const [email, setEmail] = useState(auth?.email || '')
  const [sheetErr, setSheetErr] = useState(null)
  const [sheetBusy, setSheetBusy] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const sheetIntent = useRef('export')   // 'export' = sign-in leads to saving; 'signin' = just sign in
  const pollRef = useRef(null)

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    localStorage.setItem('cr-theme', dark ? 'dark' : 'light')
  }, [dark])

  useEffect(() => {
    wake(); initAnalytics(loadAuth()?.email)
    // hash handling so this UI can live at the site root:
    //   #login=TOKEN  magic-link target · #paid Stripe return · #tool=X SEO deep links
    const h = window.location.hash
    const API = window.API_BASE || 'https://cleanreel.onrender.com'
    if (h.startsWith('#login=')) {
      const token = h.slice(7); history.replaceState(null, '', window.location.pathname)
      fetch(API + '/api/auth/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) })
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d) { const a = { session: d.session, email: d.email, credits: d.credits }; setAuth(a); saveAuth(a); identify(d.email); showToast('Signed in as ' + d.email) } else showToast('That sign-in link expired — request a new one') })
        .catch(() => {})
    } else if (h === '#paid') {
      history.replaceState(null, '', window.location.pathname)
      const a = loadAuth()
      if (a) me(a).then(info => { if (info && !info.expired) { const na = { ...a, credits: info.credits }; setAuth(na); saveAuth(na); showToast('Thanks — credits added!') } }).catch(() => {})
    } else if (h.startsWith('#tool=')) {
      const map = { remove: 'remove', erase: 'erase', enhance: 'enhance', reframe: 'reframe', blur: 'blur', captions: 'caption', reel: 'reel' }
      const t = map[h.slice(6)]
      history.replaceState(null, '', window.location.pathname)
      if (t) { setJob(t); jobRef.current = t; showToast('Add your video — we’ll take it from there') }
    }
    return () => clearInterval(pollRef.current)
  }, [])

  const showToast = useCallback((msg) => {
    setToastMsg(msg)
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToastMsg(null), 2400)
  }, [])

  const setAuthBoth = useCallback((a) => { setAuth(a); saveAuth(a); if (a?.email) identify(a.email) }, [])

  const setHint = useCallback((h) => { jobRef.current = h; setJob(h) }, [])

  const startUpload = useCallback(async (file) => {
    if (!file) return
    if (!file.type.startsWith('video/')) { showToast('That doesn’t look like a video'); return }
    const jobHint = jobRef.current
    setUpErr(null)
    lastFileRef.current = file
    setUp({ name: file.name, pct: 0 })
    let res = null
    for (let attempt = 0; attempt < 2 && !res; attempt++) {
      try {
        res = await upload(file, { intent: jobHint || 'clean', onProgress: (pct) => setUp({ name: file.name, pct }) })
      } catch {
        if (attempt === 0) { showToast('Hiccup while uploading — retrying…'); setUp({ name: file.name, pct: 0 }) }
      }
    }
    if (!res) { setUp(null); track('upload_failed', { reason: 'network' }); setUpErr({ message: 'Could not reach the server — check your connection and try again.', options: [] }); return }
    if (!res.ok) {
      setUp(null); track('upload_failed', { reason: 'rejected', code: res.status })
      const detail = res.data?.detail || 'That upload didn’t work — try again.'
      // a clip too big/long for THIS job may fit a roomier tier — offer the switch
      const secs = res.status === 413 ? (detail.match(/is (\d+)s/) ? Number(detail.match(/is (\d+)s/)[1]) : null) : null
      const fits = (t) => file.size <= (caps[t].mb << 20) && (secs == null || secs <= caps[t].seconds)
      const options = []
      const tier = tierOf(jobHint)
      if (res.status === 413 && tier === 'gpu' && fits('cpu'))
        options.push({ job: 'caption', label: `Caption it instead — up to ${Math.round(caps.cpu.seconds / 60)} min` })
      if (res.status === 413 && tier !== 'reel' && fits('reel'))
        options.push({ job: 'reel', label: `Make a Reel with it — up to ${Math.round(caps.reel.seconds / 60)} min` })
      setUpErr({ message: detail, options })
      return
    }
    const d = res.data
    track('upload_ok', { seconds: d.seconds, w: d.width, h: d.height })
    prewarm(jobHint === 'erase' ? 'erase' : jobHint === 'enhance' ? 'enhance' : 'remove')
    setVideo({ fileId: d.file_id, width: d.width, height: d.height, seconds: d.seconds })
    setUp(null); setPreview(null); setDone(null)
    setScreen('analyzing')

    if (jobHint === 'reel') {            // reels don't need watermark detection
      setRegions([]); setSelected(new Set())
      setTimeout(() => setScreen('reelplan'), MIN_ANALYZE_MS)
      return
    }
    const t0 = Date.now()
    const resp = await getRegions(d.file_id)
    const wait = Math.max(0, MIN_ANALYZE_MS - (Date.now() - t0))
    setTimeout(() => {
      const regs = resp?.regions || []
      track('regions_found', { n: regs.length, preselected: regs.filter(r => r.preselected).length, type: resp?.watermark_type })
      setRegions(regs)
      setSelected(new Set(regs.filter(r => r.preselected).map(r => r.id)))
      const dest = { enhance: 'enhance', reframe: 'reframe', blur: 'blur', caption: 'captions', reel: 'reelplan' }[jobHint] || 'mark'
      setScreen(dest)
      if (dest === 'mark') {
        const found = regs.filter(r => r.preselected)
        if (found.length) {
          showToast(found.length > 1
            ? `Found ${found.length} marks — already selected for you`
            : `Found a ${found[0].kind === 'logo' ? 'logo' : 'watermark'} — already selected for you`)
        }
      } else if (dest === 'blur') {
        const n = regs.filter(r => r.kind === 'face' || r.kind === 'plate').length
        if (n) showToast(`Found ${n} to hide — all hidden by default`)
      }
    }, wait)
  }, [showToast])

  // which task is active = which decision screen launched the job
  const activeTask = { erase: 'erase', enhance: 'enhance', reframe: 'reframe', blur: 'blur', caption: 'captions', reel: 'reel' }[job] || 'remove'

  const buildBody = useCallback((mode) => {
    const base = { file_id: video.fileId, mode, owns_rights: true }
    if (activeTask === 'enhance') {
      return { ...base, task: 'enhance', scale: enhanceOpts.upscale ? 2.0 : 1.0,
               denoise: enhanceOpts.denoise, strength: enhanceOpts.sharpen ? enhanceOpts.strength : 0 }
    }
    if (activeTask === 'reframe') {
      return { ...base, task: 'reframe', ratio: reframeOpts.ratio, fit: reframeOpts.fit, focus: reframeOpts.focus }
    }
    if (activeTask === 'blur') {
      const targets = [blurOpts.faces && 'face', blurOpts.plates && 'plate'].filter(Boolean)
      return { ...base, task: 'blur', targets: targets.length ? targets : ['face'], style: blurOpts.style, strength: 0.6 }
    }
    if (activeTask === 'captions') {
      const look = LOOKS.find(l => l.id === capOpts.look) || LOOKS[0]
      return { ...base, task: 'captions', ...look.params }
    }
    if (activeTask === 'reel') {
      const gcd = (a, b) => b ? gcd(b, a % b) : a
      const g = gcd(video.width, video.height)
      const look = LOOKS.find(l => l.id === reelOpts.look) || LOOKS[0]
      return {
        ...base, task: 'reel', fit: 'crop',
        ratio: reelOpts.crop ? '9:16' : `${video.width / g}:${video.height / g}`,
        captions: reelOpts.captions, ...(reelOpts.captions ? look.params : {}),
        cta: reelOpts.endCard && reelOpts.cta.trim() ? reelOpts.cta.trim() : null,
        card_theme: reelOpts.cardTheme,
        trim_start: reelOpts.trimStart, trim_end: reelOpts.trimEnd,
        clean_audio: reelOpts.cleanAudio,
      }
    }
    const chosen = regions.filter(r => selected.has(r.id))
    const anyMark = chosen.some(r => r.kind === 'watermark' || r.kind === 'logo')
    const extra = chosen.filter(r => !(r.kind === 'watermark' || r.kind === 'logo'))
    if (activeTask === 'erase') {
      // erase is its own engine path (motion probe + temporal ProPainter);
      // it must NOT masquerade as remove (CLE-32 find)
      return { ...base, task: 'erase', boxes: extra.length ? extra.map(r => r.bbox) : null }
    }
    return { ...base, task: 'remove', auto: anyMark, boxes: extra.length ? extra.map(r => r.bbox) : null }
  }, [activeTask, video, enhanceOpts, reframeOpts, blurOpts, capOpts, reelOpts, regions, selected])

  /** Shared job runner for preview + export. */
  const runJob = useCallback(async (mode) => {
    if (!video) return
    if (activeTask === 'remove' && !regions.filter(r => selected.has(r.id)).length) return
    const body = buildBody(mode)
    const headers = mode === 'export' ? authHeaders(auth) : {}
    const r = await fetch((window.API_BASE || 'https://cleanreel.onrender.com') + '/api/jobs', {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify(body),
    })
    return { ok: r.ok, status: r.status, data: await r.json().catch(() => null) }
  }, [activeTask, buildBody, regions, selected, video, auth])

  const decisionScreen = activeTask === 'remove' ? 'mark' : activeTask === 'captions' ? 'captions' : activeTask === 'reel' ? 'reelplan' : activeTask

  const startPreview = useCallback(async () => {
    track('preview_click', { task: activeTask, selections: selected.size })
    setWorkPct(0.02); setScreen('working')
    const res = await runJob('preview')
    if (!res?.ok || !res.data?.job_id) {
      setScreen(decisionScreen); showToast(res?.data?.detail || 'Could not start the preview — try again')
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
          track('job_done', { mode: 'preview', task: activeTask })
          setPreview({ resultUrl: absUrl(s.result_url), beforeUrl: absUrl(s.before_url),
                       srtUrl: absUrl(s.srt_url), confidence: s.qc?.confidence ?? null })
          setScreen('preview')
        } else if (s.status === 'failed') {
          clearInterval(pollRef.current)
          track('job_error', { mode: 'preview', task: activeTask })
          setScreen(decisionScreen); showToast(s.message || 'That render failed — try again')
        }
      } catch { /* transient */ }
    }, 1500)
  }, [runJob, activeTask, decisionScreen, selected, showToast])

  const startExport = useCallback(async () => {
    setSheet('saving'); setSavePct(0.02)
    const res = await runJob('export')
    if (!res?.ok) {
      if (res?.status === 401) { setAuthBoth(null); setSheetErr(null); setSheet('email'); track('export_blocked_signin'); return }
      if (res?.status === 402) {
        track('export_blocked_credits')
        try {
          const d = await fetch((window.API_BASE || 'https://cleanreel.onrender.com') + '/api/packs').then(r => r.json())
          setPacks(d?.configured ? Object.entries(d.packs || {}) : [])
        } catch { setPacks([]) }
        setSheet('credits'); return
      }
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
    else { sheetIntent.current = 'export'; setSheetErr(null); setSheet('email') }
  }, [auth, startExport])

  const onBuyPack = useCallback(async (packId) => {
    track('checkout_started', { pack: packId })
    try {
      const r = await fetch((window.API_BASE || 'https://cleanreel.onrender.com') + '/api/checkout', {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders(auth) },
        body: JSON.stringify({ pack: packId }),
      })
      const d = await r.json().catch(() => null)
      if (r.ok && d?.url) window.location.href = d.url
      else showToast(d?.detail || 'Could not start checkout')
    } catch { showToast('Network hiccup — try again') }
  }, [auth, showToast])

  const openPacks = useCallback(async () => {
    setMenuOpen(false)
    setPacks(null); setSheet('credits')
    try {
      const d = await fetch((window.API_BASE || 'https://cleanreel.onrender.com') + '/api/packs').then(r => r.json())
      setPacks(d?.configured ? Object.entries(d.packs || {}) : [])
    } catch { setPacks([]) }
  }, [])

  const signOut = useCallback(() => { setAuthBoth(null); setMenuOpen(false); showToast('Signed out') }, [setAuthBoth, showToast])

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
    if (sheetIntent.current === 'export') startExport()
    else { setSheet(null); showToast('Signed in — ' + r.data.email) }
  }, [email, setAuthBoth, startExport, showToast])

  const reset = useCallback(() => {
    clearInterval(pollRef.current)
    jobRef.current = null
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
            <div className="cr-avatarwrap">
              <button className="cr-avatar" aria-label="Account" onClick={() => setMenuOpen(o => !o)}>
                {auth?.email ? auth.email[0].toUpperCase() : '?'}
              </button>
              {menuOpen && (
                <>
                  <div className="cr-menuveil" onClick={() => setMenuOpen(false)} />
                  <div className="cr-menu">
                    {auth?.session ? (
                      <>
                        <div className="who">{auth.email}</div>
                        <div className="creds">{auth.credits ?? '…'} export credit{auth.credits === 1 ? '' : 's'}</div>
                        <a href="/account.html">My work</a>
                        <button onClick={openPacks}>Buy credits</button>
                        <button className="danger" onClick={signOut}>Sign out</button>
                      </>
                    ) : (
                      <>
                        <div className="who">Not signed in</div>
                        <div className="creds">2 free exports when you do — no card needed</div>
                        <button onClick={() => { sheetIntent.current = 'signin'; setSheetErr(null); setMenuOpen(false); setSheet('email') }}>
                          Sign in with email
                        </button>
                      </>
                    )}
                  </div>
                </>
              )}
            </div>
          </header>
          <Home
            uploading={up} error={upErr} onFile={startUpload} hint={job} onHint={setHint}
            onRetry={(j) => { setHint(j); startUpload(lastFileRef.current) }}
          />
        </>
      )}
      {screen === 'analyzing' && <Analyzing />}
      {screen === 'mark' && video && (
        <Mark
          video={video} regions={regions} selected={selected} setSelected={setSelected}
          onAddRegion={(r) => { setRegions(rs => [...rs, r]); setSelected(sel => new Set(sel).add(r.id)) }}
          onMergeSpots={(ids, r) => {
            setRegions(rs => [...rs.filter(x => !ids.includes(x.id)), r])
            setSelected(sel => { const n = new Set([...sel].filter(id => !ids.includes(id))); n.add(r.id); return n })
          }}
          onBack={reset} onPreview={startPreview} showToast={showToast}
        />
      )}
      {screen === 'enhance' && video && (
        <EnhanceScreen video={video} opts={enhanceOpts} setOpts={setEnhanceOpts} onBack={reset} onPreview={startPreview} />
      )}
      {screen === 'reframe' && video && (
        <ReframeScreen video={video} opts={reframeOpts} setOpts={setReframeOpts} onBack={reset} onPreview={startPreview} />
      )}
      {screen === 'blur' && video && (
        <BlurScreen video={video} regions={regions} opts={blurOpts} setOpts={setBlurOpts} onBack={reset} onPreview={startPreview} />
      )}
      {screen === 'captions' && video && (
        <CaptionsScreen opts={capOpts} setOpts={setCapOpts} onBack={reset} onPreview={startPreview} />
      )}
      {screen === 'reelplan' && video && (
        <ReelPlanScreen opts={reelOpts} setOpts={setReelOpts} onBack={reset}
          onNext={() => reelOpts.endCard ? setScreen('endcard') : startPreview()} />
      )}
      {screen === 'endcard' && video && (
        <EndCardScreen opts={reelOpts} setOpts={setReelOpts}
          onBack={() => setScreen('reelplan')} onNext={startPreview} />
      )}
      {screen === 'finetune' && video && (
        <FineTune video={video} opts={reelOpts} setOpts={setReelOpts}
          onBack={() => setScreen('preview')} onRebuild={startPreview} />
      )}
      {screen === 'working' && (
        <Working pct={workPct} title={TASK_META[activeTask].working.title} steps={TASK_META[activeTask].working.steps} />
      )}
      {screen === 'preview' && preview && (
        <PreviewScreen
          preview={preview} video={video}
          badgeWord={TASK_META[activeTask].badge(selectedLabels.length)}
          chips={TASK_META[activeTask].chips(selectedLabels,
            activeTask === 'enhance' ? enhanceOpts : activeTask === 'reframe' ? reframeOpts
            : activeTask === 'blur' ? blurOpts : capOpts)}
          showBefore={TASK_META[activeTask].showBefore}
          onBack={() => setScreen(decisionScreen)} onSave={onSave}
          onFineTune={activeTask === 'reel' ? () => setScreen('finetune') : null}
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
          packs={packs} onBuyPack={onBuyPack}
        />
      )}
      {toast && <div className="cr-toast" role="status">{toast}</div>}
    </div>
  )
}
