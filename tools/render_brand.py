"""Deterministic Pillow renderer for the Pawfly Home Assistant brand kit.

Writes the eight images Home Assistant serves straight from
``custom_components/pawfly/brand/`` (no brands-repo submission needed):

    icon.png        256x256      dark_icon.png        256x256
    icon@2x.png     512x512      dark_icon@2x.png     512x512
    logo.png        864x256      dark_logo.png        864x256
    logo@2x.png     1728x512     dark_logo@2x.png     1728x512

The mark: a horizontal LED light bar holding four dots (R, G, B, W - the light
is WRGB), a soft cone of aqua-white light beneath it, and a geometric fish
(leaf-shaped body + forked triangular tail, one eye) lit by the cone. Bar,
cone and fish share one vertical axis. ``../icon.svg`` is the same mark as
hand-authored vector art; this script does not read it.

The light-variant plate is deep teal/navy. ``dark_*`` variants use a light
plate with a deeper-teal mark so they stay legible on a dark HA header.

Pillow only, 4x supersampling, no randomness: re-running produces
byte-identical PNGs.

Usage: python3 tools/render_brand.py
"""

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
BRAND_DIR = REPO_ROOT / "custom_components" / "pawfly" / "brand"

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    """Liberation Sans Bold, then DejaVu Bold, then Pillow's default font."""
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


# ---------------------------------------------------------------------------
# Geometry, in a 256x256 reference space (identical numbers to ../icon.svg)
# ---------------------------------------------------------------------------

PLATE_RX = 56.0
AXIS_X = 128.0

BAR = (48.0, 30.0, 208.0, 58.0)  # x0, y0, x1, y1
BAR_RX = 14.0
LED_CY = 44.0
LED_XS = (74.0, 110.0, 146.0, 182.0)  # R, G, B, W
LED_WELL_R = 10.5
LED_R = 6.6

CONE_TOP_Y = 60.0
CONE_TOP_HALF_W = 34.0
CONE_BOTTOM_Y = 206.0
CONE_BOTTOM_HALF_W = 84.0

FISH_CY = 186.0
FISH_X0 = 58.0  # nose
FISH_LEN = 104.0  # nose -> body tip
FISH_HALF_H = 24.0
FISH_TAIL_APEX_BACK = 14.0  # tail apex sits this far inside the body tip
FISH_TAIL_LEN = 36.0  # body tip -> tail edge
FISH_TAIL_HALF_H = 26.0
FISH_TAIL_NOTCH = 26.0  # depth of the fork notch from the body tip
FISH_EYE = (80.0, FISH_CY - 4.0, 4.6)


def fish_body_points(steps=96):
    """Leaf outline: y = H*sin(pi*t^0.75) gives a rounded head and a pointed
    tail end, the same silhouette as a leaf."""
    top, bottom = [], []
    for i in range(steps + 1):
        t = i / steps
        x = FISH_X0 + FISH_LEN * t
        dy = FISH_HALF_H * math.sin(math.pi * t**0.75)
        top.append((x, FISH_CY - dy))
        bottom.append((x, FISH_CY + dy))
    return top + bottom[::-1]


def fish_tail_points():
    tip = FISH_X0 + FISH_LEN
    return [
        (tip - FISH_TAIL_APEX_BACK, FISH_CY),
        (tip + FISH_TAIL_LEN, FISH_CY - FISH_TAIL_HALF_H),
        (tip + FISH_TAIL_NOTCH, FISH_CY),
        (tip + FISH_TAIL_LEN, FISH_CY + FISH_TAIL_HALF_H),
    ]


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

LED_COLORS = (
    (0xFF, 0x4B, 0x4B),  # R
    (0x35, 0xDC, 0x7E),  # G
    (0x3D, 0x86, 0xFF),  # B
    (0xFF, 0xFF, 0xFF),  # W
)

