"""WICARA design system for Manim.

Ports the pitch deck's tokens (wicara-deck-template/css/tokens.css) to video, so
a rendered explanation looks like the product rather than like stock Manim.

The deck has two grounds. The light one — white cards on a lavender wash — is
its default. The dark one is what the deck calls the *ink movement*: an ink
plate, one violet glow, hairline rules, and text in three tiers. Video is
watched in a dark player at full bleed, so this module uses the ink ground for
every scene. Same tokens either way; nothing here is invented.

Typography is Poppins, bundled as TTFs beside this file and registered with
Pango at import. It is the same family the mobile app renders through
`GoogleFonts.poppinsTextTheme`, so app, deck and video finally agree.
"""

from __future__ import annotations

import glob
import os

import numpy as np

try:  # Manim always ships manimpango; guard so imports never hard-fail.
    import manimpango
except ImportError:  # pragma: no cover
    manimpango = None


# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")

#: Set to the Poppins family name once registration succeeds, else "" so the
#: helpers below fall back to Pango's default rather than raising.
FONT_FAMILY = ""


def register_fonts() -> str:
    """Register the bundled Poppins faces. Returns the family name, or ""."""
    global FONT_FAMILY
    if FONT_FAMILY:
        return FONT_FAMILY
    if manimpango is None:
        return ""
    registered = False
    for path in sorted(glob.glob(os.path.join(_FONT_DIR, "*.ttf"))):
        try:
            registered |= bool(manimpango.register_font(path))
        except Exception:  # pragma: no cover - font backend differences
            continue
    if registered:
        try:
            families = set(manimpango.list_fonts())
        except Exception:  # pragma: no cover
            families = set()
        if "Poppins" in families:
            FONT_FAMILY = "Poppins"
    return FONT_FAMILY


register_fonts()


def font_kwargs(weight_hint: str = "regular") -> dict:
    """Text() kwargs that pin the brand family when it is available.

    Poppins registers its heavier cuts as separate families ("Poppins SemiBold"
    and so on), so asking for BOLD on the base family alone gives Pango a
    synthetic emboldening. Pointing at the real face keeps the letterforms.
    """
    if not FONT_FAMILY:
        return {}
    face = {
        "regular": "Poppins",
        "medium": "Poppins Medium",
        "semibold": "Poppins SemiBold",
        "bold": "Poppins SemiBold",
        "extrabold": "Poppins ExtraBold",
    }.get(weight_hint, "Poppins")
    try:
        available = set(manimpango.list_fonts()) if manimpango else set()
    except Exception:  # pragma: no cover
        available = set()
    return {"font": face if face in available else FONT_FAMILY}


# --------------------------------------------------------------------------
# Palette — tokens.css, DARK GROUND section
# --------------------------------------------------------------------------

INK = "#0B1233"          # --ink, the plate
INK_LIFT = "#131C46"     # a half-step up, for banding the plate
VIOLET = "#6A2FD4"       # --violet, the glow and the far end of gradients
BLUE = "#2436D8"         # --blue
BLUE_DEEP = "#1B27A8"    # --blue-deep
GOLD = "#FFD98A"         # --gold, emphasis only

ON_INK = "#FFFFFF"       # --on-ink, titles
ON_INK_2 = "#C9CEEA"     # --on-ink-2, body
ON_INK_3 = "#9AA2C8"     # --on-ink-3, captions and eyebrows
BLUE_ON_INK = "#B9C0FF"  # --blue-on-ink, the headline accent

GOOD = "#17B0A0"         # --good
ALERT = "#CE2140"        # --alert

#: --chip-1..5. Order matters: amber and teal are interleaved between blue and
#: violet because adjacent slots are the pairs a viewer compares, and blue
#: beside violet measured ΔE 0.8 under protanopia.
CHIPS = ["#2436D8", "#F0A02C", "#6A2FD4", "#17B0A0", "#D8399B"]

#: Semantic aliases so templates stop reaching for stock YELLOW/GREEN/RED.
ACCENT = BLUE_ON_INK
HIGHLIGHT = GOLD
POSITIVE = GOOD
NEGATIVE = ALERT
RULE = "#3A4270"         # hairlines on ink, ~--border-dk flattened
GRID = "#1C2550"         # the technical grid behind the visual


