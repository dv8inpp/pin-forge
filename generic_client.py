"""
Generic retailer support: works with any product page (Walmart, Best Buy,
Target, eBay, or any individual merchant on ShareASale/CJ/Rakuten/Awin/
Impact) without needing a bespoke API integration per network.

Two things happen for a non-Amazon URL:

1. Product data (title/price/image) comes from scraping the page's own
   structured markup -- JSON-LD Product schema first (the schema.org
   standard most e-commerce platforms already emit for SEO), falling back
   to Open Graph meta tags. This works out of the box on most retail
   sites with zero configuration.

2. The affiliate/tracking link comes from a "retailer profile" you
   configure once per network: a domain pattern to match ("walmart.com")
   and a link template with a {url} placeholder. Most networks
   (ShareASale, CJ, Awin, and many Impact programs) support exactly this
   kind of templated deep link -- generate one example from your network
   dashboard, note the pattern, and PinForge applies it to every product
   automatically from then on. If no profile matches a domain, PinForge
   still scrapes the product data but asks you to paste a ready-made
   affiliate link directly (from the network's per-product deep-link
   generator) instead.
"""
import json
import re
from urllib.parse import urlparse, quote
import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}


def get_domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def apply_affiliate_template(template: str, product_url: str) -> str:
    """Substitutes {url} in a network's deep-link template with the
    URL-encoded product page URL. Falls back to returning the product
    URL unchanged if no template is configured."""
    if not template:
        return product_url
    if "{url}" in template:
        return template.replace("{url}", quote(product_url, safe=""))
    if "{url_raw}" in template:
        return template.replace("{url_raw}", product_url)
    return template  # static template with no placeholder -- used as-is


def fetch_generic_product(url: str) -> dict:
    """
    Returns {title, price, image_url} scraped from the page's own
    structured data. Raises ValueError if nothing usable was found.
    """
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=12)
        resp.raise_for_status()
    except Exception as e:
        raise ValueError(f"Could not reach that product page: {e}")

    soup = BeautifulSoup(resp.text, "html.parser")

    from_jsonld = _from_json_ld(soup)
    if from_jsonld:
        return from_jsonld

    from_og = _from_open_graph(soup)
    if from_og:
        return from_og

    raise ValueError(
        "Could not find product details on that page (checked JSON-LD "
        "structured data and Open Graph tags). Some sites block "
        "automated requests -- try a different product page, or this "
        "retailer may need a manual data entry fallback."
    )


def _from_json_ld(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        for node in _flatten_jsonld(data):
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]
            if "Product" not in types:
                continue

            title = node.get("name")
            image_url = _first(node.get("image"))

            price = None
            offers = node.get("offers")
            offer = offers[0] if isinstance(offers, list) and offers else offers
            if isinstance(offer, dict):
                amount = offer.get("price") or offer.get("lowPrice")
                currency = offer.get("priceCurrency", "")
                if amount:
                    price = _format_price(amount, currency)

            if title and image_url:
                return {"title": title, "price": price, "image_url": image_url}
    return None


def _flatten_jsonld(data):
    """JSON-LD can be a single object, a list, or wrapped in @graph."""
    if isinstance(data, list):
        for item in data:
            yield from _flatten_jsonld(item)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _flatten_jsonld(data["@graph"])
        else:
            yield data


def _from_open_graph(soup: BeautifulSoup) -> dict | None:
    def meta(prop):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return tag.get("content") if tag else None

    title = meta("og:title") or (soup.title.get_text(strip=True) if soup.title else None)
    image_url = meta("og:image")

    price = None
    amount = meta("product:price:amount") or meta("og:price:amount")
    currency = meta("product:price:currency") or meta("og:price:currency") or ""
    if amount:
        price = _format_price(amount, currency)
    if not price:
        # Last resort: a handful of very common price-display patterns.
        price_tag = soup.select_one("[itemprop=price], .price, .product-price")
        if price_tag:
            text = price_tag.get_text(strip=True) or price_tag.get("content", "")
            match = re.search(r"[\d,]+\.?\d*", text)
            if match:
                price = f"${match.group()}"

    if title and image_url:
        return {"title": title, "price": price, "image_url": image_url}
    return None


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _format_price(amount, currency: str) -> str:
    symbol = {"USD": "$", "GBP": "£", "EUR": "€", "CAD": "$", "AUD": "$"}.get(currency, "")
    return f"{symbol}{amount}" if symbol else f"{amount} {currency}".strip()
