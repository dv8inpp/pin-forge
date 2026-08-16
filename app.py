import os
import uuid
import requests
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, session, url_for
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import db
import amazon_client
import generic_client
import pin_image
import pinterest_client
import scout
import auth
import wordpress_client

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")

# The app sits behind Caddy (or another reverse proxy) in every real
# deployment -- this makes Flask trust the X-Forwarded-* headers Caddy
# sets, so rate limiting keys off the real visitor IP instead of Caddy's,
# and so the app correctly knows the original request was HTTPS.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.config.update(
    # Only send the session cookie over HTTPS. Set SESSION_COOKIE_SECURE=0
    # in .env if you're accessing the app over plain HTTP on your local
    # network without going through Caddy -- otherwise login won't work
    # on that path, since browsers withhold Secure cookies from HTTP.
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "1") == "1",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",  # Lax (not Strict) so the Pinterest OAuth redirect back still carries the session
    PERMANENT_SESSION_LIFETIME=timedelta(days=int(os.environ.get("SESSION_LIFETIME_DAYS", "7"))),
)

limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")

db.init_db()

# Routes reachable without being logged in. Everything else is gated by
# the before_request hook below.
_PUBLIC_ENDPOINTS = {"login", "static", "track_click"}


@app.before_request
def require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS or request.endpoint is None:
        return
    if not auth.is_logged_in():
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not logged in."}), 401
        return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")  # slow down password-guessing attempts
def login():
    error = None
    if request.method == "POST":
        if auth.check_password(request.form.get("password", "")):
            auth.log_in()
            next_url = request.args.get("next") or url_for("index")
            return redirect(next_url)
        error = "Incorrect password."
    return render_template(
        "login.html",
        error=error,
        brand_name=os.environ.get("BRAND_NAME", "Your Brand"),
    )


@app.route("/logout", methods=["POST"])
def logout():
    auth.log_out()
    return redirect(url_for("login"))


def _maybe_start_scheduler():
    """Optional background scheduler: set SCOUT_INTERVAL_HOURS in .env to
    have Scout run automatically. Without it, Scout only runs when you
    click 'Run Scout Now' or hit the API route yourself (e.g. from cron)."""
    interval = os.environ.get("SCOUT_INTERVAL_HOURS")
    if not interval:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        app.logger.warning("SCOUT_INTERVAL_HOURS is set but APScheduler isn't installed.")
        return

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        scout.run_all_active_watches,
        "interval",
        hours=float(interval),
        id="scout_auto_run",
        next_run_time=datetime.utcnow(),
    )
    scheduler.start()


_maybe_start_scheduler()


def _pinterest_token():
    """Just loads the stored token -- no proactive refresh here. Refreshing
    on every single request (including read-only ones like listing boards)
    created a race: Pinterest's refresh tokens are single-use, so two
    near-simultaneous requests (easy with multiple gunicorn workers) could
    both grab the same stored token, and whichever lost the race got handed
    one Pinterest had already invalidated. See _call_pinterest instead --
    that's where refreshing actually happens now, only on a real 401."""
    return db.load_token("pinterest")


def _call_pinterest(func, token, *args, **kwargs):
    """Calls a pinterest_client function with the given token. If Pinterest
    rejects it with a real 401 (expired/invalid token, not a permissions or
    validation error), refreshes once, saves the new token, and retries
    exactly once with it. Any other error (or a second failure after
    refreshing) propagates normally."""
    try:
        return func(token, *args, **kwargs)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            refreshed = pinterest_client.refresh_token(token)
            db.save_token("pinterest", refreshed)
            return func(refreshed, *args, **kwargs)
        raise


@app.route("/")
def index():
    pinterest_connected = db.load_token("pinterest") is not None
    boards = []
    if pinterest_connected:
        token = _pinterest_token()
        try:
            boards = _call_pinterest(pinterest_client.list_boards, token)
        except Exception:
            boards = []
    recent_pins = db.list_pins(limit=12)
    retailer_profiles = db.list_retailer_profiles()
    pinterest_sandbox = os.environ.get("PINTEREST_USE_SANDBOX", "").lower() in ("1", "true", "yes")
    return render_template(
        "index.html",
        pinterest_connected=pinterest_connected,
        pinterest_sandbox=pinterest_sandbox,
        boards=boards,
        recent_pins=recent_pins,
        retailer_profiles=retailer_profiles,
        brand_name=os.environ.get("BRAND_NAME", "Your Brand"),
    )