def chip(index: int) -> str:
    """Cycle the chip palette by position, as the deck does."""
    return CHIPS[index % len(CHIPS)]


# --------------------------------------------------------------------------
# Swappable palettes
# --------------------------------------------------------------------------
#
# The values above are the WICARA deck's own ground, and they stay the default.
# But a generated video should be able to follow the learner's context or stated
# preference, so the palette is data rather than a constant: every name below
# supplies the same token set, and use_palette() rebinds the module.
#
# A palette only has to declare what it changes. Anything it leaves out is
# inherited from "ink", which keeps a new palette to a handful of lines and
# guarantees no token can go undefined.

_BASE = {
    "INK": "#0B1233", "INK_LIFT": "#131C46", "VIOLET": "#6A2FD4",
    "BLUE": "#2436D8", "BLUE_DEEP": "#1B27A8", "GOLD": "#FFD98A",
    "ON_INK": "#FFFFFF", "ON_INK_2": "#C9CEEA", "ON_INK_3": "#9AA2C8",
    "BLUE_ON_INK": "#B9C0FF", "GOOD": "#17B0A0", "ALERT": "#CE2140",
    "CHIPS": ["#2436D8", "#F0A02C", "#6A2FD4", "#17B0A0", "#D8399B"],
    "RULE": "#3A4270", "GRID": "#1C2550",
}

PALETTES = {
    # The deck ground. Violet glow over deep indigo.
    "ink": {},

    # Cool teal-green aurora on near-black. Reads calm; good for biology and
    # earth science.
    "aurora": {
        "INK": "#04140F", "INK_LIFT": "#0A2018", "VIOLET": "#0F7A63",
        "BLUE": "#1C8F7A", "BLUE_DEEP": "#0B5F4E", "GOLD": "#C8F2A0",
        "ON_INK_2": "#CBE7DC", "ON_INK_3": "#8FB3A5", "BLUE_ON_INK": "#8FE8CE",
        "GOOD": "#5BD99A", "ALERT": "#E4614C",
        "CHIPS": ["#1C8F7A", "#C8F2A0", "#4EA8DE", "#5BD99A", "#E08D3C"],
        "RULE": "#26483D", "GRID": "#0D2A21",
    },

    # Warm ember on charcoal. Chemistry, energy, anything about heat.
    "ember": {
        "INK": "#170B0B", "INK_LIFT": "#251312", "VIOLET": "#B33A1E",
        "BLUE": "#C4562A", "BLUE_DEEP": "#8A2F16", "GOLD": "#FFC96B",
        "ON_INK_2": "#EFD6CB", "ON_INK_3": "#BFA096", "BLUE_ON_INK": "#FFB08A",
        "GOOD": "#8CC152", "ALERT": "#FF5C5C",
        "CHIPS": ["#C4562A", "#FFC96B", "#B33A1E", "#8CC152", "#E0619B"],
        "RULE": "#4A2A24", "GRID": "#2A1614",
    },

    # Daylight: dark ink on warm paper, for print-out and high-glare viewing.
    "paper": {
        "INK": "#F6F3EC", "INK_LIFT": "#ECE7DC", "VIOLET": "#6A2FD4",
        "BLUE": "#2436D8", "BLUE_DEEP": "#1B27A8", "GOLD": "#B07A16",
        "ON_INK": "#141726", "ON_INK_2": "#3A4058", "ON_INK_3": "#6B7290",
        "BLUE_ON_INK": "#2436D8", "GOOD": "#0E7C6E", "ALERT": "#B3162F",
        "CHIPS": ["#2436D8", "#C07A12", "#6A2FD4", "#0E7C6E", "#B02E7A"],
        "RULE": "#C9C2B4", "GRID": "#E2DCD0",
    },

    # Near-monochrome. Maximum contrast, minimum colour load; the fallback for
    # colour-vision requests where hue must never carry meaning alone.
    "mono": {
        "INK": "#0C0C0F", "INK_LIFT": "#17171C", "VIOLET": "#3A3A45",
        "BLUE": "#6E6E7C", "BLUE_DEEP": "#4A4A55", "GOLD": "#F2F2F5",
        "ON_INK_2": "#D6D6DC", "ON_INK_3": "#95959F", "BLUE_ON_INK": "#E8E8EE",
        "GOOD": "#B9B9C2", "ALERT": "#FFFFFF",
        "CHIPS": ["#8A8A96", "#E8E8EE", "#5C5C68", "#B9B9C2", "#3F3F49"],
        "RULE": "#33333C", "GRID": "#1B1B21",
    },

    # Deep plum with rose accents. Humanities, language, literature.
    "plum": {
        "INK": "#1A0A22", "INK_LIFT": "#271034", "VIOLET": "#8A2BB0",
        "BLUE": "#6D3FD4", "BLUE_DEEP": "#4B2599", "GOLD": "#FFD08A",
        "ON_INK_2": "#E4CFEC", "ON_INK_3": "#AE93BC", "BLUE_ON_INK": "#E39BD8",
        "GOOD": "#3FC7A8", "ALERT": "#FF6B85",
        "CHIPS": ["#6D3FD4", "#FFD08A", "#C44BC4", "#3FC7A8", "#FF7BA8"],
        "RULE": "#472A55", "GRID": "#2A1236",
    },
}

