// PostHog — same project key + settings as the live UI (web/index.html), so
// the funnel stays one funnel. Every event from the redesign carries ui:'new'.
const PH_KEY = 'phc_nkxXK3RjHVEvNKCqiKFuXsNecNyDpr2sU2YgTUFNPLL2'
const PH_HOST = 'https://us.i.posthog.com'

export function initAnalytics(email) {
  try {
    const s = document.createElement('script')
    s.src = PH_HOST + '/static/array.js'; s.async = true
    s.onload = () => {
      try {
        window.posthog.init(PH_KEY, {
          api_host: PH_HOST, persistence: 'localStorage',
          person_profiles: 'identified_only', autocapture: false,
        })
        if (email) window.posthog.identify(email, { email })
        track('pageview_new_ui')
      } catch { /* analytics never breaks the app */ }
    }
    document.head.appendChild(s)
  } catch { /* noop */ }
}

export function track(ev, props) {
  try { window.posthog?.capture?.(ev, { ui: 'new', ...(props || {}) }) } catch { /* noop */ }
}

export function identify(email) {
  try { if (email) window.posthog?.identify?.(email, { email }) } catch { /* noop */ }
}