# ---------------- Pinterest OAuth ----------------

@app.route("/auth/pinterest")
def auth_pinterest():
    auth_url, state = pinterest_client.get_authorization_url()
    session["pinterest_oauth_state"] = state
    return redirect(auth_url)


@app.route("/auth/pinterest/callback")
def auth_pinterest_callback():
    state = session.get("pinterest_oauth_state")
    token = pinterest_client.exchange_code_for_token(request.url, state)
    db.save_token("pinterest", token)
    return redirect(url_for("index"))


# ---------------- Click tracking ----------------

@app.route("/go/<int:pin_id>")
def track_click(pin_id):
    """Public redirect every published pin's link points at instead of the
    raw affiliate URL. Logs a click, then immediately forwards the visitor
    on -- this is the only way a self-hosted tool can see click data at
    all, since none of the affiliate networks report clicks back to us."""
    pin = db.get_pin(pin_id)
    if not pin or not pin.get("affiliate_link"):
        return "Link not found.", 404

    try:
        db.record_click(
            pin_id,
            referrer=request.referrer,
            user_agent=request.headers.get("User-Agent"),
        )
    except Exception:
        pass  # never let logging failure block the redirect itself

    return redirect(pin["affiliate_link"], code=302)


# ---------------- Core workflow ----------------

_AMAZON_DOMAINS = (
    "amazon.com", "amazon.co.uk", "amazon.ca", "amazon.de", "amazon.fr",
    "amazon.es", "amazon.it", "amazon.in", "amazon.co.jp", "amazon.com.au",
    "amazon.com.br", "amazon.com.mx", "amazon.nl", "amazon.se", "amazon.sg",
    "amazon.com.tr", "amazon.ae", "amazon.sa", "amazon.pl", "amazon.com.be",
)


