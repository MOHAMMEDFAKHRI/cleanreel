# CleanReel — turning on payments (credit packs)

The code is already built and **degrades gracefully**: you can push and deploy now,
and the site keeps working (previews are free; Export asks users to sign in; Buy
shows "being set up"). Payments switch on the moment you add the env vars below.

Model: **email magic-link sign-in → buy one-time credit packs → 1 credit = 1 export.**
Free previews stay free and need no account.

---

## Step 0 — Push the code
Double-click `push_to_github.bat`. Render redeploys the API, Netlify the site.
(Nothing breaks; payments just aren't active yet.)

## Step 1 — Neon (database that stores credit balances)  ~3 min
1. Go to **neon.tech** → sign up (free) → **Create project** (any name/region).
2. On the project dashboard, copy the **connection string** (the "Pooled connection",
   starts with `postgresql://…`). 
3. You'll paste it into Render as `DATABASE_URL` in Step 4.

## Step 2 — Resend (sends the sign-in emails)  ~3 min
1. Go to **resend.com** → sign up → **API Keys → Create** → copy the key (`re_…`).
2. Sending domain:
   - **Fastest to test:** leave `MAIL_FROM` unset — it defaults to Resend's
     `onboarding@resend.dev`, which can email **your own** address (fine for your own testing).
   - **For real users:** Resend → **Domains → Add** `cleanreel.app`, add the DNS
     records it shows (in Netlify DNS — I can do that with you), then set
     `MAIL_FROM = CleanReel <login@cleanreel.app>`.

## Step 3 — Stripe (takes the money)  ~6 min — stay in TEST mode first
1. Go to **stripe.com** → sign up. Keep the **Test mode** toggle ON (top right).
2. **Developers → API keys** → copy the **Secret key** (`sk_test_…`).
3. **Developers → Webhooks → Add endpoint**:
   - Endpoint URL: `https://cleanreel.onrender.com/api/stripe/webhook`
   - "Select events" → add **`checkout.session.completed`** → Add endpoint.
   - Click the new endpoint → **reveal Signing secret** (`whsec_…`) → copy it.
4. (No products to create — the packs are defined in code and priced at checkout.)

## Step 4 — Put the keys into Render  ~3 min
Render → your **cleanreel** service → **Environment** → add these, then **Save**
(it redeploys automatically):

| Key | Value |
|---|---|
| `DATABASE_URL` | the Neon pooled connection string |
| `APP_SECRET` | any long random string (e.g. a password-manager 40-char) |
| `RESEND_API_KEY` | the `re_…` key |
| `MAIL_FROM` | `CleanReel <login@cleanreel.app>` *(only if you verified the domain; else skip)* |
| `STRIPE_SECRET_KEY` | the `sk_test_…` key |
| `STRIPE_WEBHOOK_SECRET` | the `whsec_…` key |
| `SITE_URL` | `https://cleanreel.app` |

## Step 5 — Test the whole loop (Stripe TEST mode)
1. On cleanreel.app: enter your email → **Email me a sign-in link** → click the link.
2. You're signed in and see your credit balance (starts at a couple free credits).
3. **Buy credits** → pick a pack → Stripe Checkout → pay with test card
   **4242 4242 4242 4242**, any future expiry, any CVC/ZIP.
4. Back on the site your balance jumps by the pack amount. Upload → Export → it spends 1 credit.

## Step 6 — Go live
When the test loop works: in Stripe flip **Test mode OFF**, copy the **live**
`sk_live_…` key and make a **live** webhook (same URL + event), then update
`STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` on Render with the live values.

---

### Adjusting prices / packs
Edit `PACKS` in `backend/accounts.py` (credits + amount in US cents), push. e.g.
`"small": {"credits": 25, "amount": 600, "label": "25 exports"}` = $6 for 25.

### Notes
- I can't type your card or API keys (security) — those steps are yours. I can walk
  you click-by-click through any screen, and I'll add the Resend DNS records in Netlify.
- New sign-ins get `FREE_SIGNUP_CREDITS` (default 2) free exports; change via that env var.
