"""
Publishes each pin as a real WooCommerce product on your site, using
WooCommerce's built-in "External/Affiliate Product" type -- a product page
with a customizable button (e.g. "Shop Now") that links straight to an
external URL instead of "Add to Cart." That's exactly the right fit for
affiliate marketing: a real product listing, with none of the inventory/
fulfillment machinery a normal WooCommerce product implies.

Because it's a real product (not just a blog post), it shows up wherever
your theme already surfaces products -- New Arrivals sections, the main
Shop page, category listings -- with zero theme changes needed.

Auth uses WooCommerce's own REST API keys (Consumer Key/Secret), which are
separate from a WordPress login or Application Password. Generate one at:
WooCommerce -> Settings -> Advanced -> REST API -> Add key (Read/Write).

The "Shop Now" button still routes through PinForge's own /go/<pin_id>
redirect, so click tracking works exactly the same as the direct-link path.
"""
import os
import re
import requests


def _get_auth():
    key = os.environ.get("WOOCOMMERCE_CONSUMER_KEY")
    secret = os.environ.get("WOOCOMMERCE_CONSUMER_SECRET")
    if not (key and secret):
        return None
    return (key, secret)


def _site_url() -> str:
    return os.environ.get("WORDPRESS_SITE_URL", "").rstrip("/")


def is_configured() -> bool:
    return bool(_get_auth() and _site_url())


def _clean_price(price: str | None) -> str | None:
    """WooCommerce wants a bare numeric string ('29.99'), not '$29.99'."""
    if not price:
        return None
    match = re.search(r"[\d]+\.?\d*", price.replace(",", ""))
    return match.group() if match else None


def create_affiliate_product(
    pin_title: str,
    pin_description: str,
    price: str | None,
    product_image_url: str,
    tracking_link: str,
    button_text: str = "Shop Now",
    status: str = "publish",
) -> dict:
    """
    Creates a WooCommerce "external" product. WooCommerce fetches and
    imports product_image_url server-side -- no separate upload step
    needed on our end, unlike the WordPress media API.
    Returns {"id": ..., "url": ...}. Raises ValueError on any failure.
    """
    if not is_configured():
        raise ValueError(
            "WooCommerce isn't configured. Set WORDPRESS_SITE_URL, "
            "WOOCOMMERCE_CONSUMER_KEY, and WOOCOMMERCE_CONSUMER_SECRET in .env."
        )

    payload = {
        "name": pin_title,
        "type": "external",
        "external_url": tracking_link,
        "button_text": button_text,
        "description": pin_description,
        "short_description": pin_description,
        "images": [{"src": product_image_url}],
        "status": status,
    }
    regular_price = _clean_price(price)
    if regular_price:
        payload["regular_price"] = regular_price

    try:
        resp = requests.post(
            f"{_site_url()}/wp-json/wc/v3/products",
            auth=_get_auth(),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise ValueError(f"WooCommerce rejected the product: {e.response.text if e.response is not None else e}")
    except Exception as e:
        raise ValueError(f"Could not reach WooCommerce to create the product: {e}")

    data = resp.json()
    return {"id": data["id"], "url": data["permalink"]}
