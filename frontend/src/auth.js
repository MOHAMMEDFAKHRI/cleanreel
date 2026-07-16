import { API } from './api.js'

// Same localStorage key as the classic UI — sign in once, signed in everywhere.
const KEY = 'cr_auth'

export function loadAuth() {
  try { return JSON.parse(localStorage.getItem(KEY)) || null } catch { return null }
}
export function saveAuth(a) {
  if (a) localStorage.setItem(KEY, JSON.stringify(a))
  else localStorage.removeItem(KEY)
}
export function authHeaders(a) {
  return a?.session ? { Authorization: 'Bearer ' + a.session } : {}
}

export async function requestCode(email) {
  const r = await fetch(API + '/api/auth/request', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
  return { ok: r.ok, data: await r.json().catch(() => null) }
}

export async function submitCode(email, code) {
  const r = await fetch(API + '/api/auth/code', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, code }),
  })
  return { ok: r.ok, data: await r.json().catch(() => null) }   // {session,email,credits}
}

export async function me(a) {
  const r = await fetch(API + '/api/me', { headers: authHeaders(a) })
  if (r.status === 401) return { expired: true }
  return r.ok ? await r.json() : null                            // {email,credits}
}
