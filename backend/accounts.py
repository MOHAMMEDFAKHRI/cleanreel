"""
accounts.py — user accounts, magic-link auth, credit balances, and credit packs.

Everything degrades gracefully so the site keeps working before you finish the
external setup:
  * No DATABASE_URL  -> balances live in memory (reset on restart; fine for testing).
  * No RESEND_API_KEY-> the magic link is returned in the API response / logged
                        instead of emailed (so you can still test sign-in).
  * No STRIPE_* keys -> the buy-credits endpoints return a friendly "not configured".

Env vars (set these on Render once the accounts exist):
  DATABASE_URL          Neon Postgres connection string
  APP_SECRET            any long random string (signs login/session tokens)
  RESEND_API_KEY        Resend API key (for the magic-link email)
  MAIL_FROM             e.g. "CleanReel <login@cleanreel.app>"
  SITE_URL              https://cleanreel.app
  FREE_SIGNUP_CREDITS   free export credits granted on first sign-in (default 2)
"""
import os, time, hmac, json, base64, hashlib, urllib.request, urllib.error

APP_SECRET   = os.environ.get("APP_SECRET", "dev-insecure-change-me")
SITE_URL     = os.environ.get("SITE_URL", "https://cleanreel.app")
RESEND_KEY   = os.environ.get("RESEND_API_KEY")
MAIL_FROM    = os.environ.get("MAIL_FROM", "CleanReel <onboarding@resend.dev>")
DATABASE_URL = os.environ.get("DATABASE_URL")
FREE_SIGNUP_CREDITS = int(os.environ.get("FREE_SIGNUP_CREDITS", "2"))

# One-time credit packs. Amounts are in the smallest currency unit (US cents).
# Change freely; nothing needs to be pre-created in Stripe.
PACKS = {
    "small":  {"credits": 25,  "amount": 600,  "label": "25 exports"},   # $6
    "medium": {"credits": 60,  "amount": 1200, "label": "60 exports"},   # $12
    "large":  {"credits": 200, "amount": 3000, "label": "200 exports"},  # $30
}

