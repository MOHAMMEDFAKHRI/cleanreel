// Talks to the existing FastAPI backend on Render (same endpoints as web/index.html).
// window.API_BASE comes from /config.js at the site root; fallback for local dev.
export const API = (typeof window !== 'undefined' && window.API_BASE) || 'https://cleanreel.onrender.com'

/** Fire-and-forget server wake (Render naps between visits). */
export function wake() {
  try { fetch(API + '/api/health', { cache: 'no-store' }).catch(() => {}) } catch { /* noop */ }
}

/** Predictive GPU pre-warm once a video is in (same trick as the live UI). */
export function prewarm(task) {
  try { fetch(API + '/api/prewarm?task=' + encodeURIComponent(task), { method: 'POST', keepalive: true }).catch(() => {}) } catch { /* noop */ }
}

/**
 * Upload with progress via XHR (fetch has no upload progress).
 * Resolves { ok, status, data } — data = { file_id, width, height, seconds } on success.
 */
export function upload(file, { intent, onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const fd = new FormData()
    fd.append('file', file)
    const xhr = new XMLHttpRequest()
    xhr.open('POST', API + '/api/upload' + (intent ? `?intent=${intent}` : ''))
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      let data = null
      try { data = JSON.parse(xhr.responseText) } catch { /* non-JSON error body */ }
      resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, data })
    }
    xhr.onerror = () => reject(new Error('network'))
    xhr.send(fd)
  })
}

/** Region metadata for tap-to-select (CLE-44 phase b). Server caches per file. */
export function getRegions(fileId) {
  return fetch(`${API}/api/regions/${fileId}?targets=marks,face,plate`, { method: 'POST' })
    .then(r => r.ok ? r.json() : null).catch(() => null)
}

/** The canvas still every bbox is anchored to. */
export const frameUrl = (fileId) => `${API}/api/frame/${fileId}`

/** Free preview render. body: {file_id, task, mode:'preview', auto, boxes, owns_rights} */
export async function createJob(body) {
  const r = await fetch(API + '/api/jobs', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return { ok: r.ok, status: r.status, data: await r.json().catch(() => null) }
}

export function jobStatus(id) {
  return fetch(`${API}/api/jobs/${id}`, { cache: 'no-store' }).then(r => r.json())
}

/** result_url / before_url from job status are API-relative. */
export const absUrl = (u) => (u && u.startsWith('/') ? API + u : u)