def _generate_pin_from_product_url(
    product_url: str,
    affiliate_link_override: str | None = None,
    manual_title: str | None = None,
    manual_price: str | None = None,
    manual_image_url: str | None = None,
) -> dict:
    """Shared pipeline: any product URL -> product data -> rendered pin ->
    draft row. Used by the manual 'Forge Pin' route and Scout candidate
    approval (Amazon only, for now -- Scout's discovery logic is Amazon-
    specific since it relies on Amazon's category/rating search).

    Routes to the Amazon-specific client for Amazon URLs (live pricing via
    Creators API when eligible, ASIN-based dedup, proper affiliate tagging).
    Everything else goes through the generic scraper: product data comes
    from the page's own structured markup, and the affiliate link either
    comes from a configured retailer profile's template, or -- if none
    matches -- from affiliate_link_override, which the person pastes in
    themselves from their network's per-product deep-link generator.

    If manual_title and manual_image_url are supplied, fetching is skipped
    entirely -- this is the fallback for pages that block automated
    requests outright (Amazon in particular serves a CAPTCHA page to most
    scraping attempts, which looks like a normal 200 response but contains
    no usable data). The affiliate link is still built/required normally;
    manual entry only replaces the product *data*, not the link logic.
    """
    domain = generic_client.get_domain(product_url)
    is_amazon = any(domain == d or domain.endswith("." + d) for d in _AMAZON_DOMAINS)
    use_manual = bool(manual_title and manual_image_url)

    if is_amazon:
        asin = amazon_client.extract_asin(product_url)
        if not asin:
            raise ValueError(
                "Could not find a product ASIN in that URL. Use a standard "
                "Amazon product page URL (contains /dp/XXXXXXXXXX)."
            )
        tag = os.environ.get("AMAZON_PARTNER_TAG", "")
        country = os.environ.get("AMAZON_COUNTRY", "US")
        affiliate_link = amazon_client.build_affiliate_link(asin, tag, country)
        retailer_label = "Amazon"

        if use_manual:
            product = {
                "asin": asin, "title": manual_title, "price": manual_price,
                "image_url": manual_image_url, "source": "manual",
            }
        else:
            product = amazon_client.fetch_product(product_url)
            affiliate_link = product["affiliate_link"]
    else:
        profile = db.find_retailer_profile_for_domain(domain)
        if profile and profile["link_template"]:
            retailer_label = profile["label"]
            affiliate_link = generic_client.apply_affiliate_template(
                profile["link_template"], product_url
            )
        elif affiliate_link_override:
            retailer_label = profile["label"] if profile else domain
            affiliate_link = affiliate_link_override
        else:
            raise ValueError(
                f"No affiliate link template is configured for {domain}, and "
                f"none was provided. Either add a link template to its "
                f"retailer profile, or paste your network-generated "
                f"affiliate link for this specific product."
            )

        if use_manual:
            product = {
                "title": manual_title, "price": manual_price,
                "image_url": manual_image_url, "source": "manual",
            }
        else:
            product = generic_client.fetch_generic_product(product_url)
            product["source"] = "scrape"

    if not product.get("image_url"):
        raise ValueError("Could not find a product image on that page.")

    pin_title = product["title"][:100]
    pin_description = (
        f"{product['title']} — available now. {os.environ.get('BRAND_NAME', '')}"
        f" picks the best finds so you don't have to search. #affiliate"
    )[:800]

    filename = f"{uuid.uuid4().hex}.png"
    output_path = os.path.join("static", "generated", filename)

    pin_image.generate_pin(
        image_url=product["image_url"],
        title=pin_title,
        price=product.get("price"),
        brand_name=os.environ.get("BRAND_NAME", "Your Brand"),
        brand_website=os.environ.get("BRAND_WEBSITE", ""),
        accent_color=os.environ.get("BRAND_ACCENT_COLOR", "#E63946"),
        output_path=output_path,
    )

    pin_id = db.create_pin({
        "amazon_url": product_url,
        "asin": product.get("asin"),
        "product_title": product["title"],
        "product_price": product.get("price"),
        "product_image_url": product.get("image_url"),
        "pin_title": pin_title,
        "pin_description": pin_description,
        "affiliate_link": affiliate_link,
        "generated_image_path": output_path,
        "status": "draft",
        "retailer_label": retailer_label,
    })

    return {
        "pin_id": pin_id,
        "image_url": "/" + output_path.replace(os.sep, "/"),
        "pin_title": pin_title,
        "pin_description": pin_description,
        "affiliate_link": affiliate_link,
        "price": product.get("price"),
        "source": product.get("source"),
        "retailer_label": retailer_label,
    }


