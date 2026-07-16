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