LIGHT_VARIANT = {  # deep plate, glowing mark ("icon", "logo")
    "plate_top": (0x0F, 0x4A, 0x5C),
    "plate_bottom": (0x06, 0x1B, 0x2C),
    "plate_stroke": (0x5F, 0xE3, 0xE0, 70),
    "bar_top": (0xFF, 0xFB, 0xEE),
    "bar_bottom": (0xE6, 0xD9, 0xBD),
    "well": (0x08, 0x25, 0x33),
    "cone_top": (0xD4, 0xFF, 0xFA, 190),
    "cone_bottom": (0x4F, 0xD8, 0xDA, 6),
    "glow": (0x5F, 0xE9, 0xE4, 120),
    "bar_glow": (0xFF, 0xF1, 0xCF, 120),
    "fish": (0xC9, 0xFB, 0xF7),
    "eye": (0x08, 0x2B, 0x3A),
    "sheen": (255, 255, 255, 26),
}

DARK_VARIANT = {  # light plate, deep-teal mark ("dark_icon", "dark_logo")
    "plate_top": (0xFA, 0xFD, 0xFE),
    "plate_bottom": (0xDD, 0xEA, 0xEF),
    "plate_stroke": (0x0E, 0x5C, 0x6C, 60),
    "bar_top": (0x14, 0x6A, 0x7A),
    "bar_bottom": (0x08, 0x3B, 0x4B),
    "well": (0x04, 0x22, 0x2E),
    "cone_top": (0x0F, 0xA3, 0xB3, 150),
    "cone_bottom": (0x0F, 0xA3, 0xB3, 5),
    "glow": (0x0F, 0xA3, 0xB3, 60),
    "bar_glow": (0x0F, 0xA3, 0xB3, 60),
    "fish": (0x0B, 0x63, 0x74),
    "eye": (0xF4, 0xFC, 0xFD),
    "sheen": (255, 255, 255, 120),
}

WORDMARK_ON_LIGHT_BG = (0x07, 0x2C, 0x3A)  # logo.png
TAGLINE_ON_LIGHT_BG = (0x2A, 0x7F, 0x8F)
WORDMARK_ON_DARK_BG = (0xF2, 0xFA, 0xFC)  # dark_logo.png
TAGLINE_ON_DARK_BG = (0x9C, 0xC9, 0xD2)

SS = 4  # supersampling factor


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------


def _vgradient(w, h, top, bottom):
    """RGBA (w, h) image blending top -> bottom (each an RGB or RGBA tuple)."""
    top = tuple(top) + (255,) * (4 - len(top))
    bottom = tuple(bottom) + (255,) * (4 - len(bottom))
    column = Image.new("RGBA", (1, h))
    px = column.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        px[0, y] = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(4))
    return column.resize((w, h), Image.NEAREST)


def _sheen_band(w, h, rgba):
    """RGBA (w, h) band whose alpha eases out quadratically to 0 (no visible edge)."""
    column = Image.new("RGBA", (1, h))
    px = column.load()
    for y in range(h):
        px[0, y] = rgba[:3] + (round(rgba[3] * (1 - y / h) ** 2),)
    return column.resize((w, h), Image.NEAREST)


def _shape_layer(size, mask, fill):
    """Solid `fill` (RGB or RGBA image) clipped by an L mask; both `size` big."""
    layer = fill if isinstance(fill, Image.Image) else Image.new("RGBA", (size, size), fill)
    layer = layer.copy()
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))
    return layer


def _mask(size, painter):
    m = Image.new("L", (size, size), 0)
    painter(ImageDraw.Draw(m))
    return m