# --------------------------------------------------------------------------- #
# Signed tokens (stateless; no tokens table needed)
# --------------------------------------------------------------------------- #
def _b64e(b): return base64.urlsafe_b64encode(b).decode().rstrip("=")
def _b64d(s): return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def sign_token(email: str, purpose: str, ttl: int) -> str:
    body = {"e": email.lower().strip(), "p": purpose, "x": int(time.time()) + ttl}
    raw = _b64e(json.dumps(body, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(APP_SECRET.encode(), raw.encode(), hashlib.sha256).digest())
    return f"{raw}.{sig}"

def verify_token(token: str, purpose: str) -> str | None:
    try:
        raw, sig = token.split(".", 1)
        good = _b64e(hmac.new(APP_SECRET.encode(), raw.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        body = json.loads(_b64d(raw))
        if body.get("p") != purpose or body.get("x", 0) < int(time.time()):
            return None
        return body.get("e")
    except Exception:
        return None

# --------------------------------------------------------------------------- #
# Storage: Postgres if DATABASE_URL, else in-memory
# --------------------------------------------------------------------------- #
_mem_users: dict[str, int] = {}
_mem_events: set[str] = set()
_pg = None

def _conn():
    global _pg
    import psycopg
    if _pg is None or _pg.closed:
        _pg = psycopg.connect(DATABASE_URL, autocommit=True)
    return _pg

def init_db():
    if not DATABASE_URL:
        print("[accounts] no DATABASE_URL -> using in-memory balances (reset on restart)")
        return
    try:
        with _conn().cursor() as c:
            c.execute("CREATE TABLE IF NOT EXISTS users ("
                      "email TEXT PRIMARY KEY, credits INT NOT NULL DEFAULT 0,"
                      "created TIMESTAMPTZ DEFAULT now())")
            c.execute("CREATE TABLE IF NOT EXISTS stripe_events ("
                      "id TEXT PRIMARY KEY, seen TIMESTAMPTZ DEFAULT now())")
        print("[accounts] Postgres ready")
    except Exception as e:
        print("[accounts] DB init failed:", e)

def ensure_user(email: str) -> int:
    """Create the user on first sign-in (with the free grant); return credits."""
    email = email.lower().strip()
    if not DATABASE_URL:
        if email not in _mem_users:
            _mem_users[email] = FREE_SIGNUP_CREDITS
        return _mem_users[email]
    with _conn().cursor() as c:
        c.execute("INSERT INTO users(email,credits) VALUES(%s,%s) "
                  "ON CONFLICT(email) DO NOTHING", (email, FREE_SIGNUP_CREDITS))
        c.execute("SELECT credits FROM users WHERE email=%s", (email,))
        return int(c.fetchone()[0])

def get_credits(email: str) -> int:
    email = email.lower().strip()
    if not DATABASE_URL:
        return _mem_users.get(email, 0)
    with _conn().cursor() as c:
        c.execute("SELECT credits FROM users WHERE email=%s", (email,))
        r = c.fetchone(); return int(r[0]) if r else 0

def add_credits(email: str, n: int) -> int:
    email = email.lower().strip()
    if not DATABASE_URL:
        _mem_users[email] = _mem_users.get(email, 0) + n
        return _mem_users[email]
    with _conn().cursor() as c:
        c.execute("INSERT INTO users(email,credits) VALUES(%s,%s) "
                  "ON CONFLICT(email) DO UPDATE SET credits=users.credits+%s "
                  "RETURNING credits", (email, n, n))
        return int(c.fetchone()[0])

def use_credit(email: str) -> bool:
    """Atomically spend one credit. Returns False if the balance is 0."""
    email = email.lower().strip()
    if not DATABASE_URL:
        if _mem_users.get(email, 0) <= 0:
            return False
        _mem_users[email] -= 1; return True
    with _conn().cursor() as c:
        c.execute("UPDATE users SET credits=credits-1 "
                  "WHERE email=%s AND credits>0 RETURNING credits", (email,))
        return c.fetchone() is not None

def event_is_new(event_id: str) -> bool:
    """Idempotency for Stripe webhooks: True the first time we see an event id."""
    if not DATABASE_URL:
        if event_id in _mem_events:
            return False
        _mem_events.add(event_id); return True
    with _conn().cursor() as c:
        c.execute("INSERT INTO stripe_events(id) VALUES(%s) "
                  "ON CONFLICT DO NOTHING RETURNING id", (event_id,))
        return c.fetchone() is not None

# --------------------------------------------------------------------------- #
# Magic-link email (Resend HTTP API; stdlib only)
# --------------------------------------------------------------------------- #
def send_magic_link(email: str) -> str | None:
    """Email a one-click login link. Returns the link if it could NOT be emailed
    (no RESEND_API_KEY) so the caller can surface it for testing; else None."""
    token = sign_token(email, "login", ttl=900)          # 15 minutes
    link = f"{SITE_URL}/#login={token}"
    if not RESEND_KEY:
        print("[accounts] RESEND_API_KEY unset — magic link:", link)
        return link
    html = (f'<p>Click to sign in to CleanReel:</p>'
            f'<p><a href="{link}">Sign in to CleanReel</a></p>'
            f'<p>This link expires in 15 minutes. If you didn\'t request it, ignore this email.</p>')
    payload = json.dumps({"from": MAIL_FROM, "to": [email],
                          "subject": "Your CleanReel sign-in link", "html": html}).encode()
    req = urllib.request.Request("https://api.resend.com/emails", data=payload,
                                 headers={"Authorization": f"Bearer {RESEND_KEY}",
                                          "Content-Type": "application/json",
                                          "Accept": "application/json",
                                          # A real User-Agent avoids Cloudflare error 1010
                                          # (it bans python-urllib's default UA in front of Resend).
                                          "User-Agent": "CleanReel/1.0 (+https://cleanreel.app)"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return None
    except urllib.error.HTTPError as e:
        print("[accounts] Resend error:", e.read()[:200]); return None
    except Exception as e:
        print("[accounts] Resend send failed:", e); return None