@app.route("/api/generate", methods=["POST"])
@limiter.limit("30 per hour")
def api_generate():
    """Step 1: paste a product URL -> fetch product data + render a pin image."""
    data = request.get_json(force=True)
    product_url = (data.get("amazon_url") or "").strip()
    affiliate_link_override = (data.get("affiliate_link_override") or "").strip() or None
    manual_title = (data.get("manual_title") or "").strip() or None
    manual_price = (data.get("manual_price") or "").strip() or None
    manual_image_url = (data.get("manual_image_url") or "").strip() or None
    if not product_url:
        return jsonify({"error": "Paste a product URL first."}), 400

    try:
        result = _generate_pin_from_product_url(
            product_url, affiliate_link_override,
            manual_title, manual_price, manual_image_url,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(result)


@app.route("/api/pins/<int:pin_id>", methods=["GET"])
def api_get_pin(pin_id):
    """Loads a pin's full data back into the Bench -- used by the 'Open'
    button in the Ledger, so drafts (and already-published pins, if you
    want to re-check/republish them) aren't a dead end once you leave
    the page they were forged on."""
    pin = db.get_pin(pin_id)
    if not pin:
        return jsonify({"error": "Pin not found."}), 404

    return jsonify({
        "pin_id": pin["id"],
        "image_url": "/" + pin["generated_image_path"],
        "pin_title": pin["pin_title"],
        "pin_description": pin["pin_description"],
        "affiliate_link": pin["affiliate_link"],
        "price": pin.get("product_price"),
        "retailer_label": pin.get("retailer_label") or "Amazon",
        "wordpress_post_url": pin.get("wordpress_post_url"),
        "status": pin.get("status"),
        "pinterest_pin_id": pin.get("pinterest_pin_id"),
    })


@app.route("/api/pins/<int:pin_id>", methods=["PATCH"])
def api_update_pin(pin_id):
    """Let the user edit title/description before publishing."""
    data = request.get_json(force=True)
    fields = {k: v for k, v in data.items() if k in ("pin_title", "pin_description")}
    db.update_pin(pin_id, **fields)
    return jsonify({"ok": True})


@app.route("/api/pins/<int:pin_id>", methods=["DELETE"])
def api_delete_pin(pin_id):
    """Removes a pin entirely -- its DB row, any click history logged
    against it, and the generated PNG on disk. Works on drafts and
    published pins alike; deleting a published pin here only removes it
    from PinForge's own records, it does NOT un-publish it from Pinterest
    -- that has to be done on Pinterest directly if needed."""
    image_path = db.delete_pin(pin_id)
    if image_path is None:
        return jsonify({"error": "Pin not found."}), 404

    try:
        full_path = os.path.join(os.path.dirname(__file__), image_path)
        if os.path.isfile(full_path):
            os.remove(full_path)
    except OSError:
        pass  # DB row is already gone; a leftover file isn't worth failing the request over

    return jsonify({"ok": True})


@app.route("/api/pins/<int:pin_id>/publish", methods=["POST"])
@limiter.limit("30 per hour")
def api_publish_pin(pin_id):
    """Step 2: publish the (possibly edited) draft to a chosen Pinterest board."""
    token = _pinterest_token()
    if not token:
        return jsonify({"error": "Connect your Pinterest account first."}), 401

    data = request.get_json(force=True)
    board_id = data.get("board_id")
    if not board_id:
        return jsonify({"error": "Choose a Pinterest board."}), 400

    pin = db.get_pin(pin_id)
    if not pin:
        return jsonify({"error": "Pin not found."}), 404

    base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    if not base_url or "localhost" in base_url:
        return jsonify({
            "error": "APP_BASE_URL isn't set to your real public domain in "
                     ".env (it's still localhost or empty) -- click tracking "
                     "needs a real URL Pinterest can reach. Set it to your "
                     "actual domain, e.g. https://pinforge.yourdomain.com, "
                     "and restart the app."
        }), 400
    tracking_link = f"{base_url}/go/{pin_id}"

    # If a landing page has been created on your own WordPress site for
    # this pin, Pinterest sends visitors THERE instead of straight to the
    # affiliate link -- the landing page's own "Shop Now" button is what
    # carries them onward through /go/<id>, so click tracking still works,
    # it just happens one hop later. No landing page yet? Falls back to
    # the direct tracking link, same as always.
    pinterest_link = pin.get("wordpress_post_url") or tracking_link

    try:
        result = _call_pinterest(
            pinterest_client.create_pin,
            token,
            board_id=board_id,
            title=pin["pin_title"],
            description=pin["pin_description"],
            link=pinterest_link,
            image_path=pin["generated_image_path"],
        )
    except Exception as e:
        return jsonify({"error": f"Pinterest rejected the pin: {e}"}), 502

    db.update_pin(
        pin_id,
        status="published",
        pinterest_board_id=board_id,
        pinterest_pin_id=result.get("id"),
        published_at=__import__("datetime").datetime.utcnow().isoformat(),
    )
    return jsonify({"ok": True, "pinterest_pin_id": result.get("id")})


@app.route("/api/pins/<int:pin_id>/landing-page", methods=["POST"])
@limiter.limit("30 per hour")
def api_create_landing_page(pin_id):
    """Publishes this pin as a real WooCommerce product (External/Affiliate
    type) on your own site, so Pinterest can send traffic there instead of
    straight to the affiliate link -- and since it's a real product, not
    just a blog post, it shows up in New Arrivals / Shop / category pages
    automatically, with zero theme changes needed. Optional -- skip this
    and Publish falls back to the direct tracking link, same as before
    this feature existed."""
    pin = db.get_pin(pin_id)
    if not pin:
        return jsonify({"error": "Pin not found."}), 404

    base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    if not base_url or "localhost" in base_url:
        return jsonify({
            "error": "APP_BASE_URL isn't set to your real public domain in "
                     ".env -- the product's Shop Now button needs a real "
                     "tracking link to point at."
        }), 400
    tracking_link = f"{base_url}/go/{pin_id}"

    try:
        result = wordpress_client.create_affiliate_product(
            pin_title=pin["pin_title"],
            pin_description=pin["pin_description"],
            price=pin.get("product_price"),
            product_image_url=pin["product_image_url"],
            tracking_link=tracking_link,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    db.update_pin(
        pin_id,
        wordpress_post_id=result["id"],
        wordpress_post_url=result["url"],
    )
    return jsonify({"ok": True, "wordpress_post_url": result["url"]})


# ---------------- Retailer profiles (generic, non-Amazon retailers) ----------------

@app.route("/api/retailers", methods=["GET"])
def api_list_retailers():
    return jsonify(db.list_retailer_profiles())


@app.route("/api/retailers", methods=["POST"])
def api_create_retailer():
    data = request.get_json(force=True)
    if not data.get("label") or not data.get("domain_pattern"):
        return jsonify({"error": "A label and domain are required."}), 400
    profile_id = db.create_retailer_profile(data)
    return jsonify({"ok": True, "profile_id": profile_id})


@app.route("/api/retailers/<int:profile_id>", methods=["DELETE"])
def api_delete_retailer(profile_id):
    db.delete_retailer_profile(profile_id)
    return jsonify({"ok": True})


# ---------------- Analytics ----------------

@app.route("/api/analytics")
def api_analytics():
    return jsonify(db.get_analytics_summary())


# ---------------- Scout: automated product discovery ----------------

@app.route("/api/scout/watches", methods=["GET"])
def api_list_watches():
    return jsonify(db.list_watches())


@app.route("/api/scout/watches", methods=["POST"])
def api_create_watch():
    data = request.get_json(force=True)
    if not data.get("label") or not data.get("search_index"):
        return jsonify({"error": "A label and category are required."}), 400
    watch_id = db.create_watch(data)
    return jsonify({"ok": True, "watch_id": watch_id})


@app.route("/api/scout/watches/<int:watch_id>", methods=["DELETE"])
def api_delete_watch(watch_id):
    db.delete_watch(watch_id)
    return jsonify({"ok": True})


@app.route("/api/scout/categories")
def api_scout_categories():
    return jsonify(scout.COMMON_CATEGORIES)


@app.route("/api/scout/run", methods=["POST"])
@limiter.limit("20 per hour")
def api_scout_run():
    """Manually trigger Scout now (also runs automatically on a schedule
    if SCOUT_INTERVAL_HOURS is set in .env)."""
    data = request.get_json(silent=True) or {}
    watch_id = data.get("watch_id")
    if watch_id:
        watches = [w for w in db.list_watches() if w["id"] == watch_id]
        if not watches:
            return jsonify({"error": "Watch not found."}), 404
        results = [scout.run_watch(watches[0])]
        results[0]["watch_id"] = watch_id
    else:
        results = scout.run_all_active_watches()
    return jsonify({"ok": True, "results": results})


@app.route("/api/scout/candidates")
def api_scout_candidates():
    status = request.args.get("status", "pending")
    return jsonify(db.list_candidates(status=status))


@app.route("/api/scout/candidates/<int:candidate_id>/approve", methods=["POST"])
def api_scout_approve(candidate_id):
    """Approve a discovered product: runs it through the same forge
    pipeline as a manually-pasted URL, landing it as a draft pin."""
    candidate = db.get_candidate(candidate_id)
    if not candidate:
        return jsonify({"error": "Candidate not found."}), 404

    amazon_url = scout.candidate_to_amazon_url(candidate)
    try:
        result = _generate_pin_from_product_url(amazon_url)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    db.decide_candidate(candidate_id, status="approved", pin_id=result["pin_id"])
    return jsonify(result)


@app.route("/api/scout/candidates/<int:candidate_id>/reject", methods=["POST"])
def api_scout_reject(candidate_id):
    db.decide_candidate(candidate_id, status="rejected")
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