def render_mark(size, palette):
    """(size, size) RGBA icon: rounded plate with the bar/cone/fish mark."""
    big = size * SS
    k = big / 256.0

    def sc(pts):
        return [(x * k, y * k) for x, y in pts]

    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    # Plate: vertical gradient, soft top sheen, hairline stroke.
    plate_mask = _mask(big, lambda d: d.rounded_rectangle([0, 0, big - 1, big - 1], radius=PLATE_RX * k, fill=255))
    plate_fill = _vgradient(big, big, palette["plate_top"], palette["plate_bottom"])
    canvas.alpha_composite(_shape_layer(big, plate_mask, plate_fill))

    sheen_fill = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    sheen_fill.paste(_sheen_band(big, big // 2, palette["sheen"]), (0, 0))
    canvas.alpha_composite(_shape_layer(big, plate_mask, sheen_fill))

    # Everything below is clipped to the plate.
    content = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    # Bloom under the bar and around the fish.
    bar_x0, bar_y0, bar_x1, bar_y1 = BAR
    glow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse(
        [(AXIS_X - 62) * k, (bar_y1 - 14) * k, (AXIS_X + 62) * k, (bar_y1 + 22) * k],
        fill=palette["bar_glow"],
    )
    content.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10 * k)))

    # Light cone: trapezoid fading from source to fish, softened edges.
    cone_pts = [
        (AXIS_X - CONE_TOP_HALF_W, CONE_TOP_Y),
        (AXIS_X + CONE_TOP_HALF_W, CONE_TOP_Y),
        (AXIS_X + CONE_BOTTOM_HALF_W, CONE_BOTTOM_Y),
        (AXIS_X - CONE_BOTTOM_HALF_W, CONE_BOTTOM_Y),
    ]
    cone_mask = _mask(big, lambda d: d.polygon(sc(cone_pts), fill=255))
    cone_grad = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    y0, y1 = int(CONE_TOP_Y * k), int(CONE_BOTTOM_Y * k)
    band = _vgradient(big, y1 - y0, palette["cone_top"], palette["cone_bottom"])
    cone_grad.paste(band, (0, y0))
    cone = _shape_layer(big, cone_mask, cone_grad)
    content.alpha_composite(cone.filter(ImageFilter.GaussianBlur(1.6 * k)))

    # Glow pooled around the fish.
    pool = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(pool).ellipse(
        [(AXIS_X - 76) * k, (FISH_CY - 30) * k, (AXIS_X + 76) * k, (FISH_CY + 30) * k],
        fill=palette["glow"],
    )
    content.alpha_composite(pool.filter(ImageFilter.GaussianBlur(11 * k)))

    # Fish: leaf body + forked triangular tail, then the eye.
    fish_mask = _mask(
        big,
        lambda d: (d.polygon(sc(fish_body_points()), fill=255), d.polygon(sc(fish_tail_points()), fill=255)),
    )
    content.alpha_composite(_shape_layer(big, fish_mask, palette["fish"] + (255,)))
    ex, ey, er = FISH_EYE
    ImageDraw.Draw(content).ellipse(
        [(ex - er) * k, (ey - er) * k, (ex + er) * k, (ey + er) * k], fill=palette["eye"] + (255,)
    )

    # Light bar: gradient body, dark LED wells, R/G/B/W dots with a specular dot.
    bar_mask = _mask(
        big,
        lambda d: d.rounded_rectangle([bar_x0 * k, bar_y0 * k, bar_x1 * k, bar_y1 * k], radius=BAR_RX * k, fill=255),
    )
    bar_h = int((bar_y1 - bar_y0) * k)
    bar_grad = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    bar_grad.paste(_vgradient(big, bar_h, palette["bar_top"], palette["bar_bottom"]), (0, int(bar_y0 * k)))
    content.alpha_composite(_shape_layer(big, bar_mask, bar_grad))
    cd = ImageDraw.Draw(content)
    for x, rgb in zip(LED_XS, LED_COLORS):
        cd.ellipse(
            [(x - LED_WELL_R) * k, (LED_CY - LED_WELL_R) * k, (x + LED_WELL_R) * k, (LED_CY + LED_WELL_R) * k],
            fill=palette["well"] + (255,),
        )
        cd.ellipse(
            [(x - LED_R) * k, (LED_CY - LED_R) * k, (x + LED_R) * k, (LED_CY + LED_R) * k],
            fill=rgb + (255,),
        )

    canvas.alpha_composite(_shape_layer(big, plate_mask, content))

    stroke = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(stroke).rounded_rectangle(
        [k * 0.75, k * 0.75, big - 1 - k * 0.75, big - 1 - k * 0.75],
        radius=(PLATE_RX - 0.75) * k,
        outline=palette["plate_stroke"],
        width=max(1, round(1.5 * k)),
    )
    canvas.alpha_composite(stroke)

    return canvas.resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Wordmark lockup
