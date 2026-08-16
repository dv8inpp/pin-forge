"""
Generates a Pinterest-ready pin image (1000x1500, the recommended 2:3 ratio)
from a product photo + title text, with a branded footer bar.

No external design tool needed -- pure Pillow. Uses bundled DejaVu fonts
that ship with most Linux systems / the Pillow wheel's test fonts as a
safe default, but you can drop your own .ttf into static/fonts/ and point
FONT_BOLD / FONT_REGULAR at them for on-brand typography.
"""
import os
import io
import textwrap
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

PIN_WIDTH = 1000
PIN_HEIGHT = 1500
FOOTER_HEIGHT = 260

FONT_DIR = os.path.join(os.path.dirname(__file__), "static", "fonts")
FONT_BOLD = os.path.join(FONT_DIR, "Brand-Bold.ttf")
FONT_REGULAR = os.path.join(FONT_DIR, "Brand-Regular.ttf")


def _font(path, size, fallback_bold=False):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        # Falls back to Pillow's built-in bitmap font if no TTF is present.
        return ImageFont.load_default(size) if hasattr(ImageFont, "load_default") else ImageFont.load_default()


def _download_image(url: str) -> Image.Image:
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGBA")


def _fit_cover(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Resize+crop an image to exactly fill target_w x target_h (cover, not stretch)."""
    return ImageOps.fit(img, (target_w, target_h), method=Image.LANCZOS, centering=(0.5, 0.4))


def _wrap_text(draw, text, font, max_width):
    lines = []
    for raw_line in text.split("\n"):
        words = raw_line.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def generate_pin(
    image_url: str,
    title: str,
    price: str | None,
    brand_name: str,
    brand_website: str,
    accent_color: str,
    output_path: str,
) -> str:
    """Builds the pin and writes it to output_path (PNG). Returns output_path."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    canvas = Image.new("RGB", (PIN_WIDTH, PIN_HEIGHT), "#FFFFFF")

    # --- Product photo fills the top section ---
    photo_area_h = PIN_HEIGHT - FOOTER_HEIGHT
    try:
        product_img = _download_image(image_url)
        # Composite onto white first in case of transparency, then cover-crop
        bg = Image.new("RGBA", product_img.size, "white")
        bg.paste(product_img, mask=product_img.split()[-1])
        product_img = bg.convert("RGB")
        fitted = _fit_cover(product_img, PIN_WIDTH, photo_area_h)
    except Exception:
        fitted = Image.new("RGB", (PIN_WIDTH, photo_area_h), "#EEEEEE")

    canvas.paste(fitted, (0, 0))

    # --- Soft gradient at the bottom of the photo so title text pops ---
    gradient_h = 260
    gradient = Image.new("L", (1, gradient_h), color=0)
    for y in range(gradient_h):
        gradient.putpixel((0, y), int(180 * (y / gradient_h)))
    gradient = gradient.resize((PIN_WIDTH, gradient_h))
    shadow = Image.new("RGB", (PIN_WIDTH, gradient_h), "black")
    canvas.paste(shadow, (0, photo_area_h - gradient_h), mask=gradient)

    draw = ImageDraw.Draw(canvas)

    # --- Price badge, top-right ---
    if price:
        badge_font = _font(FONT_BOLD, 42)
        pad_x, pad_y = 28, 16
        text_w = draw.textlength(price, font=badge_font)
        badge_w, badge_h = text_w + pad_x * 2, 42 + pad_y * 2
        badge_x0, badge_y0 = PIN_WIDTH - badge_w - 32, 32
        draw.rounded_rectangle(
            [badge_x0, badge_y0, badge_x0 + badge_w, badge_y0 + badge_h],
            radius=badge_h / 2, fill=accent_color,
        )
        draw.text(
            (badge_x0 + pad_x, badge_y0 + pad_y - 4), price,
            font=badge_font, fill="white",
        )

    # --- Title, bottom of photo area (over the gradient) ---
    title_font = _font(FONT_BOLD, 58)
    max_text_w = PIN_WIDTH - 80
    lines = _wrap_text(draw, title, title_font, max_text_w)[:3]  # cap at 3 lines
    if len(_wrap_text(draw, title, title_font, max_text_w)) > 3:
        lines[-1] = lines[-1].rstrip() + "…"

    line_height = 68
    text_block_h = line_height * len(lines)
    y = photo_area_h - 40 - text_block_h
    for line in lines:
        draw.text((40, y), line, font=title_font, fill="white")
        y += line_height

    # --- Footer bar: brand name + website ---
    draw.rectangle([0, photo_area_h, PIN_WIDTH, PIN_HEIGHT], fill="#FAFAFA")
    draw.line([0, photo_area_h, PIN_WIDTH, photo_area_h], fill=accent_color, width=6)

    brand_font = _font(FONT_BOLD, 44)
    site_font = _font(FONT_REGULAR, 34)
    cta_font = _font(FONT_BOLD, 38)

    draw.text((40, photo_area_h + 40), brand_name, font=brand_font, fill="#111111")
    draw.text((40, photo_area_h + 100), brand_website, font=site_font, fill="#555555")

    cta_text = "Shop the link >"
    cta_w = draw.textlength(cta_text, font=cta_font)
    draw.rounded_rectangle(
        [40, photo_area_h + 165, 40 + cta_w + 48, photo_area_h + 165 + 66],
        radius=33, outline=accent_color, width=3,
    )
    draw.text((64, photo_area_h + 180), cta_text, font=cta_font, fill=accent_color)

    canvas.save(output_path, "PNG", optimize=True)
    return output_path
