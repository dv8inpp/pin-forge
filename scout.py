"""
Scout: automated product discovery.

Important honesty note: the Creators API's SearchItems endpoint doesn't
expose a literal "Bestsellers" sort (that only ever existed in the old,
now-retired PA-API v4). The closest available proxy is sort_by=FEATURED,
which is Amazon's blended relevance/popularity ranking for a category --
combined here with your own rating and review-count floors, it's a solid
stand-in for "what's actually good and selling in this category" even
though it isn't the literal Bestsellers list.

Each "watch" is a saved search: a category (search_index) plus optional
keywords and quality filters. Running a watch pulls current results,
filters out anything below your bar, skips products you've already
decided on (by exact ASIN, globally across every watch -- not just this
one) or that are similar enough in name to something you've already seen
(catches the same item re-listed under a different ASIN, or a near-clone
from another seller), and drops the rest into a review queue -- nothing
here auto-publishes.
"""
import os
import re
import difflib
import db
import amazon_client

try:
    from amazon_creatorsapi import AmazonCreatorsApi, Country
    from amazon_creatorsapi.models import SortBy, SearchItemsResource
except ImportError:
    AmazonCreatorsApi = None
    Country = None
    SortBy = None

# Common Amazon search categories. Not exhaustive -- the Creators API
# accepts many more SearchIndex values (see Amazon's docs), but these
# cover most gadget/lifestyle affiliate niches.
COMMON_CATEGORIES = [
    "Electronics", "HomeAndKitchen", "ToysAndGames", "SportsAndOutdoors",
    "Beauty", "PetSupplies", "OfficeProducts", "Automotive",
    "GardenAndOutdoor", "Tools", "Kitchen", "Baby",
]

# How similar two product titles need to be (0-1) before Scout treats the
# new one as "basically the same thing" and skips it rather than queuing
# it as a fresh candidate. Tune via SCOUT_SIMILARITY_THRESHOLD in .env if
# your niche needs looser/tighter matching.
DEFAULT_SIMILARITY_THRESHOLD = 0.82

_FILLER_WORDS = {
    "with", "for", "and", "the", "a", "an", "of", "in", "to", "pack",
    "set", "pcs", "piece", "pieces", "new", "premium", "pro", "plus",
}


def _normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    words = [w for w in title.split() if w not in _FILLER_WORDS]
    return " ".join(words)


def _is_similar_to_known(title: str, known_normalized_titles: list[str], threshold: float) -> bool:
    normalized = _normalize_title(title)
    if not normalized:
        return False
    for known in known_normalized_titles:
        if not known:
            continue
        ratio = difflib.SequenceMatcher(None, normalized, known).ratio()
        if ratio >= threshold:
            return True
    return False


def _get_api():
    credential_id = os.environ.get("AMAZON_CREDENTIAL_ID")
    credential_secret = os.environ.get("AMAZON_CREDENTIAL_SECRET")
    version = os.environ.get("AMAZON_CREDENTIAL_VERSION", "3.1")
    tag = os.environ.get("AMAZON_PARTNER_TAG")
    country = os.environ.get("AMAZON_COUNTRY", "US")

    if not (credential_id and credential_secret and tag and AmazonCreatorsApi):
        return None
    try:
        return AmazonCreatorsApi(
            credential_id=credential_id,
            credential_secret=credential_secret,
            version=version,
            tag=tag,
            country=Country[country.upper()],
        )
    except Exception:
        return None


