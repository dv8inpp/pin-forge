"""
Publishes a landing page to your WordPress site (reallygoodgadgets.store)
for each pin, so Pinterest traffic lands on your own site first -- where
you control the ad placement, keep the visitor on your domain, and build
SEO value over time -- rather than sending clicks straight to Amazon.

Auth uses WordPress's built-in "Application Passwords" feature (core
since WordPress 5.6, no plugin required): Users -> Profile -> Application
Passwords in wp-admin, generate one scoped to this integration. This is
NOT your real WordPress login password -- it's a separate, revokable
credential, which is exactly what you want handing to a script running on
a Raspberry Pi.

The "Shop Now" button on the published page still routes through
PinForge's own /go/<pin_id> redirect, so click tracking keeps working
across the whole funnel: Pinterest -> your WordPress page (tracked by
whatever analytics you already run there, e.g. Site Kit) -> /go/<id>
(tracked by PinForge) -> the real affiliate link.
"""
import os
import requests

_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _get_auth():
    user = os.environ.get("WORDPRESS_USERNAME")
    app_password = os.environ.get("WORDPRESS_APP_PASSWORD")
    if not (user and app_password):
        return None
    return (user, app_password)


def _site_url() -> str:
    return os.environ.get("WORDPRESS_SITE_URL", "").rstrip("/")


def is_configured() -> bool:
    return bool(_get_auth() and _site_url())


def _download_image(url: str) -> tuple[bytes, str]:
    """Returns (bytes, content_type) for a product image URL, so it can
    be re-uploaded to WordPress's own media library rather than hotlinked."""
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
    return resp.content, content_type


def _upload_media(image_bytes: bytes, filename: str, content_type: str) -> int:
    auth = _get_auth()
    site = _site_url()
    resp = requests.post(
        f"{site}/wp-json/wp/v2/media",
        auth=auth,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": content_type,
        },
        data=image_bytes,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _build_content_html(title: str, price: str | None, description: str,
                         image_url: str, tracking_link: str, brand_name: str) -> str:
    # Note: the product image is not repeated here -- create_landing_page()
    # already uploads it and sets it as the post's featured image, and the
    # site's single.php template renders that above the content.
    price_line = f"<p><strong>{price}</strong></p>" if price else ""
    return f"""
<h2>{title}</h2>
{price_line}
<p>{description}</p>
<p>
  <a href="{tracking_link}" target="_blank" rel="sponsored nofollow noopener" class="rgg-shop-now">
    Shop Now &rarr;
  </a>
</p>
<p><em>As an affiliate partner, {brand_name} earns from qualifying purchases made through links on this page.</em></p>
""".strip()


def create_landing_page(
    pin_title: str,
    pin_description: str,
    price: str | None,
    product_image_url: str,
    tracking_link: str,
    brand_name: str,
    status: str = "publish",
) -> dict:
    """
    Creates a WordPress post for this product: downloads the product photo
    and re-uploads it to your media library (rather than hotlinking Amazon's
    CDN), builds a simple product page around it with a tracked "Shop Now"
    button, and publishes it. Returns {"id": ..., "url": ...}.
    Raises ValueError with a clear message on any failure.
    """
    if not is_configured():
        raise ValueError(
            "WordPress isn't configured. Set WORDPRESS_SITE_URL, "
            "WORDPRESS_USERNAME, and WORDPRESS_APP_PASSWORD in .env."
        )

    try:
        image_bytes, content_type = _download_image(product_image_url)
    except Exception as e:
        raise ValueError(f"Could not download the product image to upload: {e}")

    ext = "png" if "png" in content_type else "jpg"
    filename = f"{pin_title[:40].strip().replace(' ', '-').lower()}.{ext}"

    try:
        media_id = _upload_media(image_bytes, filename, content_type)
    except requests.HTTPError as e:
        raise ValueError(f"WordPress rejected the image upload: {e}")
    except Exception as e:
        raise ValueError(f"Could not reach WordPress to upload the image: {e}")

    content_html = _build_content_html(
        pin_title, price, pin_description, product_image_url, tracking_link, brand_name
    )

    auth = _get_auth()
    site = _site_url()
    try:
        resp = requests.post(
            f"{site}/wp-json/wp/v2/posts",
            auth=auth,
            json={
                "title": pin_title,
                "content": content_html,
                "status": status,
                "featured_media": media_id,
            },
            timeout=30,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise ValueError(f"WordPress rejected the page: {e}")
    except Exception as e:
        raise ValueError(f"Could not reach WordPress to create the page: {e}")

    data = resp.json()
    return {"id": data["id"], "url": data["link"]}
