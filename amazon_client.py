"""
Wraps the Amazon Creators API -- the official replacement for the old
Product Advertising API (PA-API), which Amazon fully retired on
May 15, 2026. Credentials are obtained from Associates Central under
Tools -> Creators API, and look nothing like the old AWS-style access
key/secret key pair: it's a Credential ID + Credential Secret, OAuth2-based.

Creators API access requires at least 10 qualified sales in the trailing
30 days -- if you're a newer site without that yet, this module falls
back to lightweight HTML scraping of the public product page (title +
main image only -- no live price) so the app still works end to end
while you build up sales history.
"""
import os
import re
import requests
from bs4 import BeautifulSoup

try:
    from amazon_creatorsapi import AmazonCreatorsApi, Country
    from amazon_creatorsapi.models import GetItemsResource
except ImportError:
    AmazonCreatorsApi = None
    Country = None

ASIN_RE = re.compile(r"/(?:dp|gp/product|product)/([A-Z0-9]{10})")

# Creators API domain-code mapping doubles as our affiliate-link builder.
_DOMAIN_BY_COUNTRY = {
    "US": "amazon.com", "UK": "amazon.co.uk", "CA": "amazon.ca",
    "DE": "amazon.de", "FR": "amazon.fr", "ES": "amazon.es",
    "IT": "amazon.it", "IN": "amazon.in", "JP": "amazon.co.jp",
    "AU": "amazon.com.au", "BR": "amazon.com.br", "MX": "amazon.com.mx",
    "NL": "amazon.nl", "SE": "amazon.se", "SG": "amazon.sg",
    "TR": "amazon.com.tr", "AE": "amazon.ae", "SA": "amazon.sa",
    "PL": "amazon.pl", "BE": "amazon.com.be",
}


def extract_asin(url: str) -> str | None:
    match = ASIN_RE.search(url)
    if match:
        return match.group(1)
    match = re.search(r"[?&]asin=([A-Z0-9]{10})", url, re.IGNORECASE)
    return match.group(1).upper() if match else None


def build_affiliate_link(asin: str, partner_tag: str, country: str = "US") -> str:
    domain = _DOMAIN_BY_COUNTRY.get(country.upper(), "amazon.com")
    return f"https://www.{domain}/dp/{asin}?tag={partner_tag}"


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


def fetch_product(amazon_url: str) -> dict:
    """
    Returns a dict: {asin, title, price, image_url, affiliate_link, source}
    'source' is 'creatorsapi' when official data was used, or 'scrape' as
    fallback. Raises ValueError if no ASIN could be found or no data could
    be fetched from either path.
    """
    asin = extract_asin(amazon_url)
    if not asin:
        raise ValueError("Could not find a product ASIN in that URL. "
                          "Use a standard Amazon product page URL (contains /dp/XXXXXXXXXX).")

    partner_tag = os.environ.get("AMAZON_PARTNER_TAG", "")
    country = os.environ.get("AMAZON_COUNTRY", "US")
    affiliate_link = build_affiliate_link(asin, partner_tag, country)

    api = _get_api()
    if api:
        try:
            items = api.get_items(
                [asin],
                resources=[
                    GetItemsResource.ITEM_INFO_DOT_TITLE,
                    GetItemsResource.OFFERS_V2_DOT_LISTINGS_DOT_PRICE,
                    GetItemsResource.IMAGES_DOT_PRIMARY_DOT_LARGE,
                ],
            )
            if items:
                item = items[0]
                title = None
                if item.item_info and item.item_info.title:
                    title = item.item_info.title.display_value

                price = None
                if item.offers_v2 and item.offers_v2.listings:
                    price = item.offers_v2.listings[0].price.money.display_amount

                image_url = None
                if item.images and item.images.primary and item.images.primary.large:
                    image_url = item.images.primary.large.url

                return {
                    "asin": asin,
                    "title": title or "Untitled product",
                    "price": price,
                    "image_url": image_url,
                    "affiliate_link": affiliate_link,
                    "source": "creatorsapi",
                }
            creators_error = "Creators API returned no item for this ASIN."
        except Exception as e:
            creators_error = str(e)
    else:
        creators_error = (
            "Creators API credentials not configured, or you don't yet meet "
            "Amazon's qualified-sales threshold for API access."
        )

    # ---- Fallback: light scrape of the public page (title + og:image only) ----
    scraped = _scrape_fallback(amazon_url)
    if scraped:
        scraped["asin"] = asin
        scraped["affiliate_link"] = affiliate_link
        scraped["source"] = "scrape"
        return scraped

    raise ValueError(f"Could not fetch product data. Creators API: {creators_error}")


def _scrape_fallback(url: str) -> dict | None:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
    except Exception:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    title_tag = soup.find(id="productTitle") or soup.find("meta", property="og:title")
    title = None
    if title_tag:
        title = title_tag.get_text(strip=True) if title_tag.name != "meta" else title_tag.get("content")

    image_tag = soup.find("img", id="landingImage") or soup.find("meta", property="og:image")
    image_url = None
    if image_tag:
        image_url = image_tag.get("content") or image_tag.get("src")

    price = None
    price_tag = soup.select_one(".a-price .a-offscreen")
    if price_tag:
        price = price_tag.get_text(strip=True)

    if not title and not image_url:
        return None

    return {
        "title": title or "Untitled product",
        "price": price,
        "image_url": image_url,
    }
