# PinForge

Paste a product URL → get a branded Pinterest pin, edited and published
in a couple of clicks. Works with Amazon out of the box, and any other
retailer (Walmart, Best Buy, Target, eBay, or a merchant on ShareASale/
CJ/Rakuten/Awin/Impact) via a quick one-time profile. Self-hosted, single
SQLite file, no external services.

```
Product URL → [product data] → [branded pin image] → edit → publish to Pinterest
```

## What it does

1. **Intake** — paste a product URL, from Amazon or anywhere else.
2. **Forge** — Amazon URLs go through the Creators API (title, live price,
   image). Anything else is scraped directly from the page's own structured
   data (the JSON-LD/Open Graph markup most e-commerce sites already emit),
   and the affiliate link comes from a retailer profile you configure once —
   or one you paste in yourself if you haven't set one up for that domain.
   Either way, it lays out a 1000×1500 branded pin image (product photo,
   price badge, title, your logo + site in the footer).
3. **Bench** — preview the pin, edit the title/description, pick a board.
4. **Publish** — sends it straight to your Pinterest account via the
   Pinterest API.
5. **Retailers** — configure a profile per non-Amazon network: a domain to
   match, and a link template with `{url}` where the product page goes.
   Most networks (ShareASale, CJ, Awin, and many Impact programs) support
   this kind of templated deep link — generate one example from your
   network's dashboard, note the pattern, and PinForge applies it to every
   product on that domain automatically from then on. No profile for a
   domain? You can still forge a pin — Forge just asks you to paste that
   product's ready-made affiliate link instead.
6. **Ledger** — every pin you've forged (draft or published) stays logged,
   tagged with which retailer it came from, plus a running click count.
   Click **Open** on any row to load that pin back onto the Bench — this
   is how you publish a draft you made earlier, or re-check/edit a pin
   after navigating away from it.
7. **Store products** *(optional)* — on the Bench, before you Publish, you
   can click **Publish as product on [yoursite]** to auto-publish a real
   WooCommerce product: WooCommerce's built-in "External/Affiliate Product"
   type, which shows a "Shop Now" button linking out instead of "Add to
   Cart" — the right fit for affiliate marketing, with none of the
   inventory/fulfillment machinery a normal product implies. Because it's
   a real product (not just a blog post), it shows up wherever your theme
   already surfaces products — New Arrivals, the main Shop page, category
   listings — automatically, with zero theme changes needed. If you do
   this, Publish sends Pinterest to *that product page* instead of straight
   to the affiliate link — its own Shop Now button is what carries the
   visitor onward (through the same `/go/<id>` tracked redirect Analytics
   relies on), so click tracking still works, it just happens one hop
   later. This keeps the visitor on your own domain first — where you
   control ad placement (e.g. via Google Site Kit) and build SEO value over
   time — rather than handing that traffic straight to Amazon. Skip this
   and Publish falls back to the direct tracking link exactly like before
   this feature existed; it's entirely optional per pin.