def run_watch(watch: dict) -> dict:
    """
    Executes one saved watch against the Creators API, filters results,
    and stores new candidates. Returns a summary dict for the caller/UI.
    """
    api = _get_api()
    if not api:
        return {
            "ok": False,
            "error": "Amazon Creators API isn't configured (check .env), or "
                     "you don't yet meet Amazon's qualified-sales threshold "
                     "for API access.",
        }

    kwargs = {
        "search_index": watch["search_index"],
        "item_count": min(int(watch.get("item_count") or 10), 10),
        "sort_by": SortBy.FEATURED,
        "resources": [
            SearchItemsResource.ITEM_INFO_DOT_TITLE,
            SearchItemsResource.OFFERS_V2_DOT_LISTINGS_DOT_PRICE,
            SearchItemsResource.IMAGES_DOT_PRIMARY_DOT_LARGE,
            SearchItemsResource.CUSTOMER_REVIEWS_DOT_STAR_RATING,
            SearchItemsResource.CUSTOMER_REVIEWS_DOT_COUNT,
        ],
    }
    if watch.get("keywords"):
        kwargs["keywords"] = watch["keywords"]
    if watch.get("min_price"):
        kwargs["min_price"] = int(float(watch["min_price"]) * 100)  # API wants cents
    if watch.get("max_price"):
        kwargs["max_price"] = int(float(watch["max_price"]) * 100)

    try:
        result = api.search_items(**kwargs)
        items = result.items or []
    except Exception as e:
        return {"ok": False, "error": f"Creators API search failed: {e}"}

    min_rating = float(watch["min_rating"]) if watch.get("min_rating") else 0
    min_reviews = int(watch["min_reviews"]) if watch.get("min_reviews") else 0
    similarity_threshold = float(
        os.environ.get("SCOUT_SIMILARITY_THRESHOLD", DEFAULT_SIMILARITY_THRESHOLD)
    )

    known_products = db.all_known_products()
    known_asins = {p["asin"] for p in known_products}
    known_normalized_titles = [_normalize_title(p["title"]) for p in known_products if p["title"]]

    found, kept, skipped_dupe, skipped_similar, skipped_filter = 0, 0, 0, 0, 0

    for item in items:
        found += 1
        asin = item.asin
        if not asin:
            continue

        rating = None
        review_count = None
        if item.customer_reviews:
            rating = item.customer_reviews.star_rating
            review_count = item.customer_reviews.count

        if rating is not None and rating < min_rating:
            skipped_filter += 1
            continue
        if review_count is not None and review_count < min_reviews:
            skipped_filter += 1
            continue

        if asin in known_asins or db.asin_already_pinned(asin):
            skipped_dupe += 1
            continue

        title = None
        if item.item_info and item.item_info.title:
            title = item.item_info.title.display_value
        title = title or "Untitled product"

        if _is_similar_to_known(title, known_normalized_titles, similarity_threshold):
            skipped_similar += 1
            continue

        price = None
        if item.offers_v2 and item.offers_v2.listings:
            price = item.offers_v2.listings[0].price.money.display_amount

        image_url = None
        if item.images and item.images.primary and item.images.primary.large:
            image_url = item.images.primary.large.url

        new_id = db.add_candidate({
            "watch_id": watch["id"],
            "asin": asin,
            "title": title,
            "price": price,
            "image_url": image_url,
            "rating": rating,
            "review_count": review_count,
        })
        if new_id:
            kept += 1
            # Keep the in-memory pools current so later items in this same
            # batch are checked against what we just added too.
            known_asins.add(asin)
            known_normalized_titles.append(_normalize_title(title))
        else:
            skipped_dupe += 1

    db.touch_watch_run(watch["id"])

    return {
        "ok": True,
        "found": found,
        "kept": kept,
        "skipped_duplicate": skipped_dupe,
        "skipped_similar": skipped_similar,
        "skipped_filter": skipped_filter,
    }


def run_all_active_watches() -> list[dict]:
    results = []
    for watch in db.list_watches(active_only=True):
        summary = run_watch(watch)
        summary["watch_id"] = watch["id"]
        summary["watch_label"] = watch["label"]
        results.append(summary)
    return results


def candidate_to_amazon_url(candidate: dict) -> str:
    """Builds a standard product URL so the existing generate pipeline
    (which parses ASIN + affiliate link from a URL) can be reused as-is."""
    country = os.environ.get("AMAZON_COUNTRY", "US")
    domain = {
        "US": "amazon.com", "UK": "amazon.co.uk", "CA": "amazon.ca",
        "DE": "amazon.de", "FR": "amazon.fr", "ES": "amazon.es",
        "IT": "amazon.it", "IN": "amazon.in", "JP": "amazon.co.jp",
    }.get(country.upper(), "amazon.com")
    return f"https://www.{domain}/dp/{candidate['asin']}"