#: Names accepted in a spec, mapped onto a palette. Lets a spec say what the
#: lesson is about rather than which hex ramp it wants.
PALETTE_ALIASES = {
    "default": "ink", "wicara": "ink", "dark": "ink", "indigo": "ink",
    "biology": "aurora", "earth": "aurora", "nature": "aurora",
    "green": "aurora", "teal": "aurora", "calm": "aurora",
    "chemistry": "ember", "energy": "ember", "heat": "ember",
    "warm": "ember", "orange": "ember", "red": "ember",
    "light": "paper", "day": "paper", "print": "paper", "white": "paper",
    "accessible": "mono", "colorblind": "mono", "colourblind": "mono",
    "grayscale": "mono", "greyscale": "mono", "high_contrast": "mono",
    "language": "plum", "humanities": "plum", "literature": "plum",
    "purple": "plum", "pink": "plum",
}

ACTIVE_PALETTE = "ink"


def _hex_to_rgb(value):
    value = str(value).lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def mix(a, b, t):
    """Blend hex colour `a` toward `b` by `t` in 0..1."""
    ca, cb = _hex_to_rgb(a), _hex_to_rgb(b)
    return _rgb_to_hex([ca[i] + (cb[i] - ca[i]) * t for i in range(3)])


def lift(color, amount=0.45):
    """Tint toward the palette's foreground -- the readable variant on ground."""
    return mix(color, ON_INK, amount)


def deepen(color, amount=0.35):
    """Shade toward the palette's ground."""
    return mix(color, INK, amount)