8. **Analytics** — every published pin's Pinterest link points at
   PinForge's own `/go/<id>` redirect first, not straight at the affiliate
   link. That's the only way a self-hosted tool can see click data at all,
   since none of the affiliate networks report clicks back to you directly —
   the redirect logs the click, then immediately forwards the visitor on to
   the real destination (a few milliseconds of overhead, invisible to them).
   This tracks **clicks, not confirmed sales** — check Amazon Associates'
   own dashboard (or whichever network's) for actual commission data; use
   this to see which pins/products/retailers are worth making more of, not
   as a revenue report. Requires `APP_BASE_URL` in `.env` to be set to your
   real public domain (not `localhost`) — Publish will refuse to run and
   tell you clearly if it isn't, rather than silently publishing a broken
   link.
9. **Scout** — automated product discovery, **Amazon only** (it relies on
   Amazon's category/rating search, which the other retailers don't expose
   the same way). Save a "watch" (an Amazon category, optional keywords,
   and quality filters like minimum rating/review count), click **Run Scout
   Now**, and matching products land in a review queue below — nothing
   publishes automatically. Approve a candidate to run it through the same
   Forge pipeline as a pasted URL; reject to dismiss it. Set
   `SCOUT_INTERVAL_HOURS` in `.env` if you want Scout to run itself on a
   schedule instead of only on demand — it still only queues candidates for
   your review, it never auto-publishes.

   Every decision (approved, rejected, or still pending) is permanent and
   global — a product you've seen under one watch won't resurface under a
   different watch, and a product that's already been forged into a pin
   won't come back either. Scout also does fuzzy title matching, so a
   near-identical relisting (same product, different ASIN, slightly reworded
   title) gets caught too, not just exact ASIN repeats. The similarity bar
   is tuned to avoid false positives — it won't lump genuinely different
   products together just because they share a category — but you can
   loosen or tighten it with `SCOUT_SIMILARITY_THRESHOLD` (0–1, default
   0.82) in `.env` if your niche needs it.

   **Honest limitation:** the Creators API's `SearchItems` endpoint doesn't
   expose a literal "Bestsellers" sort (that only ever existed in the old,
   now-retired PA-API v4).
   Scout uses Amazon's `Featured` sort — a blended relevance/popularity
   ranking — combined with your rating and review-count filters as the
   closest available proxy for "what's actually good and selling" in a
   category.

## Why generic mode instead of a native API per retailer

Building a real API integration for Walmart/Best Buy/Target (via Impact),
eBay Partner Network, ShareASale, CJ, Rakuten, and Awin would each be its
own multi-day project — separate developer applications, separate auth
schemes, separate approval waits, similar to what Amazon's Creators API and
Pinterest's own API required. Generic mode gets you *all* of them working
today: product data comes from the page itself (works on virtually any
retailer with no configuration), and the affiliate link comes from a
template you set up once per network using their existing deep-link tool.
If one specific retailer turns out to drive real volume for you later, a
dedicated API integration for just that one program is a reasonable next
step — but building five of them upfront, before you know which one
matters, isn't a good use of time on a personal project.

## What it doesn't do (yet)

This covers the shopping-retailer → Pinterest half of your pipeline. The
distribution half you mentioned — pushing generated content to Instagram,
YouTube Shorts, and TikTok — is a separate concern with a very different
shape (those are video-first platforms with their own creative
requirements), so it's not bolted on here. When you're ready for that, a
self-hosted scheduler like [Postiz](https://github.com/gitroomhq/postiz-app)
sits well next to this app: PinForge handles the shopping→Pinterest
pipeline, Postiz handles the rest.

## 1. Get your API credentials

**Amazon Creators API**
- Sign in to [Amazon Associates](https://affiliate-program.amazon.com/) →
  Tools → Creators API (Amazon retired the old Product Advertising API on
  May 15, 2026 — this is its official replacement).
- Click **Create Application**, name it anything, then **Add New Credential**
  to get a Credential ID and Credential Secret. Copy both immediately — the
  secret is only shown once.
- Note your **Associate Tag** (this is your `reallygoodgadgets-20`-style tag).
- The Creators API requires 10+ qualified sales in the trailing 30 days —
  until you have that, PinForge automatically falls back to reading the
  public product page directly (title + image only, no price). You'll see
  a small "fallback" flag on the pin proof when this happens, and it's your
  cue to double check price/details manually before publishing.

**Pinterest API**
- Create an app at [developers.pinterest.com/apps](https://developers.pinterest.com/apps/).
- Set its **redirect URI** to `http://localhost:5000/auth/pinterest/callback`
  (or `https://yourdomain.com/auth/pinterest/callback` once deployed).
- Grab the App ID and App Secret.
- New apps get **Trial access** first. **Pinterest blocks Trial-tier apps
  from creating pins on the production API at all** (a clear error, code
  29, not a bug on this end) — set `PINTEREST_USE_SANDBOX=1` in `.env` to
  route pin creation through their Sandbox API instead, using the exact
  same credentials. Sandbox pins publish successfully and let you test the
  full pipeline (and record the demo video Standard access requires) —
  they're just only visible to you, never the public. The dashboard shows
  a clear **SANDBOX MODE** badge whenever this is on, and the "Published"
  confirmation says so too, so it's never a silent surprise about why a
  pin isn't showing up for anyone else. Once Pinterest approves Standard
  access, set it back to `0` (or remove the line) so pins go fully live.

## 2. Configure

```bash
cp .env.example .env
```

Fill in `.env`:

```
AMAZON_CREDENTIAL_ID=...
AMAZON_CREDENTIAL_SECRET=...
AMAZON_CREDENTIAL_VERSION=3.1
AMAZON_PARTNER_TAG=reallygoodgadgets-20
AMAZON_COUNTRY=US

PINTEREST_APP_ID=...
PINTEREST_APP_SECRET=...
PINTEREST_REDIRECT_URI=http://localhost:5000/auth/pinterest/callback

BRAND_NAME=Really Good Gadgets
BRAND_WEBSITE=reallygoodgadgets.com
BRAND_ACCENT_COLOR=#E63946
```

`SECRET_KEY` should be a random string — used to sign the Flask session
cookie during Pinterest OAuth. Generate one with:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## 3. Run it

### Option A — Docker (recommended for self-hosting)

```bash
docker compose up -d --build
```

App is live at `http://localhost:5000`. Data persists in `./data/` and
generated pin images in `./static/generated/` on your host, so `docker
compose down` / rebuilds don't lose anything.

To run on a real domain, put this behind a reverse proxy (Caddy/nginx) for
TLS, and update `PINTEREST_REDIRECT_URI` in `.env` and in the Pinterest app
dashboard to match your real callback URL.

### Option B — plain Python

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Visit `http://localhost:5000`.

## 4. First run

1. Click **Connect** next to "Pinterest not connected" in the top right and
   authorize the app.
2. Paste a product URL and click **Forge Pin**. For Amazon, that's it. For
   any other retailer, add a quick profile first under **Retailers** (a
   domain and a deep-link template from your network's dashboard) — or skip
   that and just paste your network's ready-made affiliate link when
   prompted.
3. Check the price badge and title (verify manually if you see the
   "fallback page read" flag — this shows up for Amazon before you're
   Creators API–eligible, and for every non-Amazon retailer by default,
   since those always come from scraping the page), edit the description
   if you want, pick a board, and hit **Publish**.
4. If Forge fails outright with an error rather than a warning flag, the
   page likely blocked the automated request entirely — Amazon in
   particular serves a CAPTCHA page to most scraping attempts, which
   returns a normal-looking response with no actual product data in it.
   When this happens, open **+ Enter product details manually** (it
   expands automatically after a failed attempt) and type in the title,
   price, and a direct image URL yourself — right-click the product photo
   on the real page and "Copy image address" to get that last one. The
   affiliate link still gets built/required normally; manual entry only
   replaces the data PinForge couldn't fetch on its own.

## Security

If this app is reachable from the public internet (which it needs to be for
the Pinterest OAuth callback to work), it **must** have `ADMIN_PASSWORD` set
in `.env` — without it, nobody can log in at all, but that also means you
can't get in either. Pick something long and random; it's the only thing
standing between a stranger and your Pinterest account and Amazon affiliate
links.

What's already handled for you:
- **Login gate** in front of every page and API route — nobody gets past
  `/login` without the password.
- **Session cookies** are `Secure` (HTTPS-only), `HttpOnly` (invisible to
  page JavaScript), and expire after `SESSION_LIFETIME_DAYS` (default 7).
- **Rate limiting** on login attempts (10/min) and the expensive endpoints
  — Forge, Publish, Scout run (20–30/hour) — so a stuck loop or a bad actor
  can't hammer your Amazon/Pinterest API quota or your Pi's CPU.
- **Non-root container user** — if the app or a dependency were ever
  compromised, the process doesn't run as root inside the container.

What's on you:
- **HTTPS itself** — this app assumes it's sitting behind Caddy (or nginx/
  another TLS-terminating proxy). It doesn't handle certificates on its own.
- **Keeping `.env` off git** — it already is, via `.gitignore`, but double
  check before you ever push this repo anywhere.
- **The Pi itself** — normal box hygiene: keep it updated (`sudo apt update
  && sudo apt upgrade`), don't reuse the admin password elsewhere, and don't
  expose SSH to the internet without a key (not password) login.

If you're accessing the app over plain HTTP on your local network (bypassing
Caddy entirely, e.g. via a router-level local DNS override pointing straight
at the Pi), set `SESSION_COOKIE_SECURE=0` in `.env` — browsers refuse to
send `Secure` cookies over a non-HTTPS connection, so login will otherwise
loop you back to the login page forever on that path. Prefer routing local
traffic through Caddy too if you can, so every access path gets both TLS and
consistent behavior.

## Project structure

```
app.py                Flask routes (generate / edit / publish / OAuth / retailers / click tracking)
auth.py                Login gate (single shared password, session-based)
amazon_client.py       Amazon Creators API wrapper + scrape fallback
generic_client.py      Universal product scraper (JSON-LD/OG) + affiliate link templating
pin_image.py           Pillow-based pin image renderer
pinterest_client.py    Pinterest OAuth2 + pin/board API calls
wordpress_client.py    Publishes affiliate products to your WooCommerce store (optional)
scout.py               Automated product discovery (Amazon only)
db.py                  SQLite persistence (pins, tokens, retailers, settings)
templates/index.html   UI
templates/login.html   Login page
static/css/style.css   Styling
static/js/app.js       Frontend logic
static/fonts/          Bundled fonts (brand overlay + UI)
```

## Notes on affiliate compliance

- Pinterest's own [Amazon Storefront linking](https://help.pinterest.com/)
  feature auto-applies your affiliate tag when you're eligible — if you have
  access to it, PinForge's manually-tagged links (`?tag=your-tag`) work the
  same way and are compatible with it.
- Always disclose affiliate relationships per FTC guidelines and Pinterest's
  own policies — the generated pin description includes `#affiliate` by
  default; keep it or adjust to match your disclosure practice.
- Re-check Amazon's and Pinterest's current affiliate-link policies
  periodically; both have changed the rules on this before.
- **Each network you add has its own terms** — commission rules, cookie
  windows, and what you're allowed to say/show in promotional content all
  vary by program. Skim the terms for any retailer/network before adding
  its profile here, the same way you would for Amazon.
- Most networks require you to be individually approved into a merchant's
  specific program (not just the network as a whole) before their tracking
  links will actually pay out — a link generated before approval may still
  render fine but won't earn commission. Confirm approval status in that
  network's dashboard, not just PinForge, if a link isn't converting.

## Adding a retailer

Amazon needs nothing extra — it's built in. For anything else:

1. Sign up for the retailer's affiliate program, or a network that carries
   it (Walmart/Best Buy/Target run through Impact; many independent brands
   run through ShareASale, CJ, Rakuten, or Awin).
2. In that network's dashboard, find their **deep link generator** (every
   major network has one — paste any product URL, get back a trackable
   link). Generate one for any product as an example.
3. Look at the URL you got back. If it contains the original product URL
   somewhere in it (usually URL-encoded), that's your template — replace
   just that embedded URL with `{url}` and everything else (your IDs, the
   domain) stays as-is.

   **Example (ShareASale):** you generate a link and get back
   ```
   https://www.shareasale.com/r.cfm?u=112233&b=4455&m=6677&urllink=https%3A%2F%2Fwww.walmart.com%2Fip%2Fexample
   ```
   Your template is everything up to `urllink=`, with `{url}` after it:
   ```
   https://www.shareasale.com/r.cfm?u=112233&b=4455&m=6677&urllink={url}
   ```
4. In PinForge, open **Retailers → Add a retailer profile**. Set the
   **domain** to `walmart.com` (just the domain, not the full URL) and
   paste your template into **Link template**.
5. From then on, pasting any `walmart.com` product URL into Intake
   auto-generates the right affiliate link for that specific product.

If a network's deep links don't follow this simple pattern (some require a
server-side API call per link rather than a static template), leave **Link
template** blank when creating the profile — PinForge will still recognize
the domain and show you the manual-paste field every time, which at least
saves you from misconfiguring an incorrect link entirely.

## Extending it

- Swap the Pillow template in `pin_image.py` for your own layout/branding.
- `db.py` has everything needed to add a "schedule for later" queue if you
  want delayed publishing instead of immediate.
- The Pinterest client (`pinterest_client.py`) is a thin wrapper — the same
  pattern (OAuth2Session + REST calls) extends cleanly if you ever want to
  add another platform's API directly instead of routing through a separate
  scheduler tool.