# ---------------------------------------------------------------------------

LOGO_ASPECT = 864 / 256  # width / height (about 3.4x)
LOGO_MARK_FRAC = 0.86  # mark size as a fraction of canvas height
LOGO_LEFT_FRAC = 0.05
LOGO_GAP_FRAC = 0.075
LOGO_NAME_FRAC = 0.50  # "Pawfly" font size / canvas height
LOGO_TAG_FRAC = 0.19  # "Aquarium Light" font size / canvas height
LOGO_TRACK_FRAC = 0.012
LOGO_LINE_GAP_FRAC = 0.07


def _tracked_width(draw, text, font, tracking):
    return sum(draw.textlength(ch, font=font) for ch in text) + tracking * (len(text) - 1)


def _draw_tracked(draw, x, y, text, font, fill, tracking):
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def render_logo(height, palette, name_rgb, tag_rgb):
    """RGBA lockup: mark on the left, "Pawfly" over "Aquarium Light" on the
    right, transparent background, exactly `height` tall."""
    width = round(height * LOGO_ASPECT)
    mark_size = round(height * LOGO_MARK_FRAC)
    mark = render_mark(mark_size, palette)

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    left = round(height * LOGO_LEFT_FRAC)
    canvas.alpha_composite(mark, (left, (height - mark_size) // 2))

    name_font = load_font(round(height * LOGO_NAME_FRAC))
    tag_font = load_font(round(height * LOGO_TAG_FRAC))
    tracking = height * LOGO_TRACK_FRAC
    draw = ImageDraw.Draw(canvas)

    # Vertically centre the two-line block on ink bounds, not font ascent.
    n_l, n_t, n_r, n_b = draw.textbbox((0, 0), "Pawfly", font=name_font)
    t_l, t_t, t_r, t_b = draw.textbbox((0, 0), "Aquarium Light", font=tag_font)
    line_gap = height * LOGO_LINE_GAP_FRAC
    block_h = (n_b - n_t) + line_gap + (t_b - t_t)
    top = (height - block_h) / 2
    text_x = left + mark_size + height * LOGO_GAP_FRAC

    name_w = _tracked_width(draw, "Pawfly", name_font, tracking)
    _draw_tracked(draw, text_x - n_l, top - n_t, "Pawfly", name_font, name_rgb + (255,), tracking)
    tag_y = top + (n_b - n_t) + line_gap
    _draw_tracked(draw, text_x - t_l, tag_y - t_t, "Aquarium Light", tag_font, tag_rgb + (255,), tracking * 0.6)

    tag_w = _tracked_width(draw, "Aquarium Light", tag_font, tracking * 0.6)
    right_edge = text_x + max(name_w, tag_w)
    assert right_edge <= width - height * 0.03, f"wordmark clipped at height {height}: {right_edge} > {width}"
    return canvas


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _save_pair(master, stem):
    """<stem>@2x.png from the 512-based master, <stem>.png as an exact half."""
    master.save(BRAND_DIR / f"{stem}@2x.png")
    half = (master.width // 2, master.height // 2)
    master.resize(half, Image.LANCZOS).save(BRAND_DIR / f"{stem}.png")


def main():
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    _save_pair(render_mark(512, LIGHT_VARIANT), "icon")
    _save_pair(render_mark(512, DARK_VARIANT), "dark_icon")
    _save_pair(render_logo(512, LIGHT_VARIANT, WORDMARK_ON_LIGHT_BG, TAGLINE_ON_LIGHT_BG), "logo")
    _save_pair(render_logo(512, DARK_VARIANT, WORDMARK_ON_DARK_BG, TAGLINE_ON_DARK_BG), "dark_logo")


if __name__ == "__main__":
    main()
