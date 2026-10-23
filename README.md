# ShortPulse — URL shortener and analytics platform

A URL shortener with rules, analytics and safety.

One link record at the centre: **C decides, Python manages, ML protects.**

## Live demo

**https://shortpulse.jainovation.xyz**

Create your own account on the site to try it. The site runs on Render's free plan: it sleeps after 15 minutes without visitors, so the first page can take up to a minute to load, and the data resets to the demo set whenever the service restarts. If the domain doesn't open on some network, the same app is also at the Render service's own `onrender.com` address.

Seeded links that show each rule (open them while signed out):

| Link | What happens |
|---|---|
| [/fest26](https://shortpulse.jainovation.xyz/fest26) | Normal redirect; its dashboard shows a click-spike alert |
| [/fest-info](https://shortpulse.jainovation.xyz/fest-info) | Landing page; time on page, scroll depth and "reading now" are measured |
| [/yt-talk](https://shortpulse.jainovation.xyz/yt-talk) | On a phone, tries to open the YouTube app |
| [/vip-preview](https://shortpulse.jainovation.xyz/vip-preview) | Password protected (password: `pulse2026`) |
| [/early-bird](https://shortpulse.jainovation.xyz/early-bird) | Expired: redirect stopped, analytics kept |
| [/first50](https://shortpulse.jainovation.xyz/first50) | Click limit reached |
| [/store](https://shortpulse.jainovation.xyz/store) | Paused by its owner |
| [/free-recharge](https://shortpulse.jainovation.xyz/free-recharge) | Disabled by an admin (phishing takedown) |

## How it's built

| Layer | What it does | Where |
|---|---|---|
| C core engine | Base62 codes, alias hash table, rule order (status → expiry → click limit → password), token-bucket rate limiter | `engine/` |
| Python web app | Flask APIs + SQLite, 2-step wizard, campaigns, tags, QR codes, CSV import, Visitor/User/Admin roles | `app/` |
| AI/ML safety layer | Random Forest bot filter, Logistic Regression URL checker, click-spike alerts | `app/ml.py`, `app/spikes.py`, `ml/` |
| Async click log | Redirect enqueues the click; a worker thread enriches, bot-filters and stores it | `app/clicklog.py` |
| App switching | YouTube / Spotify / Instagram links open the native app on phones | `app/deeplink.py`, `templates/redirect/app_switch.html` |
| QR & sharing studio | Colours, module and eye styles, logo, caption; PNG/SVG, poster, share targets | `app/qrstudio.py`, `static/js/studio.js` |
| Landing pages | Optional ShortPulse-hosted page before the destination ("link-in-bio" style) | `app/landing.py`, `templates/redirect/landing.html` |
| Engagement | Time on page, scroll depth, button choices, "reading now" | `app/engage.py`, `static/lp-beacon.js` |
| Smart paste | Ctrl+V or drag-and-drop anywhere to shorten; strips tracking parameters | `static/js/smartpaste.js` |

---

## Quick start on Windows (PowerShell)

You need **64-bit Python 3.11+** (3.12 recommended) and **64-bit gcc (MinGW-w64)**. Check what you have:

```powershell
py --list
gcc -dumpmachine     # must print x86_64-w64-mingw32; "mingw32" alone is 32-bit and won't work
```

Install whatever is missing (one time):

```powershell
winget install Python.Python.3.12
winget install MSYS2.MSYS2
C:\msys64\usr\bin\bash.exe -lc "pacman -S --needed --noconfirm mingw-w64-ucrt-x86_64-gcc"
$user = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("Path", "$user;C:\msys64\ucrt64\bin", "User")
```

Close and reopen PowerShell so the new PATH applies, then from the folder that contains `README.md`:

```powershell
.\windows.cmd setup
.\windows.cmd test
$env:ADMIN_PASSWORD = "admin12345"
$env:DEMO_PASSWORD = "demo12345"
.\windows.cmd run
```

Open http://127.0.0.1:5000 and sign in as `demo@shortpulse.local` / `demo12345` (user) or `admin@shortpulse.local` / `admin12345` (admin).

`windows.cmd` is a batch file, so PowerShell's script execution policy doesn't block it, and it calls `.venv\Scripts\python.exe` directly, so there's no venv activation step. `gunicorn` installs on Windows but only runs on Linux; locally the Flask server is used, and Render runs gunicorn.

## Quick start on Linux or macOS

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make build                      # compiles engine/libshortpulse.so (or .dylib) with gcc
ADMIN_PASSWORD=admin12345 DEMO_PASSWORD=demo12345 make run
```

```bash
make test     # C unit + concurrency tests, then 58 Python tests (no pytest needed)
make train    # retrain both models
jupyter notebook ml/train_models.ipynb   # needs: pip install -r requirements-dev.txt
```

---

## 5-minute demo script

The seeded data covers every edge case, so nothing has to be prepared live.

**Scenario 1: link to analytics (about 2 min)**
1. Sign in as the demo user. The dashboard opens with the click trace for the last 7 days. Point at the alert for `/fest26` (spike: about 40 clicks in the last hour against a typical rate under 1/hour).
2. *New link* → paste `http://paytm-secure-update.xyz/login/verify`. The warning appears while typing. Replace it with `https://en.wikipedia.org/wiki/URL_shortening`, alias `demo-day`, continue.
3. Step 2: set a click limit of 3, publish. Show the QR code.
4. Open the short link on a phone (or scan the QR). Back on the link page, the visit appears within seconds, tagged as a QR scan.
5. Open `/first50` → "reached its click limit". Open `/early-bird` → "expired". Both still show full analytics in *Links*.

**Scenario 2: bulk import to takedown (about 3 min)**
1. *Import* → *Download a sample file* → upload it. Every row is checked: duplicate alias, broken URL, reserved alias (`admin`), bad date, blocklisted domain, phishing-like URL.
2. Fix the duplicate alias and the date in the table, *Check again*, *Import valid rows*.
3. Sign in as admin → *Admin* → *All links* → disable one of the imported links. Open it in another tab: "disabled", immediately. Its stats are still on the owner's dashboard.
4. *Blocklist* → add `wikipedia.org` with "also disable existing links" to show a bulk takedown. Remove it again afterwards.

---

## Features added after Round 1

**Visitor IP addresses.** Every click stores the IP in two forms: the full address, erased automatically after `IP_RETENTION_DAYS` (default 30) and visible only to admins for abuse investigation, and a shortened form (`103.21.45.x`, IPv6 `/48`) kept for statistics and shown to link owners. Set `STORE_FULL_IP=0` to never store full addresses. `/privacy` explains this to visitors. Seeded demo clicks use documentation-only ranges (RFC 5737), never real addresses.

**Time on page and scroll depth.** Not done with an iframe wrapper, on purpose: most large sites (YouTube, Instagram, Google, banks) refuse to be framed, browsers never let a page read scroll position inside another site's frame, and showing other sites under your domain is what phishing pages do. Instead, a link owner turns on "measure engagement", ShortPulse appends `?lp_cid=<random id>` to the destination, and `/static/lp-beacon.js` on the owner's own site reports visible time and deepest scroll with `navigator.sendBeacon`. Results appear on the link page (median time, average depth, share past 75%). It only works on sites the owner controls, and ad blockers may block the beacon.

**Landing pages and live engagement.** No shortener can see inside a third-party website after redirecting, so time on page and scroll depth for *every* visit come from a landing page ShortPulse hosts: turn it on in the wizard or on the link's page, write a heading and message, pick a theme, and add up to 5 extra buttons. Every view then reports visible time, scroll depth and the button chosen, plus a heartbeat every 10 s, so the link page shows "N reading now", median and average time, a scroll-depth funnel and the share of visitors who pressed a button. The same beacon still works on the owner's own website. While a landing page is on, app switching is skipped. Seeded example: `/fest-info`.

**Showing full IP addresses to link owners.** Admins always see full IPs while they're kept. To show them to every link owner, set `OWNER_SEES_FULL_IP=1` (PowerShell: `$env:OWNER_SEES_FULL_IP = "1"` before `.\windows.cmd run`); the privacy page updates to say so. Keep it off on the public deployment: a shortener that gives full IPs to anyone who makes a link works as an "IP logger" for locating or harassing people.

**Opening the app on phones.** For YouTube (videos, Shorts, channels), Spotify (tracks, albums, playlists, artists, episodes, shows) and Instagram (profiles; posts on Android), phones get a small page that tries the app and falls back to the website after 1.6 s. Android uses `intent://` URLs with a built-in browser fallback; iPhones use the apps' URL schemes. Instagram posts on iPhone get a normal redirect, because there's no public scheme for them. Desktops, bots and links with the switch turned off get the normal 302. App URL schemes belong to the app makers and can change: **test on a real Android phone and iPhone before the demo**, including from inside WhatsApp and Instagram, whose in-app browsers sometimes block app switching.

**QR & sharing studio** (`/links/<id>/studio`). Live preview; colour presets with a contrast check that refuses light-on-dark and low-contrast codes; square, rounded or dot modules; square or softly rounded corner eyes; logo upload (re-encoded server-side; forces the highest error correction); caption; PNG up to 2048 px, SVG, copy-image, a printable poster, and share buttons (native share sheet on phones, WhatsApp, Telegram, X, LinkedIn, email). Every style combination is decoded with OpenCV in the tests. OpenCV stopped finding codes with eye corners rounder than about 0.3 module, so the "soft corners" option is deliberately gentle.

**Smart paste and instant copy.** Browsers don't let a page read the clipboard without the user acting, so nothing is read silently. Pressing Ctrl+V anywhere on a ShortPulse page (outside a text box), or dropping a link onto it, opens a quick-shorten panel. It picks the link out of pasted text, offers to remove tracking parameters (`utm_*`, `fbclid`, `gclid`, YouTube/Spotify `si`…), creates the link and copies it. High-risk URLs are sent to the full wizard, so the "review warnings before publishing" rule still holds. Copy buttons confirm with an animation; `N` opens the wizard, `/` focuses search, `C` copies on a link's page.

**Admin user management.** Admin → Users lists every account with role, status, links, clicks and last sign-in; **Manage** opens a page per user with their profile, stats, links, campaigns, recent visits (full IPs) and admin history. Admins can change a user's email and role, suspend or reactivate them (suspension ends their sessions immediately and can also disable their links), set a new password (passwords are one-way hashes, so nobody can read them), disable all their links, rename or delete their campaigns, and delete the account. Deletion is a soft delete: the user can no longer sign in and the email can be registered again, but their links are kept (disabled) so analytics stay traceable. Admins can't demote, suspend or delete themselves, and the platform always keeps one active admin. Every admin action is recorded in **Admin → Activity log** (never including passwords).

**Interface.** A frosted-glass design: translucent panels with background blur over a slowly moving colour backdrop, in light and dark mode (follows the system; toggle in the navbar). Glass surfaces stay opaque enough for readable text, and browsers without `backdrop-filter` get solid panels. Animated counters and charts, skeleton loading, a live indicator that pulses on new visits, button ripples, sections that slide in on scroll, page-to-page fades, and step transitions in the wizard. Landing pages and visitor pages use the same glass style. All motion is switched off for people who set "reduce motion".

## Edge cases from the problem statement

| Edge case | How it's handled | Test |
|---|---|---|
| Expired link | C rule check returns `expired` → 410 page. Click events are never deleted, so analytics stay. | `test_expired_link_stops_but_keeps_stats` |
| Click limit hit, simultaneous visitors | One conditional `UPDATE … WHERE click_count < max_clicks`; SQLite serialises writers so exactly N succeed. | `test_concurrency.py` (60 visitors released together on a 5-click link → exactly 5 redirects) |
| Password link | Form on GET; C decides `need/bad password`; hashes via Werkzeug; attempts rate-limited per IP + link. | `test_password_link`, `test_password_guessing_is_rate_limited` |
| Bad alias or URL | Live validation in wizard step 1, re-run on publish: scheme, credentials-in-URL, private IPs, redirect loops, blocklist, reserved/taken/invalid alias. | `WizardValidationTests` |
| Bot traffic | Declared crawlers/link previews: deterministic rule. Undeclared bots: Random Forest in the worker. Bot clicks are stored and shown separately, never mixed into people counts. | `test_scripted_burst_is_filtered_from_human_counts`, `test_declared_bot_*` |
| Malicious link | URL checker warns before publishing (high risk needs confirmation and goes to the admin review queue); admin disable is one UPDATE that the next request reads. | `test_admin_takedown_is_immediate_and_keeps_stats`, `test_blocklist_can_disable_existing_links` |

---

## Design decisions (be ready to defend these)

**The C check alone can't enforce "exactly N clicks".** Two requests can both read `click_count = N-1` before either writes. C decides the *order* of rules; the database guarantees the *count* with an atomic conditional UPDATE. Only capped links pay for this write on the redirect path; uncapped links are counted by the worker.

**302 + `Cache-Control: no-store`, never 301.** Browsers cache 301s, so repeat visits would never reach the server (lost clicks) and an admin takedown wouldn't stop people who had visited before.

**Link previews must not use up click caps.** WhatsApp, Slack and others fetch a link when it is pasted. On capped links, declared bots get a page with no redirect, so faking a bot user agent can't bypass the limit either.

**Password is checked last.** A dead (disabled, expired, used-up) link never shows a password form, so it can't be used to guess passwords.

**C state is per process.** The alias table and rate limiter live in process memory, so production runs one gunicorn worker with 8 threads. The SQLite UNIQUE constraint still catches alias races across processes; with several workers the rate limits would be per worker.

**Every top-level route is a reserved alias**, collected from Flask's URL map at startup, so no one can create `/dashboard` and get a link the router would never reach.

**Privacy.** Raw IPs are never stored: unique visitors use a salted hash of IP + user agent. Referrers are stored as domains only.

**Spike alerts are statistics, not a trained model.** Last hour vs. the previous 24 hours using median and MAD (robust z-score ≥ 4, at least 10 clicks). It works from a link's first day and each alert explains itself. This deviates from the "trained offline" note in the Round 1 diagram on purpose.

**Schema additions.** Besides the six tables in the Round 1 deck, `tags` and `link_tags` exist because comma-separated tags in one column can't be queried or kept consistent.

---

## ML: what the numbers mean

No labelled click logs or vetted URL dataset ship with this repo, so **both models train on synthetic data by default** (`ml/training.py`). Test-split results:

| Model | Precision | Recall | F1 | Rule baseline F1 (precision) |
|---|---|---|---|---|
| Bot filter (Random Forest) | 0.959 | 0.919 | 0.939 | 0.904 (0.993) |
| URL checker (Logistic Regression) | 0.947 | 0.967 | 0.957 | 0.825 (0.990) |

These prove the pipeline works, not that the models work on real traffic: the models are learning the generator. Note the rule baselines have *higher precision*; the models win on recall. Say this to the judges rather than quoting accuracy.

Known failure modes (visible in the notebook):
- **Low-and-slow bots** (browser user agent, a few clicks minutes apart) are mostly missed. Per-click features can't separate them from people.
- **URL false warnings** on legitimate hyphenated domains, login pages (`accounts.google.com/signin`) and IDs with many digits. These are *warnings*, not blocks; only scores ≥ 0.8 need confirmation.
- **Clean-looking phishing domains** are invisible to lexical features. The blocklist and admin takedown cover that.

To train on real data: `python ml/train_models.py --url-csv urls.csv --bot-csv clicks.csv` (formats in `ml/training.py`). For URLs, combine a phishing/malware feed with a list of popular benign domains, check each source's licence, and keep each domain on one side of the train/test split. For clicks, split by time and by visitor.

---

## Deploying on Render

`render.yaml` is a Blueprint: push the repo, create a Blueprint in Render, and set `ADMIN_PASSWORD`, `DEMO_PASSWORD` and `BASE_URL` when prompted.

The live demo uses the custom domain `shortpulse.jainovation.xyz`: a `CNAME` record pointing to the service's `onrender.com` hostname, added under the service's **Settings → Custom Domains**, with `BASE_URL=https://shortpulse.jainovation.xyz` so short links and QR codes use it.

- **The free tier wipes SQLite** on every deploy, restart and spin-down (free services have an ephemeral filesystem and can't attach persistent disks). `SEED_ON_START=1` rebuilds the demo data on boot. For persistence, use a paid instance with a disk and point `DATABASE_PATH` at it.
- Free services sleep after 15 minutes without traffic; open the site a minute before presenting.
- `build.sh` compiles and tests the C engine, installs dependencies, and retrains the models only if the bundled ones fail to load. If the native runtime lacks gcc, deploy with the `Dockerfile` instead.
- `TRUSTED_PROXIES=1` is set for Render's proxy. Keep it `0` anywhere clients connect directly, otherwise `X-Forwarded-For` can be spoofed to dodge rate limits.
- Location lookup is optional: download GeoLite2-City.mmdb with a free MaxMind account (don't commit it) and set `GEOLITE2_PATH`.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Session signing. Must be set in production. |
| `IP_HASH_SALT` | Salt for visitor hashes. Keep stable or unique-visitor counts reset. |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Admin account created or promoted at startup. |
| `DEMO_PASSWORD` | Password for the seeded demo user. |
| `STORE_FULL_IP` / `IP_RETENTION_DAYS` | Keep full visitor IPs (default on) and for how many days (default 30). |
| `OWNER_SEES_FULL_IP` | Show full IPs to link owners too (default off; admins always see them). |
| `GA_MEASUREMENT_ID` | Google Analytics 4 ID (e.g. `G-4C6XT5QZFH`) for ShortPulse's own pages. Not loaded on short links, landing pages or other visitor pages; query strings are stripped. Empty = off. |
| `SEED_ON_START` | `1` = load demo data when the database has no links. |
| `BASE_URL` | Public URL used in short links and QR codes. |
| `DATABASE_PATH`, `MODEL_DIR`, `GEOLITE2_PATH`, `TRUSTED_PROXIES`, `SESSION_COOKIE_SECURE` | See `app/config.py`. |

---

## API (all JSON; writes need the `X-CSRF-Token` header from the page's meta tag)

| Method & path | Role | Purpose |
|---|---|---|
| `GET/POST /<code>` | Visitor | Redirect (POST = password form) |
| `POST /api/links/validate` | User | Wizard step 1 checks |
| `POST /api/links` | User | Publish (rate-limited: 10 burst, then 10/min) |
| `GET /api/links`, `GET/PATCH /api/links/<id>` | User (own) / Admin (`?scope=all`) | List, read, edit, pause/resume |
| `GET /api/links/<id>/clicks`, `/qr.png` | User | Recent visits, QR PNG (`?download=1`) |
| `GET/POST /api/campaigns`, `DELETE /api/campaigns/<id>`, `GET /api/tags` | User | Organisation |
| `GET /api/analytics/overview`, `/api/analytics/link/<id>` | User / Admin | `period=24h|7d|30d|90d`, `campaign_id`, `tz` (minutes east of UTC) |
| `POST /api/import/preview`, `/validate`, `/commit` | User | CSV import |
| `POST /api/admin/links/<id>/disable|enable|dismiss-flag` | Admin | Takedown and review |
| `GET /api/admin/users/<id>`, `PATCH /api/admin/users/<id>`, `DELETE /api/admin/users/<id>` | Admin | View, edit (email, role, status), soft-delete an account |
| `POST /api/admin/users/<id>/password`, `/disable-links` | Admin | Set a new password; disable all of a user's links |
| `PATCH/DELETE /api/admin/campaigns/<id>`, `GET /api/admin/audit` | Admin | Manage any campaign; activity log |
| `GET/POST /api/admin/blocklist`, `DELETE /api/admin/blocklist/<id>` | Admin | Domain blocklist |
| `GET /api/admin/users`, `POST /api/admin/users/<id>/role` | Admin | Roles |
| `GET /api/links/<id>/clicks.csv` | User / Admin | All visits as CSV (IPs shortened unless admin; formula-safe) |
| `GET /api/links/<id>/qr.png`, `/qr.svg` | User | Styled QR (`size`, `download`, design overrides) |
| `GET/PUT /api/links/<id>/qr-design`, `POST/DELETE /api/links/<id>/qr-logo` | User | Save the QR design and logo |
| `POST /api/engage` | Anyone (CORS) | Engagement beacon from the owner's site |
| `GET /healthz` | Anyone | Engine version, model status, click-queue stats |

---

## Project layout

```
engine/        C engine (Base62, alias hash table, rule order, rate limiter), Makefile, C tests
app/           Flask app: schema, services, redirect, import, admin, analytics, QR studio, landing pages
app/templates, app/static
               Glass UI, Chart.js dashboard, wizard, smart paste, visitor pages
ml/            training.py, train_models.py, train_models.ipynb, models/
tests/         C, Python and JavaScript tests
render.yaml, build.sh, Dockerfile, windows.cmd
               Deployment (Render, Docker) and Windows helper
```

## Known limitations
- App switching, the native share sheet and copy-image depend on phone and browser support and haven't been tested on real devices here.
- Engagement is measured on ShortPulse landing pages and on websites where the owner added the script; never on other sites.

- Tested with Flask 3.1, scikit-learn 1.8, numpy 2.4, pandas 3.0 and Pillow 12 on Python 3.12 (Linux). The Windows build of the C engine (SRW locks, BCryptGenRandom, Win32 threads in the tests) was compiled and run against stand-in headers, not on real Windows; run `.\windows.cmd test` before relying on it. `qrcode`, `user-agents`, `geoip2` and `gunicorn` were not installed in the environment this was built in: the QR route is tested with a stub, and a small built-in user-agent parser is used when `user-agents` is missing. Run `make test` after `pip install -r requirements.txt` and do one manual QR scan before the demo.
- Click events still in the queue are lost if the process is killed with SIGKILL (normal shutdown drains it). The click *count* for capped links is written synchronously and is never lost.
- `?src=qr` can be added by hand, so QR scan counts are indicative.
- Re-sending the same import commit creates duplicate links for rows without an alias (the UI prevents this; the API doesn't).
- Unique visitors are approximate: same IP + browser counts once; people behind one campus NAT with the same browser build may be merged.

## Roadmap

Custom domains · device targeting (mobile vs desktop) · public API with keys · link-in-bio pages · workspaces and folders · a redirect cache (must be invalidated on disable/edit, or takedowns stop being immediate).