def on_ground(color, minimum=0.34):
    """Nudge a colour until it is legible on the current ground.

    Palettes differ in whether the ground is dark or light, so a template
    constant like "the alert colour" cannot be one fixed hex across all of
    them. This lifts toward the foreground on a dark ground and deepens on a
    light one, but only as far as it has to.
    """
    ground = _hex_to_rgb(INK)
    ground_luma = (0.2126 * ground[0] + 0.7152 * ground[1] + 0.0722 * ground[2]) / 255.0
    c = _hex_to_rgb(color)
    luma = (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255.0
    if abs(luma - ground_luma) >= minimum:
        return str(color)
    return lift(color, 0.5) if ground_luma < 0.5 else deepen(color, 0.5)


def resolve_palette(name):
    """Map a spec value onto a known palette name, or None."""
    if not name:
        return None
    key = str(name).strip().lower().replace("-", "_").replace(" ", "_")
    key = PALETTE_ALIASES.get(key, key)
    return key if key in PALETTES else None


def use_palette(name_or_tokens):
    """Rebind the palette globals. Accepts a name or a token dict.

    Returns the resolved palette name. Unknown names fall back to "ink" rather
    than raising -- a bad preference string must never cost a render.
    """
    global ACTIVE_PALETTE, ACCENT, HIGHLIGHT, POSITIVE, NEGATIVE

    tokens = dict(_BASE)
    if isinstance(name_or_tokens, dict):
        # An explicit token dict, e.g. a brand colour the caller supplied.
        tokens.update({k.upper(): v for k, v in name_or_tokens.items()})
        resolved = "custom"
    else:
        resolved = resolve_palette(name_or_tokens) or "ink"
        tokens.update(PALETTES[resolved])

    globals().update(tokens)

    # The semantic aliases are assignments, not references, so they have to be
    # recomputed or they keep pointing at the previous palette.
    ACCENT = tokens["BLUE_ON_INK"]
    HIGHLIGHT = tokens["GOLD"]
    POSITIVE = tokens["GOOD"]
    NEGATIVE = tokens["ALERT"]
    ACTIVE_PALETTE = resolved
    return resolved


# --------------------------------------------------------------------------
# Type scale — tokens.css, TYPE SCALE section, retuned for a 14.2x8 frame
# --------------------------------------------------------------------------
# The deck's sizes are px on a 1280 canvas. Manim's font_size is points in
# scene units, so these are the same ratios rescaled, not the raw numbers.

FS_NUMERAL = 76
FS_HERO = 52
FS_DISPLAY = 42
FS_TITLE = 34
FS_METRIC = 34
FS_HEADLINE = 26
FS_SUB = 21
FS_LEDE = 18
FS_BODY = 16
FS_LABEL = 15
FS_CAPTION = 13
FS_EYEBROW = 12

#: --tr-eyebrow: 0.12em. Pango has no tracking knob in Manim's Text, so
#: eyebrows are spaced by inserting thin spaces at build time.
EYEBROW_TRACKING = " "


# --------------------------------------------------------------------------
# Motion — motion.css
# --------------------------------------------------------------------------
# The deck's rule: only transform and opacity animate, no ease-in anywhere,
# nothing runs longer than 420ms. Video can hold a beat longer than a slide
# deck without feeling slow, so these stretch the ceiling slightly and keep
# the shape of the curve.

DUR_FAST = 0.28
DUR_BASE = 0.42
DUR_SLOW = 0.62
STAGGER = 0.08   # lag_ratio for a group reveal
RISE = 0.28      # scene units a block travels on entry (the deck's 10px)


def ease_out():
    """The deck's --ease-out. Never ease-in, never linear for content."""
    from manim import rate_functions

    return rate_functions.ease_out_cubic


# --------------------------------------------------------------------------
# Background
# --------------------------------------------------------------------------

def _radial_glow_array(width_px: int, height_px: int) -> np.ndarray:
    """The ink plate with --ink-glow baked in.

    tokens.css: radial-gradient(760px 560px at 106% -14%, rgba(106,47,212,.55),
    transparent 62%). Painted per-pixel because Manim has no radial fill, and a
    stack of concentric circles bands visibly on a dark ground.
    """
    # Read from the active palette, not written out. These were literal RGB for
    # the deck's ink, which meant a palette swap repainted every mark on the
    # plate but left the plate itself dark -- on the light "paper" palette the
    # result was dark text on a dark ground.
    ink = np.array(_hex_to_rgb(INK), dtype=np.float64)
    violet = np.array(_hex_to_rgb(VIOLET), dtype=np.float64)
    deep = np.array(_hex_to_rgb(INK_LIFT), dtype=np.float64)

    # A 55% violet wash belongs on a dark plate; on a light one it reads as a
    # stain, so the glow is scaled back as the ground gets brighter.
    ground_luma = (0.2126 * ink[0] + 0.7152 * ink[1] + 0.0722 * ink[2]) / 255.0
    glow_strength = 0.55 if ground_luma < 0.5 else 0.16
    lift_strength = 0.50 if ground_luma < 0.5 else 0.22

    ys, xs = np.mgrid[0:height_px, 0:width_px]
    u = xs / max(width_px - 1, 1)
    v = ys / max(height_px - 1, 1)

    # Primary glow: centred off-canvas top-right, elliptical, faded by 62%.
    du = (u - 1.06) / 0.594   # 760/1280 of the canvas width
    dv = (v + 0.14) / 0.622   # 560/900 of the canvas height
    r = np.sqrt(du * du + dv * dv)
    glow = np.clip(1.0 - r / 0.62, 0.0, 1.0) ** 1.5 * glow_strength

    # Secondary lift bottom-left so the plate is not flat where the glow dies.
    du2 = (u + 0.10) / 0.9
    dv2 = (v - 1.05) / 0.9
    r2 = np.sqrt(du2 * du2 + dv2 * dv2)
    lift = np.clip(1.0 - r2 / 0.95, 0.0, 1.0) ** 2.0 * lift_strength

    rgb = ink[None, None, :] * np.ones((height_px, width_px, 1))
    rgb = rgb + (violet - ink)[None, None, :] * glow[:, :, None]
    rgb = rgb + (deep - ink)[None, None, :] * lift[:, :, None]

    # A faint vignette keeps the eye centred on the visual rail.
    cu, cv = u - 0.5, v - 0.5
    vign = np.clip(1.0 - (cu * cu + cv * cv) * 0.55, 0.0, 1.0)
    rgb *= (0.88 + 0.12 * vign)[:, :, None]

    return np.clip(rgb, 0, 255).astype(np.uint8)


def make_background(scene, *, grid: bool = True):
    """Full-bleed ink plate, glow, optional technical grid and corner ticks.

    Returned as one VGroup-like list already added to the scene at the back, so
    a template can ignore it entirely.
    """
    from manim import (
        VGroup,
        ImageMobject,
        Line,
        config,
    )

    fw, fh = config.frame_width, config.frame_height
    plate = ImageMobject(_radial_glow_array(384, 216))
    plate.stretch_to_fit_width(fw)
    plate.stretch_to_fit_height(fh)
    plate.set_z_index(-100)
    scene.add(plate)

    decor = VGroup()
    if grid:
        # Blueprint rules: quiet enough to sit behind content, present enough
        # to read as an instrument panel rather than an empty background.
        for i in range(1, 8):
            x = -fw / 2 + fw * i / 8
            decor.add(
                Line(
                    [x, -fh / 2, 0], [x, fh / 2, 0],
                    stroke_color=GRID, stroke_width=1, stroke_opacity=0.45,
                )
            )
        for j in range(1, 5):
            y = -fh / 2 + fh * j / 5
            decor.add(
                Line(
                    [-fw / 2, y, 0], [fw / 2, y, 0],
                    stroke_color=GRID, stroke_width=1, stroke_opacity=0.35,
                )
            )

    # Corner ticks — the frame marks that make a panel look engineered.
    tick = 0.42
    m = 0.30
    for sx in (-1, 1):
        for sy in (-1, 1):
            cx, cy = sx * (fw / 2 - m), sy * (fh / 2 - m)
            decor.add(
                Line([cx, cy, 0], [cx - sx * tick, cy, 0],
                     stroke_color=BLUE_ON_INK, stroke_width=2, stroke_opacity=0.55),
                Line([cx, cy, 0], [cx, cy - sy * tick, 0],
                     stroke_color=BLUE_ON_INK, stroke_width=2, stroke_opacity=0.55),
            )

    decor.set_z_index(-90)
    scene.add(decor)
    return plate, decor


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------

def eyebrow(text: str, color: str = None):
    """The deck's uppercase, wide-tracked label. Sits above a title."""
    color = color or ON_INK_3
    from manim import Text

    spaced = EYEBROW_TRACKING.join(str(text).upper())
    return Text(spaced, font_size=FS_EYEBROW, color=color, **font_kwargs("semibold"))


def accent_rule(width: float = 1.6):
    """The short gradient rule under a title. Blue to violet, as every deck
    gradient runs."""
    from manim import Line

    rule = Line([0, 0, 0], [width, 0, 0], stroke_width=5)
    rule.set_stroke(color=[BLUE_ON_INK, VIOLET])
    return rule


def panel(width: float, height: float, *, radius: float = 0.22, tone: str = "fill"):
    """A card surface on ink: --ink-fill at 6%, hairline at --border-dk."""
    from manim import RoundedRectangle

    return RoundedRectangle(
        corner_radius=radius,
        width=width,
        height=height,
        fill_color=ON_INK if tone == "fill" else INK_LIFT,
        fill_opacity=0.06 if tone == "fill" else 0.85,
        stroke_color=RULE,
        stroke_opacity=0.85,
        stroke_width=1.6,
    )


def glow_dot(color: str = None, radius: float = 0.075):
    """A lit node: solid core, soft halo. Used to mark a value on a rail."""
    color = color or BLUE_ON_INK
    from manim import VGroup, Dot

    halo = Dot(radius=radius * 3.0, color=color, fill_opacity=0.16, stroke_width=0)
    mid = Dot(radius=radius * 1.8, color=color, fill_opacity=0.24, stroke_width=0)
    core = Dot(radius=radius, color=color, fill_opacity=1.0, stroke_width=0)
    return VGroup(halo, mid, core)
