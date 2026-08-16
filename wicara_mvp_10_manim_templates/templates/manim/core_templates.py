from manim import *
import os
import math
import re
import textwrap
import numpy as np

# Brand layer. Every scene draws on the deck's ink ground with the deck's
# palette and Poppins, instead of Manim's stock colours on black.
try:
    from . import wicara_theme as theme
except ImportError:  # rendered as a loose script, not a package
    import wicara_theme as theme

try:
    from . import wicara_objects as objects
except ImportError:  # rendered as a loose script, not a package
    import wicara_objects as objects

try:
    from . import wicara_motion as motion
except ImportError:  # rendered as a loose script, not a package
    import wicara_motion as motion

# ---------------------------------------------------------------------------
# Palette remap
# ---------------------------------------------------------------------------
# The ten renderers below reference Manim's stock constants ~700 times. Those
# names resolve against this module's globals, so rebinding them here — after
# `from manim import *` has populated them — repaints every template at once
# without touching a single call site.
#
# Values are the deck's dark-ground tokens. Stock RED and GREEN are tuned for
# a black canvas and go muddy on ink, so each maps to the lifted equivalent
# rather than to the light-ground token.
# Every value is derived from the active palette rather than written out, so
# switching palette repaints all ~700 call sites. sync_palette() re-runs this
# whenever a spec asks for a different one.
def sync_palette():
    global YELLOW, GOLD, BLUE, GREEN, RED, ORANGE, PURPLE, TEAL, PINK, MAROON
    global GRAY, GREY, GRAY_A, GREY_A, GRAY_B, GREY_B, GRAY_C, GREY_C
    global GRAY_D, GREY_D, GRAY_E, GREY_E, LIGHT_GREY, LIGHT_GRAY
    global DARK_GREY, DARK_GRAY
    global BLUE_A, BLUE_B, BLUE_C, BLUE_D, BLUE_E
    global GREEN_A, GREEN_B, GREEN_C, GREEN_D, GREEN_E
    global YELLOW_A, YELLOW_B, YELLOW_C, YELLOW_D, YELLOW_E
    global RED_A, RED_B, RED_C, RED_D, RED_E
    global PURPLE_A, PURPLE_B, PURPLE_C, PURPLE_D, PURPLE_E

    YELLOW = theme.GOLD              # emphasis, moving points, highlighted terms
    GOLD = theme.GOLD
    BLUE = theme.BLUE_ON_INK         # the primary rail; raw --blue is too dark
    GREEN = theme.GOOD               # correct, growth, positive delta
    RED = theme.on_ground(theme.ALERT)   # alert, legible on whichever ground
    ORANGE = theme.CHIPS[1]
    PURPLE = theme.on_ground(theme.VIOLET)
    TEAL = theme.GOOD
    PINK = theme.on_ground(theme.CHIPS[4])
    MAROON = PINK

    # Greys become the palette's text tiers, so captions and axis labels stop
    # disappearing into the plate.
    GRAY = GREY = theme.ON_INK_3
    GRAY_A = GREY_A = theme.ON_INK_2
    GRAY_B = GREY_B = theme.ON_INK_3
    GRAY_C = GREY_C = theme.RULE
    GRAY_D = GREY_D = theme.RULE
    GRAY_E = GREY_E = theme.INK_LIFT
    LIGHT_GREY = LIGHT_GRAY = theme.ON_INK_2
    DARK_GREY = DARK_GRAY = theme.RULE

    # Manim's letter variants: A is the lightest tint, E the deepest shade.
    BLUE_A, BLUE_B, BLUE_C, BLUE_D, BLUE_E = (
        theme.lift(theme.BLUE_ON_INK, 0.55), theme.BLUE_ON_INK,
        theme.BLUE_ON_INK, theme.BLUE, theme.BLUE_DEEP,
    )
    GREEN_A, GREEN_B, GREEN_C, GREEN_D, GREEN_E = (
        theme.lift(theme.GOOD, 0.55), theme.GOOD, theme.GOOD,
        theme.deepen(theme.GOOD, 0.28), theme.deepen(theme.GOOD, 0.48),
    )
    YELLOW_A, YELLOW_B, YELLOW_C, YELLOW_D, YELLOW_E = (
        theme.lift(theme.GOLD, 0.55), theme.GOLD, theme.GOLD,
        theme.deepen(theme.GOLD, 0.24), theme.deepen(theme.GOLD, 0.44),
    )
    RED_A, RED_B, RED_C, RED_D, RED_E = (
        theme.lift(RED, 0.5), RED, RED,
        theme.deepen(RED, 0.26), theme.ALERT,
    )
    PURPLE_A, PURPLE_B, PURPLE_C, PURPLE_D, PURPLE_E = (
        theme.lift(theme.VIOLET, 0.62), theme.lift(theme.VIOLET, 0.30),
        PURPLE, theme.deepen(theme.VIOLET, 0.28), theme.deepen(theme.VIOLET, 0.46),
    )


sync_palette()

try:
    from manim_voiceover import VoiceoverScene
    from manim_voiceover.services.gtts import GTTSService
except ImportError:
    VoiceoverScene = Scene
    GTTSService = None

LANGUAGE_ALIASES = {
    "id": "id",
    "id-id": "id",
    "indonesian": "id",
    "bahasa": "id",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "english": "en",
    "vi": "vi",
    "vi-vn": "vi",
    "vietnamese": "vi",
    "ms": "ms",
    "ms-my": "ms",
    "malay": "ms",
    "ja": "ja",
    "ja-jp": "ja",
    "japanese": "ja",
}

I18N_LABELS = {
    "default_title": {
        "id": "Penjelasan Konsep",
        "en": "Concept Explanation",
        "vi": "Giai thich khai niem",
    },
    "summary_title": {
        "id": "Kesimpulan",
        "en": "Summary",
        "vi": "Tong ket",
    },
    "step_prefix": {
        "id": "Langkah",
        "en": "Step",
        "vi": "Buoc",
    },
    "ratio_context_default": {
        "id": "Konteks rasio",
        "en": "Ratio context",
        "vi": "Boi canh ty le",
    },
    "graph_function_default": {
        "id": "grafik fungsi",
        "en": "function graph",
        "vi": "do thi ham so",
    },
    "moving_point_default": {
        "id": "titik",
        "en": "point",
        "vi": "diem",
    },
    "object_default": {
        "id": "Benda",
        "en": "Object",
        "vi": "Vat the",
    },
    "motion_graph_default": {
        "id": "Grafik gerak",
        "en": "Motion graph",
        "vi": "Do thi chuyen dong",
    },
    "highlight_prefix": {
        "id": "Sorot:",
        "en": "Highlight:",
        "vi": "Noi bat:",
    },
    "direction_to": {
        "id": "ke",
        "en": "to",
        "vi": "ve",
    },
    "resultant_label": {
        "id": "Resultan",
        "en": "Resultant",
        "vi": "Hop luc",
    },
}

I18N_PHRASES = {
    "Ide utama": {
        "en": "Main idea",
        "vi": "Y chinh",
    },
    "Garis bilangan membantu membandingkan posisi dan nilai angka.": {
        "en": "A number line helps compare number positions and values.",
        "vi": "Truc so giup so sanh vi tri va gia tri cua cac so.",
    },
    "Garis bilangan": {
        "en": "Number line",
        "vi": "Truc so",
    },
    "Tandai angka": {
        "en": "Mark the numbers",
        "vi": "Danh dau cac so",
    },
    "Setiap angka ditempatkan sesuai posisinya di garis bilangan.": {
        "en": "Each number is placed at its position on the number line.",
        "vi": "Moi so duoc dat dung vi tri tren truc so.",
    },
    "Bandingkan": {
        "en": "Compare",
        "vi": "So sanh",
    },
    "Arah panah menunjukkan perpindahan dari angka kiri ke angka kanan.": {
        "en": "The arrow direction shows movement from the left number to the right number.",
        "vi": "Huong mui ten cho thay su di chuyen tu so ben trai sang so ben phai.",
    },
    "Model blok": {
        "en": "Block model",
        "vi": "Mo hinh khoi",
    },
    "Setiap kotak kecil mewakili satu benda atau satu satuan.": {
        "en": "Each small block represents one object or one unit.",
        "vi": "Moi o nho dai dien cho mot vat hoac mot don vi.",
    },
    "Gabungkan jumlah": {
        "en": "Combine quantities",
        "vi": "Gop so luong",
    },
    "Kita melihat dua kelompok lalu menyatukannya menjadi satu hasil.": {
        "en": "We observe two groups and combine them into one result.",
        "vi": "Ta quan sat hai nhom roi gop lai thanh mot ket qua.",
    },
    "Bagian dari keseluruhan": {
        "en": "Part of a whole",
        "vi": "Phan cua tong the",
    },
    "Pecahan menunjukkan berapa bagian yang diambil dari satu keseluruhan.": {
        "en": "Fractions show how many parts are taken from a whole.",
        "vi": "Phan so cho biet bao nhieu phan duoc lay tu mot tong the.",
    },
    "Bandingkan bagian": {
        "en": "Compare parts",
        "vi": "So sanh phan",
    },
    "Walau jumlah potongannya berbeda, bagian yang diwarnai bisa sama besar.": {
        "en": "Even with different partitions, highlighted parts can represent the same value.",
        "vi": "Du so phan chia khac nhau, phan duoc to mau van co the bang nhau.",
    },
    "Apa itu rasio?": {
        "en": "What is a ratio?",
        "vi": "Ty le la gi?",
    },
    "Rasio membandingkan dua kuantitas dalam satu situasi.": {
        "en": "A ratio compares two quantities in the same context.",
        "vi": "Ty le so sanh hai dai luong trong cung mot boi canh.",
    },
    "Persamaan = seimbang": {
        "en": "Equation = balance",
        "vi": "Phuong trinh = can bang",
    },
    "Tanda sama dengan berarti ruas kiri dan kanan memiliki nilai yang setara.": {
        "en": "The equals sign means the left and right sides have equivalent values.",
        "vi": "Dau bang cho biet ve trai va ve phai co gia tri tuong duong.",
    },
    "Pola bertumbuh": {
        "en": "Growing pattern",
        "vi": "Mau hinh tang dan",
    },
    "Setiap suku dapat dilihat sebagai gambar atau jumlah yang berubah teratur.": {
        "en": "Each term can be seen as a visual or a quantity that changes regularly.",
        "vi": "Moi so hang co the xem nhu hinh anh hoac gia tri thay doi deu dan.",
    },
    "Apa itu luas?": {
        "en": "What is area?",
        "vi": "Dien tich la gi?",
    },
    "Luas adalah banyaknya daerah yang ditutupi oleh satuan persegi.": {
        "en": "Area is the amount of surface covered by square units.",
        "vi": "Dien tich la phan be mat duoc phu boi cac don vi vuong.",
    },
    "Apa yang dilihat?": {
        "en": "What do we see?",
        "vi": "Ta thay gi?",
    },
    "Grafik menunjukkan hubungan antara nilai x dan nilai f(x).": {
        "en": "The graph shows the relationship between x and f(x).",
        "vi": "Do thi cho thay moi quan he giua x va f(x).",
    },
    "Titik bergerak": {
        "en": "Moving point",
        "vi": "Diem chuyen dong",
    },
    "Saat x berubah, posisi titik di grafik ikut berubah.": {
        "en": "When x changes, the point position on the graph also changes.",
        "vi": "Khi x thay doi, vi tri diem tren do thi cung thay doi.",
    },
    "Laju perubahan lokal": {
        "en": "Local rate of change",
        "vi": "Toc do thay doi cuc bo",
    },
    "Gerak terhadap waktu": {
        "en": "Motion over time",
        "vi": "Chuyen dong theo thoi gian",
    },
    "Kita lihat benda bergerak, lalu hubungkan dengan grafik posisinya.": {
        "en": "We observe motion first, then connect it to a position graph.",
        "vi": "Ta quan sat vat chuyen dong roi lien he voi do thi vi tri.",
    },
    "Benda bergerak": {
        "en": "Object in motion",
        "vi": "Vat the chuyen dong",
    },
    "Posisi benda berubah seiring waktu.": {
        "en": "The object's position changes over time.",
        "vi": "Vi tri cua vat thay doi theo thoi gian.",
    },
    "Grafik posisi": {
        "en": "Position graph",
        "vi": "Do thi vi tri",
    },
    "Grafik menunjukkan hubungan antara waktu dan posisi.": {
        "en": "The graph shows the relationship between time and position.",
        "vi": "Do thi cho thay moi quan he giua thoi gian va vi tri.",
    },
    "Gaya sebagai panah": {
        "en": "Forces as arrows",
        "vi": "Luc duoc bieu dien bang mui ten",
    },
    "Panjang panah menunjukkan besar gaya, arah panah menunjukkan arah gaya.": {
        "en": "Arrow length shows force magnitude, and arrow direction shows force direction.",
        "vi": "Do dai mui ten cho biet do lon luc, huong mui ten cho biet huong luc.",
    },
    "Gaya-gaya bekerja": {
        "en": "Forces acting",
        "vi": "Cac luc tac dung",
    },
    "Setiap panah menunjukkan gaya yang bekerja pada benda.": {
        "en": "Each arrow represents a force acting on the object.",
        "vi": "Moi mui ten the hien mot luc tac dung len vat the.",
    },
    "Resultan gaya": {
        "en": "Resultant force",
        "vi": "Hop luc",
    },
    "Gaya berlawanan dikurangkan untuk mendapatkan resultannya.": {
        "en": "Opposing forces are subtracted to get the resultant force.",
        "vi": "Cac luc nguoc chieu duoc tru de tim hop luc.",
    },
    "awal": {
        "en": "start",
        "vi": "bat dau",
    },
    "akhir": {
        "en": "end",
        "vi": "ket thuc",
    },
}


# ============================================================
# WICARA MVP 10 MANIM TEMPLATES — CLEAN VERSION
# ============================================================
# Run examples:
#   manim -ql wicara_mvp_10_clean.py NumberLineQuantityTemplate
#   manim -ql wicara_mvp_10_clean.py GraphExplanationTemplate
#   manim -ql wicara_mvp_10_clean.py ForceDiagramTemplate
#
# Design goal:
# - Longer educational flow, not too short.
# - Fixed layout zones.
# - One active explanation card at a time.
# - No text stacking.
# - Clean final frame.
# - SceneSpec-driven defaults.
# ============================================================


# ============================================================
# SHARED HELPERS
# ============================================================

def clamp_text(text, max_chars=90):
    text = "" if text is None else str(text)
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def wrap_text(text, width=42):
    text = "" if text is None else str(text)
    return "\n".join(textwrap.wrap(text, width=width))


def safe_text(text, max_chars=120, width=42):
    return wrap_text(clamp_text(text, max_chars=max_chars), width=width)


def require(spec, key):
    if key not in spec or spec[key] in (None, "", []):
        raise ValueError(f"Missing required field: {key}")
    return spec[key]


def direction_vector(direction):
    direction = str(direction).lower()
    mapping = {
        "right": RIGHT,
        "left": LEFT,
        "up": UP,
        "down": DOWN,
    }
    if direction not in mapping:
        raise ValueError(f"Invalid direction: {direction}")
    return mapping[direction]


def build_function(function_spec):
    ftype = function_spec.get("type", "linear")
    p = function_spec.get("params", {})

    if ftype == "linear":
        m = float(p.get("m", 1))
        b = float(p.get("b", 0))
        return lambda x: m * x + b

    if ftype == "quadratic":
        a = float(p.get("a", 1))
        b = float(p.get("b", 0))
        c = float(p.get("c", 0))
        return lambda x: a * x**2 + b * x + c

    if ftype == "cubic":
        a = float(p.get("a", 1))
        b = float(p.get("b", 0))
        c = float(p.get("c", 0))
        d = float(p.get("d", 0))
        return lambda x: a * x**3 + b * x**2 + c * x + d

    if ftype == "exponential":
        a = float(p.get("a", 1))
        base = float(p.get("base", 2))
        k = float(p.get("k", 1))
        c = float(p.get("c", 0))
        return lambda x: a * (base ** (k * x)) + c

    if ftype == "sine":
        a = float(p.get("a", 1))
        b = float(p.get("b", 1))
        c = float(p.get("c", 0))
        d = float(p.get("d", 0))
        return lambda x: a * math.sin(b * x + c) + d

    raise ValueError(f"Unsupported function type: {ftype}")


def numerical_slope(f, x, h=1e-4):
    return (f(x + h) - f(x - h)) / (2 * h)


def _voiceover_lang_for_gtts(language: str) -> str:
    normalized = str(language or "").strip().lower()
    mapped = LANGUAGE_ALIASES.get(normalized, normalized.split("-")[0] if normalized else "id")
    if mapped in {"id", "en", "vi", "ms", "ja"}:
        return mapped
    return "en"


def _split_voiceover_script(script: str, max_chars: int = 220) -> list[str]:
    text = " ".join(str(script or "").split())
    if not text:
        return []

    parts = re.split(r"(?<=[.!?])\s+", text)
    segments: list[str] = []
    for part in parts:
        cleaned = part.strip()
        if not cleaned:
            continue
        if len(cleaned) <= max_chars:
            segments.append(cleaned)
            continue

        words = cleaned.split(" ")
        chunk: list[str] = []
        size = 0
        for word in words:
            word_len = len(word)
            if chunk and (size + 1 + word_len) > max_chars:
                segments.append(" ".join(chunk).strip())
                chunk = [word]
                size = word_len
            else:
                chunk.append(word)
                size = word_len if not chunk[:-1] else size + 1 + word_len
        if chunk:
            segments.append(" ".join(chunk).strip())
    return [segment for segment in segments if segment]


def _normalize_tts_provider(value) -> str:
    normalized = str(value or "").strip().lower()
    mapping = {
        "gtts": "gtts_voiceover",
        "gtts_voiceover": "gtts_voiceover",
        "openai": "gtts_voiceover",
        "openai_tts": "gtts_voiceover",
        "openai_voiceover": "gtts_voiceover",
        "whisper": "gtts_voiceover",
        "openai_whisper": "gtts_voiceover",
        "none": "none",
    }
    return mapping.get(normalized, "gtts_voiceover")


def _dedupe_voiceover_segments(segments: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for segment in segments:
        cleaned = " ".join(str(segment or "").split())
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


class WicaraTemplateScene(VoiceoverScene):
    SPEC = {}

    def setup(self):
        super().setup()
        self._resolve_palette()
        self._resolve_layout()
        self._apply_brand_ground()
        self._pace = None
        self._resolved_language = "id"
        self._voiceover_initialized = False
        self._voiceover_enabled = False
        self._voiceover_mode = "auto"
        self._voiceover_segments: list[str] = []
        self._voiceover_index = 0
        self._segmented_intro_queue: list[str] = []
        self._segmented_summary_queue: list[str] = []
        self._segmented_outro_queue: list[str] = []
        self._segmented_step_queues: dict[int, list[str]] = {}
        self._voiceover_provider = "none"
        self._requested_tts_provider = "gtts_voiceover"

    # --------------------------------------------------------
    # Brand ground
    # --------------------------------------------------------

    def _resolve_palette(self):
        """Pick the palette this render uses, before anything is drawn.

        Resolution order, most specific first:

          1. `palette_tokens` -- an explicit token dict, for a caller supplying
             its own brand colours.
          2. `palette` -- a named palette or an alias.
          3. `subject` / `subject_name` -- so a chemistry lesson comes out warm
             and a biology lesson comes out green without anyone choosing.
          4. WICARA_PALETTE in the environment, for previewing a spec in every
             palette without editing it.
          5. the deck default.

        Runs in setup(), so construct() and the brand ground both see the
        finished palette. A name that resolves to nothing falls through rather
        than raising: a bad preference string must never cost a render.
        """
        spec = self.SPEC if isinstance(self.SPEC, dict) else {}

        tokens = spec.get("palette_tokens")
        if isinstance(tokens, dict) and tokens:
            self._palette = theme.use_palette(tokens)
            sync_palette()
            return

        candidates = [
            spec.get("palette"),
            spec.get("color_theme"),
            spec.get("subject"),
            spec.get("subject_name"),
            os.environ.get("WICARA_PALETTE"),
        ]
        for candidate in candidates:
            resolved = theme.resolve_palette(candidate)
            if resolved:
                self._palette = theme.use_palette(resolved)
                sync_palette()
                return

        self._palette = theme.use_palette("ink")
        sync_palette()

    def _apply_brand_ground(self):
        """Ink plate, violet glow, brand face, blueprint grid and corner ticks.

        Runs from setup() so every template inherits it without changing a
        single line of its own construct().
        """
        self.camera.background_color = theme.INK
        self._apply_brand_font()
        self._brand_ground = []
        try:
            self._brand_plate, self._brand_decor = theme.make_background(self)
            self._brand_ground = [
                m for m in (self._brand_plate, self._brand_decor) if m is not None
            ]
        except Exception:
            # A themed background is never worth failing a render over.
            self._brand_plate = self._brand_decor = None

    def _apply_brand_font(self):
        """Make Poppins the default face for every Text in every template.

        The title block and cards pass theme.font_kwargs() explicitly, but the
        shared helpers -- circle_chip, simple_box and a few dozen others -- call
        Text() bare, so their labels fell back to Pango's default serif. Chip
        letters and box captions were rendering in a different typeface from the
        headings sitting right above them.

        Setting the class default fixes all of them at once, and anything that
        passes `font=` explicitly still wins.
        """
        family = theme.register_fonts()
        if not family:
            return
        for cls in (Text, Paragraph):
            try:
                cls.set_default(font=family)
            except Exception:
                # set_default is a Manim convenience, not a guarantee; a missing
                # brand face is a downgrade, never a failed render.
                pass

    # --------------------------------------------------------
    # Aspect-aware layout
    # --------------------------------------------------------
    #
    # Every zone used to be a number typed for a 16:9 frame. That is fine until
    # the same lesson has to come out as a 9:16 short for Reels or TikTok, where
    # the frame is 4.5 units wide instead of 14.2 and a card parked at x=4.05 is
    # off-screen entirely.
    #
    # So zones are computed from config, and templates compose in a *nominal*
    # 16:9 space that is mapped onto whatever frame is actually being rendered.
    # A template writes one set of coordinates and gets every aspect ratio.

    #: The space a template's own coordinates are written in.
    NOMINAL_W = 13.7
    NOMINAL_H = 3.85

    def is_portrait(self):
        return config.frame_width < config.frame_height * 0.95

    def is_square(self):
        ratio = config.frame_width / max(config.frame_height, 1e-6)
        return 0.95 <= ratio <= 1.15

    def _resolve_layout(self):
        fw, fh = config.frame_width, config.frame_height
        self._portrait = self.is_portrait() or self.is_square()

        if self._portrait:
            # Title band on top, stage in the middle, card and captions below.
            # Class-level STAGE_* overrides are ignored here: they were authored
            # against a wide frame and mean nothing at this aspect.
            self.stage_left = -fw / 2 + 0.30
            self.stage_right = fw / 2 - 0.30
            self.stage_top = fh / 2 - 2.30
            self.stage_bottom = -fh / 2 + 2.70
            self._card_zone = "bottom"
        else:
            self.stage_left = self.STAGE_LEFT
            self.stage_right = self.STAGE_RIGHT
            self.stage_top = self.STAGE_TOP
            self.stage_bottom = self.STAGE_BOTTOM
            self._card_zone = "right"

        # The nominal-to-frame transform every template draws through.
        centre, width, height = self.stage_box()
        self._scene_scale = min(
            width / self.NOMINAL_W, height / self.NOMINAL_H
        )
        self._scene_origin = centre

    def card_zone(self):
        """Where guidance cards live at this aspect."""
        return getattr(self, "_card_zone", "right")

    def P(self, x, y=0.0):
        """Map a nominal-space point onto the frame actually being rendered."""
        scale = getattr(self, "_scene_scale", 1.0)
        origin = getattr(self, "_scene_origin", ORIGIN)
        return origin + np.array([x * scale, y * scale, 0.0])

    def S(self, length):
        """Map a nominal-space length (a width, a height, a radius)."""
        return length * getattr(self, "_scene_scale", 1.0)

    def scene_content(self):
        """Everything on screen except the brand ground.

        Templates sweep the stage with `FadeOut(m) for m in self.mobjects`
        before a summary. That list now includes the ink plate and its decor,
        and fading those left the closing frame on bare camera black.
        """
        ground = set(id(m) for m in getattr(self, "_brand_ground", []))
        return [m for m in self.mobjects if id(m) not in ground]

    # --------------------------------------------------------
    # Layout zones
    # --------------------------------------------------------

    def title_zone_y(self):
        return 3.25

    def visual_center(self):
        # Pushed down from -0.40: the branded title block carries an eyebrow and
        # a gradient rule above the title, so the old centre let whatever a
        # template anchors to the top of its visual collide with the subtitle.
        return LEFT * 2.05 + DOWN * 0.72

    def right_card_center(self):
        # Keep guidance cards away from the main visual rail.
        return RIGHT * 4.05 + UP * 0.55

    def bottom_summary_y(self):
        return -3.18

    # --------------------------------------------------------
    # The stage
    # --------------------------------------------------------
    #
    # Until now "layout" was two bare points: visual_center() for the figure and
    # right_card_center() for the card. A point cannot say how wide a figure is
    # allowed to get, so templates that place by hardcoded coordinate simply ran
    # under the card -- MutationEvolutionSelection lays its second population out
    # to x=4.9 while the card starts at x=1.67. Nothing in the system objected.
    #
    # The stage is a real rectangle. Everything that is not title, card or ground
    # belongs inside it, and fit_stage() guarantees that by construction.

    STAGE_LEFT = -6.80
    STAGE_RIGHT = 1.30
    STAGE_TOP = 1.62
    STAGE_BOTTOM = -3.62

    def stage_box(self):
        """(centre, width, height) of the region a figure may occupy.

        Reads the resolved instance bounds, which _resolve_layout() sets from
        the frame; the class constants are only the 16:9 defaults.
        """
        left = getattr(self, "stage_left", self.STAGE_LEFT)
        right = getattr(self, "stage_right", self.STAGE_RIGHT)
        top = getattr(self, "stage_top", self.STAGE_TOP)
        bottom = getattr(self, "stage_bottom", self.STAGE_BOTTOM)
        centre = np.array([(left + right) / 2.0, (top + bottom) / 2.0, 0.0])
        return centre, right - left, top - bottom

    def fit_stage(self, *mobjects, margin=0.22, max_scale=2.4, align=None):
        """Scale a figure to fill the stage, then centre it there.

        Scales up as readily as down: the complaint was never only that figures
        collided with the card, it was that they sat in a thin band with the
        bottom third of the frame empty. A figure built at whatever size its
        hardcoded coordinates imply gets resized to actually use the stage.

        `align` optionally pins one edge (LEFT/RIGHT/UP/DOWN) instead of
        centring, for figures that read better anchored.
        """
        group = VGroup(*[m for m in mobjects if m is not None])
        if not len(group):
            return group

        centre, width, height = self.stage_box()
        avail_w = max(0.1, width - 2 * margin)
        avail_h = max(0.1, height - 2 * margin)

        cur_w = max(group.width, 1e-6)
        cur_h = max(group.height, 1e-6)
        factor = min(avail_w / cur_w, avail_h / cur_h, max_scale)
        if factor > 0 and abs(factor - 1.0) > 1e-3:
            group.scale(factor)

        group.move_to(centre)
        if align is not None:
            # Pin one edge to the matching stage boundary; the other axis keeps
            # the centred position from move_to above.
            limits = {
                tuple(LEFT): (0, centre[0] - width / 2 + margin),
                tuple(RIGHT): (0, centre[0] + width / 2 - margin),
                tuple(UP): (1, centre[1] + height / 2 - margin),
                tuple(DOWN): (1, centre[1] - height / 2 + margin),
            }
            found = limits.get(tuple(np.sign(align)))
            if found:
                axis, limit = found
                shift = np.zeros(3)
                shift[axis] = limit - group.get_edge_center(align)[axis]
                group.shift(shift)
        return group

    def stage_rows(self, *rows, buff=0.55, align_edge=None):
        """Stack figure rows down the stage and fit the result.

        The bio template drew generation one and generation two side by side,
        which is what pushed the second group into the card. They are a
        before/after pair -- stacked they read better *and* they use the
        vertical space that was sitting empty.
        """
        groups = [VGroup(*r) if isinstance(r, (list, tuple)) else r for r in rows]
        groups = [g for g in groups if g is not None and len(g)]
        if not groups:
            return VGroup()
        stack = VGroup(*groups)
        stack.arrange(DOWN, buff=buff, aligned_edge=align_edge or ORIGIN)
        self.fit_stage(stack)
        return stack

    # --------------------------------------------------------
    # Text/card helpers
    # --------------------------------------------------------
    def _clean_voice_text(self, value):
        return " ".join(str(value or "").split())

    def _join_narration_parts(self, first, second):
        first_text = self._clean_voice_text(first)
        second_text = self._clean_voice_text(second)
        if first_text and second_text:
            if not first_text.endswith((".", "!", "?")):
                first_text = f"{first_text}."
            return f"{first_text} {second_text}"
        return first_text or second_text

    def _build_structured_voiceover_segments(self, spec):
        segments: list[str] = []
        title = self._clean_voice_text(spec.get("title"))
        subtitle = self._clean_voice_text(spec.get("subtitle"))
        if title and subtitle:
            segments.append(f"{title}. {subtitle}")
        elif title:
            segments.append(title)
        elif subtitle:
            segments.append(subtitle)

        steps = spec.get("steps")
        if isinstance(steps, list):
            for index, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                default_step_title = (
                    f"{self.tr_key('step_prefix', spec, fallback='Langkah')} {index + 1}"
                )
                step_title = self._clean_voice_text(step.get("title", default_step_title))
                step_body = self._clean_voice_text(step.get("body"))
                if step_title and step_body:
                    if not step_title.endswith((".", "!", "?")):
                        step_title = f"{step_title}."
                    sentence = f"{step_title} {step_body}"
                else:
                    sentence = step_title or step_body
                if sentence:
                    segments.extend(_split_voiceover_script(sentence, max_chars=180))

        summary = self._clean_voice_text(spec.get("summary"))
        if summary:
            segments.extend(_split_voiceover_script(summary, max_chars=200))
        return segments

    def _build_voiceover_segments(self, spec):
        explicit_script = self._clean_voice_text(spec.get("voiceover_script"))
        explicit_segments = _split_voiceover_script(explicit_script)

        # Optional advanced mode: upstream model can pass per-step narration directly.
        structured_segments: list[str] = []
        raw_segments = spec.get("narration_segments")
        if isinstance(raw_segments, list):
            for item in raw_segments:
                if isinstance(item, str):
                    structured_segments.extend(_split_voiceover_script(item, max_chars=180))
                elif isinstance(item, dict):
                    text = self._clean_voice_text(item.get("text"))
                    if text:
                        structured_segments.extend(_split_voiceover_script(text, max_chars=180))

        if not structured_segments:
            structured_segments = self._build_structured_voiceover_segments(spec)

        if explicit_segments and structured_segments:
            # Keep explicit intro but still cover all educational steps.
            return _dedupe_voiceover_segments(explicit_segments + structured_segments)
        if explicit_segments:
            return _dedupe_voiceover_segments(explicit_segments)
        return _dedupe_voiceover_segments(structured_segments)

    def _has_segmented_narration(self, spec):
        raw_segments = spec.get("narration_segments")
        if isinstance(raw_segments, list) and len(raw_segments) > 0:
            return True

        steps = spec.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                if self._clean_voice_text(step.get("narration") or step.get("voiceover")):
                    return True

        if self._clean_voice_text(spec.get("intro_narration")):
            return True
        if self._clean_voice_text(spec.get("summary_narration")):
            return True
        return False

    def _normalize_step_index(self, raw_index):
        try:
            idx = int(raw_index)
        except (TypeError, ValueError):
            return None
        if idx >= 1:
            return idx - 1
        if idx == 0:
            return 0
        return None

    def _append_segmented_step_text(self, step_index, text):
        normalized = self._clean_voice_text(text)
        if not normalized:
            return
        idx = self._normalize_step_index(step_index)
        if idx is None:
            return
        bucket = self._segmented_step_queues.setdefault(idx, [])
        bucket.append(normalized)

    def _initialize_segmented_narration(self, spec):
        self._segmented_intro_queue = []
        self._segmented_summary_queue = []
        self._segmented_outro_queue = []
        self._segmented_step_queues = {}

        intro_text = self._clean_voice_text(spec.get("intro_narration"))
        if intro_text:
            self._segmented_intro_queue.extend(_split_voiceover_script(intro_text, max_chars=200))

        summary_text = self._clean_voice_text(spec.get("summary_narration"))
        if summary_text:
            self._segmented_summary_queue.extend(_split_voiceover_script(summary_text, max_chars=200))

        raw_segments = spec.get("narration_segments")
        if isinstance(raw_segments, list):
            for entry in raw_segments:
                if isinstance(entry, str):
                    text = self._clean_voice_text(entry)
                    if text:
                        self._segmented_intro_queue.extend(_split_voiceover_script(text, max_chars=180))
                    continue
                if not isinstance(entry, dict):
                    continue

                text = self._clean_voice_text(entry.get("text") or entry.get("narration"))
                if not text:
                    continue
                slot = str(entry.get("slot") or entry.get("type") or "").strip().lower()
                step_index = entry.get("step_index")
                if slot in {"step", "steps"} or step_index is not None:
                    self._append_segmented_step_text(step_index, text)
                    continue
                if slot in {"summary", "conclusion"}:
                    self._segmented_summary_queue.extend(_split_voiceover_script(text, max_chars=200))
                    continue
                if slot in {"outro", "closing"}:
                    self._segmented_outro_queue.extend(_split_voiceover_script(text, max_chars=200))
                    continue
                self._segmented_intro_queue.extend(_split_voiceover_script(text, max_chars=180))

        steps = spec.get("steps")
        if isinstance(steps, list):
            for step_index, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                step_narration = self._clean_voice_text(step.get("narration") or step.get("voiceover"))
                if not step_narration:
                    continue
                bucket = self._segmented_step_queues.setdefault(step_index, [])
                bucket.append(step_narration)

        self._segmented_intro_queue = _dedupe_voiceover_segments(self._segmented_intro_queue)
        self._segmented_summary_queue = _dedupe_voiceover_segments(self._segmented_summary_queue)
        self._segmented_outro_queue = _dedupe_voiceover_segments(self._segmented_outro_queue)
        for key in list(self._segmented_step_queues.keys()):
            self._segmented_step_queues[key] = _dedupe_voiceover_segments(
                self._segmented_step_queues.get(key, [])
            )

    def _pop_segmented_narration(self, *, slot="intro", step_index=None, fallback_text=""):
        fallback = self._clean_voice_text(fallback_text)
        if self._voiceover_mode != "segmented":
            return fallback

        segment = ""
        if slot == "step" and step_index is not None:
            queue = self._segmented_step_queues.get(int(step_index), [])
            if queue:
                segment = self._clean_voice_text(queue.pop(0))
        elif slot == "summary":
            if self._segmented_summary_queue:
                segment = self._clean_voice_text(self._segmented_summary_queue.pop(0))
        elif slot == "outro":
            if self._segmented_outro_queue:
                segment = self._clean_voice_text(self._segmented_outro_queue.pop(0))
        else:
            if self._segmented_intro_queue:
                segment = self._clean_voice_text(self._segmented_intro_queue.pop(0))

        return segment or fallback

    def _resolve_tts_provider(self, spec):
        requested = (
            spec.get("tts_provider")
            or spec.get("voiceover_provider")
            or os.getenv("MEDIA_TTS_PROVIDER")
            or "gtts_voiceover"
        )
        normalized = _normalize_tts_provider(requested)
        self._requested_tts_provider = normalized
        return normalized

    def _configure_gtts_voiceover(self, spec):
        if GTTSService is None:
            return False
        language = self.resolve_language(spec)
        gtts_lang = _voiceover_lang_for_gtts(language)
        self.set_speech_service(GTTSService(lang=gtts_lang))
        self._voiceover_provider = "gtts_voiceover"
        return True

    def _initialize_voiceover(self, spec):
        if self._voiceover_initialized:
            return

        self._voiceover_initialized = True
        self._voiceover_mode = "segmented" if self._has_segmented_narration(spec) else "auto"

        provider = self._resolve_tts_provider(spec)
        configured = False
        if provider == "none":
            self._voiceover_provider = "none"
            return
        configured = self._configure_gtts_voiceover(spec)

        if not configured:
            self._voiceover_provider = "none"
            return
        self._voiceover_enabled = True

        if self._voiceover_mode == "segmented":
            self._initialize_segmented_narration(spec)
            self._voiceover_segments = []
            self._voiceover_index = 0
            return

        segments = self._build_voiceover_segments(spec)
        if not segments:
            self._voiceover_provider = "none"
            self._voiceover_enabled = False
            return

        self._voiceover_segments = segments
        self._voiceover_index = 0

    def _next_voiceover_segment(self):
        if not self._voiceover_enabled:
            return None
        if self._voiceover_index >= len(self._voiceover_segments):
            return None
        segment = self._voiceover_segments[self._voiceover_index]
        self._voiceover_index += 1
        return segment

    def _play_with_voiceover_segment(self, segment, *args, **kwargs):
        run_time = kwargs.get("run_time")
        try:
            with self.voiceover(text=segment) as tracker:
                updated_kwargs = dict(kwargs)
                if tracker.duration > 0:
                    if run_time is None:
                        updated_kwargs["run_time"] = tracker.duration
                    else:
                        updated_kwargs["run_time"] = max(float(run_time), float(tracker.duration))
                return super().play(*args, **updated_kwargs)
        except Exception:
            self._voiceover_enabled = False
            return super().play(*args, **kwargs)

    def play_with_voiceover(self, narration_text, *args, **kwargs):
        if not narration_text or not self._voiceover_enabled:
            return self.play(*args, **kwargs)
        narration = self._clean_voice_text(narration_text)
        if not narration:
            return self.play(*args, **kwargs)
        # Keep auto segment cursor aligned to avoid duplicate narration later.
        if self._voiceover_mode != "segmented" and self._voiceover_index < len(self._voiceover_segments):
            self._voiceover_index += 1
        return self._play_with_voiceover_segment(narration, *args, **kwargs)

    def play(self, *args, **kwargs):
        if self._voiceover_mode == "segmented":
            return super().play(*args, **kwargs)
        segment = self._next_voiceover_segment()
        if not segment:
            return super().play(*args, **kwargs)
        return self._play_with_voiceover_segment(segment, *args, **kwargs)

    def resolve_language(self, spec=None):
        payload = spec if isinstance(spec, dict) else getattr(self, "SPEC", {})
        candidates = [
            payload.get("language"),
            payload.get("locale"),
            payload.get("lang"),
        ]
        for raw in candidates:
            if raw is None:
                continue
            normalized = str(raw).strip().lower()
            if not normalized:
                continue
            lang = LANGUAGE_ALIASES.get(normalized, normalized.split("-")[0])
            if lang in {"id", "en", "vi", "ms", "ja"}:
                self._resolved_language = lang
                return lang
        self._pace = None
        self._resolved_language = "id"
        return "id"

    def tr_key(self, key, spec=None, fallback=""):
        lang = self.resolve_language(spec)
        values = I18N_LABELS.get(key, {})
        if not values:
            return fallback
        if lang in values:
            return values[lang]
        if lang != "id" and "en" in values:
            return values["en"]
        return values.get("id", fallback)

    def tr_text(self, text, spec=None):
        if text is None:
            return ""
        lang = self.resolve_language(spec)
        if lang == "id":
            return text
        normalized = " ".join(str(text).split())
        if not normalized:
            return str(text)
        values = I18N_PHRASES.get(normalized)
        if not values:
            return str(text)
        return values.get(lang) or values.get("en") or str(text)

    def make_title_block(self, spec):
        self.resolve_language(spec)
        self._initialize_voiceover(spec)
        phase = str(spec.get("phase", "")).upper()
        level = str(spec.get("audience_level", "")).lower()

        if phase in ["A", "B", "C"] or level in ["sd", "elementary"]:
            title_size = 34
            subtitle_size = 19
            subtitle_width = 56
        elif phase in ["E", "F"] or level in ["sma", "high"]:
            title_size = 33
            subtitle_size = 18
            subtitle_width = 62
        else:
            title_size = 33
            subtitle_size = 18
            subtitle_width = 60

        title = Text(
            clamp_text(
                spec.get(
                    "title",
                    self.tr_key("default_title", spec, fallback="Penjelasan Konsep"),
                ),
                50,
            ),
            font_size=title_size,
            color=theme.ON_INK,
            **theme.font_kwargs("bold"),
        )

        # Eyebrow above, gradient rule below: the deck's title signature. The
        # eyebrow carries the phase/subject so a viewer landing mid-video knows
        # where they are.
        eyebrow_source = (
            spec.get("eyebrow")
            or spec.get("subject_name")
            or spec.get("topic")
            or self.tr_key("default_title", spec, fallback="WICARA")
        )
        head = VGroup(
            theme.eyebrow(clamp_text(str(eyebrow_source), 34)),
            title,
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.14)

        rule = theme.accent_rule(width=min(2.2, max(1.2, title.width * 0.28)))
        block = VGroup(head, rule).arrange(DOWN, aligned_edge=LEFT, buff=0.16)

        subtitle_text = spec.get("subtitle", "")
        if subtitle_text:
            subtitle = Text(
                wrap_text(clamp_text(subtitle_text, 96), subtitle_width),
                font_size=subtitle_size,
                color=theme.ON_INK_2,
                line_spacing=0.82,
                **theme.font_kwargs("regular"),
            )
            block = VGroup(block, subtitle).arrange(DOWN, aligned_edge=LEFT, buff=0.16)

        if self._portrait:
            # Centred, tighter margins, and scaled down if the headline still
            # runs past a narrow frame.
            block.to_edge(UP, buff=0.34)
            if block.width > config.frame_width - 0.6:
                block.scale_to_fit_width(config.frame_width - 0.6)
            block.move_to(
                np.array([0.0, block.get_center()[1], 0.0])
            )
        else:
            block.to_edge(UP, buff=0.30).to_edge(LEFT, buff=0.62)
        return block

    def make_card(self, title, body, color=None, width=None, body_width=None):
        # Card geometry follows the frame. A 4.75-wide card is a third of a 16:9
        # stage and wider than a 9:16 frame.
        if width is None:
            width = min(4.75, config.frame_width - 0.9)
        if body_width is None:
            body_width = 34 if not self._portrait else 30
        # Templates still pass stock Manim colours positionally. Map anything
        # that is not already a brand token onto the accent so no card falls
        # back to raw YELLOW/GREEN on the ink ground.
        accent = color if isinstance(color, str) and str(color).startswith("#") else None
        if accent is None:
            accent = theme.ACCENT
        # A palette is free to include colours that are dark on a dark ground --
        # mono's fifth chip is #3F3F49 -- and a card heading has to stay readable
        # whichever one it is handed.
        accent = theme.on_ground(accent)

        localized_title = self.tr_text(title)
        localized_body = self.tr_text(body)
        title_obj = Text(
            safe_text(localized_title, max_chars=40, width=28),
            font_size=theme.FS_SUB,
            color=accent,
            **theme.font_kwargs("semibold"),
        )

        # Manim's Text centres every line of a multi-line string, which made
        # card copy sit in a ragged column under a left-aligned heading.
        # Paragraph is the one that takes an alignment.
        body_lines = safe_text(
            localized_body, max_chars=145, width=body_width
        ).split("\n")
        body_obj = Paragraph(
            *body_lines,
            font_size=theme.FS_BODY,
            line_spacing=0.86,
            alignment="left",
            color=theme.ON_INK_2,
            **theme.font_kwargs("regular"),
        )

        group = VGroup(title_obj, body_obj).arrange(
            DOWN,
            aligned_edge=LEFT,
            buff=0.20,
        )

        box = theme.panel(
            width=max(width, group.width + 0.62),
            height=max(1.15, group.height + 0.56),
        )

        # A lit edge on the leading side, so a card reads as an active panel
        # rather than an outlined box.
        edge = Line(
            box.get_corner(UL) + DOWN * 0.16,
            box.get_corner(DL) + UP * 0.16,
            stroke_width=4,
        )
        edge.set_stroke(color=[accent, theme.VIOLET])

        group.move_to(box.get_center())
        card = VGroup(box, edge, group)
        card.wicara_card_title = self._clean_voice_text(localized_title)
        card.wicara_card_body = self._clean_voice_text(localized_body)
        card.wicara_card_narration = self._join_narration_parts(
            card.wicara_card_title,
            card.wicara_card_body,
        )
        return card

    def place_right_card(self, card):
        # There is no right rail on a 9:16 frame -- it is 4.5 units wide, and a
        # card centred at x=4.05 sits entirely off-screen. Portrait sends every
        # card to the bottom band instead, which is also where a phone viewer's
        # eye already is.
        if self.card_zone() == "bottom":
            return self.place_summary_card(card)
        card.move_to(self.right_card_center())
        return card

    def place_summary_card(self, card):
        card.to_edge(DOWN, buff=0.28)
        return card

    def replace_card(self, previous_card, next_card, zone="right", narration_text=None):
        if zone == "right":
            self.place_right_card(next_card)
        elif zone == "bottom":
            self.place_summary_card(next_card)

        if narration_text is None and self._voiceover_mode == "segmented":
            narration_text = self._clean_voice_text(
                getattr(next_card, "wicara_card_narration", "")
            )
            if previous_card is None:
                intro = self._pop_segmented_narration(slot="intro")
                if intro:
                    narration_text = intro

        if previous_card is None:
            self.play_with_voiceover(
                narration_text,
                FadeIn(next_card, shift=LEFT * 0.30),
                run_time=motion.spring_seconds("gentle"),
                rate_func=motion.spring_rate("gentle"),
            )
        else:
            # Fade rather than morph: ReplacementTransform pairs submobjects one
            # to one, so two cards whose bodies wrap to a different number of
            # lines raise "zip() argument 2 is shorter than argument 1".
            #
            # Staggered, not simultaneous. Both cards sit at the same spot, so
            # cross-fading them over one shared window left both headings legible
            # at once -- on a template that swaps cards every half second the
            # panel read as permanently double-exposed. LaggedStart clears the
            # outgoing card before the incoming one becomes readable, and the
            # shared upward shift reads as a stack advancing.
            self.play_with_voiceover(
                narration_text,
                LaggedStart(
                    FadeOut(previous_card, shift=UP * 0.28),
                    FadeIn(
                        next_card,
                        shift=UP * 0.34,
                        rate_func=motion.spring_rate("gentle"),
                    ),
                    lag_ratio=0.55,
                ),
                run_time=0.72,
            )

        return next_card

    def fade_card(self, card):
        if card is not None:
            self.play(FadeOut(card), run_time=0.32)

    def clean_summary(self, spec, active_card=None, extra_fadeouts=None):
        # Fade out everything currently on screen so the summary gets a clean frame.
        # active_card and extra_fadeouts are kept for API compatibility but are
        # subsumed — self.mobjects already contains them.
        on_screen = self.scene_content()
        if on_screen:
            self.play(*[FadeOut(m) for m in on_screen], run_time=0.42)

        summary_card = self.make_card(
            self.tr_key("summary_title", spec, fallback="Kesimpulan"),
            require(spec, "summary"),
            color=GREEN,
            width=8.5,
            body_width=64,
        )
        summary_card.center()
        summary_narration = self._pop_segmented_narration(
            slot="summary",
            fallback_text=require(spec, "summary"),
        )
        self.play_with_voiceover(
            summary_narration,
            FadeIn(summary_card, shift=UP * 0.12),
            run_time=0.55,
        )
        self.wait(2.0)
        return summary_card

    # --------------------------------------------------------
    # Pacing
    # --------------------------------------------------------
    #
    # Every render used to come out at roughly the same length no matter what
    # it was explaining, because the beats were constants: a hard max of five
    # steps and a flat 0.9s dwell after each. A one-step arithmetic reminder and
    # a six-step derivation both landed near seventeen seconds -- the first
    # dawdled, the second got truncated.
    #
    # Duration is now a function of what the spec actually contains.

    #: Characters of on-screen text a viewer gets through per second. Deliberately
    #: conservative: this is a second language for most of the audience, and the
    #: text sits beside a moving figure competing for attention.
    READ_RATE = 13.0

    def complexity(self, spec=None):
        """0..1 -- how much work this lesson is asking the viewer to do."""
        spec = spec if spec is not None else self.SPEC
        if not isinstance(spec, dict):
            return 0.35

        steps = [s for s in (spec.get("steps") or []) if isinstance(s, dict)]
        # Step count, up to eight, is the strongest single signal.
        by_count = min(len(steps), 8) / 8.0

        body_chars = sum(
            len(str(s.get("body", ""))) + len(str(s.get("title", ""))) for s in steps
        )
        by_text = min(body_chars, 900) / 900.0

        level = str(spec.get("audience_level", "")).lower()
        by_level = {
            "sd": 0.10, "elementary": 0.10,
            "smp": 0.40, "middle": 0.40,
            "sma": 0.70, "high": 0.70,
            "kuliah": 0.90, "university": 0.90,
        }.get(level, 0.45)

        # Formulae and a named phase both mean more to unpack.
        extras = 0.0
        for key in ("formula", "prime_factorization", "equation", "derivation"):
            if spec.get(key):
                extras += 0.10
        extras = min(extras, 0.25)

        score = 0.40 * by_count + 0.30 * by_text + 0.22 * by_level + extras
        return max(0.0, min(1.0, score))

    def pace(self, spec=None):
        """Multiplier applied to every timed beat. Cached per scene."""
        if getattr(self, "_pace", None) is None:
            override = None
            if isinstance(self.SPEC, dict):
                override = self.SPEC.get("pace")
            if isinstance(override, (int, float)) and override > 0:
                self._pace = float(max(0.5, min(2.5, override)))
            else:
                # 0.85x for the simplest lesson, 1.55x for the densest.
                self._pace = 0.85 + 0.70 * self.complexity(spec)
        return self._pace

    def beat(self, seconds):
        """Scale an animation run_time by this lesson's pace."""
        return float(seconds) * self.pace()

    def hold_for(self, *texts):
        """Dwell long enough to actually read `texts`, scaled by pace."""
        chars = sum(len(self._clean_voice_text(t)) for t in texts if t)
        seconds = chars / self.READ_RATE
        return float(max(0.65, min(4.0, seconds))) * self.pace()

    def step_budget(self, spec=None):
        """How many step cards this lesson earns.

        The old hard cap of five silently dropped the tail of any longer
        derivation, which is exactly where the hard part usually is.
        """
        spec = spec if spec is not None else self.SPEC
        steps = [s for s in (spec.get("steps") or []) if isinstance(s, dict)]
        if not steps:
            return 0
        return min(len(steps), 4 + int(round(self.complexity(spec) * 6)))

    def render_step_cards(self, spec, active_card=None, max_steps=None):
        steps = require(spec, "steps")
        if max_steps is None:
            max_steps = self.step_budget(spec)

        for i, step in enumerate(steps[:max_steps]):
            step_title = step.get(
                "title",
                f"{self.tr_key('step_prefix', spec, fallback='Langkah')} {i + 1}",
            )
            step_body = step.get("body", "")
            card = self.make_card(
                step_title,
                step_body,
                color=step.get("color", TEAL),
            )
            default_step_narration = self._clean_voice_text(step.get("narration") or step.get("voiceover"))
            if not default_step_narration:
                default_step_narration = self._clean_voice_text(
                    f"{step_title}. {step_body}" if step_title and step_body else step_title or step_body
                )
            step_narration = self._pop_segmented_narration(
                slot="step",
                step_index=i,
                fallback_text=default_step_narration,
            )
            active_card = self.replace_card(
                active_card,
                card,
                narration_text=step_narration,
            )
            # Dwell on what is on screen for as long as it takes to read it,
            # not for a flat 0.9s. A spec may still pin its own `wait`.
            explicit = step.get("wait")
            if isinstance(explicit, (int, float)):
                self.wait(float(explicit))
            else:
                self.wait(self.hold_for(step_title, step_body))

        return active_card


WicaraScene = WicaraTemplateScene


# ============================================================
# 1. NUMBER LINE QUANTITY
# ============================================================

class NumberLineQuantityTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_number_line_compare",
        "node_id": "km_d_matematika_bilangan_bulat",
        "template_id": "manim.number_line_quantity.v1",
        "phase": "D",
        "audience_level": "smp",
        "title": "Bilangan pada Garis Bilangan",
        "subtitle": "Semakin ke kanan, nilainya semakin besar.",
        "number_range": {"min": -5, "max": 5, "step": 1},
        "markers": [
            {"value": -3, "label": "-3", "description": "lebih kecil"},
            {"value": 2, "label": "2", "description": "lebih besar"},
        ],
        "highlight_values": [-3, 2],
        "operation": {
            "type": "compare",
            "from": -3,
            "to": 2,
            "label": "2 lebih besar dari -3",
        },
        "steps": [
            {
                "title": "Baca arah garis",
                "body": "Nilai pada garis bilangan makin besar jika bergerak ke kanan.",
                "color": BLUE,
            },
            {
                "title": "Tandai dua angka",
                "body": "-3 berada di kiri, sedangkan 2 berada di kanan.",
                "color": TEAL,
            },
            {
                "title": "Bandingkan posisi",
                "body": "Karena 2 lebih kanan, maka 2 bernilai lebih besar daripada -3.",
                "color": PURPLE,
            },
        ],
        "summary": "Pada garis bilangan, angka di kanan bernilai lebih besar.",
        "voiceover_script": "Perhatikan garis bilangan ini. Angka minus tiga berada di kiri, sedangkan angka dua berada di kanan.",
    }

    def construct(self):
        spec = self.SPEC

        nr = require(spec, "number_range")
        markers = require(spec, "markers")

        min_v = float(nr.get("min", -5))
        max_v = float(nr.get("max", 5))
        step = float(nr.get("step", 1))
        if max_v <= min_v:
            raise ValueError("number_range.max must be greater than min.")

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)

        intro_card = self.make_card(
            "Ide utama",
            "Garis bilangan membantu membandingkan posisi dan nilai angka.",
            color=BLUE,
        )
        active_card = self.replace_card(None, intro_card)
        self.wait(0.6)

        number_line = NumberLine(
            x_range=[min_v, max_v, step],
            length=6.6,
            include_numbers=True,
            font_size=19,
        )
        # Pull visual further left so the right-side teaching card never overlaps.
        number_line.move_to(self.visual_center() + LEFT * 0.60 + DOWN * 0.20)

        axis_title = Text(self.tr_text("Garis bilangan"), font_size=22, color=GRAY_A)
        axis_title.next_to(number_line, UP, buff=0.35)

        self.play(Create(number_line), FadeIn(axis_title), run_time=0.9)

        marker_groups = VGroup()
        for marker in markers:
            value = float(marker["value"])
            dot = Dot(number_line.n2p(value), radius=0.075, color=YELLOW)
            label = Text(
                clamp_text(marker.get("label", str(value)), 14),
                font_size=20,
                color=YELLOW,
            ).next_to(dot, UP, buff=0.16)

            desc = marker.get("description")
            if desc:
                desc_mob = Text(
                    clamp_text(desc, 18),
                    font_size=15,
                    color=GRAY_A,
                ).next_to(dot, DOWN, buff=0.16)
                marker_groups.add(VGroup(dot, label, desc_mob))
            else:
                marker_groups.add(VGroup(dot, label))

        marker_card = self.make_card(
            "Tandai angka",
            "Setiap angka ditempatkan sesuai posisinya di garis bilangan.",
            color=TEAL,
        )
        active_card = self.replace_card(active_card, marker_card)

        self.play(
            LaggedStart(*[FadeIn(m) for m in marker_groups], lag_ratio=0.15),
            run_time=0.85,
        )

        op = spec.get("operation", {})
        compare_group = None

        if op:
            start = float(op.get("from", markers[0]["value"]))
            end = float(op.get("to", markers[-1]["value"]))

            arrow = CurvedArrow(
                number_line.n2p(start) + DOWN * 0.82,
                number_line.n2p(end) + DOWN * 0.82,
                angle=-TAU / 6,
                color=BLUE,
                stroke_width=4,
            )

            label = Text(
                clamp_text(op.get("label", ""), 44),
                font_size=19,
                color=BLUE,
            ).next_to(arrow, DOWN, buff=0.14)

            compare_group = VGroup(arrow, label)

            compare_card = self.make_card(
                "Bandingkan",
                "Arah panah menunjukkan perpindahan dari angka kiri ke angka kanan.",
                color=PURPLE,
            )
            active_card = self.replace_card(active_card, compare_card)
            self.play(Create(arrow), FadeIn(label), run_time=0.85)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


# ============================================================
# 2. ELEMENTARY ARITHMETIC BLOCKS
# ============================================================

class ElementaryArithmeticBlocksTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_arithmetic_addition",
        "node_id": "km_a_matematika_penjumlahan_dan_pengurangan_bilangan_cacah",
        "template_id": "manim.elementary_arithmetic_blocks.v1",
        "phase": "A",
        "audience_level": "sd",
        "title": "Menjumlahkan dengan Blok",
        "subtitle": "Penjumlahan berarti menggabungkan dua kelompok benda.",
        "operation_type": "addition",
        "operands": [12, 8],
        "blocks": {"model": "counters"},
        "grouping_steps": [
            {"label": "Kelompok pertama", "value": 12},
            {"label": "Kelompok kedua", "value": 8},
            {"label": "Gabungan", "value": 20},
        ],
        "result": 20,
        "steps": [
            {
                "title": "Dua kelompok",
                "body": "Kelompok biru berisi 12 blok, kelompok hijau berisi 8 blok.",
                "color": BLUE,
            },
            {
                "title": "Gabungkan",
                "body": "Untuk menjumlahkan, kita menghitung semua blok bersama.",
                "color": TEAL,
            },
            {
                "title": "Hasil akhir",
                "body": "Jumlah semua blok adalah 20.",
                "color": GREEN,
            },
        ],
        "summary": "Penjumlahan adalah proses menggabungkan dua kelompok menjadi satu jumlah.",
        "voiceover_script": "Kita punya dua kelompok blok. Saat digabung, semua blok dihitung bersama.",
    }

    def make_blocks(self, count, color=BLUE, max_cols=10, side=0.21):
        count = int(max(0, min(count, 80)))
        blocks = VGroup()

        for i in range(count):
            sq = Square(
                side_length=side,
                stroke_width=1,
                stroke_color=WHITE,
                fill_color=color,
                fill_opacity=0.86,
            )
            row = i // max_cols
            col = i % max_cols
            sq.move_to(RIGHT * col * (side + 0.055) + DOWN * row * (side + 0.055))
            blocks.add(sq)

        blocks.center()
        return blocks

    def construct(self):
        spec = self.SPEC

        op_type = require(spec, "operation_type")
        operands = require(spec, "operands")
        result = int(require(spec, "result"))

        a = int(operands[0])
        b = int(operands[1])

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)

        symbols = {
            "addition": "+",
            "subtraction": "-",
            "multiplication": "×",
            "division": "÷",
        }
        symbol = symbols.get(op_type, "→")

        equation = Text(
            f"{a} {symbol} {b} = {result}",
            font_size=36,
            weight=BOLD,
            color=YELLOW,
        )
        equation.next_to(title_block, DOWN, buff=0.25)

        self.play(Write(equation), run_time=0.65)

        intro_card = self.make_card(
            "Model blok",
            "Setiap kotak kecil mewakili satu benda atau satu satuan.",
            color=BLUE,
        )
        active_card = self.replace_card(None, intro_card)

        if op_type == "multiplication":
            visual = VGroup()
            rows = min(a, 6)
            cols = max(1, min(b, 10))
            for r in range(rows):
                group = self.make_blocks(cols, color=TEAL, max_cols=cols, side=0.20)
                visual.add(group)
            visual.arrange(DOWN, buff=0.08)
            visual.move_to(self.visual_center())

        elif op_type == "division":
            blocks = self.make_blocks(a, color=PURPLE, max_cols=10, side=0.20)
            people = VGroup()
            for i in range(min(b, 8)):
                person = Circle(radius=0.16, color=YELLOW, fill_opacity=0.82)
                label = Text(str(i + 1), font_size=12).move_to(person)
                people.add(VGroup(person, label))
            people.arrange(RIGHT, buff=0.18)
            people.next_to(blocks, DOWN, buff=0.45)
            visual = VGroup(blocks, people).move_to(self.visual_center())

        else:
            left = self.make_blocks(a, color=BLUE, max_cols=8, side=0.21)
            right = self.make_blocks(b, color=GREEN, max_cols=8, side=0.21)

            left_label = Text(f"{a}", font_size=24, color=BLUE).next_to(left, UP, buff=0.18)
            right_label = Text(f"{b}", font_size=24, color=GREEN).next_to(right, UP, buff=0.18)

            plus = Text("+", font_size=34, weight=BOLD)

            visual = VGroup(
                VGroup(left, left_label),
                plus,
                VGroup(right, right_label),
            ).arrange(RIGHT, buff=0.45)

            visual.move_to(self.visual_center())

        self.play(FadeIn(visual, shift=UP * 0.1), run_time=0.9)

        group_card = self.make_card(
            "Gabungkan jumlah",
            "Kita melihat dua kelompok lalu menyatukannya menjadi satu hasil.",
            color=TEAL,
        )
        active_card = self.replace_card(active_card, group_card)
        self.wait(0.8)

        result_circle = Circle(radius=0.42, color=GREEN, fill_opacity=0.20)
        result_text = Text(str(result), font_size=36, weight=BOLD, color=GREEN).move_to(result_circle)
        result_group = VGroup(result_circle, result_text)
        result_group.next_to(visual, DOWN, buff=0.48)

        self.play(FadeIn(result_group, scale=0.9), run_time=0.65)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


# ============================================================
# 3. FRACTION BAR PARTITION
# ============================================================

class FractionBarPartitionTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_fraction_equivalent",
        "node_id": "km_b_matematika_pecahan_senilai_dan_perbandingan_pecahan",
        "template_id": "manim.fraction_bar_partition.v1",
        "phase": "B",
        "audience_level": "sd",
        "title": "Pecahan Senilai",
        "subtitle": "Bagian yang sama dapat ditulis dengan pecahan berbeda.",
        "representations": ["fraction"],
        "fractions": [
            {"numerator": 1, "denominator": 2, "label": "1/2"},
            {"numerator": 2, "denominator": 4, "label": "2/4"},
        ],
        "partition_count": 4,
        "highlight_parts": [1, 2],
        "equivalences": [{"left": "1/2", "right": "2/4"}],
        "steps": [
            {
                "title": "Bagi sama besar",
                "body": "Pecahan harus dibagi menjadi bagian-bagian yang sama besar.",
                "color": BLUE,
            },
            {
                "title": "Bandingkan warna",
                "body": "Bagian berwarna pada 1/2 dan 2/4 sama panjang.",
                "color": TEAL,
            },
            {
                "title": "Nilainya sama",
                "body": "Karena luas bagian berwarna sama, kedua pecahan senilai.",
                "color": GREEN,
            },
        ],
        "summary": "Pecahan senilai memiliki nilai yang sama walaupun bentuk tulisannya berbeda.",
        "voiceover_script": "Satu per dua dan dua per empat terlihat berbeda, tetapi bagian yang diwarnai sama besar.",
    }

    def make_fraction_bar(self, numerator, denominator, label, color=BLUE):
        numerator = int(numerator)
        denominator = int(denominator)

        if denominator <= 0:
            raise ValueError("denominator must be positive.")

        numerator = max(0, min(numerator, denominator))

        width = 5.35
        height = 0.56
        part_width = width / denominator

        parts = VGroup()
        for i in range(denominator):
            rect = Rectangle(
                width=part_width,
                height=height,
                stroke_color=WHITE,
                stroke_width=1.15,
                fill_color=color if i < numerator else BLACK,
                fill_opacity=0.83 if i < numerator else 0.18,
            )
            rect.move_to(RIGHT * (i - (denominator - 1) / 2) * part_width)
            parts.add(rect)

        label_mob = Text(label, font_size=25, weight=BOLD, color=color)
        label_mob.next_to(parts, LEFT, buff=0.35)

        return VGroup(label_mob, parts)

    def construct(self):
        spec = self.SPEC

        fractions = require(spec, "fractions")

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)

        intro_card = self.make_card(
            "Bagian dari keseluruhan",
            "Pecahan menunjukkan berapa bagian yang diambil dari satu keseluruhan.",
            color=BLUE,
        )
        active_card = self.replace_card(None, intro_card)

        bars = VGroup()
        colors = [BLUE, GREEN, PURPLE]

        for i, fr in enumerate(fractions[:3]):
            numerator = fr["numerator"]
            denominator = fr["denominator"]
            label = fr.get("label", f"{numerator}/{denominator}")
            bars.add(self.make_fraction_bar(numerator, denominator, label, colors[i % len(colors)]))

        bars.arrange(DOWN, aligned_edge=LEFT, buff=0.58)
        bars.move_to(self.visual_center())

        self.play(
            LaggedStart(*[FadeIn(bar, shift=UP * 0.08) for bar in bars], lag_ratio=0.18),
            run_time=1.0,
        )

        compare_card = self.make_card(
            "Bandingkan bagian",
            "Walau jumlah potongannya berbeda, bagian yang diwarnai bisa sama besar.",
            color=TEAL,
        )
        active_card = self.replace_card(active_card, compare_card)

        eqs = []
        for eq in spec.get("equivalences", [])[:2]:
            eqs.append(f"{eq.get('left')} = {eq.get('right')}")

        eq_text = None
        if eqs:
            eq_text = Text(
                "   ".join(eqs),
                font_size=30,
                color=YELLOW,
                weight=BOLD,
            )
            eq_text.next_to(bars, DOWN, buff=0.45)
            self.play(Write(eq_text), run_time=0.6)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


# ============================================================
# 4. RATIO PROPORTION
# ============================================================

class RatioProportionTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_ratio_syrup",
        "node_id": "km_d_matematika_rasio",
        "template_id": "manim.ratio_proportion.v1",
        "phase": "D",
        "audience_level": "smp",
        "title": "Rasio Gula dan Air",
        "subtitle": "Rasio menjaga perbandingan dua kuantitas.",
        "context": "Membuat sirup",
        "quantities": [
            {"label": "Gula", "value": 2, "unit": "sendok"},
            {"label": "Air", "value": 5, "unit": "gelas"},
        ],
        "ratio_pairs": [["Gula", "Air"]],
        "scale_factor": 2,
        "scaling_steps": [
            {"from": "2:5", "to": "4:10", "label": "Dikali 2"},
        ],
        "steps": [
            {
                "title": "Rasio awal",
                "body": "Gula dan air dibandingkan 2 banding 5.",
                "color": BLUE,
            },
            {
                "title": "Skalakan bersama",
                "body": "Jika gula dikali 2, air juga harus dikali 2.",
                "color": TEAL,
            },
            {
                "title": "Proporsi tetap",
                "body": "Perbandingan tetap sama karena keduanya dikalikan faktor yang sama.",
                "color": GREEN,
            },
        ],
        "summary": "Proporsi terjaga jika semua kuantitas dikalikan faktor yang sama.",
        "voiceover_script": "Rasio dua banding lima berarti dua sendok gula dipasangkan dengan lima gelas air.",
    }

    def make_quantity_bar(self, label, value, unit, color, max_value):
        value = float(value)
        safe_max = max(float(max_value), 1.0)
        fill_ratio = max(0.0, min(1.0, value / safe_max))

        label_text = Text(
            clamp_text(f"{label}: {value:g} {unit}".strip(), 28),
            font_size=22,
            color=color,
            weight=BOLD,
        )
        label_text.scale_to_fit_width(min(2.35, max(label_text.width, 1.2)))

        track = RoundedRectangle(
            corner_radius=0.06,
            width=4.55,
            height=0.48,
            stroke_color=WHITE,
            stroke_width=2,
            fill_color=BLACK,
            fill_opacity=0.35,
        )

        fill_width = max(0.26, 4.35 * fill_ratio)
        fill = RoundedRectangle(
            corner_radius=0.04,
            width=fill_width,
            height=0.32,
            stroke_width=0,
            fill_color=color,
            fill_opacity=0.82,
        )
        fill.move_to(track.get_left() + RIGHT * (0.10 + fill_width / 2))

        value_text = Text(f"{value:g}", font_size=19, color=WHITE)
        value_text.next_to(track, RIGHT, buff=0.16)

        row = VGroup(label_text, track, fill, value_text)
        label_text.align_to(track, DOWN).shift(UP * 0.04)
        label_text.next_to(track, LEFT, buff=0.22)
        return row

    def construct(self):
        spec = self.SPEC

        quantities = require(spec, "quantities")
        max_value = max(float(q.get("value", 1)) for q in quantities[:4])

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)

        context = Text(
            clamp_text(
                spec.get(
                    "context",
                    self.tr_key("ratio_context_default", spec, fallback="Konteks rasio"),
                ),
                60,
            ),
            font_size=23,
            color=GRAY_A,
        )
        context.next_to(title_block, DOWN, buff=0.24)
        self.play(FadeIn(context), run_time=0.45)

        intro_card = self.make_card(
            "Apa itu rasio?",
            "Rasio membandingkan dua kuantitas dalam satu situasi.",
            color=BLUE,
        )
        active_card = self.replace_card(None, intro_card)

        colors = [BLUE, GREEN, PURPLE, ORANGE]
        bars = VGroup()

        for i, q in enumerate(quantities[:4]):
            bars.add(
                self.make_quantity_bar(
                    q.get("label", f"Q{i + 1}"),
                    q.get("value", 1),
                    q.get("unit", ""),
                    colors[i % len(colors)],
                    max_value=max_value,
                )
            )

        bars.arrange(DOWN, aligned_edge=LEFT, buff=0.48)
        bars.move_to(self.visual_center() + LEFT * 0.15 + DOWN * 0.10)

        self.play(
            LaggedStart(*[FadeIn(bar, shift=RIGHT * 0.08) for bar in bars], lag_ratio=0.18),
            run_time=1.15,
        )

        ratio_pairs = spec.get("ratio_pairs", [])
        ratio_text = None
        if ratio_pairs:
            pair = ratio_pairs[0]
            if isinstance(pair, list) and len(pair) == 2:
                left_name, right_name = str(pair[0]), str(pair[1])
                q_map = {str(item.get("label", "")): float(item.get("value", 0)) for item in quantities}
                left_value = q_map.get(left_name, 0.0)
                right_value = q_map.get(right_name, 0.0)
                ratio_text = Text(
                    f"{left_name} : {right_name} = {left_value:g} : {right_value:g}",
                    font_size=24,
                    color=YELLOW,
                    weight=BOLD,
                )
                ratio_text.next_to(context, DOWN, buff=0.24).shift(LEFT * 1.10)
                self.play(FadeIn(ratio_text, shift=UP * 0.08), run_time=0.6)

        scale_factor = spec.get("scale_factor")
        scale_text = None
        if scale_factor is not None:
            scale_text = Text(
                f"Faktor skala: ×{scale_factor}",
                font_size=27,
                color=YELLOW,
                weight=BOLD,
            )
            scale_text.next_to(bars, DOWN, buff=0.36)
            self.play(Write(scale_text), run_time=0.5)

        transform_text = None
        for step in spec.get("scaling_steps", [])[:1]:
            transform_text = Text(
                f"{step.get('from')}  →  {step.get('to')}",
                font_size=29,
                color=GREEN,
                weight=BOLD,
            )
            if ratio_text is not None:
                transform_text.next_to(ratio_text, DOWN, buff=0.18).align_to(ratio_text, LEFT)
            else:
                transform_text.next_to(bars, UP, buff=0.34)
            self.play(FadeIn(transform_text, shift=UP * 0.08), run_time=0.5)

        if scale_factor is not None and float(scale_factor) not in (0.0, 1.0):
            scaled_values = []
            for q in quantities[:4]:
                scaled = dict(q)
                scaled["value"] = float(q.get("value", 0)) * float(scale_factor)
                scaled_values.append(scaled)

            scaled_max = max(float(item.get("value", 1)) for item in scaled_values) if scaled_values else 1.0
            scaled_bars = VGroup()
            for i, q in enumerate(scaled_values):
                scaled_bars.add(
                    self.make_quantity_bar(
                        q.get("label", f"Q{i + 1}"),
                        q.get("value", 1),
                        q.get("unit", ""),
                        colors[i % len(colors)],
                        max_value=scaled_max,
                    )
                )
            scaled_bars.arrange(DOWN, aligned_edge=LEFT, buff=0.48)
            scaled_bars.move_to(bars.get_center())

            self.play(Transform(bars, scaled_bars), run_time=1.0)
            self.play(
                LaggedStart(
                    *[Indicate(row, color=YELLOW, scale_factor=1.03) for row in bars],
                    lag_ratio=0.15,
                ),
                run_time=0.8,
            )

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


# ============================================================
# 5. EQUATION BALANCE
# ============================================================

class EquationBalanceTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_equation_balance",
        "node_id": "km_d_matematika_persamaan_linear_satu_variabel",
        "template_id": "manim.equation_balance.v1",
        "phase": "D",
        "audience_level": "smp",
        "title": "Persamaan sebagai Timbangan",
        "subtitle": "Operasi di kiri juga harus dilakukan di kanan.",
        "equation": "2x + 3 = 11",
        "left_expression": "2x + 3",
        "right_expression": "11",
        "solution_steps": [
            {
                "operation": "Kurangi 3",
                "left_result": "2x",
                "right_result": "8",
                "explanation": "Kurangi 3 di kedua sisi agar keseimbangan tetap terjaga.",
            },
            {
                "operation": "Bagi 2",
                "left_result": "x",
                "right_result": "4",
                "explanation": "Bagi kedua sisi dengan 2 supaya x berdiri sendiri.",
            },
        ],
        "final_solution": "x = 4",
        "steps": [
            {
                "title": "Jaga dua ruas",
                "body": "Ruas kiri dan ruas kanan harus tetap setara.",
                "color": BLUE,
            },
            {
                "title": "Lakukan operasi sama",
                "body": "Apa yang dilakukan ke kiri juga dilakukan ke kanan.",
                "color": TEAL,
            },
            {
                "title": "Temukan x",
                "body": "Setelah x berdiri sendiri, kita mendapatkan nilainya.",
                "color": GREEN,
            },
        ],
        "summary": "Nilai x ditemukan dengan menjaga kedua ruas tetap setara.",
        "voiceover_script": "Bayangkan persamaan seperti timbangan. Jika satu sisi diubah, sisi lainnya juga harus diubah.",
    }

    def make_balance(self):
        beam = Line(
            LEFT * 2.75,
            RIGHT * 2.75,
            color=GRAY_B,
            stroke_width=7,
        )
        pivot = Triangle(color=GRAY_B, fill_opacity=0.80).scale(0.36)
        pivot.next_to(beam, DOWN, buff=0.02)

        left_anchor = Dot(LEFT * 2.00 + DOWN * 0.02, radius=0.01, color=GRAY_B)
        right_anchor = Dot(RIGHT * 2.00 + DOWN * 0.02, radius=0.01, color=GRAY_B)

        plate_drop = 0.78
        left_plate = RoundedRectangle(
            corner_radius=0.06,
            width=1.78,
            height=0.20,
            stroke_color=BLUE,
            stroke_width=2.6,
            fill_color=BLUE_E,
            fill_opacity=0.50,
        ).move_to(left_anchor.get_center() + DOWN * plate_drop)
        right_plate = RoundedRectangle(
            corner_radius=0.06,
            width=1.78,
            height=0.20,
            stroke_color=GREEN,
            stroke_width=2.6,
            fill_color=GREEN_E,
            fill_opacity=0.50,
        ).move_to(right_anchor.get_center() + DOWN * plate_drop)

        left_rope = Line(
            left_anchor.get_center(),
            left_plate.get_top() + UP * 0.01,
            color=GRAY_B,
            stroke_width=2.3,
        )
        right_rope = Line(
            right_anchor.get_center(),
            right_plate.get_top() + UP * 0.01,
            color=GRAY_B,
            stroke_width=2.3,
        )

        balance = VGroup(
            beam,
            pivot,
            left_anchor,
            right_anchor,
            left_rope,
            right_rope,
            left_plate,
            right_plate,
        )
        balance.move_to(self.visual_center())
        return balance, pivot, left_plate, right_plate

    def make_plate_text(self, value, plate, color):
        text_mob = Text(str(value), font_size=29, color=color, weight=BOLD)
        text_bg = RoundedRectangle(
            corner_radius=0.06,
            width=text_mob.width + 0.22,
            height=text_mob.height + 0.16,
            stroke_width=0,
            fill_color=BLACK,
            fill_opacity=0.70,
        )
        text_group = VGroup(text_bg, text_mob)
        text_group.move_to(plate.get_center() + UP * 0.01)
        text_group.add_updater(lambda m, target=plate: m.move_to(target.get_center() + UP * 0.01))
        return text_group

    def construct(self):
        spec = self.SPEC

        equation = require(spec, "equation")
        solution_steps = require(spec, "solution_steps")
        final_solution = require(spec, "final_solution")

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)

        balance, pivot, left_plate, right_plate = self.make_balance()
        self.play(Create(balance), run_time=0.85)

        equation_mob = Text(equation, font_size=34, weight=BOLD, color=YELLOW)
        equation_bg = BackgroundRectangle(
            equation_mob,
            color=BLACK,
            fill_opacity=0.78,
            buff=0.12,
        )
        equation_group = VGroup(equation_bg, equation_mob)
        equation_group.next_to(balance, UP, buff=0.46)
        self.play(FadeIn(equation_group, shift=DOWN * 0.05), run_time=0.6)

        intro_card = self.make_card(
            "Persamaan = seimbang",
            "Tanda sama dengan berarti ruas kiri dan kanan memiliki nilai yang setara.",
            color=BLUE,
        )
        active_card = self.replace_card(None, intro_card)

        left = self.make_plate_text(spec.get("left_expression", ""), left_plate, BLUE)
        right = self.make_plate_text(spec.get("right_expression", ""), right_plate, GREEN)

        self.play(FadeIn(left), FadeIn(right), run_time=0.55)

        current_tilt = 0.0

        def tilt_to(target, run_time=0.35):
            nonlocal current_tilt
            delta = float(target) - float(current_tilt)
            if abs(delta) < 1e-4:
                return
            self.play(
                Rotate(balance, angle=delta, about_point=pivot.get_center()),
                run_time=run_time,
            )
            current_tilt = float(target)

        # Show that this is a dynamic balance, not a static drawing.
        tilt_to(-0.08, run_time=0.28)
        tilt_to(0.05, run_time=0.32)
        tilt_to(0.0, run_time=0.30)

        for step_index, step in enumerate(solution_steps[:4]):
            card = self.make_card(
                step.get("operation", "Operasi"),
                step.get("explanation", ""),
                color=TEAL,
            )
            operation_text = self._clean_voice_text(step.get("operation", "Operasi"))
            explanation_text = self._clean_voice_text(step.get("explanation", ""))
            solution_step_narration = self._clean_voice_text(
                step.get("narration") or step.get("voiceover")
            )
            if not solution_step_narration:
                if operation_text and explanation_text:
                    solution_step_narration = f"{operation_text}. {explanation_text}"
                else:
                    solution_step_narration = operation_text or explanation_text

            active_card = self.replace_card(
                active_card,
                card,
                narration_text=solution_step_narration,
            )

            lead_tilt = -0.06 if step_index % 2 == 0 else 0.06
            tilt_to(lead_tilt, run_time=0.30)

            new_left = self.make_plate_text(step.get("left_result", ""), left_plate, BLUE)
            new_right = self.make_plate_text(step.get("right_result", ""), right_plate, GREEN)
            new_left.move_to(left)
            new_right.move_to(right)

            self.play(
                ReplacementTransform(left, new_left),
                ReplacementTransform(right, new_right),
                run_time=0.6,
            )
            left = new_left
            right = new_right
            tilt_to(0.0, run_time=0.35)

            self.wait(0.45)

        final = Text(final_solution, font_size=40, color=YELLOW, weight=BOLD)
        final.next_to(balance, DOWN, buff=0.52)
        self.play(Write(final), run_time=0.6)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


# ============================================================
# 6. SEQUENCE PATTERN
# ============================================================

class SequencePatternTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_sequence_pattern",
        "node_id": "km_c_matematika_pola_bilangan_perkalian_dan_pembagian",
        "template_id": "manim.sequence_pattern.v1",
        "phase": "C",
        "audience_level": "sd",
        "title": "Pola Bilangan",
        "subtitle": "Pola dapat diteruskan jika aturan perubahannya diketahui.",
        "terms": [2, 4, 6, 8],
        "visual_pattern_type": "growing_dots",
        "rule": "Tambah 2 setiap langkah",
        "table_values": [
            {"n": 1, "value": 2},
            {"n": 2, "value": 4},
            {"n": 3, "value": 6},
            {"n": 4, "value": 8},
        ],
        "target_term": {"n": 5, "value": 10},
        "steps": [
            {
                "title": "Amati suku",
                "body": "Nilai suku bertambah dari 2, ke 4, ke 6, lalu ke 8.",
                "color": BLUE,
            },
            {
                "title": "Cari perubahan",
                "body": "Setiap langkah bertambah 2.",
                "color": TEAL,
            },
            {
                "title": "Lanjutkan pola",
                "body": "Jika ditambah 2 lagi, suku berikutnya adalah 10.",
                "color": GREEN,
            },
        ],
        "summary": "Aturan pola membantu kita memprediksi suku berikutnya.",
        "voiceover_script": "Lihat deret dua, empat, enam, delapan. Setiap langkah bertambah dua.",
    }

    def make_term_card(self, value, label, color=BLUE):
        value = int(max(0, min(value, 36)))

        dots = VGroup()
        for _ in range(value):
            dots.add(Dot(radius=0.045, color=YELLOW))

        if value > 0:
            cols = max(1, min(6, int(math.ceil(math.sqrt(value)))))
            dots.arrange_in_grid(cols=cols, buff=0.065)

        box = RoundedRectangle(
            width=max(0.90, dots.width + 0.35),
            height=max(0.72, dots.height + 0.42),
            corner_radius=0.12,
            color=color,
            stroke_width=1.4,
            fill_color=BLACK,
            fill_opacity=0.20,
        )

        dots.move_to(box.get_center())
        txt = Text(str(label), font_size=15, color=WHITE).next_to(box, DOWN, buff=0.10)

        return VGroup(box, dots, txt)

    def make_table(self, table_values):
        headers = VGroup(
            Text("n", font_size=18, color=YELLOW),
            Text("nilai", font_size=18, color=YELLOW),
        ).arrange(RIGHT, buff=0.45)

        rows = VGroup()
        for item in table_values[:5]:
            row = VGroup(
                Text(str(item.get("n")), font_size=17),
                Text(str(item.get("value")), font_size=17),
            ).arrange(RIGHT, buff=0.45)
            rows.add(row)

        table = VGroup(headers, rows.arrange(DOWN, buff=0.12, aligned_edge=LEFT))
        table.arrange(DOWN, buff=0.18, aligned_edge=LEFT)

        box = RoundedRectangle(
            width=table.width + 0.4,
            height=table.height + 0.35,
            corner_radius=0.12,
            color=GRAY_B,
            fill_color=BLACK,
            fill_opacity=0.35,
        )
        table.move_to(box)
        return VGroup(box, table)

    def construct(self):
        spec = self.SPEC

        terms = require(spec, "terms")
        rule = require(spec, "rule")

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)

        intro_card = self.make_card(
            "Pola bertumbuh",
            "Setiap suku dapat dilihat sebagai gambar atau jumlah yang berubah teratur.",
            color=BLUE,
        )
        active_card = self.replace_card(None, intro_card)

        term_cards = VGroup()
        for i, term in enumerate(terms[:5]):
            term_cards.add(self.make_term_card(term, f"Suku {i + 1}: {term}", color=BLUE))

        term_cards.arrange(RIGHT, buff=0.24)
        term_cards.scale(0.82)
        term_cards.move_to(LEFT * 2.15 + DOWN * 0.25)

        self.play(
            LaggedStart(*[FadeIn(card, shift=UP * 0.08) for card in term_cards], lag_ratio=0.16),
            run_time=1.0,
        )

        rule_mob = Text(rule, font_size=25, color=YELLOW, weight=BOLD)
        rule_mob.next_to(term_cards, DOWN, buff=0.35)
        self.play(Write(rule_mob), run_time=0.55)

        table_values = spec.get("table_values", [])
        table = None
        if table_values:
            table = self.make_table(table_values)
            table.scale(0.86)
            table.next_to(term_cards, RIGHT, buff=0.55)
            self.play(FadeIn(table, shift=LEFT * 0.12), run_time=0.6)

        target = spec.get("target_term")
        if isinstance(target, dict):
            target_mob = Text(
                f"Suku ke-{target.get('n')}: {target.get('value')}",
                font_size=26,
                color=GREEN,
                weight=BOLD,
            )
            target_mob.next_to(rule_mob, DOWN, buff=0.22)
            self.play(FadeIn(target_mob), run_time=0.5)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


# ============================================================
# 7. GEOMETRY AREA VOLUME
# ============================================================

class GeometryAreaVolumeTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_area_rectangle",
        "node_id": "km_b_matematika_keliling_dan_luas_persegi_persegi_panjang",
        "template_id": "manim.geometry_area_volume.v1",
        "phase": "B",
        "audience_level": "sd",
        "title": "Luas Persegi Panjang",
        "subtitle": "Luas dapat dihitung dari panjang dan lebar.",
        "shape_type": "rectangle",
        "dimensions": {"length": 6, "width": 4, "unit": "cm"},
        "transformations": [
            {"type": "fill_unit_squares", "label": "Isi dengan persegi satuan"},
        ],
        "formula_latex": "L = p \\times l",
        "highlight_features": ["panjang", "lebar", "luas"],
        "steps": [
            {
                "title": "Ukur dua sisi",
                "body": "Persegi panjang punya panjang dan lebar.",
                "color": BLUE,
            },
            {
                "title": "Lihat kotak satuan",
                "body": "Luas menunjukkan banyaknya kotak satuan yang menutup daerah.",
                "color": TEAL,
            },
            {
                "title": "Kalikan",
                "body": "Luas diperoleh dari panjang dikali lebar.",
                "color": GREEN,
            },
        ],
        "summary": "Luas persegi panjang adalah panjang dikali lebar.",
        "voiceover_script": "Untuk mencari luas persegi panjang, kita melihat panjang dan lebarnya.",
    }

    def make_rectangle_grid(self, cols, rows):
        grid = VGroup()
        for r in range(rows):
            for c in range(cols):
                sq = Square(
                    side_length=0.34,
                    stroke_color=GRAY_B,
                    stroke_width=1,
                    fill_color=BLUE,
                    fill_opacity=0.10,
                )
                sq.move_to(RIGHT * c * 0.34 + DOWN * r * 0.34)
                grid.add(sq)
        grid.center()
        return grid

    def construct(self):
        spec = self.SPEC

        shape_type = require(spec, "shape_type")
        dimensions = require(spec, "dimensions")

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)

        intro_card = self.make_card(
            "Apa itu luas?",
            "Luas adalah banyaknya daerah yang ditutupi oleh satuan persegi.",
            color=BLUE,
        )
        active_card = self.replace_card(None, intro_card)

        unit = dimensions.get("unit", "")

        if shape_type in ["rectangle", "persegi_panjang"]:
            length = float(dimensions.get("length", 6))
            width = float(dimensions.get("width", 4))

            shape = Rectangle(width=4.2, height=2.5, color=BLUE, fill_opacity=0.14)

            length_label = Text(f"{length:g} {unit}", font_size=21)
            length_label.next_to(shape, DOWN, buff=0.14)

            width_label = Text(f"{width:g} {unit}", font_size=21)
            width_label.next_to(shape, LEFT, buff=0.14)

            visual = VGroup(shape, length_label, width_label)
            visual.move_to(self.visual_center())

            self.play(Create(shape), FadeIn(length_label), FadeIn(width_label), run_time=0.85)

            grid = self.make_rectangle_grid(cols=int(min(length, 12)), rows=int(min(width, 8)))
            grid.set(width=shape.width, height=shape.height)
            grid.move_to(shape)
            self.play(FadeIn(grid), run_time=0.75)

        elif shape_type in ["triangle", "segitiga"]:
            shape = Polygon(
                LEFT * 2 + DOWN,
                RIGHT * 2 + DOWN,
                UP * 1.4,
                color=BLUE,
                fill_opacity=0.16,
            )
            visual = VGroup(shape).move_to(self.visual_center())
            self.play(Create(shape), run_time=0.85)

        elif shape_type in ["circle", "lingkaran"]:
            shape = Circle(radius=1.45, color=BLUE, fill_opacity=0.16)
            visual = VGroup(shape).move_to(self.visual_center())
            self.play(Create(shape), run_time=0.85)

        else:
            shape = Square(side_length=2.4, color=BLUE, fill_opacity=0.16)
            visual = VGroup(shape).move_to(self.visual_center())
            self.play(Create(shape), run_time=0.85)

        formula = spec.get("formula_latex")
        if formula:
            formula_mob = MathTex(formula, font_size=38, color=YELLOW)
            formula_mob.next_to(visual, DOWN, buff=0.45)
            self.play(Write(formula_mob), run_time=0.6)

        features = spec.get("highlight_features", [])
        if features:
            # This used to print "Sorot: panjang, lebar, luas" as one raw comma
            # list above the figure, which read like debug output and crowded the
            # subtitle. A row of pills reads as a deliberate legend.
            pills = VGroup()
            for index, feature in enumerate(features[:3]):
                accent = theme.chip(index)
                label = Text(
                    str(feature),
                    font_size=theme.FS_CAPTION,
                    color=accent,
                    **theme.font_kwargs("medium"),
                )
                pill = RoundedRectangle(
                    width=label.width + 0.42,
                    height=label.height + 0.26,
                    corner_radius=0.14,
                )
                pill.set_fill(color=accent, opacity=0.12)
                pill.set_stroke(color=accent, width=1.4, opacity=0.65)
                label.move_to(pill.get_center())
                pills.add(VGroup(pill, label))
            pills.arrange(RIGHT, buff=0.24)
            pills.move_to(
                np.array([visual.get_center()[0], self.stage_bottom + 0.34, 0.0])
            )
            self.play(
                LaggedStart(
                    *[FadeIn(p, shift=UP * 0.08) for p in pills], lag_ratio=0.12
                ),
                run_time=0.55,
            )

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


# ============================================================
# 8. GRAPH EXPLANATION
# ============================================================

class GraphExplanationTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_graph_quadratic_clean",
        "node_id": "km_e_matematika_fungsi_kuadrat",
        "template_id": "manim.graph_explanation.v1",
        "phase": "E",
        "audience_level": "sma",
        "title": "Grafik Fungsi Kuadrat",
        "subtitle": "Parabola membantu kita melihat perubahan nilai fungsi.",
        "formula_latex": "f(x)=x^2",
        "function": {"type": "quadratic", "params": {"a": 1, "b": 0, "c": 0}},
        "x_range": [-3, 3, 1],
        "y_range": [-1, 9, 1],
        "x_label": "x",
        "y_label": "f(x)",
        "graph_label": "kurva fungsi",
        "moving_label": "titik",
        "x_path": [-2, -1, 0, 1, 2],
        "highlight_x": 1,
        "show_slope": True,
        "slope_text": "Kemiringan lokal menunjukkan seberapa cepat nilai fungsi berubah di sekitar titik itu.",
        "steps": [
            {
                "title": "Baca sumbu",
                "body": "Sumbu horizontal menunjukkan nilai x, sedangkan sumbu vertikal menunjukkan nilai f(x).",
                "color": BLUE,
            },
            {
                "title": "Ikuti titik",
                "body": "Saat x berubah, titik pada grafik ikut berpindah sesuai nilai fungsi.",
                "color": TEAL,
            },
            {
                "title": "Lihat kemiringan",
                "body": "Garis singgung membantu melihat laju perubahan lokal pada grafik.",
                "color": RED,
            },
        ],
        "summary": "Grafik membuat hubungan antara x dan f(x) terlihat lebih jelas.",
        "voiceover_script": "Sekarang kita melihat fungsi kuadrat melalui grafik.",
    }

    def make_axes(self, spec):
        x_range = require(spec, "x_range")
        y_range = require(spec, "y_range")

        if len(x_range) != 3:
            raise ValueError("x_range must be [min, max, step].")

        if len(y_range) != 3:
            raise ValueError("y_range must be [min, max, step].")

        axes = Axes(
            x_range=x_range,
            y_range=y_range,
            x_length=6.75,
            y_length=3.85,
            tips=False,
            axis_config={"include_numbers": True, "font_size": 16},
        )
        axes.move_to(self.visual_center())

        axis_labels = axes.get_axis_labels(
            x_label=Text(spec.get("x_label", "x"), font_size=20),
            y_label=Text(spec.get("y_label", "y"), font_size=20),
        )

        return axes, axis_labels

    def construct(self):
        spec = self.SPEC

        require(spec, "function")
        require(spec, "x_range")
        require(spec, "y_range")

        f = build_function(spec.get("function", {}))

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)

        # Axes first: four things want the space around them — the formula, the
        # live value readout, the curve label and the slope — and each is now
        # anchored to one corner of the axes rather than to the title or to the
        # same edge as its neighbour, which is what put the formula straight
        # through the readout. That anchoring needs the axes to exist.
        axes, axis_labels = self.make_axes(spec)
        self.play(Create(axes), FadeIn(axis_labels), run_time=1.0)

        formula = MathTex(spec.get("formula_latex", "f(x)=x"), font_size=34, color=YELLOW)
        formula.next_to(axes, UP, buff=0.30, aligned_edge=LEFT)
        self.play(Write(formula), run_time=0.75)

        x_range = spec["x_range"]
        graph = axes.plot(f, x_range=[x_range[0], x_range[1]], color=BLUE)

        graph_label = Text(
            clamp_text(
                spec.get(
                    "graph_label",
                    self.tr_key("graph_function_default", spec, fallback="grafik fungsi"),
                ),
                24,
            ),
            font_size=17,
            color=BLUE,
        )
        graph_label.next_to(axes, DOWN, buff=0.26, aligned_edge=LEFT)

        self.play(Create(graph), FadeIn(graph_label), run_time=1.25)

        active_card = self.replace_card(
            None,
            self.make_card(
                "Apa yang dilihat?",
                "Grafik menunjukkan hubungan antara nilai x dan nilai f(x).",
                color=BLUE,
            ),
        )
        self.wait(0.6)

        x_path = spec.get("x_path", [x_range[0], x_range[1]])
        if len(x_path) < 2:
            x_path = [x_range[0], x_range[1]]

        tracker = ValueTracker(float(x_path[0]))

        dot = always_redraw(
            lambda: Dot(
                axes.c2p(tracker.get_value(), f(tracker.get_value())),
                color=YELLOW,
                radius=0.075,
            )
        )

        trace_path = TracedPath(
            dot.get_center,
            stroke_color=YELLOW_D,
            stroke_width=2.5,
            stroke_opacity=0.72,
        )

        dot_label = always_redraw(
            lambda: Text(
                clamp_text(
                    spec.get(
                        "moving_label",
                        self.tr_key("moving_point_default", spec, fallback="titik"),
                    ),
                    18,
                ),
                font_size=15,
                color=YELLOW,
            ).next_to(dot, UP, buff=0.10)
        )

        vertical_line = always_redraw(
            lambda: DashedLine(
                axes.c2p(tracker.get_value(), 0),
                axes.c2p(tracker.get_value(), f(tracker.get_value())),
                stroke_color=YELLOW,
                stroke_opacity=0.52,
                stroke_width=2,
            )
        )

        readout = always_redraw(
            lambda: Text(
                f"x={tracker.get_value():.1f}, f(x)={f(tracker.get_value()):.1f}",
                font_size=16,
                color=YELLOW,
            ).next_to(axes, UP, buff=0.30, aligned_edge=RIGHT)
        )

        self.add(trace_path)
        self.play(FadeIn(dot), FadeIn(dot_label), Create(vertical_line), FadeIn(readout), run_time=0.6)

        active_card = self.replace_card(
            active_card,
            self.make_card(
                "Titik bergerak",
                "Saat x berubah, posisi titik di grafik ikut berubah.",
                color=TEAL,
            ),
        )

        for target_x in x_path[1:]:
            self.play(
                tracker.animate.set_value(float(target_x)),
                run_time=1.0,
                rate_func=smooth,
            )
            self.play(Indicate(dot, color=YELLOW, scale_factor=1.18), run_time=0.25)

        highlight_points = spec.get("highlight_points", [])
        if isinstance(highlight_points, list):
            for item in highlight_points[:2]:
                if not isinstance(item, dict):
                    continue
                x_value = float(item.get("x", 0))
                y_value = f(x_value)
                point_dot = Dot(axes.c2p(x_value, y_value), color=GREEN, radius=0.062)
                # An 18-char hard clamp cut "point we differentiate" mid-word.
                # Wrapping keeps the whole phrase, and a wider buff lifts it off
                # the dashed drop-line it was sitting on.
                point_label = Text(
                    wrap_text(clamp_text(str(item.get("label", f"x={x_value:g}")), 40), 18),
                    font_size=14,
                    color=GREEN,
                    line_spacing=0.8,
                    **theme.font_kwargs("medium"),
                ).next_to(point_dot, UP, buff=0.22)
                self.play(FadeIn(point_dot), FadeIn(point_label), run_time=0.42)

        tangent_group = None

        if bool(spec.get("show_slope", False)):
            highlight_x = float(spec.get("highlight_x", 1))
            slope = numerical_slope(f, highlight_x)

            self.play(tracker.animate.set_value(highlight_x), run_time=0.75, rate_func=smooth)

            tangent_group = axes.get_secant_slope_group(
                x=highlight_x,
                graph=graph,
                dx=0.01,
                secant_line_length=3.5,
                secant_line_color=RED,
            )

            slope_body = spec.get(
                "slope_text",
                f"Kemiringan lokal di x={highlight_x:g} kira-kira {slope:.2f}.",
            )

            active_card = self.replace_card(
                active_card,
                self.make_card("Laju perubahan lokal", slope_body, color=RED),
            )

            self.play(Create(tangent_group), run_time=0.85)
            slope_value = Text(
                f"m \u2248 {slope:.2f}",
                font_size=22,
                color=RED,
                weight=BOLD,
            )
            slope_value.next_to(axes, DOWN, buff=0.26, aligned_edge=RIGHT)
            self.play(FadeIn(slope_value, shift=UP * 0.06), run_time=0.45)
            self.wait(0.7)

        active_card = self.render_step_cards(spec, active_card=active_card)

        self.clean_summary(
            spec,
            active_card=active_card,
            extra_fadeouts=[dot, dot_label, vertical_line],
        )


# ============================================================
# 9. MOTION KINEMATICS
# ============================================================

class MotionKinematicsTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_motion_glb",
        "node_id": "km_e_fisika_gerak_lurus_beraturan",
        "template_id": "manim.motion_kinematics.v1",
        "phase": "E",
        "audience_level": "sma",
        "title": "Gerak Lurus Beraturan",
        "subtitle": "Posisi bertambah sama setiap selang waktu.",
        "scenario": "Gerak lurus beraturan",
        "time_points": [0, 1, 2, 3, 4],
        "position_data": [0, 2, 4, 6, 8],
        "velocity_data": [2, 2, 2, 2, 2],
        "acceleration": 0,
        "graph_type": "position_time",
        "steps": [
            {
                "title": "Kecepatan tetap",
                "body": "Benda menempuh jarak yang sama tiap detik.",
                "color": BLUE,
            },
            {
                "title": "Jejak gerak",
                "body": "Posisi benda bergeser teratur sepanjang lintasan.",
                "color": TEAL,
            },
            {
                "title": "Grafik lurus",
                "body": "Posisi terhadap waktu membentuk garis lurus.",
                "color": GREEN,
            },
        ],
        "summary": "GLB memiliki kecepatan tetap dan percepatan nol.",
        "voiceover_script": "Pada gerak lurus beraturan, posisi bertambah secara teratur setiap waktu.",
    }

    def construct(self):
        spec = self.SPEC

        time_points = require(spec, "time_points")
        position_data = require(spec, "position_data")

        times = [float(x) for x in time_points]
        positions = [float(x) for x in position_data]

        if len(times) != len(positions):
            raise ValueError("time_points and position_data must have same length.")

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)

        intro_card = self.make_card(
            "Gerak terhadap waktu",
            "Kita lihat benda bergerak, lalu hubungkan dengan grafik posisinya.",
            color=BLUE,
        )
        active_card = self.replace_card(None, intro_card)

        track = Line(LEFT * 4.1, RIGHT * 1.0, color=GRAY_B)
        track.move_to(LEFT * 1.55 + UP * 0.70)

        start_label = Text(self.tr_text("awal"), font_size=16, color=GRAY_A).next_to(
            track.get_start(), DOWN, buff=0.12
        )
        end_label = Text(self.tr_text("akhir"), font_size=16, color=GRAY_A).next_to(
            track.get_end(), DOWN, buff=0.12
        )

        car = RoundedRectangle(
            width=0.58,
            height=0.34,
            corner_radius=0.08,
            color=BLUE,
            fill_opacity=0.85,
        )
        car.move_to(track.get_start())

        self.play(Create(track), FadeIn(start_label), FadeIn(end_label), FadeIn(car), run_time=0.7)

        min_pos = min(positions)
        max_pos = max(positions)
        span = max(1e-6, max_pos - min_pos)

        def pos_to_point(p):
            alpha = (p - min_pos) / span
            return interpolate(track.get_start(), track.get_end(), alpha)

        path_points = [pos_to_point(p) for p in positions]
        path = VMobject(color=YELLOW)
        path.set_points_as_corners(path_points)

        active_card = self.replace_card(
            active_card,
            self.make_card("Benda bergerak", "Posisi benda berubah seiring waktu.", color=TEAL),
        )

        self.play(MoveAlongPath(car, path), Create(path), run_time=1.4)

        y_max = max(positions) + 1
        x_step = max(1, (max(times) - min(times)) / 4)

        axes = Axes(
            x_range=[min(times), max(times), x_step],
            y_range=[min(0, min(positions)), y_max, max(1, y_max / 4)],
            x_length=5.1,
            y_length=2.55,
            tips=False,
            axis_config={"include_numbers": True, "font_size": 14},
        )
        axes.move_to(LEFT * 1.55 + DOWN * 1.55)

        graph_points = [axes.c2p(t, p) for t, p in zip(times, positions)]
        graph = VMobject(color=GREEN)
        graph.set_points_as_corners(graph_points)

        graph_label = Text(
            spec.get(
                "scenario",
                self.tr_key("motion_graph_default", spec, fallback="Grafik gerak"),
            ),
            font_size=20,
            color=GREEN,
        )
        graph_label.next_to(axes, UP, buff=0.10)

        active_card = self.replace_card(
            active_card,
            self.make_card("Grafik posisi", "Grafik menunjukkan hubungan antara waktu dan posisi.", color=GREEN),
        )

        self.play(Create(axes), Create(graph), FadeIn(graph_label), run_time=0.95)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


# ============================================================
# 10. FORCE DIAGRAM
# ============================================================

class ForceDiagramTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_force_resultant",
        "node_id": "km_d_ipa_gaya_dan_resultan_gaya",
        "template_id": "manim.force_diagram.v1",
        "phase": "D",
        "audience_level": "smp",
        "title": "Resultan Gaya",
        "subtitle": "Gaya berlawanan saling mengurangi.",
        "object": {"type": "box", "label": "Kotak"},
        "forces": [
            {"label": "F1", "magnitude": 10, "unit": "N", "direction": "right"},
            {"label": "F2", "magnitude": 4, "unit": "N", "direction": "left"},
        ],
        "resultant": {"magnitude": 6, "unit": "N", "direction": "right"},
        "motion_response": "Benda cenderung bergerak ke kanan.",
        "force_scale": 0.25,
        "steps": [
            {
                "title": "Dua gaya",
                "body": "Kotak mendapat gaya ke kanan dan gaya ke kiri.",
                "color": BLUE,
            },
            {
                "title": "Bandingkan besar",
                "body": "Gaya kanan lebih besar daripada gaya kiri.",
                "color": TEAL,
            },
            {
                "title": "Resultan",
                "body": "Selisih gaya menghasilkan resultan 6 N ke kanan.",
                "color": GREEN,
            },
        ],
        "summary": "Arah resultan gaya menentukan kecenderungan gerak benda.",
        "voiceover_script": "Kotak mendapat gaya ke kanan dan ke kiri. Karena gaya kanan lebih besar, resultannya ke kanan.",
    }

    def construct(self):
        spec = self.SPEC

        obj_spec = require(spec, "object")
        forces = require(spec, "forces")
        resultant = require(spec, "resultant")

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)

        intro_card = self.make_card(
            "Gaya sebagai panah",
            "Panjang panah menunjukkan besar gaya, arah panah menunjukkan arah gaya.",
            color=BLUE,
        )
        active_card = self.replace_card(None, intro_card)

        body_shape = RoundedRectangle(
            width=1.45,
            height=0.85,
            corner_radius=0.12,
            color=BLUE,
            fill_opacity=0.62,
        )

        body_label = Text(
            obj_spec.get("label", self.tr_key("object_default", spec, fallback="Benda")),
            font_size=22,
        ).move_to(body_shape)

        body = VGroup(body_shape, body_label)
        body.move_to(self.visual_center())

        self.play(FadeIn(body), run_time=0.55)

        scale = float(spec.get("force_scale", 0.22))
        force_mobs = VGroup()

        for i, force in enumerate(forces[:4]):
            mag = float(force["magnitude"])
            direction = force.get("direction", "right")
            unit = force.get("unit", "N")
            vec = direction_vector(direction)

            length = max(0.55, min(2.35, mag * scale))

            if direction in ["right", "left"]:
                start = body.get_center()
                start += (RIGHT if direction == "right" else LEFT) * 0.78
                start += UP * (0.26 - i * 0.18)
            else:
                start = body.get_center()
                start += (UP if direction == "up" else DOWN) * 0.48
                start += RIGHT * (i * 0.2)

            arrow = Arrow(
                start,
                start + vec * length,
                buff=0,
                color=YELLOW,
                stroke_width=5,
            )

            label = Text(
                f"{force.get('label', 'F')} = {mag:g} {unit}",
                font_size=18,
                color=YELLOW,
            )

            if direction in ["right", "left"]:
                label.next_to(arrow, UP, buff=0.10)
            else:
                label.next_to(arrow, RIGHT, buff=0.10)

            force_mobs.add(VGroup(arrow, label))

        active_card = self.replace_card(
            active_card,
            self.make_card("Gaya-gaya bekerja", "Setiap panah menunjukkan gaya yang bekerja pada benda.", color=TEAL),
        )

        self.play(
            LaggedStart(*[Create(m) for m in force_mobs], lag_ratio=0.16),
            run_time=0.95,
        )

        rmag = float(resultant["magnitude"])
        rdir = resultant.get("direction", "right")
        runit = resultant.get("unit", "N")
        rvec = direction_vector(rdir)

        start = body.get_center() + DOWN * 1.18
        rarrow = Arrow(
            start,
            start + rvec * max(0.70, min(2.50, rmag * scale)),
            buff=0,
            color=GREEN,
            stroke_width=6,
        )

        direction_word = self.tr_key("direction_to", spec, fallback="ke")
        resultant_label = self.tr_key("resultant_label", spec, fallback="Resultan")
        rlabel = Text(
            f"{resultant_label} = {rmag:g} {runit} {direction_word} {rdir}",
            font_size=22,
            color=GREEN,
            weight=BOLD,
        )
        rlabel.next_to(rarrow, DOWN, buff=0.14)

        active_card = self.replace_card(
            active_card,
            self.make_card("Resultan gaya", "Gaya berlawanan dikurangkan untuk mendapatkan resultannya.", color=GREEN),
        )

        self.play(Create(rarrow), FadeIn(rlabel), run_time=0.7)

        response = spec.get("motion_response", "")
        response_mob = None
        if response:
            response_mob = Text(
                clamp_text(response, 65),
                font_size=21,
                color=GRAY_A,
            )
            response_mob.next_to(rlabel, DOWN, buff=0.20)
            self.play(FadeIn(response_mob), run_time=0.4)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


def _clone_spec(base_spec, patch):
    spec = dict(base_spec)
    for key, value in patch.items():
        spec[key] = value
    return spec


# ============================================================
# PHASE 4 EXPANDED CORE TEMPLATES (TOP 30 TRACK)
# ============================================================


# ============================================================
# 11-30. DISTINCT TEMPLATE IMPLEMENTATIONS
# ============================================================
# These 20 templates intentionally do not subclass the original MVP
# visual templates. They keep the same Wicara scene contract, but each
# template owns a visual metaphor that matches its concept.


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _fmt_num(value, digits=2):
    value = _as_float(value, 0)
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def _safe_label(text, max_chars=32):
    return clamp_text("" if text is None else str(text), max_chars)


def _make_table(rows, col_widths=None, font_size=16, header_color=YELLOW):
    """Small deterministic table that avoids Manim Table version differences."""
    if not rows:
        return VGroup()
    col_count = max(len(row) for row in rows)
    col_widths = col_widths or [1.15] * col_count
    row_h = 0.42
    table = VGroup()
    for r, row in enumerate(rows):
        row_group = VGroup()
        for c in range(col_count):
            value = row[c] if c < len(row) else ""
            rect = Rectangle(
                width=col_widths[c],
                height=row_h,
                stroke_width=1,
                stroke_color=GRAY_B,
                fill_color=BLACK,
                fill_opacity=0.30 if r else 0.55,
            )
            label = Text(
                _safe_label(value, 24),
                font_size=font_size,
                color=header_color if r == 0 else WHITE,
                weight=BOLD if r == 0 else NORMAL,
            ).move_to(rect)
            row_group.add(VGroup(rect, label))
        row_group.arrange(RIGHT, buff=0)
        table.add(row_group)
    table.arrange(DOWN, buff=0)
    return table


def _math_or_text(expr, font_size=30, color=YELLOW):
    expr = str(expr or "").strip()
    if not expr:
        return Text("", font_size=font_size)
    try:
        return MathTex(expr, font_size=font_size, color=color)
    except Exception:
        return Text(clamp_text(expr, 72), font_size=min(font_size, 26), color=color)


def _build_axes_from_ranges(x_range, y_range, *, x_length=4.8, y_length=2.8, font_size=13):
    xr = list(x_range or [0, 10, 1])
    yr = list(y_range or [0, 10, 1])
    if len(xr) < 3:
        xr = [xr[0] if xr else 0, xr[1] if len(xr) > 1 else 10, 1]
    if len(yr) < 3:
        yr = [yr[0] if yr else 0, yr[1] if len(yr) > 1 else 10, 1]
    if xr[1] <= xr[0]:
        xr[1] = xr[0] + 1
    if yr[1] <= yr[0]:
        yr[1] = yr[0] + 1
    if xr[2] <= 0:
        xr[2] = max(1, (xr[1] - xr[0]) / 5)
    if yr[2] <= 0:
        yr[2] = max(1, (yr[1] - yr[0]) / 5)
    return Axes(
        x_range=xr,
        y_range=yr,
        x_length=x_length,
        y_length=y_length,
        tips=False,
        axis_config={"include_numbers": True, "font_size": font_size},
    )


class ProbabilityTreeTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_probability_tree_distinct",
        "node_id": "phase4_probability_compound_event",
        "template_id": "manim.probability_tree.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Pohon Peluang Dua Tahap",
        "subtitle": "Peluang gabungan dibaca dari cabang yang dilewati.",
        "root_label": "Mulai",
        "levels": [
            {
                "name": "Koin 1",
                "branches": [
                    {"label": "A", "probability": 0.5},
                    {"label": "G", "probability": 0.5},
                ],
            },
            {
                "name": "Koin 2",
                "branches": [
                    {"label": "A", "probability": 0.5},
                    {"label": "G", "probability": 0.5},
                ],
            },
        ],
        "highlight_path": ["A", "G"],
        "final_probability": 0.25,
        "steps": [
            {"title": "Tahap pertama", "body": "Dari titik mulai, kejadian pertama membagi peluang menjadi beberapa cabang."},
            {"title": "Tahap kedua", "body": "Setiap hasil tahap pertama bercabang lagi untuk kejadian berikutnya."},
            {"title": "Kalikan jalur", "body": "Peluang gabungan diperoleh dengan mengalikan peluang pada cabang jalur itu."},
        ],
        "summary": "Pohon peluang membantu menghitung peluang gabungan dengan membaca dan mengalikan cabang pada satu jalur.",
    }

    def _level_branches(self, spec):
        levels = spec.get("levels") or []
        if len(levels) >= 2:
            return levels[:2]
        return [
            {"name": "Tahap 1", "branches": [{"label": "A", "probability": 0.5}, {"label": "B", "probability": 0.5}]},
            {"name": "Tahap 2", "branches": [{"label": "C", "probability": 0.5}, {"label": "D", "probability": 0.5}]},
        ]

    def construct(self):
        spec = self.SPEC
        levels = self._level_branches(spec)
        first = levels[0].get("branches", [])[:3]
        second = levels[1].get("branches", [])[:3]
        highlight_path = [str(x) for x in spec.get("highlight_path", [])]

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(
            None,
            self.make_card("Struktur cabang", "Setiap cabang mewakili hasil yang mungkin terjadi beserta peluangnya.", color=BLUE),
        )

        root = Dot(radius=0.09, color=YELLOW).move_to(LEFT * 5.05 + DOWN * 0.05)
        root_label = Text(_safe_label(spec.get("root_label", "Mulai"), 18), font_size=17, color=YELLOW).next_to(root, LEFT, buff=0.10)
        tree = VGroup(root, root_label)
        level1_nodes = []
        level2_nodes = []
        edges = VGroup()

        y_positions_1 = [1.35, 0.05, -1.25][: max(1, len(first))]
        for i, branch in enumerate(first):
            node = Dot(radius=0.08, color=TEAL).move_to(LEFT * 3.0 + UP * y_positions_1[i])
            label = Text(_safe_label(branch.get("label", f"B{i+1}"), 12), font_size=16, color=TEAL).next_to(node, RIGHT, buff=0.08)
            line = Line(root.get_center(), node.get_center(), color=GRAY_B)
            prob_label = Text(_fmt_num(branch.get("probability", 0)), font_size=14, color=GRAY_A).move_to(line.point_from_proportion(0.52) + UP * 0.12)
            group = VGroup(node, label)
            level1_nodes.append((branch, node, group))
            edges.add(VGroup(line, prob_label))
            tree.add(group)

        if not first:
            first = [{"label": "A", "probability": 1}]
        if not second:
            second = [{"label": "B", "probability": 1}]

        for i, (b1, node1, _) in enumerate(level1_nodes):
            local_y = [0.45, -0.35, -1.05][: max(1, len(second))]
            for j, branch in enumerate(second):
                y = node1.get_y() + (local_y[j] if len(second) > 1 else 0)
                node = Dot(radius=0.07, color=GREEN).move_to(LEFT * 0.85 + UP * y)
                combined = f"{b1.get('label','')}→{branch.get('label','')}"
                label = Text(_safe_label(combined, 14), font_size=14, color=GREEN).next_to(node, RIGHT, buff=0.06)
                line = Line(node1.get_center(), node.get_center(), color=GRAY_B)
                prob_label = Text(_fmt_num(branch.get("probability", 0)), font_size=13, color=GRAY_A).move_to(line.point_from_proportion(0.52) + UP * 0.10)
                level2_nodes.append((b1, branch, node, VGroup(node, label)))
                edges.add(VGroup(line, prob_label))
                tree.add(VGroup(node, label))

        level_names = VGroup(
            Text(_safe_label(levels[0].get("name", "Tahap 1"), 18), font_size=16, color=GRAY_A).move_to(LEFT * 3.0 + UP * 2.05),
            Text(_safe_label(levels[1].get("name", "Tahap 2"), 18), font_size=16, color=GRAY_A).move_to(LEFT * 0.85 + UP * 2.05),
        )
        tree.add(edges, level_names)
        self.play(Create(edges), FadeIn(VGroup(root, root_label)), LaggedStart(*[FadeIn(g[2]) for g in level1_nodes], lag_ratio=0.12), run_time=1.0)

        active_card = self.replace_card(active_card, self.make_card("Semua kemungkinan", "Cabang tahap kedua menunjukkan pasangan hasil dari dua kejadian.", color=TEAL))
        self.play(LaggedStart(*[FadeIn(item[3]) for item in level2_nodes], lag_ratio=0.08), FadeIn(level_names), run_time=1.0)

        selected = []
        if len(highlight_path) >= 2:
            for b1, b2, node, node_group in level2_nodes:
                if str(b1.get("label")) == highlight_path[0] and str(b2.get("label")) == highlight_path[1]:
                    selected.append((b1, b2, node, node_group))
                    break
        if selected:
            b1, b2, node, node_group = selected[0]
            path1 = Line(root.get_center(), np.array([-3.0, node_group.get_y(), 0.0]), color=YELLOW, stroke_width=6)
            # Use actual first-level node closest to selected branch.
            for cand_b, cand_node, _ in level1_nodes:
                if str(cand_b.get("label")) == str(b1.get("label")):
                    path1 = Line(root.get_center(), cand_node.get_center(), color=YELLOW, stroke_width=6)
                    path2 = Line(cand_node.get_center(), node.get_center(), color=YELLOW, stroke_width=6)
                    break
            else:
                path2 = Line(root.get_center(), node.get_center(), color=YELLOW, stroke_width=6)
            result = spec.get("final_probability")
            if result is None:
                result = _as_float(b1.get("probability", 1), 1) * _as_float(b2.get("probability", 1), 1)
            formula = Text(
                f"P({highlight_path[0]} lalu {highlight_path[1]}) = {_fmt_num(b1.get('probability'))} × {_fmt_num(b2.get('probability'))} = {_fmt_num(result)}",
                font_size=20,
                color=YELLOW,
            ).move_to(LEFT * 2.7 + DOWN * 2.45)
            active_card = self.replace_card(active_card, self.make_card("Jalur terpilih", "Peluang gabungan pada satu jalur dihitung dengan perkalian.", color=YELLOW))
            self.play(Create(path1), Create(path2), FadeIn(formula), run_time=0.9)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ScientificInquiryDataTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_scientific_inquiry_distinct",
        "node_id": "phase4_science_inquiry_data",
        "template_id": "manim.scientific_inquiry_data.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Inkuiri Ilmiah dari Data",
        "subtitle": "Pertanyaan, variabel, data, lalu kesimpulan.",
        "research_question": "Apakah durasi cahaya memengaruhi tinggi tanaman?",
        "hypothesis": "Semakin lama terkena cahaya, tanaman tumbuh lebih tinggi.",
        "variables": {
            "independent": "Durasi cahaya",
            "dependent": "Tinggi tanaman",
            "controlled": ["Jenis tanaman", "Air", "Media tanam"],
        },
        "observations": [
            {"x": 2, "y": 4.0, "label": "2 jam"},
            {"x": 4, "y": 6.1, "label": "4 jam"},
            {"x": 6, "y": 8.2, "label": "6 jam"},
            {"x": 8, "y": 9.0, "label": "8 jam"},
        ],
        "x_label": "cahaya",
        "y_label": "tinggi",
        "conclusion": "Data mendukung hipotesis karena tinggi tanaman cenderung naik saat durasi cahaya bertambah.",
        "steps": [
            {"title": "Mulai dari pertanyaan", "body": "Pertanyaan menentukan data apa yang perlu dikumpulkan."},
            {"title": "Pisahkan variabel", "body": "Variabel bebas diubah, variabel terikat diamati, variabel kontrol dijaga tetap."},
            {"title": "Tarik kesimpulan", "body": "Kesimpulan harus kembali ke pola data, bukan hanya tebakan."},
        ],
        "summary": "Inkuiri ilmiah yang baik menghubungkan pertanyaan, variabel, data, dan kesimpulan secara konsisten.",
    }

    def construct(self):
        spec = self.SPEC
        observations = spec.get("observations", []) or []
        if not observations:
            observations = [{"x": 1, "y": 1}, {"x": 2, "y": 2}]

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(
            None,
            self.make_card("Pertanyaan penelitian", spec.get("research_question", "Pertanyaan menentukan arah eksperimen."), color=BLUE),
        )

        question_box = RoundedRectangle(width=5.4, height=0.8, corner_radius=0.15, color=BLUE, fill_opacity=0.30)
        question_text = Text(safe_text(spec.get("research_question", "Pertanyaan"), 80, 42), font_size=17, line_spacing=0.82).move_to(question_box)
        question_group = VGroup(question_box, question_text).move_to(LEFT * 2.65 + UP * 1.65)

        variables = spec.get("variables", {}) or {}
        variable_rows = [
            ["Variabel", "Isi"],
            ["Bebas", variables.get("independent", "-")],
            ["Terikat", variables.get("dependent", "-")],
            ["Kontrol", ", ".join(variables.get("controlled", [])[:3]) or "-"],
        ]
        variable_table = _make_table(variable_rows, col_widths=[1.05, 2.9], font_size=13)
        variable_table.next_to(question_group, DOWN, buff=0.28)

        self.play(FadeIn(question_group), run_time=0.55)
        active_card = self.replace_card(active_card, self.make_card("Variabel eksperimen", "Pisahkan hal yang diubah, diukur, dan dijaga tetap.", color=TEAL))
        self.play(FadeIn(variable_table), run_time=0.65)

        xs = [_as_float(o.get("x"), i + 1) for i, o in enumerate(observations)]
        ys = [_as_float(o.get("y"), 0) for o in observations]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(0, min(ys)), max(ys) + max(1, (max(ys) - min(ys)) * 0.15)
        axes = _build_axes_from_ranges([xmin, xmax, max(1, (xmax - xmin) / 4)], [ymin, ymax, max(1, ymax / 4)], x_length=4.8, y_length=2.45)
        axes.move_to(LEFT * 2.55 + DOWN * 1.30)
        dots = VGroup(*[Dot(axes.c2p(x, y), radius=0.055, color=YELLOW) for x, y in zip(xs, ys)])
        if len(xs) > 1:
            line = VMobject(color=GREEN, stroke_width=3)
            line.set_points_as_corners([axes.c2p(x, y) for x, y in zip(xs, ys)])
        else:
            line = VGroup()
        graph_label = Text("data pengamatan", font_size=17, color=GREEN).next_to(axes, UP, buff=0.08)

        active_card = self.replace_card(active_card, self.make_card("Data diamati", "Titik data membantu melihat pola, bukan hanya membaca angka satu per satu.", color=GREEN))
        self.play(Create(axes), FadeIn(graph_label), LaggedStart(*[FadeIn(dot) for dot in dots], lag_ratio=0.12), run_time=0.9)
        if len(xs) > 1:
            self.play(Create(line), run_time=0.55)

        conclusion = Text(safe_text(spec.get("conclusion", spec.get("summary", "")), 105, 48), font_size=16, color=YELLOW, line_spacing=0.82)
        conclusion.next_to(axes, DOWN, buff=0.18)
        active_card = self.replace_card(active_card, self.make_card("Kesimpulan berbasis data", "Kesimpulan harus sesuai dengan pola yang terlihat pada hasil pengamatan.", color=YELLOW))
        self.play(FadeIn(conclusion), run_time=0.55)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class FinancialGrowthTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_financial_growth_distinct",
        "node_id": "phase5_financial_compound_growth",
        "template_id": "manim.financial_growth.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Pertumbuhan Nilai Majemuk",
        "subtitle": "Kenaikan persentase dihitung dari nilai periode sebelumnya.",
        "initial_amount": 100,
        "rate_percent": 20,
        "periods": 5,
        "values": [100, 120, 144, 173, 207],
        "currency": "ribu",
        "formula_latex": "A_n=A_0(1+r)^n",
        "steps": [
            {"title": "Nilai awal", "body": "Mulai dari modal atau nilai dasar pada periode pertama."},
            {"title": "Persentase tumbuh", "body": "Setiap periode, kenaikan dihitung dari nilai terbaru, bukan nilai awal saja."},
            {"title": "Efek majemuk", "body": "Selisih antar batang makin besar karena basis perhitungannya ikut membesar."},
        ],
        "summary": "Pertumbuhan majemuk membuat nilai bertambah berdasarkan persentase dari nilai periode sebelumnya.",
    }

    def construct(self):
        spec = self.SPEC
        values = spec.get("values") or []
        if not values:
            amount = _as_float(spec.get("initial_amount", 100), 100)
            rate = _as_float(spec.get("rate_percent", 10), 10) / 100
            periods = max(2, _as_int(spec.get("periods", 5), 5))
            values = [round(amount * ((1 + rate) ** i), 2) for i in range(periods)]

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Nilai bertumbuh", "Batang menunjukkan nilai dari periode ke periode.", color=BLUE))

        max_v = max(values) if values else 1
        base_y = -2.10
        left_x = -5.0
        bar_w = min(0.48, 3.85 / max(1, len(values)))
        bars = VGroup()
        labels = VGroup()
        for i, value in enumerate(values[:8]):
            h = max(0.20, 2.55 * _as_float(value, 0) / max_v)
            bar = Rectangle(width=bar_w, height=h, stroke_color=BLUE, fill_color=BLUE, fill_opacity=0.78)
            bar.move_to(RIGHT * (left_x + i * (bar_w + 0.20)) + UP * (base_y + h / 2))
            val_label = Text(_fmt_num(value), font_size=13, color=YELLOW).next_to(bar, UP, buff=0.07)
            period_label = Text(f"P{i}", font_size=13, color=GRAY_A).next_to(bar, DOWN, buff=0.07)
            bars.add(bar)
            labels.add(val_label, period_label)
        axis = Line(LEFT * 5.35 + UP * base_y, LEFT * 0.85 + UP * base_y, color=GRAY_B)
        formula = _math_or_text(spec.get("formula_latex", "A_n=A_0(1+r)^n"), font_size=29, color=YELLOW).move_to(LEFT * 2.95 + UP * 1.65)
        rate_text = Text(f"r = {_fmt_num(spec.get('rate_percent', 0))}% per periode", font_size=19, color=GRAY_A).next_to(formula, DOWN, buff=0.10)

        self.play(Create(axis), FadeIn(formula), FadeIn(rate_text), run_time=0.65)
        active_card = self.replace_card(active_card, self.make_card("Rumus majemuk", "Faktor 1+r dikalikan berulang untuk setiap periode.", color=TEAL))
        self.play(LaggedStart(*[GrowFromEdge(bar, DOWN) for bar in bars], lag_ratio=0.10), FadeIn(labels), run_time=1.0)

        arrows = VGroup()
        for i in range(min(len(bars) - 1, 5)):
            arrow = CurvedArrow(bars[i].get_top() + UP * 0.08, bars[i + 1].get_top() + UP * 0.08, angle=-TAU / 8, color=GREEN, stroke_width=3)
            arrows.add(arrow)
        if arrows:
            active_card = self.replace_card(active_card, self.make_card("Efek berantai", "Kenaikan berikutnya memakai nilai yang sudah bertambah.", color=GREEN))
            self.play(LaggedStart(*[Create(a) for a in arrows], lag_ratio=0.12), run_time=0.8)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class DataRepresentationTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_data_representation_distinct",
        "node_id": "phase4_statistics_data_display",
        "template_id": "manim.data_representation.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Representasi Data Kategori",
        "subtitle": "Diagram batang membuat perbandingan kategori lebih cepat terlihat.",
        "categories": [
            {"label": "A", "value": 12},
            {"label": "B", "value": 19},
            {"label": "C", "value": 7},
            {"label": "D", "value": 15},
        ],
        "unit": "siswa",
        "highlight_category": "B",
        "steps": [
            {"title": "Ubah tabel jadi visual", "body": "Setiap kategori diberi batang dengan tinggi sesuai nilainya."},
            {"title": "Bandingkan tinggi", "body": "Batang yang lebih tinggi menunjukkan nilai yang lebih besar."},
            {"title": "Ambil informasi penting", "body": "Kita dapat cepat melihat kategori terbesar dan terkecil."},
        ],
        "summary": "Diagram batang cocok untuk membandingkan nilai antar kategori secara visual.",
    }

    def construct(self):
        spec = self.SPEC
        categories = spec.get("categories") or []
        if not categories:
            categories = [{"label": "A", "value": 1}, {"label": "B", "value": 2}]
        categories = categories[:7]
        values = [_as_float(c.get("value"), 0) for c in categories]
        max_v = max(values) if values else 1
        highlight = str(spec.get("highlight_category", ""))

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Dari data ke diagram", "Angka dalam tabel diterjemahkan menjadi tinggi batang.", color=BLUE))

        rows = [["Kategori", "Nilai"]] + [[c.get("label", ""), _fmt_num(c.get("value", 0))] for c in categories]
        table = _make_table(rows, col_widths=[1.12, 1.0], font_size=13).move_to(LEFT * 4.55 + UP * 0.15)
        self.play(FadeIn(table), run_time=0.65)

        base_y = -2.10
        bars = VGroup()
        labels = VGroup()
        for i, cat in enumerate(categories):
            value = _as_float(cat.get("value"), 0)
            h = max(0.16, 2.55 * value / max_v)
            label_text = str(cat.get("label", f"K{i+1}"))
            color = YELLOW if label_text == highlight else TEAL
            bar = Rectangle(width=0.46, height=h, stroke_color=color, fill_color=color, fill_opacity=0.76)
            bar.move_to(LEFT * 2.9 + RIGHT * (i * 0.62) + UP * (base_y + h / 2))
            val = Text(_fmt_num(value), font_size=13, color=color).next_to(bar, UP, buff=0.06)
            lab = Text(_safe_label(label_text, 8), font_size=13, color=GRAY_A).next_to(bar, DOWN, buff=0.06)
            bars.add(bar)
            labels.add(val, lab)
        axis = Line(LEFT * 3.25 + UP * base_y, RIGHT * 1.35 + UP * base_y, color=GRAY_B)
        unit = Text(spec.get("unit", "jumlah"), font_size=16, color=GRAY_A).next_to(axis, DOWN, buff=0.32)
        chart_title = Text("diagram batang", font_size=19, color=TEAL).move_to(LEFT * 0.9 + UP * 1.55)

        active_card = self.replace_card(active_card, self.make_card("Diagram batang", "Panjang batang sebanding dengan nilai kategorinya.", color=TEAL))
        self.play(Create(axis), FadeIn(chart_title), FadeIn(unit), LaggedStart(*[GrowFromEdge(b, DOWN) for b in bars], lag_ratio=0.10), FadeIn(labels), run_time=1.0)

        if highlight:
            selected = [bars[i] for i, cat in enumerate(categories) if str(cat.get("label")) == highlight]
            if selected:
                surround = SurroundingRectangle(selected[0], color=YELLOW, buff=0.06)
                active_card = self.replace_card(active_card, self.make_card("Sorot kategori", f"Kategori {highlight} menjadi fokus karena nilainya ingin dibandingkan.", color=YELLOW))
                self.play(Create(surround), run_time=0.45)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class StatisticsCenterSpreadTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_statistics_center_spread_distinct",
        "node_id": "phase4_statistics_center_spread",
        "template_id": "manim.statistics_center_spread.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Pemusatan dan Penyebaran Data",
        "subtitle": "Nilai tengah dan rentang memberi dua informasi berbeda.",
        "data_values": [4, 5, 5, 6, 7, 8, 10],
        "center_metrics": {"mean": 6.4, "median": 6},
        "spread_metrics": {"range": 6, "min": 4, "max": 10},
        "steps": [
            {"title": "Susun data", "body": "Dot plot menempatkan setiap nilai pada garis bilangan."},
            {"title": "Cari pusat", "body": "Mean dan median menjelaskan nilai yang mewakili tengah data."},
            {"title": "Lihat sebaran", "body": "Range menunjukkan jarak dari nilai terkecil sampai terbesar."},
        ],
        "summary": "Pusat data menjelaskan nilai representatif, sedangkan penyebaran menjelaskan variasi data.",
    }

    def construct(self):
        spec = self.SPEC
        data = sorted([_as_float(x, 0) for x in (spec.get("data_values") or [1, 2, 3])])
        min_v = min(data)
        max_v = max(data)
        if max_v <= min_v:
            max_v = min_v + 1

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Dot plot", "Setiap titik mewakili satu data pada garis nilai.", color=BLUE))

        line = NumberLine(x_range=[min_v, max_v, max(1, (max_v - min_v) / 6)], length=5.6, include_numbers=True, font_size=14)
        line.move_to(LEFT * 2.65 + DOWN * 0.95)
        stacks = {}
        dots = VGroup()
        for value in data:
            key = round(value, 6)
            stacks[key] = stacks.get(key, 0) + 1
            dot = Dot(line.n2p(value) + UP * (0.18 * stacks[key]), radius=0.055, color=YELLOW)
            dots.add(dot)
        self.play(Create(line), LaggedStart(*[FadeIn(d) for d in dots], lag_ratio=0.08), run_time=0.9)

        center = spec.get("center_metrics", {}) or {}
        mean = _as_float(center.get("mean"), sum(data) / len(data))
        median = _as_float(center.get("median"), data[len(data) // 2])
        mean_line = Line(line.n2p(mean) + DOWN * 0.25, line.n2p(mean) + UP * 1.0, color=GREEN, stroke_width=4)
        median_line = DashedLine(line.n2p(median) + DOWN * 0.25, line.n2p(median) + UP * 1.0, color=TEAL, stroke_width=3)
        mean_label = Text(f"mean {_fmt_num(mean)}", font_size=15, color=GREEN).next_to(mean_line, UP, buff=0.07)
        median_label = Text(f"median {_fmt_num(median)}", font_size=15, color=TEAL).next_to(median_line, DOWN, buff=0.10)
        active_card = self.replace_card(active_card, self.make_card("Ukuran pusat", "Mean memakai semua nilai, median melihat posisi tengah setelah data diurutkan.", color=GREEN))
        self.play(Create(mean_line), FadeIn(mean_label), Create(median_line), FadeIn(median_label), run_time=0.75)

        spread = spec.get("spread_metrics", {}) or {}
        smin = _as_float(spread.get("min"), min(data))
        smax = _as_float(spread.get("max"), max(data))
        range_val = _as_float(spread.get("range"), smax - smin)
        brace = BraceBetweenPoints(line.n2p(smin) + DOWN * 0.50, line.n2p(smax) + DOWN * 0.50, DOWN, color=PURPLE)
        range_label = Text(f"range = {_fmt_num(range_val)}", font_size=17, color=PURPLE).next_to(brace, DOWN, buff=0.08)
        metric_rows = [["Pusat", "Nilai"], ["Mean", _fmt_num(mean)], ["Median", _fmt_num(median)], ["Range", _fmt_num(range_val)]]
        table = _make_table(metric_rows, col_widths=[1.25, 1.05], font_size=14).move_to(LEFT * 4.75 + UP * 1.18)
        active_card = self.replace_card(active_card, self.make_card("Ukuran sebaran", "Range menunjukkan lebar persebaran dari minimum ke maksimum.", color=PURPLE))
        self.play(Create(brace), FadeIn(range_label), FadeIn(table), run_time=0.75)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class GeometryTransformTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_geometry_transform_distinct",
        "node_id": "phase4_congruence_similarity_transform",
        "template_id": "manim.geometry_transform.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Transformasi Geometri",
        "subtitle": "Translasi, rotasi, dan refleksi mengubah posisi atau orientasi bangun.",
        "shape_points": [[-1.0, -0.6], [0.9, -0.6], [-0.2, 0.9]],
        "transformations": [
            {"type": "translate", "vector": [2.0, 0.7], "label": "Translasi"},
            {"type": "rotate", "angle_degrees": 90, "label": "Rotasi 90°"},
            {"type": "reflect", "axis": "y", "label": "Refleksi sumbu-y"},
        ],
        "invariant_text": "Ukuran dan bentuk tetap, posisi/orientasi berubah.",
        "steps": [
            {"title": "Bangun awal", "body": "Amati titik-titik dan sisi bangun sebelum berubah."},
            {"title": "Terapkan transformasi", "body": "Setiap transformasi punya aturan posisi yang jelas."},
            {"title": "Cek sifat tetap", "body": "Pada transformasi kaku, bentuk dan ukuran tidak berubah."},
        ],
        "summary": "Transformasi geometri memindahkan atau mengubah orientasi bangun dengan aturan yang dapat dilacak.",
    }

    def _polygon_from_points(self, points, color=BLUE):
        pts = [LEFT * 2.9 + RIGHT * _as_float(p[0], 0) + UP * _as_float(p[1], 0) for p in points]
        return Polygon(*pts, color=color, fill_color=color, fill_opacity=0.28, stroke_width=3)

    def construct(self):
        spec = self.SPEC
        points = spec.get("shape_points") or [[-1, -0.6], [1, -0.6], [0, 0.8]]
        transformations = spec.get("transformations") or []

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Bangun awal", "Kita mulai dari satu bangun dengan titik dan sisi yang jelas.", color=BLUE))

        original = self._polygon_from_points(points, color=BLUE)
        original_label = Text("awal", font_size=17, color=BLUE).next_to(original, DOWN, buff=0.10)
        self.play(FadeIn(original), FadeIn(original_label), run_time=0.65)

        current = original.copy()
        labels = VGroup(original_label)
        colors = [TEAL, GREEN, YELLOW]
        for i, tr in enumerate(transformations[:3]):
            new_shape = current.copy().set_color(colors[i]).set_fill(colors[i], opacity=0.24)
            typ = str(tr.get("type", "translate")).lower()
            if typ == "translate":
                v = tr.get("vector", [1.3, 0.4])
                new_shape.shift(RIGHT * _as_float(v[0], 1.3) + UP * _as_float(v[1], 0.4))
            elif typ == "rotate":
                new_shape.rotate(_as_float(tr.get("angle_degrees", 90), 90) * DEGREES, about_point=current.get_center())
                new_shape.shift(RIGHT * 2.0 + DOWN * 0.25)
            elif typ == "reflect":
                axis = str(tr.get("axis", "y")).lower()
                new_shape = current.copy().set_color(colors[i]).set_fill(colors[i], opacity=0.24)
                if axis == "x":
                    new_shape.apply_function(lambda p: np.array([p[0], -p[1] - 0.45, p[2]]))
                else:
                    new_shape.apply_function(lambda p: np.array([-p[0] - 1.05, p[1], p[2]]))
            else:
                new_shape.shift(RIGHT * 1.3)
            label = Text(_safe_label(tr.get("label", typ), 18), font_size=16, color=colors[i]).next_to(new_shape, DOWN, buff=0.10)
            arrow = Arrow(current.get_right() + RIGHT * 0.10, new_shape.get_left() + LEFT * 0.10, buff=0.05, color=GRAY_B, stroke_width=3)
            active_card = self.replace_card(active_card, self.make_card(_safe_label(tr.get("label", typ), 24), "Bangun baru dibuat dari aturan transformasi, bukan digambar sembarang.", color=colors[i]))
            self.play(Create(arrow), TransformFromCopy(current, new_shape), FadeIn(label), run_time=0.85)
            current = new_shape
            labels.add(label, arrow)

        invariant = Text(safe_text(spec.get("invariant_text", "Bentuk dan ukuran tetap."), 80, 42), font_size=18, color=YELLOW, line_spacing=0.82).move_to(LEFT * 2.7 + DOWN * 2.35)
        active_card = self.replace_card(active_card, self.make_card("Sifat yang tetap", "Bandingkan sisi dan bentuk: transformasi kaku tidak mengubah ukuran.", color=YELLOW))
        self.play(FadeIn(invariant), run_time=0.55)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ExponentialGrowthTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_exponential_growth_distinct",
        "node_id": "phase5_exponential_growth_model",
        "template_id": "manim.exponential_growth.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Pertumbuhan Eksponensial",
        "subtitle": "Nilai bertambah dengan faktor kali tetap.",
        "initial": 1,
        "growth_factor": 2,
        "periods": 5,
        "values": [1, 2, 4, 8, 16],
        "formula_latex": "a_n=a_0\\cdot r^n",
        "steps": [
            {"title": "Faktor tetap", "body": "Setiap periode dikalikan faktor yang sama."},
            {"title": "Pertumbuhan makin cepat", "body": "Tambahan absolut makin besar walaupun faktornya tetap."},
            {"title": "Model prediksi", "body": "Rumus eksponensial membantu memperkirakan periode berikutnya."},
        ],
        "summary": "Pertumbuhan eksponensial terjadi ketika nilai berulang kali dikalikan faktor tetap.",
    }

    def construct(self):
        spec = self.SPEC
        values = spec.get("values") or []
        if not values:
            initial = _as_float(spec.get("initial", 1), 1)
            factor = _as_float(spec.get("growth_factor", 2), 2)
            periods = max(2, _as_int(spec.get("periods", 5), 5))
            values = [initial * (factor ** i) for i in range(periods)]
        values = values[:6]

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Duplikasi berulang", "Pada tiap periode, setiap unit menghasilkan beberapa unit baru.", color=BLUE))

        levels = VGroup()
        max_v = max(values)
        for i, value in enumerate(values):
            count = min(32, max(1, int(round(_as_float(value, 1)))))
            dots = VGroup()
            cols = min(8, max(1, math.ceil(math.sqrt(count))))
            for j in range(count):
                dot = Dot(radius=0.035, color=YELLOW)
                dot.move_to(RIGHT * ((j % cols) * 0.14) + DOWN * ((j // cols) * 0.14))
                dots.add(dot)
            dots.center()
            dots.move_to(LEFT * 5.0 + RIGHT * (i * 0.82) + UP * (0.65 - min(0.5, count / 60)))
            label = Text(_fmt_num(value), font_size=14, color=YELLOW).next_to(dots, DOWN, buff=0.08)
            period = Text(f"n={i}", font_size=12, color=GRAY_A).next_to(label, DOWN, buff=0.04)
            levels.add(VGroup(dots, label, period))
        arrows = VGroup(*[Arrow(levels[i].get_right(), levels[i+1].get_left(), buff=0.05, color=GRAY_B, stroke_width=3) for i in range(len(levels)-1)])
        formula = _math_or_text(spec.get("formula_latex", "a_n=a_0 r^n"), font_size=30, color=GREEN).move_to(LEFT * 2.75 + DOWN * 1.55)
        factor_text = Text(f"faktor kali = {_fmt_num(spec.get('growth_factor', 2))}", font_size=18, color=GREEN).next_to(formula, DOWN, buff=0.10)

        self.play(LaggedStart(*[FadeIn(g) for g in levels], lag_ratio=0.12), run_time=0.95)
        active_card = self.replace_card(active_card, self.make_card("Faktor kali", "Panah menunjukkan perpindahan dari satu periode ke periode berikutnya.", color=TEAL))
        self.play(LaggedStart(*[Create(a) for a in arrows], lag_ratio=0.10), FadeIn(formula), FadeIn(factor_text), run_time=0.8)

        max_h = 2.0
        bars = VGroup()
        for i, value in enumerate(values):
            h = max(0.12, max_h * _as_float(value, 0) / max_v)
            bar = Rectangle(width=0.32, height=h, color=PURPLE, fill_opacity=0.65).move_to(LEFT * 4.55 + RIGHT * (i * 0.48) + DOWN * (2.2 - h / 2))
            bars.add(bar)
        active_card = self.replace_card(active_card, self.make_card("Kurva makin curam", "Jika dibuat grafik, kenaikan absolut terlihat makin besar.", color=PURPLE))
        self.play(LaggedStart(*[GrowFromEdge(b, DOWN) for b in bars], lag_ratio=0.08), run_time=0.75)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class FunctionMappingTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_function_mapping_distinct",
        "node_id": "phase4_function_mapping_rule",
        "template_id": "manim.function_mapping.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Fungsi sebagai Mesin Pemetaan",
        "subtitle": "Setiap input diproses oleh aturan dan menghasilkan tepat satu output.",
        "rule_text": "kalikan 2 lalu tambah 1",
        "formula_latex": "f(x)=2x+1",
        "pairs": [{"input": -1, "output": -1}, {"input": 0, "output": 1}, {"input": 1, "output": 3}, {"input": 2, "output": 5}],
        "steps": [
            {"title": "Masukkan input", "body": "Nilai x masuk ke mesin fungsi satu per satu."},
            {"title": "Gunakan aturan", "body": "Aturan yang sama diterapkan pada semua input."},
            {"title": "Hasil unik", "body": "Untuk setiap input, fungsi memberi tepat satu output."},
        ],
        "summary": "Fungsi adalah aturan yang memasangkan setiap input dengan tepat satu output.",
    }

    def construct(self):
        spec = self.SPEC
        pairs = spec.get("pairs") or []
        if not pairs:
            pairs = [{"input": 0, "output": 0}]
        pairs = pairs[:5]

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Input ke output", "Fungsi bisa dipahami seperti mesin yang menerapkan aturan tertentu.", color=BLUE))

        input_nodes = VGroup()
        output_nodes = VGroup()
        for i, pair in enumerate(pairs):
            y = 1.35 - i * 0.58
            in_circle = Circle(radius=0.22, color=BLUE, fill_opacity=0.35).move_to(LEFT * 5.0 + UP * y)
            in_label = Text(_fmt_num(pair.get("input", 0)), font_size=15, color=BLUE).move_to(in_circle)
            out_circle = Circle(radius=0.22, color=GREEN, fill_opacity=0.35).move_to(LEFT * 0.55 + UP * y)
            out_label = Text(_fmt_num(pair.get("output", 0)), font_size=15, color=GREEN).move_to(out_circle)
            input_nodes.add(VGroup(in_circle, in_label))
            output_nodes.add(VGroup(out_circle, out_label))

        machine = RoundedRectangle(width=1.65, height=1.15, corner_radius=0.18, color=YELLOW, fill_opacity=0.22).move_to(LEFT * 2.75 + UP * 0.25)
        machine_label = Text(safe_text(spec.get("rule_text", "aturan"), 36, 16), font_size=15, color=YELLOW, line_spacing=0.82).move_to(machine)
        formula = _math_or_text(spec.get("formula_latex", "f(x)"), font_size=29, color=YELLOW).next_to(machine, DOWN, buff=0.20)
        arrows = VGroup()
        for i in range(len(pairs)):
            arrows.add(Arrow(input_nodes[i].get_right(), machine.get_left(), buff=0.05, color=GRAY_B, stroke_width=2.6))
            arrows.add(Arrow(machine.get_right(), output_nodes[i].get_left(), buff=0.05, color=GRAY_B, stroke_width=2.6))

        self.play(FadeIn(input_nodes), run_time=0.55)
        active_card = self.replace_card(active_card, self.make_card("Aturan fungsi", "Aturan yang sama digunakan untuk semua nilai input.", color=YELLOW))
        self.play(FadeIn(machine), FadeIn(machine_label), FadeIn(formula), LaggedStart(*[Create(a) for a in arrows], lag_ratio=0.05), run_time=0.9)
        active_card = self.replace_card(active_card, self.make_card("Output tunggal", "Setiap input di kiri memiliki satu pasangan output di kanan.", color=GREEN))
        self.play(FadeIn(output_nodes), run_time=0.55)

        rows = [["x", "f(x)"]] + [[_fmt_num(p.get("input", 0)), _fmt_num(p.get("output", 0))] for p in pairs]
        table = _make_table(rows, col_widths=[0.8, 0.9], font_size=14).move_to(LEFT * 4.85 + DOWN * 2.05)
        self.play(FadeIn(table), run_time=0.55)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class GeometryMeasurementTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_geometry_measurement_distinct",
        "node_id": "phase4_geometry_measurement_area_perimeter",
        "template_id": "manim.geometry_measurement.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Mengukur Luas dan Keliling",
        "subtitle": "Dimensi sisi menentukan perhitungan luas dan keliling.",
        "shape_type": "rectangle",
        "dimensions": {"length": 8, "width": 5, "unit": "cm"},
        "formula_latex": "L=p\\times l,\\quad K=2(p+l)",
        "results": {"area": 40, "perimeter": 26},
        "steps": [
            {"title": "Ukur sisi", "body": "Panjang dan lebar menjadi data awal perhitungan."},
            {"title": "Isi satuan persegi", "body": "Luas berarti banyaknya persegi satuan yang menutup daerah."},
            {"title": "Jumlah sisi luar", "body": "Keliling menghitung panjang seluruh batas luar bangun."},
        ],
        "summary": "Luas mengukur daerah tertutup, sedangkan keliling mengukur batas luarnya.",
    }

    def construct(self):
        spec = self.SPEC
        dims = spec.get("dimensions", {}) or {}
        length = _as_float(dims.get("length", 8), 8)
        width = _as_float(dims.get("width", 5), 5)
        unit = dims.get("unit", "satuan")
        area = spec.get("results", {}).get("area", length * width)
        perimeter = spec.get("results", {}).get("perimeter", 2 * (length + width))

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Bangun terukur", "Kita ukur sisi utama sebelum menghitung besaran geometri.", color=BLUE))

        scale = min(0.46, 3.2 / max(1, length), 2.0 / max(1, width))
        rect = Rectangle(width=length * scale, height=width * scale, color=BLUE, fill_opacity=0.18, stroke_width=3).move_to(LEFT * 2.85 + DOWN * 0.1)
        length_arrow = DoubleArrow(rect.get_bottom() + LEFT * rect.width / 2, rect.get_bottom() + RIGHT * rect.width / 2, buff=0, color=YELLOW).next_to(rect, DOWN, buff=0.15)
        length_label = Text(f"p = {_fmt_num(length)} {unit}", font_size=16, color=YELLOW).next_to(length_arrow, DOWN, buff=0.05)
        width_arrow = DoubleArrow(rect.get_left() + DOWN * rect.height / 2, rect.get_left() + UP * rect.height / 2, buff=0, color=GREEN).next_to(rect, LEFT, buff=0.15)
        width_label = Text(f"l = {_fmt_num(width)} {unit}", font_size=16, color=GREEN).next_to(width_arrow, LEFT, buff=0.05).rotate(PI/2)
        self.play(Create(rect), FadeIn(length_arrow), FadeIn(length_label), FadeIn(width_arrow), FadeIn(width_label), run_time=0.85)

        grid = VGroup()
        cols = min(int(round(length)), 10)
        rows = min(int(round(width)), 7)
        cell_w = rect.width / max(1, cols)
        cell_h = rect.height / max(1, rows)
        for c in range(cols):
            for r in range(rows):
                sq = Rectangle(width=cell_w, height=cell_h, stroke_width=0.6, stroke_color=GRAY_B, fill_opacity=0)
                sq.move_to(rect.get_left() + RIGHT * (cell_w * (c + 0.5)) + DOWN * rect.height / 2 + UP * (cell_h * (r + 0.5)))
                grid.add(sq)
        active_card = self.replace_card(active_card, self.make_card("Luas", "Daerah di dalam bangun dapat ditutup oleh satuan persegi.", color=TEAL))
        self.play(Create(grid), run_time=0.65)

        formula = _math_or_text(spec.get("formula_latex", "L=p\\times l"), font_size=30, color=YELLOW).move_to(LEFT * 2.85 + DOWN * 2.15)
        result_text = Text(f"L = {_fmt_num(area)} {unit}²     K = {_fmt_num(perimeter)} {unit}", font_size=18, color=WHITE).next_to(formula, DOWN, buff=0.13)
        active_card = self.replace_card(active_card, self.make_card("Keliling", "Batas luar dihitung dengan menjumlahkan seluruh sisi.", color=GREEN))
        self.play(FadeIn(formula), FadeIn(result_text), Circumscribe(rect, color=GREEN), run_time=0.85)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class GeometryTheoremTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_geometry_theorem_distinct",
        "node_id": "phase4_angle_sum_triangle",
        "template_id": "manim.geometry_theorem.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Jumlah Sudut Segitiga",
        "subtitle": "Tiga sudut dalam segitiga membentuk garis lurus 180°.",
        "angles": [{"label": "A", "value": 50}, {"label": "B", "value": 60}, {"label": "C", "value": 70}],
        "formula_latex": "\\angle A+\\angle B+\\angle C=180^\\circ",
        "steps": [
            {"title": "Tiga sudut", "body": "Setiap titik segitiga memiliki satu sudut dalam."},
            {"title": "Pindahkan sudut", "body": "Jika ketiganya disusun berdampingan, bentuknya garis lurus."},
            {"title": "Total 180°", "body": "Garis lurus membuktikan jumlah sudut segitiga adalah 180 derajat."},
        ],
        "summary": "Jumlah sudut dalam segitiga selalu 180 derajat.",
    }

    def construct(self):
        spec = self.SPEC
        angles = spec.get("angles") or [{"label": "A", "value": 60}, {"label": "B", "value": 60}, {"label": "C", "value": 60}]
        colors = [BLUE, TEAL, YELLOW]

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Tiga sudut dalam", "Segitiga punya tiga sudut yang totalnya selalu sama.", color=BLUE))

        A = LEFT * 4.4 + DOWN * 1.05
        B = LEFT * 1.1 + DOWN * 1.05
        C = LEFT * 2.75 + UP * 1.30
        tri = Polygon(A, B, C, color=WHITE, fill_color=BLUE, fill_opacity=0.12, stroke_width=3)
        labels = VGroup(
            Text("A", font_size=16, color=BLUE).next_to(A, DOWN, buff=0.10),
            Text("B", font_size=16, color=TEAL).next_to(B, DOWN, buff=0.10),
            Text("C", font_size=16, color=YELLOW).next_to(C, UP, buff=0.10),
        )
        arcs = VGroup(
            Arc(radius=0.36, start_angle=0, angle=52 * DEGREES, color=BLUE).move_arc_center_to(A),
            Arc(radius=0.36, start_angle=128 * DEGREES, angle=52 * DEGREES, color=TEAL).move_arc_center_to(B),
            Arc(radius=0.36, start_angle=230 * DEGREES, angle=78 * DEGREES, color=YELLOW).move_arc_center_to(C),
        )
        angle_labels = VGroup()
        for i, angle in enumerate(angles[:3]):
            value = _fmt_num(angle.get("value", 60))
            angle_labels.add(Text(f"{angle.get('label', chr(65+i))}={value}°", font_size=15, color=colors[i]).move_to(LEFT * 4.55 + RIGHT * i * 1.2 + UP * 1.95))
        self.play(Create(tri), FadeIn(labels), Create(arcs), FadeIn(angle_labels), run_time=0.9)

        line = Line(LEFT * 4.7 + DOWN * 2.20, LEFT * 0.85 + DOWN * 2.20, color=GRAY_B, stroke_width=4)
        pieces = VGroup()
        x0 = -4.35
        for i, angle in enumerate(angles[:3]):
            piece = Sector(outer_radius=0.42, angle=max(20, _as_float(angle.get("value", 60), 60)) * DEGREES, color=colors[i], fill_opacity=0.45, stroke_color=colors[i])
            piece.move_to(RIGHT * (x0 + i * 1.05) + DOWN * 2.20)
            pieces.add(piece)
        formula = _math_or_text(spec.get("formula_latex", "A+B+C=180^\\circ"), font_size=30, color=YELLOW).move_to(LEFT * 2.75 + UP * 2.35)
        active_card = self.replace_card(active_card, self.make_card("Susun menjadi garis lurus", "Ketika sudut-sudut disusun berdampingan, totalnya menjadi 180°.", color=YELLOW))
        self.play(Create(line), TransformFromCopy(arcs, pieces), FadeIn(formula), run_time=0.85)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class HeatEnergyMachineTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_heat_energy_machine_distinct",
        "node_id": "phase5_heat_transfer_model",
        "template_id": "manim.heat_energy_machine.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Kalor dan Perubahan Suhu",
        "subtitle": "Energi panas yang masuk menaikkan suhu benda.",
        "system": {"object_label": "Air", "mass_label": "m", "initial_temp": 25, "final_temp": 60, "unit": "°C"},
        "heat_flow": [{"label": "Q masuk", "amount": "mcΔT"}],
        "formula_latex": "Q=m c \\Delta T",
        "timeline": [{"time": 0, "temp": 25}, {"time": 5, "temp": 42}, {"time": 10, "temp": 60}],
        "steps": [
            {"title": "Energi masuk", "body": "Kalor mengalir dari sumber panas ke benda."},
            {"title": "Suhu naik", "body": "Partikel bergerak lebih cepat sehingga suhu meningkat."},
            {"title": "Hubungkan rumus", "body": "Perubahan suhu terkait dengan massa dan kalor jenis."},
        ],
        "summary": "Kalor yang diterima sistem dapat mengubah suhu sesuai hubungan Q = mcΔT.",
    }

    def construct(self):
        spec = self.SPEC
        system = spec.get("system", {}) or {}
        initial = _as_float(system.get("initial_temp", 25), 25)
        final = _as_float(system.get("final_temp", 60), 60)
        unit = system.get("unit", "°C")

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Sistem panas", "Kita amati benda yang menerima energi panas dari sumber.", color=BLUE))

        flame = VGroup()
        for i, color in enumerate([RED, ORANGE, YELLOW]):
            flame.add(Triangle(color=color, fill_color=color, fill_opacity=0.75).scale(0.42 - i*0.08).move_to(LEFT * 4.55 + DOWN * (0.10 - i*0.10)))
        beaker = RoundedRectangle(width=1.25, height=1.55, corner_radius=0.12, color=BLUE, fill_opacity=0.12).move_to(LEFT * 2.65 + DOWN * 0.1)
        liquid = Rectangle(width=1.10, height=0.80, color=TEAL, fill_color=TEAL, fill_opacity=0.45, stroke_opacity=0).align_to(beaker, DOWN).shift(UP * 0.08)
        obj_label = Text(system.get("object_label", "Benda"), font_size=18, color=TEAL).next_to(beaker, UP, buff=0.10)
        heat_arrow = Arrow(flame.get_right() + RIGHT * 0.15, beaker.get_left() + LEFT * 0.10, buff=0.05, color=YELLOW, stroke_width=5)
        heat_label = Text(spec.get("heat_flow", [{}])[0].get("label", "Q"), font_size=18, color=YELLOW).next_to(heat_arrow, UP, buff=0.08)
        thermometer = RoundedRectangle(width=0.18, height=1.75, corner_radius=0.08, color=WHITE).next_to(beaker, RIGHT, buff=0.35)
        bulb = Circle(radius=0.18, color=RED, fill_color=RED, fill_opacity=0.85).next_to(thermometer, DOWN, buff=-0.04)
        level = Rectangle(width=0.10, height=0.45, color=RED, fill_color=RED, fill_opacity=0.85).align_to(thermometer, DOWN).shift(UP * 0.10)
        temp_label = Text(f"{_fmt_num(initial)}{unit}", font_size=16, color=GRAY_A).next_to(thermometer, RIGHT, buff=0.10)
        system_group = VGroup(flame, beaker, liquid, obj_label, heat_arrow, heat_label, thermometer, bulb, level, temp_label)
        self.play(FadeIn(flame), FadeIn(beaker), FadeIn(liquid), FadeIn(obj_label), run_time=0.65)
        active_card = self.replace_card(active_card, self.make_card("Kalor mengalir", "Panah menunjukkan energi panas masuk ke sistem.", color=YELLOW))
        self.play(Create(heat_arrow), FadeIn(heat_label), FadeIn(thermometer), FadeIn(bulb), FadeIn(level), FadeIn(temp_label), run_time=0.75)

        new_level = Rectangle(width=0.10, height=1.15, color=RED, fill_color=RED, fill_opacity=0.85).align_to(thermometer, DOWN).shift(UP * 0.10)
        new_temp_label = Text(f"{_fmt_num(final)}{unit}", font_size=16, color=RED).next_to(thermometer, RIGHT, buff=0.10)
        formula = _math_or_text(spec.get("formula_latex", "Q=mc\\Delta T"), font_size=32, color=YELLOW).move_to(LEFT * 2.65 + DOWN * 2.05)
        active_card = self.replace_card(active_card, self.make_card("Suhu naik", "Saat energi bertambah, pembacaan termometer meningkat.", color=RED))
        self.play(Transform(level, new_level), Transform(temp_label, new_temp_label), FadeIn(formula), run_time=0.85)

        timeline = spec.get("timeline") or []
        rows = [["t", "T"]] + [[_fmt_num(x.get("time", 0)), f"{_fmt_num(x.get('temp', 0))}{unit}"] for x in timeline[:4]]
        table = _make_table(rows, col_widths=[0.75, 1.0], font_size=13).move_to(LEFT * 4.85 + UP * 1.35)
        active_card = self.replace_card(active_card, self.make_card("Data suhu", "Perubahan suhu dapat dicatat sebagai data waktu terhadap temperatur.", color=GREEN))
        self.play(FadeIn(table), run_time=0.55)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class WaveOpticsTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_wave_optics_distinct",
        "node_id": "phase5_wave_optics_sinusoidal_model",
        "template_id": "manim.wave_optics.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Gelombang Sinus",
        "subtitle": "Amplitudo, panjang gelombang, dan fase dapat dibaca dari bentuk gelombang.",
        "amplitude": 1.2,
        "wavelength": 3.0,
        "phase_shift": 0.0,
        "formula_latex": "y=A\\sin(kx+\\phi)",
        "steps": [
            {"title": "Baca amplitudo", "body": "Amplitudo adalah simpangan maksimum dari garis setimbang."},
            {"title": "Baca panjang gelombang", "body": "Jarak puncak ke puncak berikutnya adalah satu panjang gelombang."},
            {"title": "Fase bergeser", "body": "Perubahan fase menggeser bentuk gelombang ke kiri atau kanan."},
        ],
        "summary": "Gelombang sinus dapat dijelaskan lewat amplitudo, panjang gelombang, dan fase.",
    }

    def construct(self):
        spec = self.SPEC
        A = _as_float(spec.get("amplitude", 1.2), 1.2)
        lam = max(0.5, _as_float(spec.get("wavelength", 3.0), 3.0))
        phase = _as_float(spec.get("phase_shift", 0), 0)

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Bentuk periodik", "Gelombang berulang secara teratur di sepanjang sumbu posisi.", color=BLUE))

        axes = Axes(x_range=[-1, 7, 1], y_range=[-2, 2, 1], x_length=5.8, y_length=2.8, tips=False, axis_config={"include_numbers": False})
        axes.move_to(LEFT * 2.75 + DOWN * 0.30)
        equilibrium = DashedLine(axes.c2p(-1, 0), axes.c2p(7, 0), color=GRAY_B)
        k = TAU / lam
        graph = axes.plot(lambda x: A * math.sin(k * x + phase), x_range=[-1, 7], color=YELLOW, stroke_width=4)
        formula = _math_or_text(spec.get("formula_latex", "y=A\\sin(kx+\\phi)"), font_size=30, color=YELLOW).move_to(LEFT * 2.75 + UP * 1.85)
        self.play(Create(axes), Create(equilibrium), Create(graph), FadeIn(formula), run_time=1.0)

        amp_arrow = DoubleArrow(axes.c2p(0, 0), axes.c2p(0, A), buff=0, color=GREEN)
        amp_label = Text(f"A = {_fmt_num(A)}", font_size=16, color=GREEN).next_to(amp_arrow, LEFT, buff=0.08)
        active_card = self.replace_card(active_card, self.make_card("Amplitudo", "Amplitudo mengukur simpangan maksimum dari garis setimbang.", color=GREEN))
        self.play(Create(amp_arrow), FadeIn(amp_label), run_time=0.55)

        wave_arrow = DoubleArrow(axes.c2p(0.25 * lam, A + 0.35), axes.c2p(1.25 * lam, A + 0.35), buff=0, color=TEAL)
        wave_label = Text(f"λ = {_fmt_num(lam)}", font_size=16, color=TEAL).next_to(wave_arrow, UP, buff=0.07)
        active_card = self.replace_card(active_card, self.make_card("Panjang gelombang", "Jarak antara dua puncak berurutan disebut panjang gelombang.", color=TEAL))
        self.play(Create(wave_arrow), FadeIn(wave_label), run_time=0.55)

        shifted = axes.plot(lambda x: A * math.sin(k * x + phase + PI / 4), x_range=[-1, 7], color=PURPLE, stroke_width=3)
        phase_label = Text("fase bergeser", font_size=16, color=PURPLE).next_to(shifted, DOWN, buff=0.12)
        active_card = self.replace_card(active_card, self.make_card("Fase", "Gelombang dengan fase berbeda tampak bergeser, tetapi bentuk periodiknya tetap.", color=PURPLE))
        self.play(TransformFromCopy(graph, shifted), FadeIn(phase_label), run_time=0.75)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class StoichiometryBoardTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_stoichiometry_board_distinct",
        "node_id": "phase5_chemistry_stoichiometry_mole_ratio",
        "template_id": "manim.stoichiometry_board.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Stoikiometri dari Koefisien Reaksi",
        "subtitle": "Koefisien persamaan seimbang menjadi rasio mol antar zat.",
        "balanced_equation": "N₂ + 3H₂ → 2NH₃",
        "species": [{"formula": "N₂", "coef": 1}, {"formula": "H₂", "coef": 3}, {"formula": "NH₃", "coef": 2}],
        "given": {"species": "N₂", "amount": 2, "unit": "mol"},
        "target": {"species": "NH₃", "amount": 4, "unit": "mol"},
        "steps": [
            {"title": "Baca koefisien", "body": "Koefisien menyatakan perbandingan mol dalam reaksi seimbang."},
            {"title": "Buat rasio", "body": "Gunakan rasio zat target terhadap zat yang diketahui."},
            {"title": "Hitung target", "body": "Mol target diperoleh dari mol diketahui dikali rasio koefisien."},
        ],
        "summary": "Stoikiometri memakai koefisien reaksi seimbang untuk mengonversi jumlah mol antar zat.",
    }

    def construct(self):
        spec = self.SPEC
        species = spec.get("species") or []
        given = spec.get("given", {}) or {}
        target = spec.get("target", {}) or {}

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Persamaan seimbang", "Koefisien di depan rumus kimia adalah kunci rasio mol.", color=BLUE))

        equation = Text(spec.get("balanced_equation", spec.get("final_solution", "N₂ + 3H₂ → 2NH₃")), font_size=30, color=YELLOW, weight=BOLD)
        equation.move_to(LEFT * 2.75 + UP * 1.75)
        self.play(Write(equation), run_time=0.75)

        rows = [["Zat", "Koef", "Makna"]]
        for item in species[:5]:
            rows.append([item.get("formula", "?"), _fmt_num(item.get("coef", 1)), f"{_fmt_num(item.get('coef', 1))} mol"])
        ratio_table = _make_table(rows, col_widths=[0.95, 0.75, 1.2], font_size=14).move_to(LEFT * 4.35 + DOWN * 0.05)
        active_card = self.replace_card(active_card, self.make_card("Tabel rasio", "Setiap koefisien dibaca sebagai bagian dari perbandingan mol.", color=TEAL))
        self.play(FadeIn(ratio_table), run_time=0.65)

        given_text = f"Diketahui: {_fmt_num(given.get('amount', 1))} {given.get('unit', 'mol')} {given.get('species', '?')}"
        target_text = f"Target: {target.get('species', '?')}"
        given_box = self.make_card("Diketahui", given_text, color=BLUE, width=3.1, body_width=24).move_to(LEFT * 1.35 + UP * 0.25)
        target_box = self.make_card("Ditanya", target_text, color=GREEN, width=3.1, body_width=24).move_to(LEFT * 1.35 + DOWN * 1.05)
        arrow = Arrow(given_box.get_bottom(), target_box.get_top(), buff=0.10, color=YELLOW)
        self.play(FadeIn(given_box), FadeIn(target_box), Create(arrow), run_time=0.65)

        result = f"{_fmt_num(given.get('amount', 1))} × rasio = {_fmt_num(target.get('amount', 0))} {target.get('unit', 'mol')} {target.get('species', '?')}"
        result_text = Text(safe_text(result, 80, 44), font_size=18, color=YELLOW).move_to(LEFT * 2.55 + DOWN * 2.45)
        active_card = self.replace_card(active_card, self.make_card("Konversi mol", "Kalikan jumlah diketahui dengan rasio koefisien target terhadap koefisien awal.", color=YELLOW))
        self.play(FadeIn(result_text), run_time=0.55)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ElementaryNumberLinePlaceValueTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_elementary_place_value_distinct",
        "node_id": "phase2_place_value_number_line",
        "template_id": "manim.elementary_number_line_place_value.v1",
        "phase": "B",
        "audience_level": "sd",
        "language": "id",
        "title": "Nilai Tempat di Garis Bilangan",
        "subtitle": "Puluhan dan satuan membantu membaca posisi angka.",
        "number": 47,
        "place_values": [{"place": "Puluhan", "digit": 4, "value": 40}, {"place": "Satuan", "digit": 7, "value": 7}],
        "number_range": {"min": 0, "max": 100, "step": 10},
        "steps": [
            {"title": "Pisah nilai tempat", "body": "Angka 47 terdiri dari 4 puluhan dan 7 satuan."},
            {"title": "Bangun nilainya", "body": "Empat puluhan bernilai 40, lalu ditambah 7 satuan."},
            {"title": "Letakkan di garis", "body": "Angka 47 berada setelah 40 dan sebelum 50."},
        ],
        "summary": "Nilai tempat membantu membangun angka dan menempatkannya pada garis bilangan.",
    }

    def construct(self):
        spec = self.SPEC
        number = _as_int(spec.get("number", 47), 47)
        place_values = spec.get("place_values") or [{"place": "Puluhan", "digit": number // 10, "value": (number // 10) * 10}, {"place": "Satuan", "digit": number % 10, "value": number % 10}]
        nr = spec.get("number_range", {}) or {}

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Nilai tempat", "Setiap digit memiliki nilai berbeda tergantung posisinya.", color=BLUE))

        rows = [["Tempat", "Digit", "Nilai"]] + [[p.get("place", ""), _fmt_num(p.get("digit", 0)), _fmt_num(p.get("value", 0))] for p in place_values]
        table = _make_table(rows, col_widths=[1.3, 0.75, 0.95], font_size=14).move_to(LEFT * 4.15 + UP * 1.05)
        number_text = Text(str(number), font_size=52, color=YELLOW, weight=BOLD).move_to(LEFT * 1.45 + UP * 1.10)
        expanded = Text(" + ".join([_fmt_num(p.get("value", 0)) for p in place_values]), font_size=25, color=GREEN).next_to(number_text, DOWN, buff=0.18)
        self.play(FadeIn(number_text), FadeIn(table), run_time=0.65)
        active_card = self.replace_card(active_card, self.make_card("Bentuk panjang", "Nilai tiap digit dijumlahkan untuk membentuk angka lengkap.", color=GREEN))
        self.play(FadeIn(expanded), run_time=0.5)

        min_v = _as_float(nr.get("min", 0), 0)
        max_v = _as_float(nr.get("max", 100), 100)
        step = _as_float(nr.get("step", 10), 10)
        line = NumberLine(x_range=[min_v, max_v, step], length=5.8, include_numbers=True, font_size=14).move_to(LEFT * 2.75 + DOWN * 1.35)
        dot = Dot(line.n2p(number), radius=0.08, color=YELLOW)
        dot_label = Text(str(number), font_size=19, color=YELLOW).next_to(dot, UP, buff=0.10)
        ten_floor = (number // 10) * 10
        interval = Line(line.n2p(ten_floor), line.n2p(ten_floor + 10), color=GREEN, stroke_width=6)
        active_card = self.replace_card(active_card, self.make_card("Posisi angka", f"{number} berada di antara {ten_floor} dan {ten_floor + 10}.", color=YELLOW))
        self.play(Create(line), Create(interval), FadeIn(dot), FadeIn(dot_label), run_time=0.85)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class QuadraticModelTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_quadratic_model_distinct",
        "node_id": "phase5_quadratic_model_vertex_roots",
        "template_id": "manim.quadratic_model.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Model Fungsi Kuadrat",
        "subtitle": "Parabola memperlihatkan titik puncak, akar, dan arah bukaan.",
        "function": {"type": "quadratic", "params": {"a": 1, "b": -2, "c": -3}},
        "x_range": [-3, 5, 1],
        "y_range": [-5, 10, 1],
        "formula_latex": "f(x)=x^2-2x-3",
        "vertex": {"x": 1, "y": -4},
        "roots": [-1, 3],
        "steps": [
            {"title": "Arah bukaan", "body": "Tanda koefisien a menentukan parabola terbuka ke atas atau ke bawah."},
            {"title": "Titik puncak", "body": "Puncak menunjukkan nilai minimum atau maksimum."},
            {"title": "Akar fungsi", "body": "Akar adalah titik potong grafik dengan sumbu x."},
        ],
        "summary": "Grafik kuadrat membantu membaca puncak, akar, dan perilaku fungsi secara visual.",
    }

    def construct(self):
        spec = self.SPEC
        f = build_function(spec.get("function", {"type": "quadratic", "params": {}}))
        xr = spec.get("x_range", [-3, 5, 1])
        yr = spec.get("y_range", [-5, 10, 1])

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Parabola", "Fungsi kuadrat membentuk kurva parabola yang simetris terhadap sumbu tertentu.", color=BLUE))

        axes = _build_axes_from_ranges(xr, yr, x_length=5.6, y_length=3.2)
        axes.move_to(LEFT * 2.75 + DOWN * 0.35)
        graph = axes.plot(f, x_range=[xr[0], xr[1]], color=YELLOW, stroke_width=4)
        formula = _math_or_text(spec.get("formula_latex", "f(x)=ax^2+bx+c"), font_size=30, color=YELLOW).move_to(LEFT * 2.75 + UP * 2.15)
        self.play(Create(axes), Create(graph), FadeIn(formula), run_time=1.0)

        vertex = spec.get("vertex", {}) or {}
        vx = _as_float(vertex.get("x", 0), 0)
        vy = _as_float(vertex.get("y", f(vx)), f(vx))
        vdot = Dot(axes.c2p(vx, vy), color=GREEN, radius=0.07)
        vlabel = Text(f"puncak ({_fmt_num(vx)}, {_fmt_num(vy)})", font_size=15, color=GREEN).next_to(vdot, DOWN, buff=0.10)
        sym_axis = DashedLine(axes.c2p(vx, yr[0]), axes.c2p(vx, yr[1]), color=GREEN)
        active_card = self.replace_card(active_card, self.make_card("Titik puncak", "Puncak adalah titik ekstrem parabola dan menjadi pusat simetri.", color=GREEN))
        self.play(Create(sym_axis), FadeIn(vdot), FadeIn(vlabel), run_time=0.7)

        root_mobs = VGroup()
        for r in (spec.get("roots") or [])[:3]:
            root = _as_float(r, 0)
            dot = Dot(axes.c2p(root, 0), color=TEAL, radius=0.06)
            label = Text(f"x={_fmt_num(root)}", font_size=14, color=TEAL).next_to(dot, UP, buff=0.08)
            root_mobs.add(VGroup(dot, label))
        if root_mobs:
            active_card = self.replace_card(active_card, self.make_card("Akar fungsi", "Akar adalah posisi saat nilai fungsi sama dengan nol.", color=TEAL))
            self.play(FadeIn(root_mobs), run_time=0.6)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ScatterAssociationTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_scatter_association_distinct",
        "node_id": "phase4_statistics_scatter_association",
        "template_id": "manim.scatter_association.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Asosiasi pada Diagram Pencar",
        "subtitle": "Sebaran titik menunjukkan kecenderungan hubungan dua variabel.",
        "points": [[1, 2.2], [2, 3.1], [3, 4.1], [4, 4.7], [5, 6.0], [6, 6.7], [7, 8.0]],
        "trend_line": {"m": 0.95, "b": 1.1},
        "x_range": [0, 8, 1],
        "y_range": [0, 9, 1],
        "association": "positif",
        "steps": [
            {"title": "Plot pasangan data", "body": "Setiap titik menyimpan satu pasangan nilai x dan y."},
            {"title": "Lihat arah sebaran", "body": "Jika titik naik dari kiri ke kanan, asosiasinya positif."},
            {"title": "Bukan sebab-akibat", "body": "Asosiasi menunjukkan pola hubungan, bukan bukti penyebab langsung."},
        ],
        "summary": "Diagram pencar membantu membaca arah dan kekuatan asosiasi antar dua variabel.",
    }

    def construct(self):
        spec = self.SPEC
        points = spec.get("points") or [[0, 0], [1, 1]]
        xr = spec.get("x_range", [0, 8, 1])
        yr = spec.get("y_range", [0, 9, 1])

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Pasangan data", "Satu titik menunjukkan satu pengamatan dengan dua variabel.", color=BLUE))

        axes = _build_axes_from_ranges(xr, yr, x_length=5.4, y_length=3.15)
        axes.move_to(LEFT * 2.75 + DOWN * 0.35)
        dots = VGroup(*[Dot(axes.c2p(_as_float(p[0], 0), _as_float(p[1], 0)), color=YELLOW, radius=0.055) for p in points[:30]])
        self.play(Create(axes), LaggedStart(*[FadeIn(dot) for dot in dots], lag_ratio=0.04), run_time=0.95)

        trend = spec.get("trend_line", {}) or {}
        m = _as_float(trend.get("m", 1), 1)
        b = _as_float(trend.get("b", 0), 0)
        line = axes.plot(lambda x: m * x + b, x_range=[xr[0], xr[1]], color=GREEN, stroke_width=4)
        label = Text(f"asosiasi {spec.get('association', 'positif')}", font_size=18, color=GREEN).next_to(line, UP, buff=0.10)
        active_card = self.replace_card(active_card, self.make_card("Garis kecenderungan", "Garis bantu merangkum arah umum dari sebaran titik.", color=GREEN))
        self.play(Create(line), FadeIn(label), run_time=0.65)

        cluster = SurroundingRectangle(dots, color=TEAL, buff=0.15)
        active_card = self.replace_card(active_card, self.make_card("Kekuatan pola", "Semakin rapat titik di sekitar garis, pola asosiasinya semakin jelas.", color=TEAL))
        self.play(Create(cluster), run_time=0.50)

        warning = Text("asosiasi ≠ sebab-akibat", font_size=20, color=YELLOW, weight=BOLD).move_to(LEFT * 2.75 + DOWN * 2.45)
        self.play(FadeIn(warning), run_time=0.45)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ElectricityMagnetismTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_electricity_magnetism_distinct",
        "node_id": "phase5_physics_electric_magnetic_field",
        "template_id": "manim.electricity_magnetism.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Medan Listrik dan Magnet",
        "subtitle": "Muatan bergerak dipengaruhi medan listrik dan medan magnet.",
        "charge": {"label": "q+", "sign": "+"},
        "electric_field": {"direction": "right", "label": "E"},
        "magnetic_field": {"direction": "out", "label": "B"},
        "trajectory_label": "lintasan membelok",
        "steps": [
            {"title": "Medan listrik", "body": "Medan listrik memberi gaya searah medan untuk muatan positif."},
            {"title": "Medan magnet", "body": "Medan magnet memengaruhi muatan yang bergerak."},
            {"title": "Lintasan berubah", "body": "Gabungan gaya dapat mengubah arah gerak partikel."},
        ],
        "summary": "Muatan dalam medan listrik dan magnet dapat mengalami gaya yang mengubah geraknya.",
    }

    def construct(self):
        spec = self.SPEC
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Medan listrik", "Panah sejajar menggambarkan arah medan listrik di suatu daerah.", color=BLUE))

        field_arrows = VGroup()
        for y in [-1.2, -0.45, 0.3, 1.05]:
            arrow = Arrow(LEFT * 5.25 + UP * y, LEFT * 1.2 + UP * y, buff=0, color=BLUE, stroke_width=3)
            field_arrows.add(arrow)
        e_label = Text(spec.get("electric_field", {}).get("label", "E"), font_size=24, color=BLUE).move_to(LEFT * 5.0 + UP * 1.65)
        self.play(LaggedStart(*[Create(a) for a in field_arrows], lag_ratio=0.08), FadeIn(e_label), run_time=0.85)

        charge = Circle(radius=0.20, color=YELLOW, fill_color=YELLOW, fill_opacity=0.85).move_to(LEFT * 4.85 + DOWN * 1.70)
        charge_label = Text(spec.get("charge", {}).get("label", "q"), font_size=15, color=BLACK).move_to(charge)
        particle = VGroup(charge, charge_label)
        path = VMobject(color=YELLOW)
        path.set_points_smoothly([LEFT * 4.85 + DOWN * 1.70, LEFT * 3.70 + DOWN * 0.55, LEFT * 2.25 + UP * 0.65, LEFT * 0.95 + UP * 1.20])
        path.set_stroke(width=4)
        active_card = self.replace_card(active_card, self.make_card("Muatan bergerak", "Partikel bermuatan bergerak melewati daerah bermedan.", color=YELLOW))
        self.play(FadeIn(particle), run_time=0.25)
        self.play(MoveAlongPath(particle, path), Create(path), run_time=1.05)

        b_symbols = VGroup()
        for x in [-4.6, -3.7, -2.8, -1.9, -1.0]:
            for y in [-1.25, -0.25, 0.75]:
                circle = Circle(radius=0.10, color=PURPLE)
                dot = Dot(radius=0.025, color=PURPLE).move_to(circle)
                b_symbols.add(VGroup(circle, dot).move_to(RIGHT * x + UP * y))
        b_label = Text(spec.get("magnetic_field", {}).get("label", "B keluar bidang"), font_size=18, color=PURPLE).move_to(LEFT * 1.20 + DOWN * 1.85)
        active_card = self.replace_card(active_card, self.make_card("Medan magnet", "Simbol titik menunjukkan medan magnet keluar dari bidang layar.", color=PURPLE))
        self.play(FadeIn(b_symbols), FadeIn(b_label), run_time=0.75)

        traj_label = Text(spec.get("trajectory_label", "lintasan membelok"), font_size=18, color=YELLOW).next_to(path, UP, buff=0.10)
        self.play(FadeIn(traj_label), run_time=0.45)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class EnergyEnvironmentSystemTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_energy_environment_system_distinct",
        "node_id": "phase5_energy_environment_system",
        "template_id": "manim.energy_environment_system.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Sistem Energi dan Lingkungan",
        "subtitle": "Aliran energi menentukan manfaat sekaligus dampak lingkungan.",
        "nodes": [
            {"label": "Sumber", "detail": "matahari/batubara"},
            {"label": "Konversi", "detail": "pembangkit"},
            {"label": "Pemakaian", "detail": "listrik"},
            {"label": "Dampak", "detail": "emisi/limbah"},
        ],
        "flows": ["energi masuk", "energi listrik", "dampak"],
        "impact_metrics": [{"label": "Emisi fosil", "value": 8}, {"label": "Emisi terbarukan", "value": 2}],
        "steps": [
            {"title": "Lacak aliran energi", "body": "Energi berpindah dari sumber ke proses konversi lalu ke pengguna."},
            {"title": "Hitung dampak", "body": "Setiap pilihan energi memiliki keluaran lingkungan berbeda."},
            {"title": "Bandingkan skenario", "body": "Sistem lebih bersih jika dampaknya lebih rendah untuk manfaat yang sama."},
        ],
        "summary": "Analisis sistem energi melihat sumber, proses, manfaat, dan dampak lingkungan sebagai satu rangkaian.",
    }

    def construct(self):
        spec = self.SPEC
        nodes = spec.get("nodes") or []
        if not nodes:
            nodes = [{"label": "Sumber"}, {"label": "Konversi"}, {"label": "Pakai"}, {"label": "Dampak"}]
        nodes = nodes[:4]

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Rantai sistem", "Energi tidak berdiri sendiri: ada sumber, proses, pemakaian, dan dampak.", color=BLUE))

        colors = [YELLOW, BLUE, GREEN, RED]
        node_groups = VGroup()
        for i, node in enumerate(nodes):
            box = RoundedRectangle(width=1.25, height=0.82, corner_radius=0.15, color=colors[i], fill_color=colors[i], fill_opacity=0.20)
            title = Text(_safe_label(node.get("label", f"N{i+1}"), 13), font_size=15, color=colors[i], weight=BOLD)
            detail = Text(_safe_label(node.get("detail", ""), 16), font_size=11, color=GRAY_A)
            text = VGroup(title, detail).arrange(DOWN, buff=0.05).move_to(box)
            group = VGroup(box, text).move_to(LEFT * 5.0 + RIGHT * i * 1.45 + UP * 0.55)
            node_groups.add(group)
        arrows = VGroup(*[Arrow(node_groups[i].get_right(), node_groups[i+1].get_left(), buff=0.05, color=GRAY_B, stroke_width=3) for i in range(len(node_groups)-1)])
        self.play(LaggedStart(*[FadeIn(g) for g in node_groups], lag_ratio=0.12), LaggedStart(*[Create(a) for a in arrows], lag_ratio=0.10), run_time=1.0)

        flow_labels = spec.get("flows") or []
        flow_mobs = VGroup()
        for i, label in enumerate(flow_labels[:len(arrows)]):
            flow_mobs.add(Text(_safe_label(label, 18), font_size=12, color=GRAY_A).next_to(arrows[i], DOWN, buff=0.08))
        if flow_mobs:
            self.play(FadeIn(flow_mobs), run_time=0.4)

        metrics = spec.get("impact_metrics") or []
        base_y = -2.0
        bars = VGroup()
        labels = VGroup()
        max_v = max([_as_float(m.get("value", 0), 0) for m in metrics] or [1])
        for i, metric in enumerate(metrics[:3]):
            h = max(0.20, 2.0 * _as_float(metric.get("value", 0), 0) / max_v)
            color = RED if i == 0 else GREEN
            bar = Rectangle(width=0.55, height=h, color=color, fill_color=color, fill_opacity=0.70).move_to(LEFT * 4.25 + RIGHT * i * 1.2 + UP * (base_y + h/2))
            lab = Text(_safe_label(metric.get("label", ""), 18), font_size=12, color=GRAY_A).next_to(bar, DOWN, buff=0.08)
            val = Text(_fmt_num(metric.get("value", 0)), font_size=14, color=color).next_to(bar, UP, buff=0.06)
            bars.add(bar)
            labels.add(lab, val)
        active_card = self.replace_card(active_card, self.make_card("Dampak lingkungan", "Dampak dapat dibandingkan dengan indikator seperti emisi atau limbah.", color=GREEN))
        self.play(LaggedStart(*[GrowFromEdge(b, DOWN) for b in bars], lag_ratio=0.12), FadeIn(labels), run_time=0.8)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ModernAtomicNuclearTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_modern_atomic_nuclear_distinct",
        "node_id": "phase5_atomic_nuclear_decay",
        "template_id": "manim.modern_atomic_nuclear.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Model Atom dan Peluruhan Inti",
        "subtitle": "Inti, elektron, dan peluruhan dapat divisualkan sebagai struktur dan proses.",
        "atom": {"protons": 6, "neutrons": 6, "electrons": 6, "label": "C-12"},
        "decay": {"type": "alpha", "before": "U-238", "after": "Th-234", "particle": "α"},
        "half_life_values": [8, 4, 2, 1],
        "steps": [
            {"title": "Struktur atom", "body": "Inti berisi proton dan neutron, elektron berada di sekitar inti."},
            {"title": "Peluruhan inti", "body": "Inti tidak stabil dapat memancarkan partikel dan berubah menjadi inti lain."},
            {"title": "Jumlah berkurang", "body": "Pada waktu paruh, jumlah inti tersisa menjadi setengahnya."},
        ],
        "summary": "Fisika atom modern melihat struktur atom sekaligus proses perubahan inti seperti peluruhan radioaktif.",
    }

    def construct(self):
        spec = self.SPEC
        atom = spec.get("atom", {}) or {}
        decay = spec.get("decay", {}) or {}
        values = spec.get("half_life_values") or [8, 4, 2, 1]

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Struktur atom", "Model atom memisahkan inti dan elektron agar perannya mudah dilihat.", color=BLUE))

        nucleus = VGroup()
        count = min(14, _as_int(atom.get("protons", 3), 3) + _as_int(atom.get("neutrons", 3), 3))
        for i in range(count):
            angle = TAU * i / max(1, count)
            radius = 0.08 + 0.22 * (i % 3) / 3
            color = RED if i % 2 == 0 else BLUE
            nucleus.add(Dot(radius=0.055, color=color).move_to(LEFT * 3.75 + UP * 0.55 + RIGHT * math.cos(angle) * radius + UP * math.sin(angle) * radius))
        orbits = VGroup(*[Circle(radius=r, color=GRAY_B, stroke_width=1.5).move_to(LEFT * 3.75 + UP * 0.55) for r in [0.75, 1.10]])
        electrons = VGroup()
        for i in range(min(8, _as_int(atom.get("electrons", 6), 6))):
            r = 0.75 if i < 2 else 1.10
            angle = TAU * i / max(1, min(8, _as_int(atom.get("electrons", 6), 6)))
            electrons.add(Dot(radius=0.045, color=YELLOW).move_to(LEFT * 3.75 + UP * 0.55 + RIGHT * math.cos(angle) * r + UP * math.sin(angle) * r))
        atom_label = Text(atom.get("label", "Atom"), font_size=18, color=YELLOW).next_to(orbits, DOWN, buff=0.12)
        self.play(Create(orbits), FadeIn(nucleus), FadeIn(electrons), FadeIn(atom_label), run_time=0.95)

        before = Text(decay.get("before", "Inti awal"), font_size=22, color=RED).move_to(LEFT * 4.65 + DOWN * 1.75)
        after = Text(decay.get("after", "Inti baru"), font_size=22, color=GREEN).move_to(LEFT * 2.00 + DOWN * 1.75)
        particle = Text(decay.get("particle", "α"), font_size=24, color=YELLOW).move_to(LEFT * 3.25 + DOWN * 1.25)
        decay_arrow = Arrow(before.get_right(), after.get_left(), buff=0.12, color=YELLOW, stroke_width=4)
        active_card = self.replace_card(active_card, self.make_card("Peluruhan inti", "Inti tidak stabil dapat memancarkan partikel dan berubah menjadi inti lain.", color=YELLOW))
        self.play(FadeIn(before), Create(decay_arrow), FadeIn(particle), FadeIn(after), run_time=0.75)

        max_v = max(values)
        bars = VGroup()
        for i, v in enumerate(values[:5]):
            h = max(0.14, 1.35 * _as_float(v, 0) / max_v)
            bar = Rectangle(width=0.34, height=h, color=PURPLE, fill_color=PURPLE, fill_opacity=0.65).move_to(LEFT * 1.30 + RIGHT * i * 0.47 + UP * (0.05 + h/2))
            label = Text(_fmt_num(v), font_size=12, color=PURPLE).next_to(bar, UP, buff=0.04)
            bars.add(VGroup(bar, label))
        half_label = Text("waktu paruh", font_size=16, color=PURPLE).next_to(bars, DOWN, buff=0.12)
        active_card = self.replace_card(active_card, self.make_card("Waktu paruh", "Setiap interval waktu paruh menyisakan sekitar setengah jumlah sebelumnya.", color=PURPLE))
        self.play(LaggedStart(*[GrowFromEdge(b[0], DOWN) for b in bars], lag_ratio=0.10), FadeIn(bars), FadeIn(half_label), run_time=0.85)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ChemistryReactionEquationTemplate(WicaraTemplateScene):
    SPEC = {
        "id": "sample_chem_reaction_equation_distinct",
        "node_id": "phase5_chemical_reaction_balancing",
        "template_id": "manim.chem_reaction_equation.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Menyeimbangkan Persamaan Reaksi",
        "subtitle": "Jumlah atom tiap unsur harus sama di kiri dan kanan.",
        "equation": "Fe + O₂ → Fe₂O₃",
        "final_solution": "4Fe + 3O₂ → 2Fe₂O₃",
        "atom_counts": [
            {"element": "Fe", "left_before": 1, "right_before": 2, "left_after": 4, "right_after": 4},
            {"element": "O", "left_before": 2, "right_before": 3, "left_after": 6, "right_after": 6},
        ],
        "balancing_steps": [
            {"label": "Set Fe₂O₃ = 2", "equation": "Fe + O₂ → 2Fe₂O₃"},
            {"label": "Set Fe = 4", "equation": "4Fe + O₂ → 2Fe₂O₃"},
            {"label": "Set O₂ = 3", "equation": "4Fe + 3O₂ → 2Fe₂O₃"},
        ],
        "steps": [
            {"title": "Hitung atom awal", "body": "Bandingkan jumlah atom setiap unsur pada kedua ruas."},
            {"title": "Ubah koefisien", "body": "Koefisien mengubah jumlah molekul tanpa mengganti rumus zat."},
            {"title": "Cek ulang", "body": "Persamaan seimbang jika jumlah atom kiri dan kanan sama."},
        ],
        "summary": "Persamaan reaksi seimbang mempertahankan jumlah atom setiap unsur pada kedua ruas.",
    }

    def construct(self):
        spec = self.SPEC
        counts = spec.get("atom_counts") or []
        steps = spec.get("balancing_steps") or spec.get("solution_steps") or []

        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Hukum kekekalan atom", "Atom tidak hilang; jumlah tiap unsur harus sama di kedua sisi reaksi.", color=BLUE))

        equation = Text(spec.get("equation", "Fe + O₂ → Fe₂O₃"), font_size=29, color=YELLOW, weight=BOLD).move_to(LEFT * 2.75 + UP * 1.85)
        self.play(Write(equation), run_time=0.75)

        rows = [["Unsur", "Kiri awal", "Kanan awal", "Kiri akhir", "Kanan akhir"]]
        for row in counts[:5]:
            rows.append([
                row.get("element", "?"),
                _fmt_num(row.get("left_before", 0)),
                _fmt_num(row.get("right_before", 0)),
                _fmt_num(row.get("left_after", 0)),
                _fmt_num(row.get("right_after", 0)),
            ])
        table = _make_table(rows, col_widths=[0.75, 0.9, 0.95, 0.9, 0.95], font_size=12).move_to(LEFT * 2.85 + UP * 0.40)
        active_card = self.replace_card(active_card, self.make_card("Inventaris atom", "Tabel atom membuat ketidakseimbangan terlihat jelas.", color=TEAL))
        self.play(FadeIn(table), run_time=0.65)

        step_group = VGroup()
        for i, st in enumerate(steps[:3]):
            label = st.get("label") or st.get("operation") or f"Langkah {i+1}"
            eq = st.get("equation") or f"{st.get('left_result','')} → {st.get('right_result','')}"
            box = RoundedRectangle(width=4.7, height=0.45, corner_radius=0.10, color=[BLUE, TEAL, GREEN][i], fill_opacity=0.20)
            text = Text(f"{label}: {eq}", font_size=13, color=WHITE).move_to(box)
            step_group.add(VGroup(box, text))
        step_group.arrange(DOWN, buff=0.12).move_to(LEFT * 2.75 + DOWN * 1.20)
        active_card = self.replace_card(active_card, self.make_card("Koefisien bertahap", "Ubah koefisien secara sistematis, lalu cek atom lagi.", color=GREEN))
        self.play(LaggedStart(*[FadeIn(g) for g in step_group], lag_ratio=0.12), run_time=0.8)

        final_eq = Text(spec.get("final_solution", ""), font_size=25, color=GREEN, weight=BOLD).move_to(LEFT * 2.75 + DOWN * 2.35)
        active_card = self.replace_card(active_card, self.make_card("Persamaan seimbang", "Setelah jumlah atom sama, persamaan dapat dipakai untuk analisis reaksi.", color=YELLOW))
        self.play(FadeIn(final_eq), Circumscribe(final_eq, color=GREEN), run_time=0.75)

        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)
# ============================================================
# PHASE 5: TEMPLATE 30-60 BUNDLE (MERGED)
# ============================================================

def simple_box(label, detail=None, width=1.55, height=0.88, color=BLUE, font_size=20):
    box = RoundedRectangle(
        width=width,
        height=height,
        corner_radius=0.15,
        color=color,
        fill_color=color,
        fill_opacity=0.18,
        stroke_width=2,
    )
    title = Text(str(label), font_size=font_size, color=color, weight=BOLD)
    if detail:
        subtitle = Text(str(detail), font_size=max(12, font_size - 7), color=WHITE)
        texts = VGroup(title, subtitle).arrange(DOWN, buff=0.06)
    else:
        texts = title
    texts.move_to(box.get_center())
    return VGroup(box, texts)


def circle_chip(label, radius=0.34, color=BLUE, font_size=18, fill_opacity=0.20):
    circ = Circle(radius=radius, color=color, fill_color=color, fill_opacity=fill_opacity, stroke_width=2)
    txt = Text(str(label), font_size=font_size, color=color, weight=BOLD).move_to(circ)
    return VGroup(circ, txt)


def atom_chip(label, color=BLUE, radius=0.26, font_size=15):
    outer = Circle(radius=radius, color=color, stroke_width=2)
    inner = Dot(radius=0.06, color=color)
    txt = Text(str(label), font_size=font_size, color=color).next_to(outer, DOWN, buff=0.05)
    return VGroup(outer, inner, txt)


def arrow_between(a, b, color=WHITE, buff=0.12, stroke_width=3):
    return Arrow(a.get_right(), b.get_left(), buff=buff, color=color, stroke_width=stroke_width)


def down_arrow(a, b, color=WHITE, buff=0.08, stroke_width=3):
    return Arrow(a.get_bottom(), b.get_top(), buff=buff, color=color, stroke_width=stroke_width)


def make_step_badges(steps, color=BLUE, font_size=15):
    badges = VGroup()
    for idx, step in enumerate(steps[:4]):
        badge = circle_chip(str(idx + 1), radius=0.18, color=color, font_size=font_size, fill_opacity=0.22)
        label = Text(str(step), font_size=13, color=WHITE)
        group = VGroup(badge, label).arrange(RIGHT, buff=0.10)
        badges.add(group)
    badges.arrange(DOWN, aligned_edge=LEFT, buff=0.12)
    return badges


# -----------------------------------------------------------------------------
# 30-60 SPEC DEFINITIONS
# -----------------------------------------------------------------------------

TEMPLATE_30_60_SPECS = {
    30: {
        "id": "sample_ecosystem_interdependence_30",
        "node_id": "phase5_sd_ecosystem_interdependence",
        "template_id": "manim.sd_ecosystem_food_chain.v1",
        "phase": "E",
        "audience_level": "sd",
        "language": "id",
        "title": "Keterkaitan dalam Ekosistem",
        "subtitle": "Makhluk hidup saling bergantung melalui habitat, makanan, dan peran masing-masing.",
        "sun_label": "Matahari",
        "chain": ["Rumput", "Belalang", "Katak", "Ular", "Elang"],
        "habitat_label": "Sawah",
        "decomposer_label": "Pengurai",
        "steps": [
            {"title": "Sumber energi", "body": "Energi awal datang dari matahari lalu ditangkap tumbuhan."},
            {"title": "Rantai makanan", "body": "Setiap makhluk hidup dapat menjadi sumber makanan bagi makhluk hidup lain."},
            {"title": "Keseimbangan", "body": "Jika satu bagian terganggu, seluruh ekosistem juga ikut terpengaruh."},
        ],
        "summary": "Ekosistem bekerja sebagai jaringan saling ketergantungan antara komponen hidup dan lingkungan.",
    },
    31: {
        "id": "sample_energy_forms_31",
        "node_id": "phase5_sd_energy_forms",
        "template_id": "manim.sd_energy_forms.v1",
        "phase": "E",
        "audience_level": "sd",
        "language": "id",
        "title": "Perubahan Bentuk Energi",
        "subtitle": "Energi panas, cahaya, bunyi, dan listrik dapat berubah dari satu bentuk ke bentuk lain.",
        "forms": [
            {"label": "Listrik", "detail": "setrika"},
            {"label": "Panas", "detail": "kompor"},
            {"label": "Cahaya", "detail": "lampu"},
            {"label": "Bunyi", "detail": "speaker"},
        ],
        "steps": [
            {"title": "Kenali bentuk energi", "body": "Benda di sekitar kita memanfaatkan berbagai bentuk energi."},
            {"title": "Lihat perubahan", "body": "Energi listrik bisa berubah menjadi cahaya, panas, atau bunyi."},
            {"title": "Hubungkan dengan contoh", "body": "Setiap alat rumah tangga memberi contoh perubahan energi yang berbeda."},
        ],
        "summary": "Energi tidak hilang, tetapi dapat berpindah dan berubah bentuk sesuai proses yang terjadi.",
    },
    32: {
        "id": "sample_quadratic_model_32",
        "node_id": "phase5_quadratic_application",
        "template_id": "manim.quadratic_model.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Grafik Fungsi Kuadrat",
        "subtitle": "Parabola memperlihatkan akar, titik puncak, dan arah bukaan fungsi kuadrat.",
        "function": {"type": "quadratic", "params": {"a": 1, "b": -4, "c": 3}},
        "x_range": [-1, 5, 1],
        "y_range": [-2, 8, 1],
        "formula_latex": "f(x)=x^2-4x+3",
        "vertex": {"x": 2, "y": -1},
        "roots": [1, 3],
        "steps": [
            {"title": "Arah bukaan", "body": "Karena koefisien a positif, parabola terbuka ke atas."},
            {"title": "Titik puncak", "body": "Titik puncak menunjukkan nilai minimum fungsi."},
            {"title": "Akar fungsi", "body": "Akar dibaca sebagai titik potong grafik dengan sumbu x."},
        ],
        "summary": "Representasi grafik memudahkan pembacaan sifat penting dari fungsi kuadrat.",
    },
    33: {
        "id": "sample_bivariable_association_33",
        "node_id": "phase4_bivariable_association",
        "template_id": "manim.scatter_association.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Asosiasi Dua Variabel",
        "subtitle": "Diagram pencar membantu membaca kecenderungan hubungan antara waktu belajar dan nilai tes.",
        "points": [[1, 55], [2, 60], [3, 63], [4, 70], [5, 74], [6, 81], [7, 84]],
        "trend_line": {"m": 4.8, "b": 50},
        "x_range": [0, 8, 1],
        "y_range": [45, 90, 5],
        "association": "positif",
        "steps": [
            {"title": "Plot data", "body": "Setiap titik mewakili satu pasangan data waktu belajar dan nilai."},
            {"title": "Baca kecenderungan", "body": "Titik yang naik ke kanan menunjukkan asosiasi positif."},
            {"title": "Tafsir hati-hati", "body": "Asosiasi tidak otomatis berarti sebab-akibat."},
        ],
        "summary": "Hubungan dua variabel dapat diringkas melalui pola sebaran dan garis kecenderungan.",
    },
    34: {
        "id": "sample_life_structure_classification_34",
        "node_id": "phase5_bio_structure_classification",
        "template_id": "manim.bio_structure_labeling.v1",
        "phase": "E",
        "audience_level": "smp",
        "language": "id",
        "title": "Struktur dan Klasifikasi Makhluk Hidup",
        "subtitle": "Makhluk hidup dapat dikenali dari ciri tubuh lalu dikelompokkan ke dalam klasifikasi tertentu.",
        "organism": "Tumbuhan berbunga",
        "levels": ["Makhluk hidup", "Tumbuhan", "Berbiji", "Angiospermae"],
        "parts": ["Akar", "Batang", "Daun", "Bunga"],
        "steps": [
            {"title": "Amati struktur", "body": "Bagian tubuh membantu mengenali fungsi dan identitas organisme."},
            {"title": "Kelompokkan ciri", "body": "Ciri yang sama dipakai untuk menempatkan organisme ke kelompok tertentu."},
            {"title": "Hubungkan", "body": "Struktur yang tampak mendukung klasifikasi yang lebih sistematis."},
        ],
        "summary": "Struktur organisme dan klasifikasi saling terkait untuk memahami keragaman makhluk hidup.",
    },
    35: {
        "id": "sample_electricity_magnetism_circuit_35",
        "node_id": "phase5_electricity_magnetism_circuit",
        "template_id": "manim.electricity_magnetism_circuit.v1",
        "phase": "E",
        "audience_level": "smp",
        "language": "id",
        "title": "Listrik, Magnet, dan Rangkaian",
        "subtitle": "Arus listrik pada rangkaian dapat menyalakan lampu dan menimbulkan efek magnet pada kumparan.",
        "steps": [
            {"title": "Rangkaian tertutup", "body": "Baterai, kabel, sakelar, dan lampu harus terhubung membentuk lintasan tertutup."},
            {"title": "Arus mengalir", "body": "Saat sakelar ditutup, arus listrik mengalir dan lampu menyala."},
            {"title": "Efek magnet", "body": "Kumparan berarus dapat menarik benda logam kecil seperti elektromagnet sederhana."},
        ],
        "summary": "Listrik dan magnet saling berhubungan dalam banyak alat sederhana di sekitar kita.",
    },
    36: {
        "id": "sample_environment_energy_system_36",
        "node_id": "phase5_environment_energy_system",
        "template_id": "manim.energy_environment_system.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Energi, Iklim, dan Lingkungan",
        "subtitle": "Pilihan sumber energi memengaruhi emisi dan kualitas lingkungan.",
        "nodes": [
            {"label": "Sumber", "detail": "fosil/terbarukan"},
            {"label": "Konversi", "detail": "pembangkit"},
            {"label": "Pemakaian", "detail": "transportasi/rumah"},
            {"label": "Dampak", "detail": "emisi"},
        ],
        "flows": ["energi primer", "energi listrik", "dampak lingkungan"],
        "impact_metrics": [{"label": "Fosil", "value": 8}, {"label": "Terbarukan", "value": 3}],
        "steps": [
            {"title": "Lacak sistem", "body": "Energi bergerak dari sumber ke pemakaian melalui tahap konversi."},
            {"title": "Bandingkan dampak", "body": "Setiap sumber energi memiliki jejak lingkungan yang berbeda."},
            {"title": "Ambil keputusan", "body": "Pilihan energi perlu mempertimbangkan manfaat dan dampak jangka panjang."},
        ],
        "summary": "Analisis sistem energi membantu menjelaskan hubungan antara kebutuhan energi dan perubahan lingkungan.",
    },
    37: {
        "id": "sample_modern_atomic_nuclear_37",
        "node_id": "phase5_modern_atomic_nuclear",
        "template_id": "manim.modern_atomic_nuclear.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Atom dan Inti Atom",
        "subtitle": "Model atom modern membantu menjelaskan inti, elektron, dan peluruhan radioaktif sederhana.",
        "isotope": {"label": "C-14", "proton": 6, "neutron": 8, "electron": 6},
        "half_life_bars": [100, 50, 25, 12.5],
        "steps": [
            {"title": "Bagian atom", "body": "Atom memiliki inti berisi proton dan neutron, serta elektron di sekitarnya."},
            {"title": "Isotop", "body": "Isotop memiliki jumlah proton sama tetapi neutron berbeda."},
            {"title": "Peluruhan", "body": "Jumlah inti radioaktif berkurang secara bertahap menurut waktu paruh."},
        ],
        "summary": "Konsep atom modern menggabungkan struktur atom dan sifat inti yang dapat berubah.",
    },
    38: {
        "id": "sample_virus_lifecycle_health_38",
        "node_id": "phase5_bio_virus_lifecycle",
        "template_id": "manim.bio_virus_lifecycle.v1",
        "phase": "E",
        "audience_level": "smp",
        "language": "id",
        "title": "Virus, Siklus, dan Kesehatan",
        "subtitle": "Virus masuk ke sel inang, memperbanyak diri, lalu dapat memengaruhi kesehatan manusia.",
        "stages": ["Menempel", "Masuk", "Replikasi", "Perakitan", "Keluar"],
        "health_actions": ["Cuci tangan", "Vaksin", "Masker"],
        "steps": [
            {"title": "Kenali virus", "body": "Virus membutuhkan sel inang untuk memperbanyak diri."},
            {"title": "Ikuti siklus", "body": "Siklus hidup virus terdiri atas tahapan masuk, replikasi, dan pelepasan."},
            {"title": "Jaga kesehatan", "body": "Pencegahan penyakit dilakukan dengan perilaku hidup sehat dan perlindungan diri."},
        ],
        "summary": "Memahami siklus hidup virus membantu menjelaskan cara pencegahan penyakit secara ilmiah.",
    },
    39: {
        "id": "sample_mutation_evolution_selection_39",
        "node_id": "phase5_bio_evolution_selection",
        "template_id": "manim.bio_evolution_selection.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Mutasi, Evolusi, dan Seleksi",
        "subtitle": "Variasi muncul melalui mutasi dan dapat dipilih oleh lingkungan melalui seleksi alam.",
        "population_labels": ["Variasi A", "Variasi B", "Variasi C"],
        "environment_factor": "Lingkungan kering",
        "steps": [
            {"title": "Variasi", "body": "Populasi memiliki perbedaan sifat yang dapat diwariskan."},
            {"title": "Seleksi", "body": "Lingkungan menyeleksi individu yang lebih sesuai untuk bertahan hidup."},
            {"title": "Perubahan populasi", "body": "Sifat yang menguntungkan cenderung lebih sering muncul pada generasi berikutnya."},
        ],
        "summary": "Evolusi dapat dipahami sebagai perubahan komposisi sifat dalam populasi dari waktu ke waktu.",
    },
    40: {
        "id": "sample_reaction_equation_conservation_40",
        "node_id": "phase5_reaction_equation_conservation",
        "template_id": "manim.chem_reaction_equation.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Persamaan Reaksi dan Kekekalan",
        "subtitle": "Jumlah atom harus tetap sama di ruas kiri dan kanan persamaan reaksi.",
        "unbalanced_equation": "H2 + O2 -> H2O",
        "balanced_equation": "2H2 + O2 -> 2H2O",
        "reactant_counts": [{"label": "H", "left": 2, "right": 2}, {"label": "O", "left": 2, "right": 1}],
        "balanced_counts": [{"label": "H", "left": 4, "right": 4}, {"label": "O", "left": 2, "right": 2}],
        "steps": [
            {"title": "Hitung atom", "body": "Setiap unsur dihitung pada pereaksi dan hasil reaksi."},
            {"title": "Setarakan koefisien", "body": "Koefisien diubah tanpa mengubah rumus kimia zat."},
            {"title": "Cek kekekalan", "body": "Jumlah atom akhir harus sama pada kedua ruas."},
        ],
        "summary": "Penyetaraan persamaan reaksi menegaskan hukum kekekalan massa pada tingkat partikel.",
    },
    41: {
        "id": "sample_reaction_rate_collision_41",
        "node_id": "phase5_reaction_rate_collision",
        "template_id": "manim.chem_particle_reaction_rate.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Laju Reaksi dan Tumbukan",
        "subtitle": "Reaksi kimia berlangsung lebih cepat jika tumbukan efektif antar partikel lebih sering terjadi.",
        "factors": ["Suhu", "Konsentrasi", "Luas permukaan", "Katalis"],
        "steps": [
            {"title": "Partikel bergerak", "body": "Partikel pereaksi selalu bergerak dan saling bertumbukan."},
            {"title": "Tumbukan efektif", "body": "Tidak semua tumbukan menghasilkan reaksi; orientasi dan energi harus sesuai."},
            {"title": "Faktor laju", "body": "Suhu, konsentrasi, luas permukaan, dan katalis memengaruhi jumlah tumbukan efektif."},
        ],
        "summary": "Teori tumbukan menjelaskan mengapa kondisi tertentu dapat mempercepat atau memperlambat reaksi.",
    },
    42: {
        "id": "sample_redox_electrochemistry_42",
        "node_id": "phase5_redox_electrochemistry",
        "template_id": "manim.chem_redox_electrochemistry.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Redoks dan Elektrokimia",
        "subtitle": "Elektron berpindah dari anoda ke katoda dalam sel elektrokimia.",
        "left_half": "Zn -> Zn2+ + 2e-",
        "right_half": "Cu2+ + 2e- -> Cu",
        "cell_label": "Sel Volta",
        "steps": [
            {"title": "Oksidasi", "body": "Anoda melepaskan elektron sehingga terjadi oksidasi."},
            {"title": "Reduksi", "body": "Katoda menerima elektron sehingga terjadi reduksi."},
            {"title": "Arus dan ion", "body": "Aliran elektron di kawat dan perpindahan ion menjaga reaksi tetap berlangsung."},
        ],
        "summary": "Konsep redoks terhubung langsung dengan cara kerja sel volta, baterai, dan proses elektrolisis.",
    },
    43: {
        "id": "sample_organic_functional_group_43",
        "node_id": "phase5_organic_functional_group",
        "template_id": "manim.chem_organic_structure.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Struktur Organik dan Gugus Fungsi",
        "subtitle": "Kerangka karbon dan gugus fungsi menentukan sifat senyawa organik.",
        "molecules": [
            {"name": "Etanol", "formula": "C2H5OH", "group": "-OH"},
            {"name": "Asam asetat", "formula": "CH3COOH", "group": "-COOH"},
            {"name": "Propanon", "formula": "CH3COCH3", "group": "C=O"},
        ],
        "steps": [
            {"title": "Kerangka karbon", "body": "Atom karbon dapat membentuk rantai lurus, bercabang, atau cincin."},
            {"title": "Gugus fungsi", "body": "Gugus fungsi memberi ciri reaksi dan sifat khas pada senyawa."},
            {"title": "Bandingkan contoh", "body": "Senyawa dengan kerangka berbeda dapat dibedakan lewat gugus fungsi utamanya."},
        ],
        "summary": "Mengenali gugus fungsi membantu menghubungkan struktur organik dengan sifat dan kegunaannya.",
    },
    44: {
        "id": "sample_arithmetic_operation_44",
        "node_id": "phase3_elementary_arithmetic_operation",
        "template_id": "manim.elementary_arithmetic_blocks.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Operasi Hitung dengan Blok",
        "subtitle": "Penjumlahan dan pengurangan dapat dipahami sebagai penggabungan dan pengambilan blok.",
        "expression": "7 - 3 = 4",
        "left_count": 7,
        "remove_count": 3,
        "result_count": 4,
        "steps": [
            {"title": "Mulai dari jumlah awal", "body": "Tampilkan banyak benda sesuai bilangan pertama."},
            {"title": "Ambil sebagian", "body": "Untuk pengurangan, beberapa benda dipisahkan atau diambil."},
            {"title": "Hitung sisa", "body": "Benda yang tersisa menunjukkan hasil operasi hitung."},
        ],
        "summary": "Model konkret membantu siswa memahami makna operasi hitung, bukan sekadar hafalan simbol.",
    },
    45: {
        "id": "sample_equation_balance_unknown_45",
        "node_id": "phase3_equation_unknown_balance",
        "template_id": "manim.equation_balance.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Nilai Tak Diketahui sebagai Keseimbangan",
        "subtitle": "Kotak kosong dapat dicari dengan menjaga agar kedua sisi tetap seimbang.",
        "equation_latex": "x + 4 = 9",
        "left_terms": ["x", "4"],
        "right_terms": ["9"],
        "unknown_value": 5,
        "steps": [
            {"title": "Lihat keseimbangan", "body": "Persamaan berarti nilai kiri dan kanan harus sama."},
            {"title": "Balik operasi", "body": "Untuk mencari x, kurangi kedua sisi dengan 4."},
            {"title": "Temukan nilai", "body": "Setelah disederhanakan, diperoleh x sama dengan 5."},
        ],
        "summary": "Model keseimbangan memudahkan pemahaman tentang makna persamaan dan nilai yang belum diketahui.",
    },
    46: {
        "id": "sample_data_representation_summary_46",
        "node_id": "phase3_data_representation_summary",
        "template_id": "manim.elementary_data_chart.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Dari Tabel ke Diagram",
        "subtitle": "Data sederhana dapat diringkas dalam bentuk tabel, piktogram, dan diagram batang.",
        "categories": ["Apel", "Jeruk", "Mangga", "Pisang"],
        "values": [4, 2, 5, 3],
        "steps": [
            {"title": "Catat data", "body": "Data awal bisa ditulis dalam tabel sederhana."},
            {"title": "Ubah ke gambar", "body": "Piktogram menampilkan data dengan simbol yang mudah dibaca."},
            {"title": "Bandingkan", "body": "Diagram batang memudahkan melihat kategori terbanyak dan tersedikit."},
        ],
        "summary": "Representasi data membantu membaca dan membandingkan informasi secara lebih cepat.",
    },
    47: {
        "id": "sample_body_senses_health_47",
        "node_id": "phase3_body_senses_health",
        "template_id": "manim.sd_body_senses_health.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Tubuh, Pancaindra, dan Kesehatan",
        "subtitle": "Bagian tubuh dan pancaindra memiliki fungsi yang perlu dijaga kesehatannya.",
        "senses": ["Mata", "Telinga", "Hidung", "Lidah", "Kulit"],
        "healthy_habits": ["Cuci tangan", "Makan sehat", "Tidur cukup"],
        "steps": [
            {"title": "Kenali pancaindra", "body": "Setiap indra membantu kita menerima informasi dari lingkungan."},
            {"title": "Hubungkan fungsi", "body": "Mata untuk melihat, telinga untuk mendengar, dan seterusnya."},
            {"title": "Rawat tubuh", "body": "Kebiasaan hidup sehat membantu tubuh dan indra bekerja dengan baik."},
        ],
        "summary": "Belajar bagian tubuh perlu disertai pemahaman cara menjaga kesehatan sehari-hari.",
    },
    48: {
        "id": "sample_living_things_lifecycle_48",
        "node_id": "phase3_living_things_lifecycle",
        "template_id": "manim.sd_life_cycle_classification.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Makhluk Hidup, Klasifikasi, dan Siklus Hidup",
        "subtitle": "Makhluk hidup memiliki ciri tertentu dan mengalami tahapan pertumbuhan dalam siklus hidupnya.",
        "categories": ["Hewan", "Tumbuhan"],
        "life_cycle": ["Telur", "Larva", "Pupa", "Kupu-kupu"],
        "steps": [
            {"title": "Ciri makhluk hidup", "body": "Makhluk hidup tumbuh, bernapas, membutuhkan makanan, dan berkembang biak."},
            {"title": "Kelompokkan", "body": "Makhluk hidup dapat dikelompokkan sebagai hewan atau tumbuhan berdasarkan cirinya."},
            {"title": "Ikuti siklus hidup", "body": "Beberapa hewan mengalami perubahan bentuk bertahap hingga dewasa."},
        ],
        "summary": "Klasifikasi dan siklus hidup membantu memahami keragaman serta perubahan pada makhluk hidup.",
    },
    49: {
        "id": "sample_force_motion_simple_machine_49",
        "node_id": "phase3_force_motion_simple_machine",
        "template_id": "manim.sd_force_motion.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Gaya, Gerak, dan Pesawat Sederhana",
        "subtitle": "Gaya dapat mengubah gerak benda dan pesawat sederhana membantu meringankan kerja.",
        "machines": ["Tuas", "Katrol", "Bidang miring"],
        "steps": [
            {"title": "Gaya dorong atau tarik", "body": "Benda dapat bergerak saat diberi gaya dorong atau tarik."},
            {"title": "Arah gerak", "body": "Gaya dapat mempercepat, memperlambat, atau mengubah arah gerak benda."},
            {"title": "Bantuan alat", "body": "Pesawat sederhana membantu manusia melakukan kerja dengan lebih mudah."},
        ],
        "summary": "Konsep gaya dan gerak menjadi lebih konkret saat dikaitkan dengan alat sederhana di sekitar kita.",
    },
    50: {
        "id": "sample_algebra_expression_50",
        "node_id": "phase4_algebra_expression_transform",
        "template_id": "manim.algebra_expression.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Transformasi Bentuk Aljabar",
        "subtitle": "Ekspresi aljabar dapat diperluas, disederhanakan, dan difaktorkan dengan tetap menjaga kesetaraan.",
        "expression_start": "2(x + 3) + x",
        "expression_expand": "2x + 6 + x",
        "expression_simplify": "3x + 6",
        "expression_factor": "3(x + 2)",
        "steps": [
            {"title": "Distribusikan", "body": "Kalikan faktor di luar kurung ke setiap suku di dalam kurung."},
            {"title": "Gabungkan suku sejenis", "body": "Suku yang memiliki variabel sama dapat dijumlahkan."},
            {"title": "Faktorkan kembali", "body": "Bentuk sederhana dapat diubah lagi menjadi bentuk faktor yang ekuivalen."},
        ],
        "summary": "Transformasi aljabar membantu berpindah antara bentuk ekspresi yang berbeda tetapi setara.",
    },
    51: {
        "id": "sample_inequality_region_51",
        "node_id": "phase5_inequality_region",
        "template_id": "manim.inequality_region.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Pertidaksamaan dan Daerah Solusi",
        "subtitle": "Daerah solusi pada bidang koordinat menunjukkan semua pasangan titik yang memenuhi pertidaksamaan.",
        "inequality_latex": r"y \leq x + 1",
        "boundary_label": "y = x + 1",
        "x_range": [-3, 4, 1],
        "y_range": [-2, 5, 1],
        "steps": [
            {"title": "Gambar garis batas", "body": "Ubah pertidaksamaan menjadi persamaan untuk mendapatkan garis batas."},
            {"title": "Uji titik", "body": "Gunakan titik uji untuk menentukan sisi daerah yang memenuhi."},
            {"title": "Arsir solusi", "body": "Semua titik pada daerah diarsir merupakan solusi pertidaksamaan."},
        ],
        "summary": "Visualisasi daerah solusi sangat membantu ketika mempelajari pertidaksamaan satu atau dua variabel.",
    },
    52: {
        "id": "sample_trigonometric_ratio_triangle_52",
        "node_id": "phase5_trigonometric_ratio_triangle",
        "template_id": "manim.trig_ratio_triangle.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Perbandingan Trigonometri pada Segitiga",
        "subtitle": "Sinus, cosinus, dan tangen dibentuk dari hubungan sisi-sisi pada segitiga siku-siku.",
        "sides": {"depan": 3, "samping": 4, "miring": 5},
        "theta_label": "θ",
        "steps": [
            {"title": "Tentukan sudut acuan", "body": "Pilih sudut yang akan dipakai untuk membaca sisi depan dan sisi samping."},
            {"title": "Identifikasi sisi", "body": "Setiap sisi memiliki peran berbeda: depan, samping, dan miring."},
            {"title": "Bangun rasio", "body": "sin θ = depan/miring, cos θ = samping/miring, tan θ = depan/samping."},
        ],
        "summary": "Trigonometri dasar berangkat dari hubungan sederhana pada segitiga siku-siku.",
    },
    53: {
        "id": "sample_function_composition_inverse_53",
        "node_id": "phase5_function_composition_inverse",
        "template_id": "manim.function_composition_transform.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Komposisi dan Invers Fungsi",
        "subtitle": "Fungsi dapat dipandang sebagai mesin yang mengubah input menjadi output, lalu dibalik dengan fungsi invers.",
        "input_value": 2,
        "f_label": "f(x)=2x+1",
        "g_label": "g(x)=x^2",
        "inverse_label": "f^{-1}(x)=(x-1)/2",
        "steps": [
            {"title": "Komposisi", "body": "Pada komposisi, output dari fungsi pertama menjadi input fungsi berikutnya."},
            {"title": "Urutan penting", "body": "f∘g dan g∘f belum tentu menghasilkan nilai yang sama."},
            {"title": "Invers", "body": "Fungsi invers membalik perubahan sehingga output dapat dikembalikan menjadi input semula."},
        ],
        "summary": "Model mesin fungsi membantu memvisualkan komposisi, transformasi, dan gagasan fungsi invers.",
    },
    54: {
        "id": "sample_acid_base_safety_54",
        "node_id": "phase5_acid_base_safety",
        "template_id": "manim.chem_acid_base_safety.v1",
        "phase": "E",
        "audience_level": "smp",
        "language": "id",
        "title": "Asam, Basa, dan Keselamatan",
        "subtitle": "Zat asam dan basa di sekitar kita perlu dikenali sifatnya serta ditangani dengan aman.",
        "ph_values": [{"label": "Cuka", "value": 3}, {"label": "Air", "value": 7}, {"label": "Sabun", "value": 10}],
        "safety_items": ["Sarung tangan", "Kacamata", "Label bahan"],
        "steps": [
            {"title": "Kenali pH", "body": "Asam memiliki pH di bawah 7, sedangkan basa di atas 7."},
            {"title": "Temukan contoh", "body": "Banyak bahan rumah tangga dapat dikelompokkan sebagai asam atau basa."},
            {"title": "Utamakan keselamatan", "body": "Penanganan zat kimia harus memperhatikan alat pelindung dan petunjuk label."},
        ],
        "summary": "Konsep asam-basa sebaiknya selalu diajarkan bersama konteks penggunaan aman dalam kehidupan sehari-hari.",
    },
    55: {
        "id": "sample_wave_sound_light_55",
        "node_id": "phase5_wave_sound_light",
        "template_id": "manim.wave_sound_light.v1",
        "phase": "E",
        "audience_level": "smp",
        "language": "id",
        "title": "Gelombang, Bunyi, dan Cahaya",
        "subtitle": "Gelombang memiliki panjang gelombang, amplitudo, dan frekuensi yang berkaitan dengan bunyi serta cahaya.",
        "wave_labels": ["Puncak", "Lembah", "λ", "A"],
        "steps": [
            {"title": "Amati bentuk gelombang", "body": "Gelombang memiliki puncak, lembah, amplitudo, dan panjang gelombang."},
            {"title": "Hubungkan dengan bunyi", "body": "Bunyi dapat dipahami melalui getaran dan perambatan gelombang."},
            {"title": "Hubungkan dengan cahaya", "body": "Cahaya juga menunjukkan sifat gelombang seperti pemantulan dan pembiasan sederhana."},
        ],
        "summary": "Satu kerangka gelombang dapat dipakai untuk menjelaskan bunyi maupun cahaya dasar.",
    },
    56: {
        "id": "sample_measurement_uncertainty_56",
        "node_id": "phase5_measurement_uncertainty",
        "template_id": "manim.measurement_uncertainty.v1",
        "phase": "E",
        "audience_level": "smp",
        "language": "id",
        "title": "Pengukuran dan Ketidakpastian",
        "subtitle": "Setiap hasil pengukuran memiliki satuan, ketelitian alat, dan ketidakpastian tertentu.",
        "tool_label": "Penggaris",
        "readings": [12.2, 12.3, 12.2],
        "reported_value": "12,23 ± 0,05 cm",
        "steps": [
            {"title": "Baca skala alat", "body": "Hasil ukur ditentukan dengan memperhatikan satuan dan skala terkecil alat."},
            {"title": "Ulangi pengukuran", "body": "Pengukuran berulang membantu melihat variasi hasil."},
            {"title": "Laporkan hasil", "body": "Hasil akhir ditulis bersama satuan dan ketidakpastian pengukuran."},
        ],
        "summary": "Belajar mengukur tidak hanya tentang angka, tetapi juga tentang kualitas hasil dan ketelitiannya.",
    },
    57: {
        "id": "sample_motion_kinematics_57",
        "node_id": "phase4_motion_kinematics",
        "template_id": "manim.motion_kinematics.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Kinematika Gerak",
        "subtitle": "Gerak dapat dijelaskan melalui posisi, kecepatan, dan grafik perubahan terhadap waktu.",
        "positions": [0, 2, 5, 9],
        "times": [0, 1, 2, 3],
        "steps": [
            {"title": "Posisi terhadap waktu", "body": "Gerak diamati dengan membandingkan perubahan posisi pada selang waktu tertentu."},
            {"title": "Makna kecepatan", "body": "Kecepatan menyatakan seberapa cepat posisi berubah."},
            {"title": "Baca grafik", "body": "Kemiringan grafik posisi-waktu memberi petunjuk tentang kecepatan gerak."},
        ],
        "summary": "Representasi tabel, lintasan, dan grafik saling melengkapi untuk menjelaskan gerak.",
    },
    58: {
        "id": "sample_heat_temperature_transfer_58",
        "node_id": "phase5_heat_temperature_transfer",
        "template_id": "manim.heat_transfer_thermo.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Suhu, Kalor, dan Perpindahan Panas",
        "subtitle": "Kalor berpindah dari suhu tinggi ke suhu rendah melalui konduksi, konveksi, atau radiasi.",
        "modes": ["Konduksi", "Konveksi", "Radiasi"],
        "temperatures": [80, 30],
        "steps": [
            {"title": "Bedakan suhu dan kalor", "body": "Suhu menyatakan derajat panas, sedangkan kalor adalah energi yang berpindah."},
            {"title": "Arah perpindahan", "body": "Kalor selalu berpindah dari benda bersuhu lebih tinggi ke lebih rendah."},
            {"title": "Tiga mekanisme", "body": "Perpindahan panas dapat terjadi lewat konduksi, konveksi, dan radiasi."},
        ],
        "summary": "Konsep suhu dan kalor menjadi lebih jelas saat dikaitkan dengan arah aliran energi dan mekanismenya.",
    },
    59: {
        "id": "sample_electric_circuit_59",
        "node_id": "phase5_electric_circuit",
        "template_id": "manim.electric_circuit.v1",
        "phase": "E",
        "audience_level": "smp",
        "language": "id",
        "title": "Rangkaian Listrik Sederhana",
        "subtitle": "Baterai, sakelar, kabel, dan lampu bekerja bersama dalam lintasan arus tertutup.",
        "components": ["Baterai", "Sakelar", "Lampu"],
        "steps": [
            {"title": "Susun komponen", "body": "Komponen utama rangkaian dihubungkan dengan kabel penghantar."},
            {"title": "Tutup sakelar", "body": "Arus hanya dapat mengalir saat lintasan tertutup."},
            {"title": "Bandingkan kondisi", "body": "Saat rangkaian terbuka, arus terputus dan lampu padam."},
        ],
        "summary": "Rangkaian listrik sederhana menegaskan bahwa arus membutuhkan jalur tertutup untuk mengalir.",
    },
    60: {
        "id": "sample_chemistry_inquiry_safety_60",
        "node_id": "phase5_chemistry_inquiry_safety",
        "template_id": "manim.chem_lab_safety.v1",
        "phase": "E",
        "audience_level": "smp",
        "language": "id",
        "title": "Hakikat Kimia dan Keselamatan Laboratorium",
        "subtitle": "Belajar kimia dimulai dari observasi zat dan perubahan, namun harus selalu disertai prosedur keselamatan.",
        "icons": ["Gelas kimia", "Api", "Kacamata", "Label bahaya"],
        "steps": [
            {"title": "Amati materi", "body": "Kimia mempelajari komposisi, sifat, dan perubahan zat."},
            {"title": "Kerja ilmiah", "body": "Eksperimen dilakukan dengan pengamatan, pencatatan, dan penarikan kesimpulan."},
            {"title": "Patuhi aturan", "body": "Keselamatan laboratorium harus diutamakan sebelum, saat, dan sesudah eksperimen."},
        ],
        "summary": "Pembelajaran kimia yang baik selalu menggabungkan rasa ingin tahu ilmiah dengan budaya keselamatan.",
    },
}


# -----------------------------------------------------------------------------
# NEW DISTINCT TEMPLATES
# -----------------------------------------------------------------------------

class EcosystemInterdependenceTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[30]

    def construct(self):
        spec = self.SPEC
        chain = spec.get("chain", [])[:5]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Ekosistem", "Makhluk hidup saling terhubung lewat aliran energi dan peran dalam habitat.", color=GREEN))

        sun = circle_chip(spec.get("sun_label", "Matahari"), radius=0.42, color=YELLOW, font_size=16, fill_opacity=0.28).move_to(LEFT * 5.2 + UP * 1.4)
        habitat = RoundedRectangle(width=6.6, height=2.8, corner_radius=0.18, color=GREEN, fill_color=GREEN, fill_opacity=0.10).move_to(LEFT * 2.1 + DOWN * 0.45)
        habitat_label = Text(spec.get("habitat_label", "Habitat"), font_size=20, color=GREEN, weight=BOLD).next_to(habitat, UP, buff=0.08)
        self.play(FadeIn(habitat), FadeIn(habitat_label), FadeIn(sun), run_time=0.7)

        nodes = VGroup()
        x_positions = [-4.8, -3.3, -1.8, -0.3, 1.2]
        colors = [GREEN, TEAL, BLUE, PURPLE, RED]
        for idx, label in enumerate(chain):
            node = simple_box(label, width=1.25, height=0.76, color=colors[idx % len(colors)], font_size=18)
            node.move_to(RIGHT * x_positions[idx] + DOWN * 0.35)
            nodes.add(node)
        arrows = VGroup(*[Arrow(nodes[i].get_right(), nodes[i + 1].get_left(), buff=0.10, color=WHITE, stroke_width=3) for i in range(len(nodes) - 1)])
        solar = Arrow(sun.get_bottom(), nodes[0].get_top(), buff=0.12, color=YELLOW, stroke_width=3)
        self.play(LaggedStart(*[FadeIn(node) for node in nodes], lag_ratio=0.12), Create(arrows), Create(solar), run_time=1.1)

        decomposer = simple_box(spec.get("decomposer_label", "Pengurai"), detail="mengembalikan unsur hara", width=1.8, height=0.86, color=ORANGE, font_size=18).move_to(RIGHT * 2.75 + DOWN * 1.65)
        cycle1 = CurvedArrow(nodes[-1].get_bottom() + DOWN * 0.06, decomposer.get_left(), angle=-0.6, color=ORANGE)
        cycle2 = CurvedArrow(decomposer.get_top(), nodes[0].get_bottom() + DOWN * 0.06, angle=-0.6, color=ORANGE)
        active_card = self.replace_card(active_card, self.make_card("Pengurai", "Sisa makhluk hidup diuraikan lalu unsur hara kembali dimanfaatkan tumbuhan.", color=ORANGE))
        self.play(FadeIn(decomposer), Create(cycle1), Create(cycle2), run_time=0.9)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class EnergyFormsConversionTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[31]

    def construct(self):
        spec = self.SPEC
        forms = spec.get("forms", [])[:4]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Bentuk energi", "Energi dapat hadir sebagai panas, cahaya, bunyi, listrik, dan bentuk lain.", color=BLUE))

        center = circle_chip("Energi", radius=0.56, color=YELLOW, font_size=24, fill_opacity=0.25).move_to(LEFT * 2.2 + DOWN * 0.2)
        self.play(FadeIn(center), run_time=0.45)
        positions = [LEFT * 4.5 + UP * 1.2, LEFT * 4.5 + DOWN * 1.4, LEFT * 0.0 + UP * 1.2, LEFT * 0.0 + DOWN * 1.4]
        groups = VGroup()
        arrows = VGroup()
        colors = [BLUE, RED, TEAL, PURPLE]
        for idx, item in enumerate(forms):
            box = simple_box(item.get("label", "Energi"), detail=item.get("detail"), width=1.6, height=0.85, color=colors[idx], font_size=18).move_to(positions[idx])
            groups.add(box)
            arrows.add(Arrow(center.get_center(), box.get_center(), buff=0.58, color=colors[idx], stroke_width=3))
        self.play(LaggedStart(*[FadeIn(g) for g in groups], lag_ratio=0.12), LaggedStart(*[Create(a) for a in arrows], lag_ratio=0.08), run_time=1.1)

        flow_labels = VGroup(
            Text("listrik → panas", font_size=16, color=RED),
            Text("listrik → cahaya", font_size=16, color=TEAL),
            Text("listrik → bunyi", font_size=16, color=PURPLE),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.10).move_to(RIGHT * 3.95 + DOWN * 0.2)
        frame = RoundedRectangle(width=2.7, height=1.5, corner_radius=0.16, color=WHITE).move_to(flow_labels)
        active_card = self.replace_card(active_card, self.make_card("Perubahan energi", "Satu bentuk energi dapat berubah menjadi bentuk lain saat alat bekerja.", color=TEAL))
        self.play(FadeIn(frame), FadeIn(flow_labels), run_time=0.7)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class LifeStructureClassificationTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[34]

    def construct(self):
        spec = self.SPEC
        levels = spec.get("levels", [])[:4]
        parts = spec.get("parts", [])[:4]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Struktur organisme", "Bagian tubuh memberi petunjuk tentang fungsi dan identitas makhluk hidup.", color=GREEN))

        organism = RoundedRectangle(width=2.0, height=2.7, corner_radius=0.18, color=GREEN, fill_color=GREEN, fill_opacity=0.12).move_to(LEFT * 3.9 + DOWN * 0.15)
        stem = Line(organism.get_bottom() + DOWN * 0.55, organism.get_center() + DOWN * 0.15, color=GREEN)
        leaves = VGroup(
            Ellipse(width=0.7, height=0.28, color=GREEN, fill_color=GREEN, fill_opacity=0.25).rotate(0.45).move_to(organism.get_center() + LEFT * 0.35 + UP * 0.15),
            Ellipse(width=0.7, height=0.28, color=GREEN, fill_color=GREEN, fill_opacity=0.25).rotate(-0.45).move_to(organism.get_center() + RIGHT * 0.35 + UP * 0.15),
            Ellipse(width=0.7, height=0.28, color=GREEN, fill_color=GREEN, fill_opacity=0.25).rotate(0.45).move_to(organism.get_center() + LEFT * 0.35 + DOWN * 0.35),
            Ellipse(width=0.7, height=0.28, color=GREEN, fill_color=GREEN, fill_opacity=0.25).rotate(-0.45).move_to(organism.get_center() + RIGHT * 0.35 + DOWN * 0.35),
        )
        flower = Circle(radius=0.18, color=YELLOW, fill_color=YELLOW, fill_opacity=0.8).move_to(organism.get_top() + DOWN * 0.35)
        org_label = Text(spec.get("organism", "Organisme"), font_size=18, color=GREEN, weight=BOLD).next_to(organism, DOWN, buff=0.08)
        self.play(FadeIn(organism), Create(stem), FadeIn(leaves), FadeIn(flower), FadeIn(org_label), run_time=1.0)

        part_labels = VGroup()
        target_points = [organism.get_bottom() + LEFT * 0.1, organism.get_center() + DOWN * 0.2, organism.get_center() + UP * 0.35, flower.get_center()]
        for idx, part in enumerate(parts):
            text = Text(part, font_size=15, color=WHITE)
            text.move_to(LEFT * 1.55 + UP * (1.2 - idx * 0.7))
            arrow = Arrow(text.get_right(), target_points[idx], buff=0.08, color=WHITE, stroke_width=2.5)
            part_labels.add(VGroup(text, arrow))
        active_card = self.replace_card(active_card, self.make_card("Identifikasi bagian", "Label struktur membantu menghubungkan nama bagian dan fungsinya.", color=TEAL))
        self.play(LaggedStart(*[FadeIn(g) for g in part_labels], lag_ratio=0.10), run_time=0.8)

        classes = VGroup(*[simple_box(level, width=1.55, height=0.62, color=BLUE if i < 2 else PURPLE, font_size=17) for i, level in enumerate(levels)]).arrange(DOWN, buff=0.10).move_to(RIGHT * 3.8 + DOWN * 0.1)
        braces = Brace(classes, LEFT, color=WHITE)
        class_label = Text("Klasifikasi", font_size=18, color=WHITE).next_to(braces, LEFT, buff=0.08)
        self.play(FadeIn(classes), FadeIn(braces), FadeIn(class_label), run_time=0.8)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ElectricityMagnetismCircuitTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[35]

    def construct(self):
        spec = self.SPEC
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Rangkaian", "Arus listrik memerlukan lintasan tertutup untuk mengalir.", color=BLUE))

        wire = VMobject(color=WHITE, stroke_width=4)
        pts = [LEFT * 5 + UP * 1.3, LEFT * 1.8 + UP * 1.3, LEFT * 1.8 + DOWN * 1.3, LEFT * 5 + DOWN * 1.3, LEFT * 5 + UP * 1.3]
        wire.set_points_as_corners(pts)
        battery1 = Line(LEFT * 5 + UP * 0.35, LEFT * 5 + DOWN * 0.10, color=YELLOW, stroke_width=5)
        battery2 = Line(LEFT * 4.7 + UP * 0.55, LEFT * 4.7 + DOWN * 0.30, color=YELLOW, stroke_width=3)
        switch = VGroup(Line(LEFT * 3.65 + UP * 1.3, LEFT * 3.2 + UP * 1.3, color=WHITE, stroke_width=4), Line(LEFT * 3.2 + UP * 1.3, LEFT * 2.8 + UP * 1.55, color=WHITE, stroke_width=4))
        bulb = Circle(radius=0.28, color=YELLOW, fill_color=YELLOW, fill_opacity=0.28).move_to(LEFT * 1.8 + DOWN * 0.1)
        filament = Text("X", font_size=18, color=YELLOW).move_to(bulb)
        self.play(Create(wire), FadeIn(battery1), FadeIn(battery2), FadeIn(switch), FadeIn(bulb), FadeIn(filament), run_time=1.0)

        active_card = self.replace_card(active_card, self.make_card("Arus mengalir", "Saat sakelar ditutup, elektron bergerak sepanjang rangkaian dan lampu menyala.", color=YELLOW))
        close_switch = Line(LEFT * 3.2 + UP * 1.3, LEFT * 2.8 + UP * 1.3, color=YELLOW, stroke_width=4)
        glow = SurroundingRectangle(bulb, color=YELLOW, buff=0.12)
        current_arrows = VGroup(
            Arrow(LEFT * 4.6 + UP * 1.3, LEFT * 4.0 + UP * 1.3, buff=0.08, color=YELLOW),
            Arrow(LEFT * 1.8 + UP * 0.7, LEFT * 1.8 + DOWN * 0.2, buff=0.08, color=YELLOW),
            Arrow(LEFT * 3.8 + DOWN * 1.3, LEFT * 4.6 + DOWN * 1.3, buff=0.08, color=YELLOW),
        )
        self.play(Transform(switch[1], close_switch), FadeIn(current_arrows), Create(glow), run_time=0.9)

        coil = VGroup(*[Arc(radius=0.22, angle=PI, color=PURPLE).shift(RIGHT * (2.7 + i * 0.28) + DOWN * 0.25) for i in range(5)])
        nail = Rectangle(width=0.22, height=1.1, color=GRAY, fill_color=GRAY, fill_opacity=0.55).move_to(RIGHT * 4.3 + DOWN * 0.25)
        clips = VGroup(*[SmallDot(color=WHITE).move_to(RIGHT * 5.15 + UP * (0.55 - i * 0.18)) for i in range(4)])
        arrow = CurvedArrow(RIGHT * 3.6 + DOWN * 0.85, RIGHT * 5.0 + UP * 0.2, angle=-0.6, color=PURPLE)
        active_card = self.replace_card(active_card, self.make_card("Elektromagnet", "Kumparan berarus dapat menimbulkan medan magnet yang menarik logam kecil.", color=PURPLE))
        self.play(FadeIn(coil), FadeIn(nail), FadeIn(clips), Create(arrow), run_time=0.9)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class VirusLifecycleHealthTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[38]

    def construct(self):
        spec = self.SPEC
        stages = spec.get("stages", [])[:5]
        health = spec.get("health_actions", [])[:3]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Virus", "Virus memerlukan sel inang untuk memperbanyak diri.", color=RED))

        stage_groups = VGroup()
        positions = [LEFT * 5.0 + UP * 0.5, LEFT * 3.3 + UP * 1.3, LEFT * 1.5 + UP * 0.5, RIGHT * 0.2 + UP * 1.3, RIGHT * 2.0 + UP * 0.5]
        for idx, stg in enumerate(stages):
            cell = Circle(radius=0.48, color=BLUE, fill_color=BLUE, fill_opacity=0.14)
            virus = Star(n=8, outer_radius=0.18, color=RED, fill_color=RED, fill_opacity=0.8)
            virus.move_to(cell.get_center() + (LEFT * 0.12 if idx % 2 == 0 else RIGHT * 0.12))
            label = Text(stg, font_size=15, color=WHITE).next_to(cell, DOWN, buff=0.06)
            group = VGroup(cell, virus, label).move_to(positions[idx])
            stage_groups.add(group)
        arrows = VGroup(*[CurvedArrow(stage_groups[i].get_right(), stage_groups[i + 1].get_left(), angle=0.2 if i % 2 == 0 else -0.2, color=WHITE) for i in range(len(stage_groups) - 1)])
        self.play(LaggedStart(*[FadeIn(g) for g in stage_groups], lag_ratio=0.1), LaggedStart(*[Create(a) for a in arrows], lag_ratio=0.08), run_time=1.2)

        health_boxes = VGroup(*[simple_box(item, width=1.45, height=0.62, color=GREEN, font_size=16) for item in health]).arrange(DOWN, buff=0.10).move_to(RIGHT * 4.2 + DOWN * 1.0)
        shield = Shield().scale(0.55).set_color(GREEN).move_to(RIGHT * 2.9 + DOWN * 1.0) if 'Shield' in globals() else circle_chip("+", radius=0.28, color=GREEN, font_size=20).move_to(RIGHT * 2.9 + DOWN * 1.0)
        active_card = self.replace_card(active_card, self.make_card("Pencegahan", "Perlindungan diri dan perilaku sehat membantu mencegah penyebaran penyakit.", color=GREEN))
        self.play(FadeIn(health_boxes), FadeIn(shield), run_time=0.8)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class MutationEvolutionSelectionTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[39]

    def construct(self):
        spec = self.SPEC
        labels = spec.get("population_labels", [])[:3]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Variasi populasi", "Individu dalam populasi tidak selalu sama persis; ada variasi sifat.", color=TEAL))

        # The two generations are a before/after pair, so they stack. Laid out
        # side by side (the old xs2 ran to x=4.9) the second one landed under the
        # card, and both were squeezed into a band with the lower third of the
        # frame empty. Stacked, they read as a sequence and fill the stage.
        colors = [BLUE, GREEN, ORANGE]

        def generation(picks, caption):
            row = VGroup(
                *[
                    circle_chip(
                        lbl, radius=0.30, color=col, font_size=18, fill_opacity=0.26
                    )
                    for lbl, col in picks
                ]
            ).arrange(RIGHT, buff=0.40)
            cap = Text(
                caption,
                font_size=theme.FS_LABEL,
                color=theme.ON_INK_3,
                **theme.font_kwargs("medium"),
            )
            return VGroup(row, cap).arrange(DOWN, buff=0.22), row

        gen1_block, gen1 = generation(
            [
                (labels[i % len(labels)][-1], colors[i % len(colors)])
                for i in range(6)
            ],
            self.tr_text("Generasi awal"),
        )
        gen2_block, gen2 = generation(
            [
                (labels[1][-1] if i < 4 else labels[2][-1], GREEN if i < 4 else ORANGE)
                for i in range(6)
            ],
            self.tr_text("Generasi berikutnya"),
        )

        env = simple_box(
            spec.get("environment_factor", "Lingkungan"),
            width=2.4,
            height=0.72,
            color=RED,
            font_size=18,
        )
        press = VGroup(
            Arrow(UP * 0.34, DOWN * 0.34, buff=0.0, color=theme.ON_INK_3, stroke_width=3),
            env,
            Arrow(UP * 0.34, DOWN * 0.34, buff=0.0, color=theme.ON_INK_3, stroke_width=3),
        ).arrange(RIGHT, buff=0.55)

        self.stage_rows(gen1_block, press, gen2_block, buff=0.50)

        self.play(
            LaggedStart(*[FadeIn(p, scale=0.9) for p in gen1], lag_ratio=0.08),
            FadeIn(gen1_block[1]),
            run_time=0.9,
        )
        self.play(FadeIn(press, shift=DOWN * 0.10), run_time=0.6)
        active_card = self.replace_card(active_card, self.make_card("Seleksi alam", "Lingkungan menyeleksi sifat yang paling menguntungkan untuk bertahan hidup.", color=GREEN))
        self.play(
            LaggedStart(*[FadeIn(p, scale=0.9) for p in gen2], lag_ratio=0.08),
            FadeIn(gen2_block[1]),
            run_time=0.9,
        )
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ReactionRateCollisionTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[41]

    def construct(self):
        spec = self.SPEC
        factors = spec.get("factors", [])[:4]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Partikel bereaksi", "Reaksi bergantung pada tumbukan antar partikel pereaksi.", color=BLUE))

        box = RoundedRectangle(width=4.5, height=2.5, corner_radius=0.16, color=WHITE).move_to(LEFT * 2.7 + DOWN * 0.15)
        particles_a = VGroup(*[Dot(radius=0.08, color=BLUE).move_to(box.get_center() + LEFT * (1.5 - 0.7 * i) + UP * (0.65 - 0.45 * (i % 2))) for i in range(4)])
        particles_b = VGroup(*[Dot(radius=0.08, color=RED).move_to(box.get_center() + RIGHT * (1.3 - 0.55 * i) + DOWN * (0.55 - 0.32 * (i % 2))) for i in range(4)])
        self.play(FadeIn(box), FadeIn(particles_a), FadeIn(particles_b), run_time=0.7)

        paths = VGroup(
            Arrow(particles_a[0].get_center(), particles_b[0].get_center(), buff=0.12, color=YELLOW),
            Arrow(particles_a[2].get_center(), particles_b[2].get_center(), buff=0.12, color=YELLOW),
        )
        spark = Star(n=6, outer_radius=0.18, color=YELLOW, fill_color=YELLOW, fill_opacity=0.8).move_to((particles_a[0].get_center() + particles_b[0].get_center()) / 2)
        active_card = self.replace_card(active_card, self.make_card("Tumbukan efektif", "Tumbukan harus cukup energi dan orientasinya tepat agar reaksi terjadi.", color=YELLOW))
        self.play(LaggedStart(*[Create(p) for p in paths], lag_ratio=0.08), FadeIn(spark), run_time=0.8)

        factor_boxes = VGroup(*[simple_box(f, width=1.5, height=0.58, color=GREEN if i < 2 else TEAL, font_size=15) for i, f in enumerate(factors)]).arrange(DOWN, buff=0.10).move_to(RIGHT * 3.95 + DOWN * 0.05)
        frame = SurroundingRectangle(factor_boxes, color=WHITE, buff=0.12)
        self.play(FadeIn(frame), FadeIn(factor_boxes), run_time=0.75)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class RedoxElectrochemistryTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[42]

    def construct(self):
        spec = self.SPEC
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Sel elektrokimia", "Dua setengah sel dihubungkan untuk menghasilkan aliran elektron.", color=BLUE))

        beaker_l = RoundedRectangle(width=1.7, height=2.1, corner_radius=0.12, color=BLUE).move_to(LEFT * 3.8 + DOWN * 0.1)
        beaker_r = RoundedRectangle(width=1.7, height=2.1, corner_radius=0.12, color=GREEN).move_to(LEFT * 0.7 + DOWN * 0.1)
        sol_l = Rectangle(width=1.52, height=1.05, color=BLUE, fill_color=BLUE, fill_opacity=0.25).move_to(beaker_l.get_bottom() + UP * 0.55)
        sol_r = Rectangle(width=1.52, height=1.05, color=GREEN, fill_color=GREEN, fill_opacity=0.25).move_to(beaker_r.get_bottom() + UP * 0.55)
        elec_l = Rectangle(width=0.18, height=1.5, color=GRAY, fill_color=GRAY, fill_opacity=0.7).move_to(beaker_l.get_center() + UP * 0.15)
        elec_r = Rectangle(width=0.18, height=1.5, color=GRAY, fill_color=GRAY, fill_opacity=0.7).move_to(beaker_r.get_center() + UP * 0.15)
        wire = ArcBetweenPoints(elec_l.get_top(), elec_r.get_top(), angle=-1.0, color=WHITE)
        bulb = Circle(radius=0.22, color=YELLOW, fill_color=YELLOW, fill_opacity=0.25).move_to((wire.get_center()) + UP * 0.55)
        salt_bridge = Line(beaker_l.get_right() + UP * 0.2, beaker_r.get_left() + UP * 0.2, color=PURPLE, stroke_width=6)
        labels = VGroup(
            Text(spec.get("left_half", "oksidasi"), font_size=16, color=BLUE).next_to(beaker_l, DOWN, buff=0.10),
            Text(spec.get("right_half", "reduksi"), font_size=16, color=GREEN).next_to(beaker_r, DOWN, buff=0.10),
            Text(spec.get("cell_label", "Sel"), font_size=18, color=WHITE).next_to(bulb, UP, buff=0.10),
        )
        self.play(FadeIn(beaker_l), FadeIn(beaker_r), FadeIn(sol_l), FadeIn(sol_r), FadeIn(elec_l), FadeIn(elec_r), Create(wire), FadeIn(bulb), FadeIn(salt_bridge), FadeIn(labels), run_time=1.2)

        electrons = VGroup(*[SmallDot(color=YELLOW).move_to(interpolate(elec_l.get_top(), elec_r.get_top(), t)) for t in [0.2, 0.45, 0.7]])
        ion_arrows = VGroup(Arrow(beaker_l.get_right(), beaker_r.get_left(), buff=0.25, color=PURPLE), Arrow(beaker_r.get_left() + DOWN * 0.35, beaker_l.get_right() + DOWN * 0.35, buff=0.25, color=PURPLE))
        active_card = self.replace_card(active_card, self.make_card("Elektron dan ion", "Elektron mengalir melalui kawat, sedangkan ion berpindah lewat jembatan garam.", color=PURPLE))
        self.play(FadeIn(electrons), Create(ion_arrows), run_time=0.8)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class OrganicStructureFunctionalGroupTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[43]

    def construct(self):
        spec = self.SPEC
        mols = spec.get("molecules", [])[:3]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Senyawa organik", "Senyawa organik memiliki kerangka karbon dan dapat dibedakan dengan gugus fungsi.", color=BLUE))

        cards = VGroup()
        x_positions = [-4.4, -2.0, 0.4]
        colors = [TEAL, ORANGE, PURPLE]
        for idx, mol in enumerate(mols):
            frame = RoundedRectangle(width=2.05, height=2.1, corner_radius=0.16, color=colors[idx], fill_color=colors[idx], fill_opacity=0.10)
            name = Text(mol.get("name", "Senyawa"), font_size=18, color=colors[idx], weight=BOLD)
            formula = _math_or_text(mol.get("formula", "C_xH_y"), font_size=24, color=WHITE)
            group = simple_box(mol.get("group", "-X"), width=1.0, height=0.50, color=colors[idx], font_size=16)
            card = VGroup(frame, VGroup(name, formula, group).arrange(DOWN, buff=0.16)).move_to(RIGHT * x_positions[idx] + DOWN * 0.05)
            cards.add(card)
        self.play(LaggedStart(*[FadeIn(c) for c in cards], lag_ratio=0.12), run_time=1.0)

        carbon_chain = VGroup(*[Circle(radius=0.13, color=WHITE).move_to(RIGHT * (-5.1 + i * 0.36) + DOWN * 2.0) for i in range(7)])
        links = VGroup(*[Line(carbon_chain[i].get_right(), carbon_chain[i + 1].get_left(), color=WHITE) for i in range(len(carbon_chain) - 1)])
        chain_label = Text("Kerangka karbon", font_size=18, color=WHITE).next_to(carbon_chain, UP, buff=0.10)
        active_card = self.replace_card(active_card, self.make_card("Kerangka dan gugus", "Kerangka karbon menunjukkan susunan utama, sedangkan gugus fungsi menentukan sifat khas.", color=TEAL))
        self.play(FadeIn(carbon_chain), FadeIn(links), FadeIn(chain_label), run_time=0.8)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class DataRepresentationSummaryTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[46]

    def construct(self):
        spec = self.SPEC
        cats = spec.get("categories", [])[:4]
        vals = spec.get("values", [])[:4]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Tabel data", "Data awal dapat dicatat dalam tabel sederhana.", color=BLUE))

        rows = VGroup()
        for idx, (c, v) in enumerate(zip(cats, vals)):
            left = simple_box(c, width=1.4, height=0.5, color=BLUE, font_size=16)
            right = simple_box(str(v), width=0.8, height=0.5, color=TEAL, font_size=16)
            row = VGroup(left, right).arrange(RIGHT, buff=0.08)
            rows.add(row)
        rows.arrange(DOWN, buff=0.08).move_to(LEFT * 4.45 + DOWN * 0.2)
        table_frame = SurroundingRectangle(rows, color=WHITE, buff=0.12)
        self.play(FadeIn(table_frame), FadeIn(rows), run_time=0.8)

        pictos = VGroup()
        for idx, (c, v) in enumerate(zip(cats, vals)):
            icon_row = VGroup(*[Dot(radius=0.08, color=YELLOW).shift(RIGHT * (0.22 * j)) for j in range(v)]).arrange(RIGHT, buff=0.08)
            label = Text(c, font_size=14, color=WHITE)
            group = VGroup(label, icon_row).arrange(RIGHT, buff=0.12)
            pictos.add(group)
        pictos.arrange(DOWN, aligned_edge=LEFT, buff=0.12).move_to(LEFT * 1.25 + DOWN * 0.1)
        active_card = self.replace_card(active_card, self.make_card("Piktogram", "Simbol berulang membantu pembaca muda melihat banyaknya data.", color=YELLOW))
        self.play(FadeIn(pictos), run_time=0.8)

        axes = Axes(x_range=[0, 5, 1], y_range=[0, max(vals) + 1, 1], x_length=2.7, y_length=2.1, axis_config={"include_numbers": False, "stroke_width": 2})
        axes.move_to(RIGHT * 3.65 + DOWN * 0.2)
        bars = VGroup()
        for idx, v in enumerate(vals):
            bar = Rectangle(width=0.36, height=(v / (max(vals) + 1)) * 1.75 + 0.15, color=GREEN, fill_color=GREEN, fill_opacity=0.35)
            bar.move_to(axes.c2p(idx + 0.7, 0) + UP * (bar.height / 2))
            label = Text(cats[idx][0], font_size=13, color=WHITE).next_to(bar, DOWN, buff=0.05)
            bars.add(VGroup(bar, label))
        active_card = self.replace_card(active_card, self.make_card("Diagram batang", "Diagram batang membuat perbandingan kategori menjadi lebih jelas.", color=GREEN))
        self.play(Create(axes), LaggedStart(*[FadeIn(b) for b in bars], lag_ratio=0.08), run_time=0.95)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class BodySensesHealthTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[47]

    def construct(self):
        spec = self.SPEC
        senses = spec.get("senses", [])[:5]
        habits = spec.get("healthy_habits", [])[:3]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Pancaindra", "Pancaindra membantu kita menerima informasi dari lingkungan.", color=BLUE))

        head = Circle(radius=0.62, color=WHITE).move_to(LEFT * 3.6 + UP * 0.55)
        body = Line(head.get_bottom(), head.get_bottom() + DOWN * 1.35, color=WHITE)
        arms = VGroup(Line(body.get_center() + UP * 0.45, body.get_center() + LEFT * 0.85 + UP * 0.1, color=WHITE), Line(body.get_center() + UP * 0.45, body.get_center() + RIGHT * 0.85 + UP * 0.1, color=WHITE))
        legs = VGroup(Line(body.get_bottom(), body.get_bottom() + LEFT * 0.65 + DOWN * 0.95, color=WHITE), Line(body.get_bottom(), body.get_bottom() + RIGHT * 0.65 + DOWN * 0.95, color=WHITE))
        self.play(FadeIn(head), Create(body), Create(arms), Create(legs), run_time=0.7)

        sense_points = [head.get_center() + LEFT * 0.20 + UP * 0.15, head.get_center() + RIGHT * 0.20 + UP * 0.15, head.get_center() + LEFT * 0.65, head.get_center() + RIGHT * 0.65, body.get_bottom() + RIGHT * 1.0]
        labels = VGroup()
        text_positions = [LEFT * 5.3 + UP * 1.45, LEFT * 5.3 + UP * 0.75, LEFT * 5.3 + DOWN * 0.05, LEFT * 5.3 + DOWN * 0.85, LEFT * 5.3 + DOWN * 1.65]
        for idx, s in enumerate(senses):
            t = Text(s, font_size=15, color=WHITE).move_to(text_positions[idx])
            arr = Arrow(t.get_right(), sense_points[idx], buff=0.06, color=WHITE, stroke_width=2.5)
            labels.add(VGroup(t, arr))
        self.play(LaggedStart(*[FadeIn(l) for l in labels], lag_ratio=0.08), run_time=0.8)

        habit_boxes = VGroup(*[simple_box(h, width=1.7, height=0.58, color=GREEN, font_size=15) for h in habits]).arrange(DOWN, buff=0.10).move_to(RIGHT * 3.8 + DOWN * 0.2)
        active_card = self.replace_card(active_card, self.make_card("Jaga kesehatan", "Kebiasaan sehat membantu tubuh dan pancaindra berfungsi baik.", color=GREEN))
        self.play(FadeIn(habit_boxes), run_time=0.7)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class LivingThingsLifecycleClassificationTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[48]

    def construct(self):
        spec = self.SPEC
        cats = spec.get("categories", [])[:2]
        cycle = spec.get("life_cycle", [])[:4]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Kelompok makhluk hidup", "Makhluk hidup dapat dikelompokkan berdasarkan ciri umum yang dimilikinya.", color=TEAL))

        cat_boxes = VGroup(*[simple_box(c, width=1.9, height=0.72, color=TEAL if i == 0 else GREEN, font_size=18) for i, c in enumerate(cats)]).arrange(RIGHT, buff=0.35).move_to(LEFT * 2.7 + UP * 1.1)
        self.play(FadeIn(cat_boxes), run_time=0.6)

        cycle_nodes = VGroup()
        positions = [RIGHT * 1.0 + UP * 0.75, RIGHT * 3.0 + UP * 0.75, RIGHT * 3.0 + DOWN * 0.95, RIGHT * 1.0 + DOWN * 0.95]
        for idx, label in enumerate(cycle):
            node = circle_chip(label, radius=0.38, color=BLUE if idx < 2 else PURPLE, font_size=14, fill_opacity=0.22).move_to(positions[idx])
            cycle_nodes.add(node)
        cycle_arrows = VGroup(
            Arrow(cycle_nodes[0].get_right(), cycle_nodes[1].get_left(), buff=0.10, color=WHITE),
            Arrow(cycle_nodes[1].get_bottom(), cycle_nodes[2].get_top(), buff=0.10, color=WHITE),
            Arrow(cycle_nodes[2].get_left(), cycle_nodes[3].get_right(), buff=0.10, color=WHITE),
            Arrow(cycle_nodes[3].get_top(), cycle_nodes[0].get_bottom(), buff=0.10, color=WHITE),
        )
        active_card = self.replace_card(active_card, self.make_card("Siklus hidup", "Tahapan hidup dapat disusun sebagai urutan perubahan dari awal hingga dewasa.", color=BLUE))
        self.play(LaggedStart(*[FadeIn(n) for n in cycle_nodes], lag_ratio=0.08), LaggedStart(*[Create(a) for a in cycle_arrows], lag_ratio=0.08), run_time=1.0)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ForceMotionSimpleMachineTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[49]

    def construct(self):
        spec = self.SPEC
        machines = spec.get("machines", [])[:3]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Gaya dan gerak", "Dorongan atau tarikan dapat membuat benda bergerak.", color=BLUE))

        cart = Rectangle(width=1.6, height=0.7, color=TEAL, fill_color=TEAL, fill_opacity=0.25).move_to(LEFT * 4.1 + DOWN * 0.25)
        wheels = VGroup(Circle(radius=0.16, color=WHITE).move_to(cart.get_bottom() + LEFT * 0.45 + DOWN * 0.1), Circle(radius=0.16, color=WHITE).move_to(cart.get_bottom() + RIGHT * 0.45 + DOWN * 0.1))
        push = Arrow(LEFT * 5.3 + DOWN * 0.25, cart.get_left(), buff=0.08, color=YELLOW, stroke_width=4)
        motion = Arrow(cart.get_right(), cart.get_right() + RIGHT * 1.25, buff=0.05, color=GREEN, stroke_width=4)
        self.play(FadeIn(cart), FadeIn(wheels), Create(push), Create(motion), run_time=0.9)

        machine_boxes = VGroup(*[simple_box(m, width=1.7, height=0.62, color=ORANGE if i == 0 else PURPLE if i == 1 else GREEN, font_size=15) for i, m in enumerate(machines)]).arrange(DOWN, buff=0.12).move_to(RIGHT * 3.8 + DOWN * 0.15)
        icons = VGroup(
            Line(RIGHT * 1.9 + UP * 1.0, RIGHT * 3.0 + DOWN * 0.1, color=ORANGE, stroke_width=4),
            Arc(radius=0.35, angle=PI, color=PURPLE).move_to(RIGHT * 2.9 + UP * 0.05),
            Line(RIGHT * 2.2 + DOWN * 1.0, RIGHT * 3.2 + DOWN * 0.45, color=GREEN, stroke_width=4),
        )
        active_card = self.replace_card(active_card, self.make_card("Pesawat sederhana", "Alat sederhana membantu mengubah arah gaya atau memperkecil usaha.", color=ORANGE))
        self.play(FadeIn(machine_boxes), FadeIn(icons), run_time=0.8)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class AlgebraExpressionTransformationTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[50]

    def construct(self):
        spec = self.SPEC
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Ekspresi awal", "Bentuk aljabar dapat diubah sambil tetap mempertahankan nilai yang sama.", color=BLUE))

        exprs = [spec.get("expression_start"), spec.get("expression_expand"), spec.get("expression_simplify"), spec.get("expression_factor")]
        expr_mobs = VGroup()
        for idx, expr in enumerate(exprs):
            eq = _math_or_text(expr, font_size=28, color=WHITE).move_to(LEFT * 2.4 + UP * (1.25 - idx * 0.95))
            expr_mobs.add(eq)
        arrows = VGroup(*[Arrow(expr_mobs[i].get_bottom(), expr_mobs[i + 1].get_top(), buff=0.08, color=YELLOW) for i in range(len(expr_mobs) - 1)])
        self.play(LaggedStart(*[FadeIn(m) for m in expr_mobs], lag_ratio=0.10), LaggedStart(*[Create(a) for a in arrows], lag_ratio=0.10), run_time=1.0)

        badges = make_step_badges(["distribusi", "gabung suku", "faktorisasi"], color=TEAL).move_to(RIGHT * 3.95 + DOWN * 0.2)
        active_card = self.replace_card(active_card, self.make_card("Langkah transformasi", "Distribusi, penyederhanaan, dan faktorisasi adalah operasi yang sering dipakai.", color=TEAL))
        self.play(FadeIn(badges), run_time=0.7)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class InequalityRegionTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[51]

    def construct(self):
        spec = self.SPEC
        xr = spec.get("x_range", [-3, 4, 1])
        yr = spec.get("y_range", [-2, 5, 1])
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Garis batas", "Pertidaksamaan dua variabel dianalisis melalui garis batas pada bidang koordinat.", color=BLUE))

        axes = _build_axes_from_ranges(xr, yr, x_length=5.4, y_length=3.3)
        axes.move_to(LEFT * 2.75 + DOWN * 0.25)
        line = axes.plot(lambda x: x + 1, x_range=[xr[0], xr[1]], color=YELLOW, stroke_width=4)
        line_label = _math_or_text(spec.get("boundary_label", "y=x+1"), font_size=22, color=YELLOW).move_to(LEFT * 1.0 + UP * 1.6)
        self.play(Create(axes), Create(line), FadeIn(line_label), run_time=1.0)

        poly = Polygon(axes.c2p(xr[0], yr[0]), axes.c2p(xr[1], yr[0]), axes.c2p(xr[1], xr[1] + 1), axes.c2p(xr[0], xr[0] + 1), color=GREEN, fill_color=GREEN, fill_opacity=0.20, stroke_opacity=0)
        test_point = Dot(axes.c2p(0, 0), color=RED)
        tp_label = Text("uji (0,0)", font_size=15, color=RED).next_to(test_point, RIGHT, buff=0.08)
        active_card = self.replace_card(active_card, self.make_card("Uji titik", "Titik uji membantu menentukan sisi mana yang memenuhi pertidaksamaan.", color=RED))
        self.play(FadeIn(poly), FadeIn(test_point), FadeIn(tp_label), run_time=0.8)

        ineq = _math_or_text(spec.get("inequality_latex", r"y \le x+1"), font_size=28, color=GREEN).move_to(RIGHT * 3.9 + DOWN * 0.2)
        self.play(FadeIn(ineq), run_time=0.4)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class TrigonometricRatioTriangleTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[52]

    def construct(self):
        spec = self.SPEC
        sides = spec.get("sides", {"depan": 3, "samping": 4, "miring": 5})
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Segitiga siku-siku", "Trigonometri dasar dibangun dari hubungan sisi-sisi pada segitiga siku-siku.", color=BLUE))

        a = LEFT * 4.9 + DOWN * 1.4
        b = LEFT * 1.7 + DOWN * 1.4
        c = LEFT * 4.9 + UP * 1.0
        tri = Polygon(a, b, c, color=WHITE, stroke_width=3)
        right_mark = VGroup(Line(a + RIGHT * 0.28, a + RIGHT * 0.28 + UP * 0.28, color=WHITE), Line(a + UP * 0.28, a + RIGHT * 0.28 + UP * 0.28, color=WHITE))
        theta = Arc(radius=0.42, start_angle=0, angle=0.62, color=YELLOW).move_to(a + RIGHT * 0.2 + UP * 0.2)
        theta_label = Text(spec.get("theta_label", "θ"), font_size=18, color=YELLOW).move_to(a + RIGHT * 0.55 + UP * 0.15)
        labels = VGroup(
            Text(f"depan = {sides.get('depan', 3)}", font_size=16, color=GREEN).next_to(Line(c, b), RIGHT, buff=0.15),
            Text(f"samping = {sides.get('samping', 4)}", font_size=16, color=BLUE).next_to(Line(a, c), LEFT, buff=0.15),
            Text(f"miring = {sides.get('miring', 5)}", font_size=16, color=PURPLE).next_to(Line(a, b), DOWN, buff=0.15),
        )
        self.play(Create(tri), FadeIn(right_mark), Create(theta), FadeIn(theta_label), FadeIn(labels), run_time=1.0)

        ratios = VGroup(
            _math_or_text(r"\sin\theta = \frac{depan}{miring}", font_size=24, color=GREEN),
            _math_or_text(r"\cos\theta = \frac{samping}{miring}", font_size=24, color=BLUE),
            _math_or_text(r"\tan\theta = \frac{depan}{samping}", font_size=24, color=PURPLE),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.16).move_to(RIGHT * 3.55 + DOWN * 0.15)
        active_card = self.replace_card(active_card, self.make_card("Rasio trigonometrik", "Sinus, cosinus, dan tangen berasal dari pembagian sisi yang tepat.", color=GREEN))
        self.play(FadeIn(ratios), run_time=0.8)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class FunctionCompositionInverseTransformTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[53]

    def construct(self):
        spec = self.SPEC
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Mesin fungsi", "Fungsi dapat dibayangkan sebagai mesin yang memproses input menjadi output.", color=BLUE))

        inp = simple_box(f"x={spec.get('input_value', 2)}", width=1.0, height=0.62, color=YELLOW, font_size=18).move_to(LEFT * 5.1)
        fbox = simple_box(spec.get("f_label", "f"), width=1.7, height=0.9, color=TEAL, font_size=17).move_to(LEFT * 2.9)
        gbox = simple_box(spec.get("g_label", "g"), width=1.7, height=0.9, color=PURPLE, font_size=17).move_to(LEFT * 0.7)
        out = simple_box("hasil", width=1.1, height=0.62, color=GREEN, font_size=18).move_to(RIGHT * 1.6)
        arrs = VGroup(Arrow(inp.get_right(), fbox.get_left(), buff=0.10, color=WHITE), Arrow(fbox.get_right(), gbox.get_left(), buff=0.10, color=WHITE), Arrow(gbox.get_right(), out.get_left(), buff=0.10, color=WHITE))
        self.play(FadeIn(inp), FadeIn(fbox), FadeIn(gbox), FadeIn(out), LaggedStart(*[Create(a) for a in arrs], lag_ratio=0.08), run_time=1.0)

        inv = simple_box(spec.get("inverse_label", "f^{-1}"), width=2.1, height=0.8, color=ORANGE, font_size=16).move_to(RIGHT * 4.2)
        inv_arrow = CurvedArrow(out.get_right(), inv.get_left(), angle=-0.4, color=ORANGE)
        back_arrow = CurvedArrow(inv.get_bottom(), inp.get_bottom(), angle=-0.6, color=ORANGE)
        active_card = self.replace_card(active_card, self.make_card("Fungsi invers", "Fungsi invers berusaha membalik proses sehingga kita dapat kembali ke input semula.", color=ORANGE))
        self.play(FadeIn(inv), Create(inv_arrow), Create(back_arrow), run_time=0.8)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class AcidBaseSafetyContextTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[54]

    def construct(self):
        spec = self.SPEC
        vals = spec.get("ph_values", [])[:3]
        items = spec.get("safety_items", [])[:3]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Skala pH", "Skala pH membantu menempatkan zat sebagai asam, netral, atau basa.", color=BLUE))

        line = NumberLine(x_range=[0, 14, 1], length=6.2, include_numbers=True, color=WHITE).move_to(LEFT * 2.0 + DOWN * 0.1)
        acid_zone = Rectangle(width=2.65, height=0.32, color=RED, fill_color=RED, fill_opacity=0.22).move_to(line.n2p(3) + DOWN * 0.26)
        neutral = Rectangle(width=0.42, height=0.32, color=YELLOW, fill_color=YELLOW, fill_opacity=0.22).move_to(line.n2p(7) + DOWN * 0.26)
        base_zone = Rectangle(width=2.65, height=0.32, color=BLUE, fill_color=BLUE, fill_opacity=0.22).move_to(line.n2p(11) + DOWN * 0.26)
        self.play(Create(line), FadeIn(acid_zone), FadeIn(neutral), FadeIn(base_zone), run_time=0.9)

        markers = VGroup()
        for idx, item in enumerate(vals):
            dot = Dot(line.n2p(item.get("value", 7)), color=[RED, YELLOW, BLUE][idx])
            label = Text(f"{item.get('label')}: {item.get('value')}", font_size=15, color=WHITE).next_to(dot, UP, buff=0.12)
            markers.add(VGroup(dot, label))
        self.play(FadeIn(markers), run_time=0.7)

        safety = VGroup(*[simple_box(it, width=1.65, height=0.56, color=GREEN, font_size=15) for it in items]).arrange(DOWN, buff=0.10).move_to(RIGHT * 4.1 + DOWN * 0.1)
        active_card = self.replace_card(active_card, self.make_card("Keselamatan", "Zat asam dan basa harus digunakan dengan alat pelindung dan prosedur aman.", color=GREEN))
        self.play(FadeIn(safety), run_time=0.7)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class WaveSoundLightTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[55]

    def construct(self):
        spec = self.SPEC
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Bentuk gelombang", "Gelombang dapat digambarkan dengan puncak, lembah, amplitudo, dan panjang gelombang.", color=BLUE))

        axes = Axes(x_range=[0, 8, 1], y_range=[-2, 2, 1], x_length=5.6, y_length=2.8, axis_config={"include_numbers": False, "stroke_width": 2})
        axes.move_to(LEFT * 2.75 + DOWN * 0.2)
        graph = axes.plot(lambda x: math.sin(x * math.pi / 2), x_range=[0, 8], color=YELLOW, stroke_width=4)
        self.play(Create(axes), Create(graph), run_time=0.9)

        crest = Dot(axes.c2p(1, 1), color=GREEN)
        trough = Dot(axes.c2p(3, -1), color=RED)
        amp = Brace(Line(axes.c2p(5, 0), axes.c2p(5, 1)), RIGHT, color=BLUE)
        amp_label = Text("A", font_size=18, color=BLUE).next_to(amp, RIGHT, buff=0.06)
        lam = DoubleArrow(axes.c2p(1, 1.45), axes.c2p(5, 1.45), color=PURPLE)
        lam_label = Text("λ", font_size=18, color=PURPLE).next_to(lam, UP, buff=0.06)
        active_card = self.replace_card(active_card, self.make_card("Besaran gelombang", "Amplitudo dan panjang gelombang adalah dua besaran penting yang dapat dibaca dari grafik.", color=PURPLE))
        self.play(FadeIn(crest), FadeIn(trough), FadeIn(amp), FadeIn(amp_label), FadeIn(lam), FadeIn(lam_label), run_time=0.8)

        panels = VGroup(simple_box("Bunyi", detail="getaran medium", width=1.7, height=0.75, color=TEAL, font_size=18), simple_box("Cahaya", detail="gelombang elektromagnetik", width=1.9, height=0.75, color=ORANGE, font_size=17)).arrange(DOWN, buff=0.14).move_to(RIGHT * 4.0 + DOWN * 0.05)
        self.play(FadeIn(panels), run_time=0.7)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class MeasurementUncertaintyTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[56]

    def construct(self):
        spec = self.SPEC
        readings = spec.get("readings", [])[:3]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Alat ukur", "Hasil pengukuran harus dibaca dengan memperhatikan skala terkecil alat.", color=BLUE))

        ruler = Rectangle(width=5.7, height=0.65, color=YELLOW, fill_color=YELLOW, fill_opacity=0.18).move_to(LEFT * 2.4 + DOWN * 0.1)
        ticks = VGroup()
        for i in range(0, 21):
            h = 0.30 if i % 5 == 0 else 0.18
            x = ruler.get_left()[0] + 0.18 + i * 0.26
            ticks.add(Line([x, ruler.get_center()[1] - h / 2, 0], [x, ruler.get_center()[1] + h / 2, 0], color=WHITE, stroke_width=2))
        obj = Rectangle(width=3.25, height=0.28, color=GREEN, fill_color=GREEN, fill_opacity=0.40).move_to(LEFT * 2.65 + DOWN * 0.1)
        self.play(FadeIn(ruler), FadeIn(ticks), FadeIn(obj), run_time=0.9)

        rows = VGroup(*[Text(f"ukur {i+1}: {r} cm", font_size=16, color=WHITE) for i, r in enumerate(readings)]).arrange(DOWN, aligned_edge=LEFT, buff=0.10).move_to(RIGHT * 3.7 + UP * 0.2)
        result = simple_box(spec.get("reported_value", "hasil"), width=2.4, height=0.64, color=PURPLE, font_size=16).move_to(RIGHT * 3.8 + DOWN * 1.05)
        active_card = self.replace_card(active_card, self.make_card("Pelaporan", "Beberapa hasil ukur dapat diringkas menjadi nilai akhir dengan ketidakpastian.", color=PURPLE))
        self.play(FadeIn(rows), FadeIn(result), run_time=0.8)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class HeatTemperatureTransferTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[58]

    def construct(self):
        spec = self.SPEC
        modes = spec.get("modes", [])[:3]
        temps = spec.get("temperatures", [80, 30])
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Arah kalor", "Kalor berpindah dari benda bersuhu tinggi ke benda bersuhu rendah.", color=RED))

        hot = Circle(radius=0.48, color=RED, fill_color=RED, fill_opacity=0.25).move_to(LEFT * 4.35 + UP * 0.1)
        cold = Circle(radius=0.48, color=BLUE, fill_color=BLUE, fill_opacity=0.25).move_to(LEFT * 1.8 + UP * 0.1)
        hot_label = Text(f"{temps[0]}°C", font_size=18, color=RED).move_to(hot)
        cold_label = Text(f"{temps[1]}°C", font_size=18, color=BLUE).move_to(cold)
        flow = Arrow(hot.get_right(), cold.get_left(), buff=0.1, color=YELLOW, stroke_width=4)
        self.play(FadeIn(hot), FadeIn(cold), FadeIn(hot_label), FadeIn(cold_label), Create(flow), run_time=0.9)

        mode_boxes = VGroup(*[simple_box(m, width=1.8, height=0.62, color=[ORANGE, TEAL, PURPLE][i], font_size=16) for i, m in enumerate(modes)]).arrange(DOWN, buff=0.12).move_to(RIGHT * 3.9 + DOWN * 0.1)
        examples = VGroup(
            Text("sendok logam", font_size=14, color=ORANGE),
            Text("air mendidih", font_size=14, color=TEAL),
            Text("sinar matahari", font_size=14, color=PURPLE),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.19).next_to(mode_boxes, RIGHT, buff=0.16)
        active_card = self.replace_card(active_card, self.make_card("Mekanisme perpindahan", "Konduksi, konveksi, dan radiasi adalah tiga cara utama panas berpindah.", color=ORANGE))
        self.play(FadeIn(mode_boxes), FadeIn(examples), run_time=0.8)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ElectricCircuitTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[59]

    def construct(self):
        spec = self.SPEC
        comps = spec.get("components", [])[:3]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Komponen rangkaian", "Rangkaian listrik sederhana tersusun dari sumber, penghantar, beban, dan sakelar.", color=BLUE))

        # open circuit on left
        wire_left = VMobject(color=WHITE, stroke_width=4)
        pts = [LEFT * 5.1 + UP * 1.0, LEFT * 2.5 + UP * 1.0, LEFT * 2.5 + DOWN * 1.0, LEFT * 5.1 + DOWN * 1.0, LEFT * 5.1 + UP * 1.0]
        wire_left.set_points_as_corners(pts)
        bulb_left = Circle(radius=0.24, color=GRAY, fill_color=GRAY, fill_opacity=0.15).move_to(LEFT * 2.5 + UP * 0.0)
        switch_open = VGroup(Line(LEFT * 3.9 + UP * 1.0, LEFT * 3.45 + UP * 1.0, color=WHITE, stroke_width=4), Line(LEFT * 3.45 + UP * 1.0, LEFT * 3.05 + UP * 1.28, color=WHITE, stroke_width=4))
        batt = VGroup(Line(LEFT * 5.1 + UP * 0.25, LEFT * 5.1 + DOWN * 0.20, color=YELLOW, stroke_width=5), Line(LEFT * 4.85 + UP * 0.38, LEFT * 4.85 + DOWN * 0.03, color=YELLOW, stroke_width=3))
        self.play(Create(wire_left), FadeIn(bulb_left), FadeIn(switch_open), FadeIn(batt), run_time=0.9)
        open_label = Text("terbuka", font_size=16, color=GRAY).next_to(bulb_left, DOWN, buff=0.08)
        self.play(FadeIn(open_label), run_time=0.2)

        # closed circuit on right
        frame = RoundedRectangle(width=3.8, height=3.0, corner_radius=0.18, color=WHITE).move_to(RIGHT * 3.0)
        bulb = Circle(radius=0.26, color=YELLOW, fill_color=YELLOW, fill_opacity=0.35).move_to(RIGHT * 4.1 + UP * 0.0)
        switch_closed = Line(RIGHT * 2.35 + UP * 1.1, RIGHT * 2.95 + UP * 1.1, color=YELLOW, stroke_width=4)
        wire_r = VMobject(color=WHITE, stroke_width=4)
        pts_r = [RIGHT * 1.7 + UP * 1.1, RIGHT * 4.1 + UP * 1.1, RIGHT * 4.1 + DOWN * 1.1, RIGHT * 1.7 + DOWN * 1.1, RIGHT * 1.7 + UP * 1.1]
        wire_r.set_points_as_corners(pts_r)
        batt_r = VGroup(Line(RIGHT * 1.7 + UP * 0.25, RIGHT * 1.7 + DOWN * 0.20, color=YELLOW, stroke_width=5), Line(RIGHT * 1.95 + UP * 0.38, RIGHT * 1.95 + DOWN * 0.03, color=YELLOW, stroke_width=3))
        active_card = self.replace_card(active_card, self.make_card("Rangkaian tertutup", "Saat sakelar tertutup, arus mengalir dan lampu menyala.", color=YELLOW))
        self.play(FadeIn(frame), Create(wire_r), FadeIn(batt_r), FadeIn(bulb), FadeIn(switch_closed), run_time=0.9)
        current = VGroup(Arrow(RIGHT * 2.2 + UP * 1.1, RIGHT * 3.5 + UP * 1.1, buff=0.08, color=YELLOW), Arrow(RIGHT * 4.1 + DOWN * 0.4, RIGHT * 4.1 + DOWN * 0.9, buff=0.08, color=YELLOW), Arrow(RIGHT * 3.5 + DOWN * 1.1, RIGHT * 2.1 + DOWN * 1.1, buff=0.08, color=YELLOW))
        self.play(FadeIn(current), run_time=0.45)

        comp_boxes = VGroup(*[simple_box(c, width=1.35, height=0.48, color=TEAL, font_size=14) for c in comps]).arrange(DOWN, buff=0.08).move_to(RIGHT * 0.35 + DOWN * 2.15)
        self.play(FadeIn(comp_boxes), run_time=0.4)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ChemistryInquirySafetyTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_30_60_SPECS[60]

    def construct(self):
        spec = self.SPEC
        icons = spec.get("icons", [])[:4]
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Hakikat kimia", "Kimia mempelajari zat, sifatnya, serta perubahan yang dialaminya.", color=BLUE))

        beaker = RoundedRectangle(width=1.45, height=1.9, corner_radius=0.14, color=BLUE).move_to(LEFT * 4.3 + DOWN * 0.2)
        liquid = Rectangle(width=1.24, height=0.92, color=BLUE, fill_color=BLUE, fill_opacity=0.24).move_to(beaker.get_bottom() + UP * 0.5)
        flame = Triangle(color=ORANGE, fill_color=ORANGE, fill_opacity=0.45).scale(0.35).move_to(LEFT * 4.25 + DOWN * 1.65)
        bubbles = VGroup(*[Dot(radius=0.05, color=WHITE).move_to(beaker.get_center() + LEFT * 0.25 + UP * (0.1 * i)) for i in range(4)])
        self.play(FadeIn(beaker), FadeIn(liquid), FadeIn(flame), FadeIn(bubbles), run_time=0.8)

        inquiry = VGroup(
            simple_box("Amati", width=1.4, height=0.56, color=TEAL, font_size=16),
            simple_box("Catat", width=1.4, height=0.56, color=TEAL, font_size=16),
            simple_box("Simpulkan", width=1.7, height=0.56, color=TEAL, font_size=16),
        ).arrange(DOWN, buff=0.10).move_to(LEFT * 1.25 + DOWN * 0.25)
        arrows = VGroup(Arrow(inquiry[0].get_bottom(), inquiry[1].get_top(), buff=0.06, color=WHITE), Arrow(inquiry[1].get_bottom(), inquiry[2].get_top(), buff=0.06, color=WHITE))
        active_card = self.replace_card(active_card, self.make_card("Penyelidikan ilmiah", "Observasi, pencatatan, dan kesimpulan adalah inti proses kerja ilmiah.", color=TEAL))
        self.play(FadeIn(inquiry), FadeIn(arrows), run_time=0.8)

        safety = VGroup(*[simple_box(ic, width=1.7, height=0.54, color=GREEN, font_size=14) for ic in icons]).arrange(DOWN, buff=0.08).move_to(RIGHT * 3.95 + DOWN * 0.1)
        active_card = self.replace_card(active_card, self.make_card("Budaya keselamatan", "Sebelum bereksperimen, siswa harus memahami aturan keselamatan dan simbol bahaya.", color=GREEN))
        self.play(FadeIn(safety), run_time=0.7)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


# -----------------------------------------------------------------------------
# CONCEPT-SPECIFIC WRAPPERS OVER STABLE MVP TEMPLATES
# -----------------------------------------------------------------------------

class QuadraticModelConceptTemplate(QuadraticModelTemplate):
    SPEC = TEMPLATE_30_60_SPECS[32]


class BivariableAssociationRegressionTemplate(ScatterAssociationTemplate):
    SPEC = TEMPLATE_30_60_SPECS[33]


class EnvironmentEnergySystemConceptTemplate(EnergyEnvironmentSystemTemplate):
    SPEC = TEMPLATE_30_60_SPECS[36]


class ModernAtomicNuclearConceptTemplate(ModernAtomicNuclearTemplate):
    SPEC = TEMPLATE_30_60_SPECS[37]


class ReactionEquationConservationConceptTemplate(ChemistryReactionEquationTemplate):
    SPEC = TEMPLATE_30_60_SPECS[40]


class ArithmeticOperationConcreteTemplate(ElementaryArithmeticBlocksTemplate):
    SPEC = TEMPLATE_30_60_SPECS[44]


class EquationBalanceUnknownTemplate(EquationBalanceTemplate):
    SPEC = TEMPLATE_30_60_SPECS[45]


class MotionKinematicsConceptTemplate(MotionKinematicsTemplate):
    SPEC = TEMPLATE_30_60_SPECS[57]


# -----------------------------------------------------------------------------
# REGISTRY
# -----------------------------------------------------------------------------

TEMPLATE_30_60_REGISTRY = {
    30: {"class_name": "EcosystemInterdependenceTemplate", "template_id": TEMPLATE_30_60_SPECS[30]["template_id"], "status": "new_distinct"},
    31: {"class_name": "EnergyFormsConversionTemplate", "template_id": TEMPLATE_30_60_SPECS[31]["template_id"], "status": "new_distinct"},
    32: {"class_name": "QuadraticModelConceptTemplate", "template_id": TEMPLATE_30_60_SPECS[32]["template_id"], "status": "wrapper_existing"},
    33: {"class_name": "BivariableAssociationRegressionTemplate", "template_id": TEMPLATE_30_60_SPECS[33]["template_id"], "status": "wrapper_existing"},
    34: {"class_name": "LifeStructureClassificationTemplate", "template_id": TEMPLATE_30_60_SPECS[34]["template_id"], "status": "new_distinct"},
    35: {"class_name": "ElectricityMagnetismCircuitTemplate", "template_id": TEMPLATE_30_60_SPECS[35]["template_id"], "status": "new_distinct"},
    36: {"class_name": "EnvironmentEnergySystemConceptTemplate", "template_id": TEMPLATE_30_60_SPECS[36]["template_id"], "status": "wrapper_existing"},
    37: {"class_name": "ModernAtomicNuclearConceptTemplate", "template_id": TEMPLATE_30_60_SPECS[37]["template_id"], "status": "wrapper_existing"},
    38: {"class_name": "VirusLifecycleHealthTemplate", "template_id": TEMPLATE_30_60_SPECS[38]["template_id"], "status": "new_distinct"},
    39: {"class_name": "MutationEvolutionSelectionTemplate", "template_id": TEMPLATE_30_60_SPECS[39]["template_id"], "status": "new_distinct"},
    40: {"class_name": "ReactionEquationConservationConceptTemplate", "template_id": TEMPLATE_30_60_SPECS[40]["template_id"], "status": "wrapper_existing"},
    41: {"class_name": "ReactionRateCollisionTemplate", "template_id": TEMPLATE_30_60_SPECS[41]["template_id"], "status": "new_distinct"},
    42: {"class_name": "RedoxElectrochemistryTemplate", "template_id": TEMPLATE_30_60_SPECS[42]["template_id"], "status": "new_distinct"},
    43: {"class_name": "OrganicStructureFunctionalGroupTemplate", "template_id": TEMPLATE_30_60_SPECS[43]["template_id"], "status": "new_distinct"},
    44: {"class_name": "ArithmeticOperationConcreteTemplate", "template_id": TEMPLATE_30_60_SPECS[44]["template_id"], "status": "wrapper_existing"},
    45: {"class_name": "EquationBalanceUnknownTemplate", "template_id": TEMPLATE_30_60_SPECS[45]["template_id"], "status": "wrapper_existing"},
    46: {"class_name": "DataRepresentationSummaryTemplate", "template_id": TEMPLATE_30_60_SPECS[46]["template_id"], "status": "new_distinct"},
    47: {"class_name": "BodySensesHealthTemplate", "template_id": TEMPLATE_30_60_SPECS[47]["template_id"], "status": "new_distinct"},
    48: {"class_name": "LivingThingsLifecycleClassificationTemplate", "template_id": TEMPLATE_30_60_SPECS[48]["template_id"], "status": "new_distinct"},
    49: {"class_name": "ForceMotionSimpleMachineTemplate", "template_id": TEMPLATE_30_60_SPECS[49]["template_id"], "status": "new_distinct"},
    50: {"class_name": "AlgebraExpressionTransformationTemplate", "template_id": TEMPLATE_30_60_SPECS[50]["template_id"], "status": "new_distinct"},
    51: {"class_name": "InequalityRegionTemplate", "template_id": TEMPLATE_30_60_SPECS[51]["template_id"], "status": "new_distinct"},
    52: {"class_name": "TrigonometricRatioTriangleTemplate", "template_id": TEMPLATE_30_60_SPECS[52]["template_id"], "status": "new_distinct"},
    53: {"class_name": "FunctionCompositionInverseTransformTemplate", "template_id": TEMPLATE_30_60_SPECS[53]["template_id"], "status": "new_distinct"},
    54: {"class_name": "AcidBaseSafetyContextTemplate", "template_id": TEMPLATE_30_60_SPECS[54]["template_id"], "status": "new_distinct"},
    55: {"class_name": "WaveSoundLightTemplate", "template_id": TEMPLATE_30_60_SPECS[55]["template_id"], "status": "new_distinct"},
    56: {"class_name": "MeasurementUncertaintyTemplate", "template_id": TEMPLATE_30_60_SPECS[56]["template_id"], "status": "new_distinct"},
    57: {"class_name": "MotionKinematicsConceptTemplate", "template_id": TEMPLATE_30_60_SPECS[57]["template_id"], "status": "wrapper_existing"},
    58: {"class_name": "HeatTemperatureTransferTemplate", "template_id": TEMPLATE_30_60_SPECS[58]["template_id"], "status": "new_distinct"},
    59: {"class_name": "ElectricCircuitTemplate", "template_id": TEMPLATE_30_60_SPECS[59]["template_id"], "status": "new_distinct"},
    60: {"class_name": "ChemistryInquirySafetyTemplate", "template_id": TEMPLATE_30_60_SPECS[60]["template_id"], "status": "new_distinct"},
}


__all__ = [
    "TEMPLATE_30_60_SPECS",
    "TEMPLATE_30_60_REGISTRY",
    "EcosystemInterdependenceTemplate",
    "EnergyFormsConversionTemplate",
    "QuadraticModelConceptTemplate",
    "BivariableAssociationRegressionTemplate",
    "LifeStructureClassificationTemplate",
    "ElectricityMagnetismCircuitTemplate",
    "EnvironmentEnergySystemConceptTemplate",
    "ModernAtomicNuclearConceptTemplate",
    "VirusLifecycleHealthTemplate",
    "MutationEvolutionSelectionTemplate",
    "ReactionEquationConservationConceptTemplate",
    "ReactionRateCollisionTemplate",
    "RedoxElectrochemistryTemplate",
    "OrganicStructureFunctionalGroupTemplate",
    "ArithmeticOperationConcreteTemplate",
    "EquationBalanceUnknownTemplate",
    "DataRepresentationSummaryTemplate",
    "BodySensesHealthTemplate",
    "LivingThingsLifecycleClassificationTemplate",
    "ForceMotionSimpleMachineTemplate",
    "AlgebraExpressionTransformationTemplate",
    "InequalityRegionTemplate",
    "TrigonometricRatioTriangleTemplate",
    "FunctionCompositionInverseTransformTemplate",
    "AcidBaseSafetyContextTemplate",
    "WaveSoundLightTemplate",
    "MeasurementUncertaintyTemplate",
    "MotionKinematicsConceptTemplate",
    "HeatTemperatureTransferTemplate",
    "ElectricCircuitTemplate",
    "ChemistryInquirySafetyTemplate",
]

# ============================================================
# END PHASE 5: TEMPLATE 30-60 BUNDLE (MERGED)
# ============================================================
# ============================================================
# PHASE 6: TEMPLATE 61-107 MANIM BUNDLE (MERGED)
# ============================================================

def _safe_text(value, default="-"):
    value = default if value is None else value
    return str(value)


def _box(label, detail=None, width=1.7, height=0.76, color=BLUE, font_size=18):
    rect = RoundedRectangle(
        width=width,
        height=height,
        corner_radius=0.14,
        color=color,
        fill_color=color,
        fill_opacity=0.16,
        stroke_width=2,
    )
    title = Text(_safe_text(label), font_size=font_size, color=color, weight=BOLD)
    if detail:
        subtitle = Text(_safe_text(detail), font_size=max(11, font_size - 7), color=WHITE)
        content = VGroup(title, subtitle).arrange(DOWN, buff=0.05)
    else:
        content = title
    content.move_to(rect.get_center())
    return VGroup(rect, content)


def _chip(label, radius=0.28, color=BLUE, font_size=16, fill_opacity=0.18):
    circ = Circle(radius=radius, color=color, fill_color=color, fill_opacity=fill_opacity, stroke_width=2)
    text = Text(_safe_text(label), font_size=font_size, color=color, weight=BOLD).move_to(circ)
    return VGroup(circ, text)


def _mini_dot(label=None, color=BLUE, radius=0.085):
    dot = Dot(radius=radius, color=color)
    if label is None:
        return dot
    text = Text(str(label), font_size=12, color=color).next_to(dot, DOWN, buff=0.04)
    return VGroup(dot, text)


def _formula(text, font_size=28, color=WHITE):
    try:
        return MathTex(str(text), font_size=font_size, color=color)
    except Exception:
        return Text(str(text), font_size=max(14, font_size - 6), color=color)


def _axes(x_range, y_range, x_length=5.4, y_length=3.0):
    return Axes(
        x_range=x_range,
        y_range=y_range,
        x_length=x_length,
        y_length=y_length,
        axis_config={"include_numbers": True, "font_size": 18, "stroke_width": 2},
        tips=False,
    )


def _bar(value, max_value, width=0.42, max_height=1.8, color=BLUE):
    h = 0.12 + (float(value) / max(1.0, float(max_value))) * max_height
    return Rectangle(width=width, height=h, color=color, fill_color=color, fill_opacity=0.32)


# -----------------------------------------------------------------------------
# Manim-only specs for concept_type_priority rows 61-107
# Rows skipped intentionally: remotion_svg, remotion_or_rive, remotion_or_manim.
# -----------------------------------------------------------------------------

TEMPLATE_61_107_MANIM_SPECS = {
    62: {
        "id": "sample_equilibrium_shift_62",
        "node_id": "phase5_chem_equilibrium_shift",
        "template_id": "manim.chem_equilibrium_shift.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Kesetimbangan Kimia dan Le Chatelier",
        "subtitle": "Sistem setimbang akan bergeser untuk mengurangi pengaruh perubahan konsentrasi, tekanan, atau suhu.",
        "equation_latex": r"N_2O_4(g) \rightleftharpoons 2NO_2(g)",
        "left_label": "reaktan",
        "right_label": "produk",
        "stressors": [
            {"label": "+ reaktan", "shift": "kanan"},
            {"label": "+ produk", "shift": "kiri"},
            {"label": "naik suhu", "shift": "tergantung ΔH"},
        ],
        "steps": [
            {"title": "Mulai dari keadaan setimbang", "body": "Laju reaksi maju dan balik sama sehingga komposisi terlihat stabil."},
            {"title": "Beri gangguan", "body": "Perubahan konsentrasi, tekanan, atau suhu mengganggu keadaan setimbang."},
            {"title": "Prediksi pergeseran", "body": "Sistem bergeser ke arah yang mengurangi dampak gangguan tersebut."},
        ],
        "summary": "Prinsip Le Chatelier membantu memprediksi arah perubahan sistem kesetimbangan secara kualitatif.",
    },
    63: {
        "id": "sample_thermochemistry_energy_profile_63",
        "node_id": "phase5_thermochemistry_energy_profile",
        "template_id": "manim.chem_energy_profile.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Profil Energi Termokimia",
        "subtitle": "Perubahan entalpi dapat dibaca dari posisi energi reaktan dan produk pada diagram energi.",
        "reaction_type": "eksoterm",
        "delta_h_label": r"\Delta H < 0",
        "reactant_energy": 4.0,
        "product_energy": 1.7,
        "activation_energy": 6.2,
        "steps": [
            {"title": "Tentukan energi awal", "body": "Reaktan memiliki tingkat energi tertentu sebelum reaksi berlangsung."},
            {"title": "Lewati energi aktivasi", "body": "Reaksi membutuhkan energi aktivasi untuk mencapai keadaan transisi."},
            {"title": "Baca ΔH", "body": "Selisih energi produk dan reaktan menunjukkan apakah reaksi menyerap atau melepas kalor."},
        ],
        "summary": "Diagram profil energi memperjelas hubungan antara energi aktivasi, produk, reaktan, dan entalpi reaksi.",
    },
    64: {
        "id": "sample_pattern_sequence_generalization_64",
        "node_id": "phase3_pattern_sequence_generalization",
        "template_id": "manim.sequence_pattern.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Pola, Barisan, dan Generalisasi Awal",
        "subtitle": "Pola gambar dapat diubah menjadi barisan bilangan lalu digeneralisasi menjadi aturan sederhana.",
        "terms": [3, 5, 7, 9],
        "term_labels": ["pola 1", "pola 2", "pola 3", "pola 4"],
        "rule_text": "tambah 2 setiap langkah",
        "rule": "Tambah 2 setiap langkah",
        "table_values": [
            {"n": 1, "value": 3},
            {"n": 2, "value": 5},
            {"n": 3, "value": 7},
            {"n": 4, "value": 9}
        ],
        "target_term": {"n": 5, "value": 11},
        "steps": [
            {"title": "Amati pola", "body": "Setiap pola memiliki jumlah benda yang dapat dihitung."},
            {"title": "Bandingkan antar pola", "body": "Selisih yang tetap menunjukkan aturan pertumbuhan."},
            {"title": "Buat generalisasi", "body": "Aturan pola membantu memprediksi pola berikutnya tanpa menggambar semuanya."},
        ],
        "summary": "Generalisasi pola adalah jembatan awal dari gambar konkret menuju pemikiran aljabar.",
    },
    65: {
        "id": "sample_shape_identification_geometry_65",
        "node_id": "phase3_shape_identification_geometry",
        "template_id": "manim.elementary_shapes.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Bangun Datar, Bangun Ruang, dan Komposisi",
        "subtitle": "Bangun dapat dikenali dari sisi, sudut, permukaan, dan cara beberapa bentuk disusun.",
        "flat_shapes": ["Segitiga", "Persegi", "Lingkaran"],
        "solid_shapes": ["Kubus", "Tabung", "Bola"],
        "composition_label": "rumah = persegi + segitiga",
        "steps": [
            {"title": "Kenali ciri", "body": "Bangun datar memiliki sisi dan sudut, sedangkan bangun ruang memiliki volume."},
            {"title": "Bandingkan bentuk", "body": "Perbedaan jumlah sisi, sudut, dan permukaan membantu klasifikasi."},
            {"title": "Susun komposisi", "body": "Beberapa bangun sederhana dapat disusun menjadi gambar atau objek baru."},
        ],
        "summary": "Identifikasi bentuk membantu siswa membaca struktur geometri di benda sekitar.",
    },
    66: {
        "id": "sample_area_perimeter_volume_66",
        "node_id": "phase3_area_perimeter_volume",
        "template_id": "manim.area_volume_decomposition.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Keliling, Luas, Volume, dan Jaring-jaring",
        "subtitle": "Keliling mengukur batas, luas mengukur daerah, dan volume mengukur ruang yang terisi.",
        "rectangle": {"width": 4, "height": 3},
        "cuboid_net_faces": ["atas", "bawah", "depan", "belakang", "kiri", "kanan"],
        "steps": [
            {"title": "Keliling", "body": "Keliling didapat dari panjang lintasan di tepi bangun."},
            {"title": "Luas", "body": "Luas dapat dipahami sebagai banyaknya kotak satuan yang menutupi daerah."},
            {"title": "Volume dan jaring-jaring", "body": "Bangun ruang dapat dibuka menjadi jaring-jaring untuk memahami permukaannya."},
        ],
        "summary": "Pengukuran geometri menjadi jelas jika batas, daerah, ruang, dan jaring-jaring dibedakan.",
    },
    72: {
        "id": "sample_matrix_operation_72",
        "node_id": "phase5_matrix_operation_model",
        "template_id": "manim.matrix_operations.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Matriks dan Operasi Data Tersusun",
        "subtitle": "Matriks menyimpan data dalam baris dan kolom sehingga operasi dapat dilakukan secara terstruktur.",
        "matrix_a": [[1, 2], [3, 4]],
        "matrix_b": [[2, 0], [1, 3]],
        "operation": "addition",
        "result_matrix": [[3, 2], [4, 7]],
        "steps": [
            {"title": "Baca posisi elemen", "body": "Setiap angka memiliki alamat baris dan kolom."},
            {"title": "Samakan ukuran", "body": "Penjumlahan matriks membutuhkan ukuran baris dan kolom yang sama."},
            {"title": "Operasikan elemen bersesuaian", "body": "Elemen pada posisi yang sama dijumlahkan untuk membentuk matriks hasil."},
        ],
        "summary": "Operasi matriks adalah cara sistematis mengolah data yang tersusun dalam grid angka.",
    },
    73: {
        "id": "sample_geodesic_coordinate_73",
        "node_id": "phase5_geodesic_coordinate_model",
        "template_id": "manim.geodesic_coordinate.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Koordinat dan Jarak pada Permukaan Bumi",
        "subtitle": "Jarak di permukaan Bumi mengikuti lengkungan, bukan garis lurus pada bidang datar.",
        "points": [{"label": "A", "lat": -6, "lon": 107}, {"label": "B", "lat": -8, "lon": 112}],
        "arc_label": "jarak lintasan permukaan",
        "steps": [
            {"title": "Tandai koordinat", "body": "Lokasi di Bumi dapat dinyatakan dengan lintang dan bujur."},
            {"title": "Perhatikan kelengkungan", "body": "Permukaan Bumi melengkung sehingga jarak terpendek mengikuti busur."},
            {"title": "Bandingkan peta dan globe", "body": "Representasi datar dapat mengubah persepsi jarak dan arah."},
        ],
        "summary": "Koordinat geografis menghubungkan posisi, arah, dan jarak pada permukaan Bumi yang melengkung.",
    },
    75: {
        "id": "sample_motion_force_pressure_75",
        "node_id": "phase4_motion_force_pressure",
        "template_id": "manim.force_motion_pressure.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Gerak, Gaya, Tekanan, dan Mesin Sederhana",
        "subtitle": "Gaya memengaruhi gerak, sedangkan tekanan bergantung pada besar gaya dan luas bidang tekan.",
        "force_value": 60,
        "area_large": 6,
        "area_small": 2,
        "machines": ["tuas", "bidang miring", "katrol"],
        "steps": [
            {"title": "Gaya mengubah gerak", "body": "Dorongan atau tarikan dapat mempercepat, memperlambat, atau mengubah arah gerak benda."},
            {"title": "Tekanan", "body": "Tekanan bertambah jika gaya sama diberikan pada luas bidang yang lebih kecil."},
            {"title": "Mesin sederhana", "body": "Pesawat sederhana membantu mengubah besar atau arah gaya yang diperlukan."},
        ],
        "summary": "Gerak, gaya, tekanan, dan mesin sederhana saling terkait dalam banyak kejadian sehari-hari.",
    },
    76: {
        "id": "sample_work_energy_power_76",
        "node_id": "phase5_work_energy_power",
        "template_id": "manim.energy_transfer.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Usaha, Energi, dan Daya",
        "subtitle": "Usaha memindahkan energi, sedangkan daya menyatakan seberapa cepat energi dipindahkan.",
        "force": 20,
        "distance": 5,
        "time": 4,
        "work_latex": r"W = F \cdot s = 100\ J",
        "power_latex": r"P = W/t = 25\ W",
        "steps": [
            {"title": "Ada gaya dan perpindahan", "body": "Usaha terjadi jika gaya menyebabkan perpindahan searah komponen gaya."},
            {"title": "Energi berpindah", "body": "Usaha dapat dilihat sebagai proses pemindahan energi ke benda."},
            {"title": "Hitung daya", "body": "Daya lebih besar jika usaha yang sama dilakukan dalam waktu lebih singkat."},
        ],
        "summary": "Usaha, energi, dan daya menjelaskan hubungan antara gaya, perpindahan, waktu, dan transfer energi.",
    },
    77: {
        "id": "sample_scalar_vector_77",
        "node_id": "phase4_scalar_vector_model",
        "template_id": "manim.vector_diagram.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Skalar, Vektor, dan Resultan",
        "subtitle": "Skalar hanya memiliki besar, sedangkan vektor memiliki besar dan arah.",
        "vectors": [{"label": "A", "x": 3, "y": 1}, {"label": "B", "x": 1, "y": 2}],
        "resultant": {"label": "R", "x": 4, "y": 3},
        "steps": [
            {"title": "Bedakan skalar dan vektor", "body": "Massa dan suhu adalah skalar, sedangkan perpindahan dan gaya adalah vektor."},
            {"title": "Gambar arah", "body": "Panah menunjukkan besar sekaligus arah suatu vektor."},
            {"title": "Jumlahkan vektor", "body": "Resultan dapat diperoleh dengan menyambung panah secara kepala-ke-ekor."},
        ],
        "summary": "Diagram vektor membantu memahami arah, besar, dan resultan secara visual.",
    },
    78: {
        "id": "sample_force_newton_diagram_78",
        "node_id": "phase4_force_newton_diagram",
        "template_id": "manim.force_diagram.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Gaya dan Hukum Newton",
        "subtitle": "Diagram gaya bebas membantu melihat resultan gaya dan hubungannya dengan percepatan.",
        "object_label": "balok",
        "object": {"type": "box", "label": "balok"},
        "forces": [
            {"label": "N", "direction": "up", "magnitude": 10},
            {"label": "W", "direction": "down", "magnitude": 10},
            {"label": "F", "direction": "right", "magnitude": 6},
            {"label": "f", "direction": "left", "magnitude": 2},
        ],
        "resultant_label": "ΣF = ma",
        "resultant": {"magnitude": 4, "unit": "N", "direction": "right"},
        "motion_response": "Balok cenderung dipercepat ke kanan karena resultan gaya ke kanan.",
        "force_scale": 0.24,
        "steps": [
            {"title": "Pisahkan benda", "body": "Diagram gaya bebas menggambar satu benda dan semua gaya yang bekerja padanya."},
            {"title": "Jumlahkan gaya", "body": "Gaya yang berlawanan arah saling mengurangi untuk menghasilkan resultan."},
            {"title": "Hubungkan dengan gerak", "body": "Jika resultan gaya tidak nol, benda mengalami percepatan."},
        ],
        "summary": "Hukum Newton menjadi lebih mudah dipahami melalui diagram gaya dan resultan gaya.",
    },
    79: {
        "id": "sample_fluid_pressure_79",
        "node_id": "phase5_fluid_pressure_model",
        "template_id": "manim.fluid_pressure.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Fluida dan Tekanan Hidrostatis",
        "subtitle": "Tekanan fluida bertambah seiring kedalaman karena berat kolom fluida di atas titik tersebut.",
        "depths": [1, 2, 3],
        "pressure_values": [1, 2, 3],
        "formula_latex": r"P = \rho g h",
        "steps": [
            {"title": "Tentukan kedalaman", "body": "Titik yang lebih dalam menanggung kolom fluida yang lebih tinggi."},
            {"title": "Tekanan meningkat", "body": "Semakin besar h, semakin besar tekanan hidrostatis."},
            {"title": "Arah tekanan", "body": "Tekanan fluida bekerja ke segala arah pada titik dalam fluida."},
        ],
        "summary": "Model tekanan fluida menjelaskan mengapa benda di tempat lebih dalam menerima tekanan lebih besar.",
    },
    80: {
        "id": "sample_electromagnetism_field_80",
        "node_id": "phase5_electromagnetism_field_model",
        "template_id": "manim.electromagnetism_field.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Medan Listrik, Magnet, dan Elektromagnetisme",
        "subtitle": "Muatan, arus, dan medan saling berhubungan dalam fenomena elektromagnetik.",
        "charge_label": "+q",
        "current_label": "I",
        "field_labels": ["E", "B"],
        "steps": [
            {"title": "Medan listrik", "body": "Muatan listrik menghasilkan medan listrik di sekitarnya."},
            {"title": "Medan magnet", "body": "Arus listrik menghasilkan medan magnet melingkar di sekitar kawat."},
            {"title": "Elektromagnetisme", "body": "Perubahan dan interaksi medan listrik-magnet menjelaskan banyak teknologi listrik."},
        ],
        "summary": "Elektromagnetisme menyatukan konsep muatan, arus, medan listrik, dan medan magnet.",
    },
    87: {
        "id": "sample_inheritance_probability_87",
        "node_id": "phase5_inheritance_probability_model",
        "template_id": "manim.bio_genetics_probability.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Pewarisan Sifat dan Peluang Genetika",
        "subtitle": "Punnett square menunjukkan kemungkinan kombinasi alel dari kedua induk.",
        "parents": ["Aa", "Aa"],
        "gametes": [["A", "a"], ["A", "a"]],
        "offspring": [["AA", "Aa"], ["Aa", "aa"]],
        "ratio": "3 dominan : 1 resesif",
        "steps": [
            {"title": "Tentukan gamet", "body": "Setiap induk menyumbangkan satu alel melalui gamet."},
            {"title": "Isi kotak Punnett", "body": "Kombinasi alel dibaca dari pertemuan baris dan kolom."},
            {"title": "Hitung peluang", "body": "Perbandingan genotipe atau fenotipe dihitung dari jumlah kotak yang sesuai."},
        ],
        "summary": "Peluang genetika membantu memprediksi variasi sifat keturunan secara sederhana.",
    },
    90: {
        "id": "sample_measurement_unit_conversion_90",
        "node_id": "phase3_measurement_unit_conversion",
        "template_id": "manim.measurement_units.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Pengukuran, Satuan, dan Konversi Sederhana",
        "subtitle": "Satuan dapat dikonversi dengan memahami tangga satuan dan faktor pengali.",
        "quantity": "panjang",
        "conversion_chain": ["m", "dm", "cm", "mm"],
        "example": "2 m = 200 cm",
        "steps": [
            {"title": "Kenali besaran", "body": "Panjang, massa, dan waktu diukur dengan satuan yang berbeda."},
            {"title": "Gunakan tangga satuan", "body": "Berpindah satu langkah pada satuan panjang berarti mengalikan atau membagi 10."},
            {"title": "Terapkan pada contoh", "body": "Konversi dilakukan dengan menghitung jumlah langkah antar satuan."},
        ],
        "summary": "Konversi satuan menjadi mudah jika arah perpindahan dan faktor pengalinya jelas.",
    },
    91: {
        "id": "sample_financial_quantity_91",
        "node_id": "phase3_financial_quantity_model",
        "template_id": "manim.elementary_finance_timeline.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Literasi Finansial Dasar",
        "subtitle": "Uang dapat digunakan, ditabung, dan direncanakan melalui keputusan sederhana sehari-hari.",
        "timeline": [
            {"label": "Uang saku", "amount": 20000},
            {"label": "Jajan", "amount": -8000},
            {"label": "Tabung", "amount": 12000},
        ],
        "goal": "beli buku",
        "steps": [
            {"title": "Catat pemasukan", "body": "Uang saku adalah contoh pemasukan sederhana."},
            {"title": "Bedakan kebutuhan dan keinginan", "body": "Sebagian uang digunakan untuk membeli, sebagian dapat ditabung."},
            {"title": "Rencanakan tujuan", "body": "Tabungan membantu mencapai tujuan tertentu di masa depan."},
        ],
        "summary": "Literasi finansial awal melatih siswa membuat keputusan sederhana tentang penggunaan uang.",
    },
    92: {
        "id": "sample_factor_multiple_divisibility_92",
        "node_id": "phase3_factor_multiple_divisibility",
        "template_id": "manim.factor_tree_multiple_grid.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Faktor, Kelipatan, FPB, KPK, dan Keterbagian",
        "subtitle": "Faktor membagi habis suatu bilangan, sedangkan kelipatan muncul dari perkalian berulang.",
        "numbers": [12, 18],
        "factor_tree": {"root": 12, "children": [3, 4], "leafs": [3, 2, 2]},
        "multiples_a": [12, 24, 36, 48],
        "multiples_b": [18, 36, 54, 72],
        "highlight": {"fpb": 6, "kpk": 36},
        "steps": [
            {"title": "Cari faktor", "body": "Faktor adalah bilangan yang membagi habis bilangan lain."},
            {"title": "Cari kelipatan", "body": "Kelipatan diperoleh dari perkalian bilangan dengan 1, 2, 3, dan seterusnya."},
            {"title": "Temukan FPB dan KPK", "body": "FPB berasal dari faktor bersama terbesar, KPK dari kelipatan bersama terkecil."},
        ],
        "summary": "Faktor dan kelipatan adalah dasar untuk memahami FPB, KPK, dan aturan keterbagian.",
    },
    93: {
        "id": "sample_angle_symmetry_transformation_93",
        "node_id": "phase3_angle_symmetry_transformation",
        "template_id": "manim.elementary_geometry_transform.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Sudut, Simetri, dan Transformasi Awal",
        "subtitle": "Bentuk dapat diputar, dicerminkan, dan digeser sambil mempertahankan sifat tertentu.",
        "angle_degrees": 90,
        "transformations": ["refleksi", "rotasi", "translasi"],
        "steps": [
            {"title": "Ukur sudut", "body": "Sudut menunjukkan besar bukaan antara dua garis."},
            {"title": "Lihat simetri", "body": "Bentuk simetris dapat dilipat atau dicerminkan sehingga kedua sisi cocok."},
            {"title": "Coba transformasi", "body": "Translasi, rotasi, dan refleksi mengubah posisi atau arah bentuk."},
        ],
        "summary": "Sudut dan transformasi membantu siswa memahami bagaimana bentuk bergerak dan tetap dikenali.",
    },
    94: {
        "id": "sample_chance_probability_informal_94",
        "node_id": "phase3_chance_probability_informal",
        "template_id": "manim.elementary_probability.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Peluang Informal dan Peluang Sederhana",
        "subtitle": "Peluang dapat dikenalkan melalui kata mungkin, mustahil, pasti, dan percobaan sederhana.",
        "events": [
            {"label": "Matahari terbit", "chance": "pasti"},
            {"label": "Koin muncul gambar", "chance": "mungkin"},
            {"label": "Dadu muncul 7", "chance": "mustahil"},
        ],
        "experiment": {"success": 3, "total": 6, "label": "angka genap pada dadu"},
        "steps": [
            {"title": "Gunakan bahasa peluang", "body": "Peristiwa dapat disebut pasti, mungkin, kecil peluangnya, atau mustahil."},
            {"title": "Coba percobaan", "body": "Koin dan dadu memberi contoh hasil yang tidak selalu sama."},
            {"title": "Hitung peluang sederhana", "body": "Peluang dapat dihitung sebagai hasil yang diinginkan dibanding semua kemungkinan."},
        ],
        "summary": "Peluang informal membangun intuisi sebelum siswa memakai pecahan dan rumus peluang formal.",
    },
    95: {
        "id": "sample_ratio_scale_proportion_95",
        "node_id": "phase3_ratio_scale_proportion",
        "template_id": "manim.ratio_proportion.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Rasio, Skala, dan Penalaran Proporsional Awal",
        "subtitle": "Rasio membandingkan dua jumlah, sedangkan skala mempertahankan perbandingan saat ukuran berubah.",
        "ratio": [2, 3],
        "labels": ["sirup", "air"],
        "context": "Membuat minuman dengan perbandingan sirup dan air",
        "quantities": [
            {"label": "sirup", "value": 2, "unit": "bagian"},
            {"label": "air", "value": 3, "unit": "bagian"}
        ],
        "ratio_pairs": [["sirup", "air"]],
        "scale_factor": 2,
        "scaling_steps": [{"from": "2:3", "to": "4:6", "label": "dikali 2"}],
        "steps": [
            {"title": "Bandingkan dua jumlah", "body": "Rasio 2 banding 3 berarti ada dua bagian pertama untuk tiga bagian kedua."},
            {"title": "Perbesar dengan skala", "body": "Jika kedua bagian dikalikan faktor yang sama, perbandingan tetap sama."},
            {"title": "Gunakan proporsi", "body": "Penalaran proporsional membantu menyelesaikan masalah resep, peta, dan ukuran."},
        ],
        "summary": "Rasio dan skala membantu memahami perbandingan yang tetap walau ukuran berubah.",
    },
    97: {
        "id": "sample_factorization_divisibility_97",
        "node_id": "phase4_factorization_divisibility_model",
        "template_id": "manim.factor_tree.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Faktorisasi dan Keterbagian",
        "subtitle": "Pohon faktor memecah bilangan menjadi faktor prima untuk membaca struktur keterbagian.",
        "number": 84,
        "tree_levels": [[84], [2, 42], [2, 2, 21], [2, 2, 3, 7]],
        "prime_factorization": r"84 = 2^2 \times 3 \times 7",
        "steps": [
            {"title": "Pecah bilangan", "body": "Mulai dari bilangan besar lalu pecah menjadi dua faktor."},
            {"title": "Lanjutkan sampai prima", "body": "Faktor komposit dipecah lagi sampai semua daun adalah bilangan prima."},
            {"title": "Tulis faktorisasi", "body": "Faktor prima disusun sebagai perkalian yang setara dengan bilangan awal."},
        ],
        "summary": "Faktorisasi prima memudahkan analisis keterbagian, FPB, KPK, dan penyederhanaan pecahan.",
    },
    98: {
        "id": "sample_graph_function_98",
        "node_id": "phase4_graph_function_model",
        "template_id": "manim.graph_explanation.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Grafik Fungsi dan Interpretasi Kurva",
        "subtitle": "Grafik fungsi menghubungkan input dan output sehingga perubahan dapat dibaca secara visual.",
        "function": {"type": "linear", "params": {"m": 2, "b": 1}},
        "x_range": [-2, 4, 1],
        "y_range": [-3, 10, 1],
        "formula_latex": "f(x)=2x+1",
        "moving_points": [-1, 0, 2, 3],
        "x_path": [-1, 0, 2, 3],
        "graph_label": "garis fungsi",
        "moving_label": "titik input-output",
        "show_slope": True,
        "highlight_x": 2,
        "steps": [
            {"title": "Input dan output", "body": "Setiap nilai x menghasilkan satu nilai y pada grafik fungsi."},
            {"title": "Baca kemiringan", "body": "Kemiringan garis menunjukkan laju perubahan output terhadap input."},
            {"title": "Interpretasi konteks", "body": "Grafik dapat dipakai untuk menjelaskan tren dalam situasi nyata."},
        ],
        "summary": "Grafik fungsi membuat hubungan antar variabel lebih mudah diamati dan ditafsirkan.",
    },
    99: {
        "id": "sample_spatial_net_99",
        "node_id": "phase4_spatial_net_model",
        "template_id": "manim.spatial_net.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Jaring-jaring dan Representasi 3D",
        "subtitle": "Bangun ruang dapat dibuka menjadi jaring-jaring untuk memahami susunan permukaannya.",
        "solid": "kubus",
        "faces": 6,
        "net_layout": [[0, 1], [1, 1], [2, 1], [3, 1], [1, 0], [1, 2]],
        "steps": [
            {"title": "Kenali sisi bangun", "body": "Kubus memiliki enam sisi berbentuk persegi."},
            {"title": "Buka permukaan", "body": "Jika beberapa rusuk dipotong, permukaan dapat dibentangkan menjadi jaring-jaring."},
            {"title": "Lipat kembali", "body": "Jaring-jaring yang benar dapat dilipat kembali menjadi bangun ruang semula."},
        ],
        "summary": "Jaring-jaring menghubungkan representasi datar dengan pemahaman bangun ruang 3D.",
    },
    100: {
        "id": "sample_measurement_data_process_100",
        "node_id": "phase4_measurement_data_process",
        "template_id": "manim.measurement_data.v1",
        "phase": "D",
        "audience_level": "smp",
        "language": "id",
        "title": "Pengukuran dan Data IPA",
        "subtitle": "Data hasil pengukuran perlu dicatat, diplot, dan dianalisis untuk menarik kesimpulan ilmiah.",
        "measurements": [
            {"time": 0, "temperature": 25},
            {"time": 1, "temperature": 32},
            {"time": 2, "temperature": 38},
            {"time": 3, "temperature": 43},
        ],
        "x_label": "waktu",
        "y_label": "suhu",
        "steps": [
            {"title": "Catat pengukuran", "body": "Setiap data harus memiliki nilai, satuan, dan waktu atau kondisi pengamatan."},
            {"title": "Ubah ke grafik", "body": "Grafik membantu melihat pola perubahan yang sulit terlihat dari tabel saja."},
            {"title": "Tarik kesimpulan", "body": "Kesimpulan didukung oleh pola data, bukan hanya dugaan."},
        ],
        "summary": "Proses pengukuran IPA mengubah observasi menjadi data yang dapat dianalisis secara ilmiah.",
    },
    102: {
        "id": "sample_momentum_impulse_collision_102",
        "node_id": "phase5_momentum_impulse_collision",
        "template_id": "manim.momentum_collision.v1",
        "phase": "E",
        "audience_level": "sma",
        "language": "id",
        "title": "Momentum, Impuls, dan Tumbukan",
        "subtitle": "Momentum berubah ketika impuls bekerja, dan total momentum dapat kekal pada sistem tertutup.",
        "masses": [2, 1],
        "velocities_before": [3, -1],
        "velocities_after": [1, 3],
        "impulse_latex": r"J = \Delta p = F \Delta t",
        "steps": [
            {"title": "Hitung momentum", "body": "Momentum bergantung pada massa dan kecepatan benda."},
            {"title": "Tumbukan", "body": "Saat benda bertumbukan, gaya bekerja selama selang waktu singkat."},
            {"title": "Impuls dan perubahan", "body": "Impuls sama dengan perubahan momentum benda."},
        ],
        "summary": "Momentum dan impuls menjelaskan perubahan gerak pada tumbukan, pantulan, dan interaksi singkat.",
    },
    106: {
        "id": "sample_coordinate_spatial_106",
        "node_id": "phase3_coordinate_spatial_model",
        "template_id": "manim.coordinate_grid_elementary.v1",
        "phase": "C",
        "audience_level": "sd",
        "language": "id",
        "title": "Koordinat dan Posisi Ruang",
        "subtitle": "Posisi dapat dijelaskan dengan pasangan koordinat dan arah gerak pada grid sederhana.",
        "points": [{"label": "A", "x": 1, "y": 2}, {"label": "B", "x": 4, "y": 3}, {"label": "C", "x": 2, "y": 5}],
        "path": ["A", "B", "C"],
        "steps": [
            {"title": "Baca sumbu", "body": "Sumbu mendatar dan tegak membantu menentukan posisi titik."},
            {"title": "Tentukan koordinat", "body": "Koordinat ditulis sebagai pasangan nilai x dan y."},
            {"title": "Ikuti rute", "body": "Urutan titik dapat membentuk jalur atau pergerakan di bidang."},
        ],
        "summary": "Grid koordinat membantu siswa menjelaskan posisi dan arah secara presisi.",
    },
}


# -----------------------------------------------------------------------------
# Template implementations
# -----------------------------------------------------------------------------

class ChemicalEquilibriumShiftTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[62]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Kesetimbangan dinamis", "Laju reaksi maju dan balik sama, tetapi partikel tetap bereaksi.", color=BLUE))
        eq = _formula(spec["equation_latex"], font_size=32, color=YELLOW).move_to(LEFT * 2.55 + UP * 1.55)
        left = _box(spec.get("left_label", "reaktan"), width=1.65, color=BLUE).move_to(LEFT * 4.25 + DOWN * 0.25)
        right = _box(spec.get("right_label", "produk"), width=1.65, color=GREEN).move_to(LEFT * 1.0 + DOWN * 0.25)
        fwd = Arrow(left.get_right(), right.get_left(), buff=0.10, color=YELLOW)
        rev = Arrow(right.get_left() + DOWN * 0.28, left.get_right() + DOWN * 0.28, buff=0.10, color=YELLOW)
        self.play(FadeIn(eq), FadeIn(left), FadeIn(right), Create(fwd), Create(rev), run_time=0.95)
        stress_boxes = VGroup()
        for item in spec.get("stressors", [])[:3]:
            stress_boxes.add(_box(item["label"], detail=f"geser: {item['shift']}", width=2.0, height=0.62, color=ORANGE, font_size=15))
        stress_boxes.arrange(DOWN, buff=0.12).move_to(RIGHT * 3.85 + DOWN * 0.1)
        shift_arrow = CurvedArrow(left.get_top(), right.get_top(), angle=-0.4, color=ORANGE)
        active_card = self.replace_card(active_card, self.make_card("Gangguan sistem", "Arah pergeseran dipilih agar gangguan menjadi lebih kecil.", color=ORANGE))
        self.play(FadeIn(stress_boxes), Create(shift_arrow), run_time=0.8)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ThermochemistryEnergyProfileTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[63]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Profil energi", "Diagram energi menunjukkan perubahan energi selama reaksi berlangsung.", color=BLUE))
        axes = _axes([0, 5, 1], [0, 7, 1], x_length=5.4, y_length=3.2).move_to(LEFT * 2.65 + DOWN * 0.35)
        re = float(spec.get("reactant_energy", 4))
        pe = float(spec.get("product_energy", 2))
        ae = float(spec.get("activation_energy", 6))
        curve = VMobject(color=YELLOW, stroke_width=4)
        curve.set_points_smoothly([axes.c2p(0.4, re), axes.c2p(1.5, re + 0.4), axes.c2p(2.4, ae), axes.c2p(3.5, pe + 0.6), axes.c2p(4.6, pe)])
        r_line = DashedLine(axes.c2p(0.3, re), axes.c2p(4.8, re), color=BLUE)
        p_line = DashedLine(axes.c2p(0.3, pe), axes.c2p(4.8, pe), color=GREEN)
        dh = DoubleArrow(axes.c2p(4.95, re), axes.c2p(4.95, pe), color=RED, buff=0)
        dh_label = _formula(spec.get("delta_h_label", r"\Delta H"), font_size=24, color=RED).next_to(dh, RIGHT, buff=0.08)
        self.play(Create(axes), Create(curve), FadeIn(r_line), FadeIn(p_line), FadeIn(dh), FadeIn(dh_label), run_time=1.15)
        labels = VGroup(Text("reaktan", font_size=16, color=BLUE).move_to(axes.c2p(0.7, re + 0.35)), Text("produk", font_size=16, color=GREEN).move_to(axes.c2p(4.2, pe + 0.35)), Text("Ea", font_size=18, color=YELLOW).move_to(axes.c2p(2.6, ae + 0.45)))
        self.play(FadeIn(labels), run_time=0.45)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class PatternSequenceGeneralizationConceptTemplate(SequencePatternTemplate):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[64]


class ElementaryShapesIdentificationTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[65]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Ciri bentuk", "Bentuk dikenali dari sisi, sudut, bidang, dan ruang yang ditempati.", color=BLUE))
        tri = Triangle(color=YELLOW, fill_color=YELLOW, fill_opacity=0.18).scale(0.55)
        sq = Square(side_length=1.0, color=GREEN, fill_color=GREEN, fill_opacity=0.18)
        circ = Circle(radius=0.52, color=BLUE, fill_color=BLUE, fill_opacity=0.18)
        flat = VGroup(tri, sq, circ).arrange(RIGHT, buff=0.45).move_to(LEFT * 3.25 + UP * 0.85)
        labels = VGroup(*[Text(s, font_size=15, color=WHITE).next_to(m, DOWN, buff=0.08) for s, m in zip(spec["flat_shapes"], flat)])
        cube_front = Square(side_length=0.72, color=PURPLE, fill_color=PURPLE, fill_opacity=0.15)
        cube_back = cube_front.copy().shift(UP * 0.22 + RIGHT * 0.22)
        cube_edges = VGroup(*[Line(a, b, color=PURPLE) for a, b in zip(
            [cube_front.get_corner(UR), cube_front.get_corner(DR), cube_front.get_corner(UL), cube_front.get_corner(DL)],
            [cube_back.get_corner(UR), cube_back.get_corner(DR), cube_back.get_corner(UL), cube_back.get_corner(DL)]
        )])
        cube = VGroup(cube_back, cube_front, cube_edges).move_to(LEFT * 4.2 + DOWN * 1.25)
        cyl_body = Rectangle(width=0.72, height=0.85, color=ORANGE, fill_color=ORANGE, fill_opacity=0.12)
        cyl_top = Ellipse(width=0.72, height=0.22, color=ORANGE).next_to(cyl_body, UP, buff=-0.11)
        cyl_bottom = Ellipse(width=0.72, height=0.22, color=ORANGE).next_to(cyl_body, DOWN, buff=-0.11)
        cylinder = VGroup(cyl_body, cyl_top, cyl_bottom).move_to(LEFT * 2.9 + DOWN * 1.25)
        sphere = Circle(radius=0.43, color=TEAL, fill_color=TEAL, fill_opacity=0.18).move_to(LEFT * 1.55 + DOWN * 1.25)
        sphere.add(Arc(radius=0.33, angle=TAU, color=TEAL).scale([1, 0.35, 1]).move_to(sphere))
        solids = VGroup(cube, cylinder, sphere)
        solid_labels = VGroup(*[Text(s, font_size=15, color=WHITE).next_to(m, DOWN, buff=0.10) for s, m in zip(spec["solid_shapes"], solids)])
        self.play(FadeIn(flat), FadeIn(labels), FadeIn(solids), FadeIn(solid_labels), run_time=1.0)
        house_base = Square(side_length=0.9, color=GREEN, fill_color=GREEN, fill_opacity=0.2)
        roof = Triangle(color=YELLOW, fill_color=YELLOW, fill_opacity=0.2).scale(0.52).next_to(house_base, UP, buff=0)
        house = VGroup(house_base, roof).move_to(RIGHT * 3.75 + DOWN * 0.15)
        comp = Text(spec.get("composition_label", "komposisi"), font_size=18, color=WHITE).next_to(house, DOWN, buff=0.12)
        active_card = self.replace_card(active_card, self.make_card("Komposisi", "Bangun sederhana dapat disusun menjadi objek yang lebih kompleks.", color=GREEN))
        self.play(FadeIn(house), FadeIn(comp), run_time=0.7)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class AreaVolumeDecompositionTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[66]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Keliling dan luas", "Keliling melihat batas; luas melihat daerah yang tertutup.", color=BLUE))
        w, h = spec.get("rectangle", {}).get("width", 4), spec.get("rectangle", {}).get("height", 3)
        grid = VGroup()
        for i in range(w):
            for j in range(h):
                grid.add(Square(side_length=0.38, color=BLUE, fill_color=BLUE, fill_opacity=0.10).move_to(LEFT * 4.5 + RIGHT * i * 0.39 + UP * (j * 0.39 - 0.4)))
        border = SurroundingRectangle(grid, color=YELLOW, buff=0)
        p_label = Text(f"K = 2({w}+{h})", font_size=18, color=YELLOW).next_to(border, UP, buff=0.10)
        a_label = Text(f"L = {w}×{h}", font_size=18, color=BLUE).next_to(border, DOWN, buff=0.10)
        self.play(FadeIn(grid), Create(border), FadeIn(p_label), FadeIn(a_label), run_time=1.0)
        # Cube net: cross arrangement of six squares.
        coords = [(0, 0), (1, 0), (2, 0), (3, 0), (1, 1), (1, -1)]
        net = VGroup()
        for idx, (x, y) in enumerate(coords):
            sq = Square(side_length=0.55, color=GREEN, fill_color=GREEN, fill_opacity=0.15).move_to(RIGHT * 2.3 + RIGHT * x * 0.56 + UP * y * 0.56 + DOWN * 0.2)
            txt = Text(str(idx + 1), font_size=14, color=GREEN).move_to(sq)
            net.add(VGroup(sq, txt))
        active_card = self.replace_card(active_card, self.make_card("Jaring-jaring", "Jaring-jaring memperlihatkan semua sisi bangun ruang dalam bentuk datar.", color=GREEN))
        self.play(FadeIn(net), run_time=0.75)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class MatrixOperationModelTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[72]

    def _matrix_mob(self, data, color=WHITE):
        entries = [[str(x) for x in row] for row in data]
        mat = Matrix(entries, element_alignment_corner=ORIGIN).scale(0.78)
        mat.set_color(color)
        return mat

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Data tersusun", "Matriks menyimpan angka dalam baris dan kolom.", color=BLUE))
        A = self._matrix_mob(spec["matrix_a"], BLUE).move_to(LEFT * 4.6 + UP * 0.35)
        B = self._matrix_mob(spec["matrix_b"], GREEN).move_to(LEFT * 2.35 + UP * 0.35)
        R = self._matrix_mob(spec["result_matrix"], YELLOW).move_to(RIGHT * 0.05 + UP * 0.35)
        plus = Text("+", font_size=30, color=WHITE).move_to((A.get_right() + B.get_left()) / 2)
        eq = Text("=", font_size=30, color=WHITE).move_to((B.get_right() + R.get_left()) / 2)
        self.play(FadeIn(A), FadeIn(plus), FadeIn(B), FadeIn(eq), FadeIn(R), run_time=0.95)
        row_col = VGroup(_box("baris", width=1.2, color=TEAL), _box("kolom", width=1.2, color=PURPLE)).arrange(RIGHT, buff=0.2).move_to(RIGHT * 3.9 + DOWN * 0.55)
        active_card = self.replace_card(active_card, self.make_card("Elemen bersesuaian", "Penjumlahan dilakukan pada elemen dengan posisi baris dan kolom yang sama.", color=YELLOW))
        self.play(FadeIn(row_col), run_time=0.55)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class GeodesicCoordinateModelTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[73]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Koordinat Bumi", "Lintang dan bujur menentukan posisi pada permukaan Bumi.", color=BLUE))
        globe = Circle(radius=1.65, color=BLUE, fill_color=BLUE, fill_opacity=0.08).move_to(LEFT * 3.0 + DOWN * 0.1)
        meridians = VGroup(*[Ellipse(width=0.35 + i * 0.45, height=3.3, color=BLUE, stroke_opacity=0.45).move_to(globe) for i in range(1, 4)])
        parallels = VGroup(*[Line(globe.get_left() + UP * y, globe.get_right() + UP * y, color=BLUE, stroke_opacity=0.35) for y in [-0.8, 0, 0.8]])
        A = Dot(globe.get_center() + LEFT * 0.75 + UP * 0.45, color=YELLOW)
        B = Dot(globe.get_center() + RIGHT * 0.85 + DOWN * 0.55, color=GREEN)
        arc = ArcBetweenPoints(A.get_center(), B.get_center(), angle=-1.0, color=YELLOW, stroke_width=4)
        labels = VGroup(Text("A", font_size=18, color=YELLOW).next_to(A, UP, buff=0.08), Text("B", font_size=18, color=GREEN).next_to(B, DOWN, buff=0.08), Text(spec.get("arc_label", "jarak permukaan"), font_size=16, color=YELLOW).next_to(arc, RIGHT, buff=0.10))
        self.play(FadeIn(globe), FadeIn(meridians), FadeIn(parallels), FadeIn(A), FadeIn(B), Create(arc), FadeIn(labels), run_time=1.1)
        flat = _box("peta datar", detail="distorsi jarak", width=2.0, color=PURPLE).move_to(RIGHT * 3.7 + DOWN * 0.15)
        self.play(FadeIn(flat), run_time=0.45)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class MotionForcePressureModelTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[75]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Gaya dan gerak", "Gaya dapat mengubah keadaan gerak benda.", color=BLUE))
        block = Rectangle(width=1.2, height=0.75, color=TEAL, fill_color=TEAL, fill_opacity=0.2).move_to(LEFT * 4.2 + UP * 0.8)
        force = Arrow(block.get_left() + LEFT * 1.1, block.get_left(), buff=0.08, color=YELLOW, stroke_width=4)
        motion = Arrow(block.get_right(), block.get_right() + RIGHT * 1.1, buff=0.08, color=GREEN, stroke_width=4)
        self.play(FadeIn(block), Create(force), Create(motion), run_time=0.75)
        base_y = -1.15
        large = Rectangle(width=1.35, height=0.55, color=BLUE, fill_color=BLUE, fill_opacity=0.18).move_to(LEFT * 3.8 + DOWN * 0.85)
        small = Rectangle(width=0.55, height=0.55, color=RED, fill_color=RED, fill_opacity=0.18).move_to(LEFT * 2.0 + DOWN * 0.85)
        arrows = VGroup(Arrow(large.get_top() + UP * 0.55, large.get_top(), buff=0.04, color=YELLOW), Arrow(small.get_top() + UP * 0.55, small.get_top(), buff=0.04, color=YELLOW))
        labels = VGroup(Text("area besar → tekanan kecil", font_size=14, color=BLUE).next_to(large, DOWN, buff=0.08), Text("area kecil → tekanan besar", font_size=14, color=RED).next_to(small, DOWN, buff=0.08))
        active_card = self.replace_card(active_card, self.make_card("Tekanan", "Untuk gaya yang sama, bidang tekan lebih kecil menghasilkan tekanan lebih besar.", color=RED))
        self.play(FadeIn(large), FadeIn(small), FadeIn(arrows), FadeIn(labels), run_time=0.8)
        machines = VGroup(*[_box(m, width=1.45, height=0.55, color=ORANGE, font_size=14) for m in spec.get("machines", [])]).arrange(DOWN, buff=0.10).move_to(RIGHT * 3.9 + DOWN * 0.1)
        self.play(FadeIn(machines), run_time=0.5)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class WorkEnergyPowerModelTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[76]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Usaha", "Usaha terjadi saat gaya menyebabkan perpindahan.", color=BLUE))
        box = Rectangle(width=1.1, height=0.65, color=TEAL, fill_color=TEAL, fill_opacity=0.22).move_to(LEFT * 4.45 + DOWN * 0.25)
        force = Arrow(box.get_left() + LEFT * 1.0, box.get_left(), buff=0.08, color=YELLOW, stroke_width=4)
        path = Arrow(box.get_center(), LEFT * 1.5 + DOWN * 0.25, buff=0.6, color=GREEN, stroke_width=4)
        labels = VGroup(Text(f"F={spec.get('force')} N", font_size=17, color=YELLOW).next_to(force, UP, buff=0.08), Text(f"s={spec.get('distance')} m", font_size=17, color=GREEN).next_to(path, DOWN, buff=0.08))
        self.play(FadeIn(box), Create(force), Create(path), FadeIn(labels), run_time=0.85)
        formulas = VGroup(_formula(spec.get("work_latex"), 26, BLUE), _formula(spec.get("power_latex"), 26, PURPLE)).arrange(DOWN, aligned_edge=LEFT, buff=0.18).move_to(RIGHT * 3.55 + DOWN * 0.1)
        transfer = CurvedArrow(LEFT * 2.5 + UP * 0.9, RIGHT * 2.2 + UP * 0.9, angle=-0.3, color=ORANGE)
        active_card = self.replace_card(active_card, self.make_card("Energi dan daya", "Usaha memindahkan energi, daya mengukur laju perpindahan energi.", color=PURPLE))
        self.play(FadeIn(formulas), Create(transfer), run_time=0.8)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ScalarVectorModelTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[77]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Skalar vs vektor", "Vektor perlu arah, bukan hanya besar.", color=BLUE))
        axes = _axes([-1, 6, 1], [-1, 5, 1], x_length=5.0, y_length=3.2).move_to(LEFT * 2.9 + DOWN * 0.3)
        self.play(Create(axes), run_time=0.55)
        origin = axes.c2p(0, 0)
        arrows = VGroup()
        current = origin
        colors = [YELLOW, GREEN]
        for idx, v in enumerate(spec.get("vectors", [])):
            end = current + (axes.c2p(v["x"], v["y"]) - axes.c2p(0, 0))
            arr = Arrow(current, end, buff=0, color=colors[idx], stroke_width=4)
            label = Text(v["label"], font_size=18, color=colors[idx]).next_to(arr, UP, buff=0.05)
            arrows.add(VGroup(arr, label))
            current = end
        res = spec.get("resultant", {})
        r_arrow = Arrow(origin, axes.c2p(res.get("x", 4), res.get("y", 3)), buff=0, color=RED, stroke_width=5)
        r_label = Text(res.get("label", "R"), font_size=20, color=RED).next_to(r_arrow, RIGHT, buff=0.08)
        self.play(LaggedStart(*[Create(a) for a in arrows], lag_ratio=0.12), run_time=0.75)
        active_card = self.replace_card(active_card, self.make_card("Resultan", "Resultan menyatakan gabungan beberapa vektor sebagai satu panah akhir.", color=RED))
        self.play(Create(r_arrow), FadeIn(r_label), run_time=0.65)
        scalar = _box("skalar", detail="besar saja", width=1.45, color=BLUE).move_to(RIGHT * 3.55 + UP * 0.55)
        vector = _box("vektor", detail="besar + arah", width=1.65, color=RED).next_to(scalar, DOWN, buff=0.18)
        self.play(FadeIn(scalar), FadeIn(vector), run_time=0.45)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ForceNewtonDiagramConceptTemplate(ForceDiagramTemplate):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[78]


class FluidPressureModelTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[79]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Tekanan fluida", "Semakin dalam titik dalam fluida, semakin besar tekanan yang diterima.", color=BLUE))
        tank = RoundedRectangle(width=3.0, height=3.0, corner_radius=0.12, color=BLUE).move_to(LEFT * 3.4 + DOWN * 0.15)
        water = Rectangle(width=2.8, height=2.45, color=BLUE, fill_color=BLUE, fill_opacity=0.18).move_to(tank.get_bottom() + UP * 1.22)
        self.play(FadeIn(tank), FadeIn(water), run_time=0.7)
        depths = spec.get("depths", [1, 2, 3])
        pressures = spec.get("pressure_values", [1, 2, 3])
        arrows = VGroup()
        for i, (d, p) in enumerate(zip(depths, pressures)):
            y = 0.8 - i * 0.75
            point = Dot(LEFT * 3.95 + UP * y, color=YELLOW)
            arr = Arrow(point.get_center(), point.get_center() + RIGHT * (0.35 + 0.25 * p), buff=0.04, color=YELLOW, stroke_width=3)
            label = Text(f"h={d}", font_size=14, color=WHITE).next_to(point, LEFT, buff=0.07)
            arrows.add(VGroup(point, arr, label))
        self.play(LaggedStart(*[FadeIn(a) for a in arrows], lag_ratio=0.10), run_time=0.8)
        formula = _formula(spec.get("formula_latex"), 30, GREEN).move_to(RIGHT * 3.55 + UP * 0.1)
        active_card = self.replace_card(active_card, self.make_card("Rumus", "Tekanan hidrostatis sebanding dengan massa jenis, gravitasi, dan kedalaman.", color=GREEN))
        self.play(FadeIn(formula), run_time=0.45)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ElectromagnetismFieldModelTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[80]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Medan listrik", "Muatan menghasilkan medan listrik yang digambarkan dengan panah keluar atau masuk.", color=BLUE))
        charge = _chip(spec.get("charge_label", "+q"), radius=0.38, color=YELLOW, font_size=18, fill_opacity=0.25).move_to(LEFT * 3.9 + DOWN * 0.05)
        e_arrows = VGroup()
        for ang in np.linspace(0, TAU, 8, endpoint=False):
            start = charge.get_center() + np.array([math.cos(ang), math.sin(ang), 0]) * 0.55
            end = charge.get_center() + np.array([math.cos(ang), math.sin(ang), 0]) * 1.15
            e_arrows.add(Arrow(start, end, buff=0, color=BLUE, stroke_width=2.5))
        self.play(FadeIn(charge), LaggedStart(*[Create(a) for a in e_arrows], lag_ratio=0.04), run_time=0.9)
        wire = Line(RIGHT * 0.4 + DOWN * 1.4, RIGHT * 0.4 + UP * 1.4, color=WHITE, stroke_width=5)
        current = Arrow(RIGHT * 0.4 + DOWN * 1.4, RIGHT * 0.4 + UP * 1.4, buff=0.10, color=YELLOW, stroke_width=3)
        b_loops = VGroup(*[Circle(radius=r, color=PURPLE, stroke_opacity=0.75).move_to(wire.get_center()) for r in [0.45, 0.75, 1.05]])
        active_card = self.replace_card(active_card, self.make_card("Medan magnet", "Arus listrik pada kawat menimbulkan medan magnet melingkar di sekitarnya.", color=PURPLE))
        self.play(Create(wire), Create(current), FadeIn(b_loops), run_time=0.85)
        labels = VGroup(Text("E", font_size=24, color=BLUE).move_to(LEFT * 5.25 + UP * 1.65), Text("B", font_size=24, color=PURPLE).move_to(RIGHT * 1.85 + UP * 1.45), Text(spec.get("current_label", "I"), font_size=22, color=YELLOW).next_to(current, RIGHT, buff=0.08))
        self.play(FadeIn(labels), run_time=0.35)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class InheritanceProbabilityModelTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[87]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Punnett square", "Kotak Punnett menampilkan kemungkinan kombinasi alel dari dua induk.", color=BLUE))
        offspring = spec.get("offspring", [["AA", "Aa"], ["Aa", "aa"]])
        grid = VGroup()
        size = 0.72
        origin = LEFT * 3.7 + DOWN * 0.25
        for r in range(2):
            for c in range(2):
                sq = Square(side_length=size, color=WHITE, fill_color=[GREEN, TEAL, TEAL, RED][r * 2 + c], fill_opacity=0.16).move_to(origin + RIGHT * c * size + DOWN * r * size)
                txt = Text(offspring[r][c], font_size=18, color=WHITE).move_to(sq)
                grid.add(VGroup(sq, txt))
        gametes = spec.get("gametes", [["A", "a"], ["A", "a"]])
        top = VGroup(*[Text(x, font_size=18, color=YELLOW).move_to(origin + RIGHT * i * size + UP * 0.55) for i, x in enumerate(gametes[0])])
        left = VGroup(*[Text(x, font_size=18, color=YELLOW).move_to(origin + LEFT * 0.55 + DOWN * i * size) for i, x in enumerate(gametes[1])])
        self.play(FadeIn(grid), FadeIn(top), FadeIn(left), run_time=0.9)
        ratio = _box(spec.get("ratio", "rasio"), width=2.4, color=GREEN, font_size=17).move_to(RIGHT * 3.6 + DOWN * 0.1)
        active_card = self.replace_card(active_card, self.make_card("Peluang sifat", "Jumlah kotak dengan sifat tertentu dapat diubah menjadi rasio atau peluang.", color=GREEN))
        self.play(FadeIn(ratio), run_time=0.55)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class MeasurementUnitConversionTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[90]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Tangga satuan", "Konversi satuan memakai faktor pengali sesuai jarak antar satuan.", color=BLUE))
        units = spec.get("conversion_chain", [])
        boxes = VGroup(*[_box(u, width=0.85, height=0.58, color=BLUE if i % 2 == 0 else TEAL, font_size=17) for i, u in enumerate(units)]).arrange(RIGHT, buff=0.28).move_to(LEFT * 2.75 + UP * 0.55)
        arrows = VGroup(*[Arrow(boxes[i].get_right(), boxes[i + 1].get_left(), buff=0.08, color=YELLOW) for i in range(len(boxes) - 1)])
        mults = VGroup(*[Text("×10", font_size=15, color=YELLOW).next_to(a, UP, buff=0.06) for a in arrows])
        self.play(FadeIn(boxes), Create(arrows), FadeIn(mults), run_time=0.9)
        example = _box(spec.get("example", "contoh"), width=2.5, color=GREEN, font_size=20).move_to(RIGHT * 3.65 + DOWN * 0.2)
        active_card = self.replace_card(active_card, self.make_card("Contoh", "Dari meter ke centimeter turun dua langkah, jadi dikali 100.", color=GREEN))
        self.play(FadeIn(example), run_time=0.55)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ElementaryFinanceTimelineTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[91]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Catatan uang", "Uang masuk, uang keluar, dan tabungan bisa dicatat sebagai alur sederhana.", color=BLUE))
        items = spec.get("timeline", [])
        nodes = VGroup()
        for i, item in enumerate(items):
            color = GREEN if item.get("amount", 0) >= 0 else RED
            nodes.add(_box(item["label"], detail=f"Rp{abs(item['amount']):,}".replace(",", "."), width=1.65, height=0.82, color=color, font_size=16))
        nodes.arrange(RIGHT, buff=0.35).move_to(LEFT * 2.8 + DOWN * 0.1)
        arrows = VGroup(*[Arrow(nodes[i].get_right(), nodes[i + 1].get_left(), buff=0.08, color=WHITE) for i in range(len(nodes) - 1)])
        self.play(FadeIn(nodes), Create(arrows), run_time=0.9)
        goal = _box("tujuan", detail=spec.get("goal", "tabungan"), width=1.65, color=YELLOW, font_size=16).move_to(RIGHT * 3.9 + DOWN * 0.1)
        active_card = self.replace_card(active_card, self.make_card("Rencana", "Keputusan finansial sederhana membantu mencapai tujuan yang jelas.", color=YELLOW))
        self.play(FadeIn(goal), Create(Arrow(nodes[-1].get_right(), goal.get_left(), buff=0.12, color=YELLOW)), run_time=0.6)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class FactorTreeMultipleGridTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[92]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Faktor", "Faktor adalah pembagi habis suatu bilangan.", color=BLUE))
        root = _chip("12", radius=0.28, color=YELLOW).move_to(LEFT * 4.5 + UP * 1.0)
        c1 = _chip("3", radius=0.23, color=GREEN).move_to(LEFT * 5.0 + UP * 0.2)
        c2 = _chip("4", radius=0.23, color=GREEN).move_to(LEFT * 4.0 + UP * 0.2)
        l1 = _chip("2", radius=0.20, color=TEAL).move_to(LEFT * 4.3 + DOWN * 0.65)
        l2 = _chip("2", radius=0.20, color=TEAL).move_to(LEFT * 3.7 + DOWN * 0.65)
        lines = VGroup(Line(root.get_bottom(), c1.get_top(), color=WHITE), Line(root.get_bottom(), c2.get_top(), color=WHITE), Line(c2.get_bottom(), l1.get_top(), color=WHITE), Line(c2.get_bottom(), l2.get_top(), color=WHITE))
        self.play(FadeIn(root), FadeIn(c1), FadeIn(c2), FadeIn(l1), FadeIn(l2), Create(lines), run_time=0.85)
        rows = VGroup()
        for label, vals in [("12", spec.get("multiples_a", [])), ("18", spec.get("multiples_b", []))]:
            row = VGroup(Text(label, font_size=16, color=YELLOW), *[_box(str(v), width=0.62, height=0.42, color=GREEN if v == spec.get("highlight", {}).get("kpk") else BLUE, font_size=13) for v in vals]).arrange(RIGHT, buff=0.08)
            rows.add(row)
        rows.arrange(DOWN, aligned_edge=LEFT, buff=0.16).move_to(RIGHT * 2.6 + DOWN * 0.1)
        active_card = self.replace_card(active_card, self.make_card("Kelipatan", "KPK adalah kelipatan bersama terkecil yang muncul pada kedua daftar.", color=GREEN))
        self.play(FadeIn(rows), run_time=0.7)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ElementaryGeometryTransformTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[93]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Sudut", "Sudut mengukur besar bukaan dua garis.", color=BLUE))
        p = LEFT * 4.3 + DOWN * 0.5
        ray1 = Line(p, p + RIGHT * 1.5, color=YELLOW, stroke_width=4)
        ray2 = Line(p, p + UP * 1.5, color=YELLOW, stroke_width=4)
        arc = Arc(radius=0.45, start_angle=0, angle=PI / 2, color=YELLOW).move_to(p + RIGHT * 0.23 + UP * 0.23)
        label = Text(f"{spec.get('angle_degrees', 90)}°", font_size=18, color=YELLOW).move_to(p + RIGHT * 0.7 + UP * 0.45)
        self.play(Create(ray1), Create(ray2), Create(arc), FadeIn(label), run_time=0.75)
        shape = Polygon(LEFT * 1.3 + DOWN * 0.6, LEFT * 0.5 + DOWN * 0.6, LEFT * 0.9 + UP * 0.2, color=GREEN, fill_color=GREEN, fill_opacity=0.18)
        mirror_line = DashedLine(LEFT * 0.1 + DOWN * 1.1, LEFT * 0.1 + UP * 1.1, color=WHITE)
        reflected = shape.copy().flip(axis=RIGHT).move_to(RIGHT * 0.7 + DOWN * 0.35).set_color(TEAL)
        active_card = self.replace_card(active_card, self.make_card("Simetri dan transformasi", "Refleksi, rotasi, dan translasi mengubah posisi bentuk dengan aturan tertentu.", color=GREEN))
        self.play(FadeIn(shape), Create(mirror_line), FadeIn(reflected), run_time=0.85)
        badges = VGroup(*[_box(t, width=1.25, height=0.48, color=PURPLE, font_size=13) for t in spec.get("transformations", [])]).arrange(DOWN, buff=0.08).move_to(RIGHT * 3.9 + DOWN * 0.1)
        self.play(FadeIn(badges), run_time=0.45)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class ElementaryProbabilityTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[94]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Bahasa peluang", "Peluang dapat dikenalkan dengan kata pasti, mungkin, dan mustahil.", color=BLUE))
        event_boxes = VGroup()
        colors = {"pasti": GREEN, "mungkin": YELLOW, "mustahil": RED}
        for e in spec.get("events", []):
            event_boxes.add(_box(e["label"], detail=e["chance"], width=2.15, height=0.62, color=colors.get(e["chance"], BLUE), font_size=14))
        event_boxes.arrange(DOWN, buff=0.12).move_to(LEFT * 3.6 + DOWN * 0.15)
        self.play(FadeIn(event_boxes), run_time=0.8)
        exp = spec.get("experiment", {})
        total = exp.get("total", 6)
        success = exp.get("success", 3)
        outcomes = VGroup(*[_chip(str(i + 1), radius=0.20, color=GREEN if i < success else GRAY, font_size=13) for i in range(total)]).arrange(RIGHT, buff=0.12).move_to(RIGHT * 3.0 + UP * 0.35)
        frac = _formula(rf"P=\frac{{{success}}}{{{total}}}", 28, YELLOW).next_to(outcomes, DOWN, buff=0.20)
        active_card = self.replace_card(active_card, self.make_card("Peluang sederhana", "Peluang dihitung dari hasil yang diinginkan dibanding seluruh kemungkinan.", color=YELLOW))
        self.play(FadeIn(outcomes), FadeIn(frac), run_time=0.75)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class RatioScaleProportionConceptTemplate(RatioProportionTemplate):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[95]


class FactorizationDivisibilityTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[97]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Pohon faktor", "Bilangan komposit dapat dipecah menjadi faktor sampai tersisa faktor prima.", color=BLUE))
        levels = spec.get("tree_levels", [])
        groups = VGroup()
        for r, vals in enumerate(levels):
            row = VGroup(*[_chip(str(v), radius=0.26, color=YELLOW if r == 0 else GREEN if r < len(levels) - 1 else TEAL, font_size=15) for v in vals]).arrange(RIGHT, buff=0.42)
            groups.add(row)
        groups.arrange(DOWN, buff=0.52)

        # Branches are measured off the arranged rows, then grouped with them, so
        # the whole tree scales as one piece when it is fitted to the stage.
        lines = VGroup()
        for i in range(len(groups) - 1):
            for a in groups[i]:
                for b in groups[i + 1]:
                    if abs(a.get_center()[0] - b.get_center()[0]) < 0.85:
                        lines.add(Line(a.get_bottom(), b.get_top(), color=WHITE, stroke_opacity=0.45))
        tree = VGroup(groups, lines)

        # The result used to be parked at RIGHT * 3.35, which is inside the card
        # zone -- it rendered on top of the card and spilled past its bottom
        # edge. It belongs under the tree it summarises.
        result = _formula(spec.get("prime_factorization"), 30, YELLOW)
        self.stage_rows(tree, result, buff=0.48)

        self.play(FadeIn(groups), FadeIn(lines), run_time=1.0)
        active_card = self.replace_card(active_card, self.make_card("Faktorisasi prima", "Daun pohon faktor disusun sebagai perkalian faktor prima.", color=YELLOW))
        self.play(FadeIn(result), run_time=0.55)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class GraphFunctionConceptTemplate(GraphExplanationTemplate):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[98]


class SpatialNetModelTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[99]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Dari 3D ke jaring-jaring", "Bangun ruang dapat dibayangkan sebagai permukaan yang dibuka.", color=BLUE))
        cube_front = Square(side_length=1.0, color=BLUE, fill_color=BLUE, fill_opacity=0.14)
        cube_back = cube_front.copy().shift(UP * 0.30 + RIGHT * 0.30)
        cube_edges = VGroup(*[Line(a, b, color=BLUE) for a, b in zip(
            [cube_front.get_corner(UR), cube_front.get_corner(DR), cube_front.get_corner(UL), cube_front.get_corner(DL)],
            [cube_back.get_corner(UR), cube_back.get_corner(DR), cube_back.get_corner(UL), cube_back.get_corner(DL)]
        )])
        cube = VGroup(cube_back, cube_front, cube_edges).move_to(LEFT * 4.0 + DOWN * 0.15)
        cube_label = Text(spec.get("solid", "kubus"), font_size=18, color=BLUE).next_to(cube, DOWN, buff=0.10)
        self.play(FadeIn(cube), FadeIn(cube_label), run_time=0.6)
        coords = spec.get("net_layout", [])
        net = VGroup()
        for i, (x, y) in enumerate(coords):
            sq = Square(side_length=0.52, color=GREEN, fill_color=GREEN, fill_opacity=0.15).move_to(RIGHT * 1.2 + RIGHT * x * 0.54 + UP * y * 0.54 + DOWN * 0.2)
            text = Text(str(i + 1), font_size=13, color=GREEN).move_to(sq)
            net.add(VGroup(sq, text))
        arrow = Arrow(cube.get_right(), net.get_left(), buff=0.2, color=YELLOW)
        active_card = self.replace_card(active_card, self.make_card("Jaring-jaring", "Susunan enam persegi tertentu dapat dilipat kembali menjadi kubus.", color=GREEN))
        self.play(Create(arrow), FadeIn(net), run_time=0.85)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class MeasurementDataProcessTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[100]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Tabel pengukuran", "Data IPA harus dicatat dengan pasangan nilai dan satuan yang jelas.", color=BLUE))
        data = spec.get("measurements", [])
        table_rows = VGroup()
        for d in data:
            table_rows.add(VGroup(_box(str(d["time"]), width=0.65, height=0.42, color=BLUE, font_size=13), _box(str(d["temperature"]), width=0.85, height=0.42, color=TEAL, font_size=13)).arrange(RIGHT, buff=0.06))
        table_rows.arrange(DOWN, buff=0.06).move_to(LEFT * 4.45 + DOWN * 0.1)
        self.play(FadeIn(table_rows), run_time=0.7)
        axes = _axes([0, 4, 1], [20, 50, 10], x_length=3.5, y_length=2.35).move_to(LEFT * 1.4 + DOWN * 0.15)
        points = [axes.c2p(d["time"], d["temperature"]) for d in data]
        dots = VGroup(*[Dot(p, color=YELLOW) for p in points])
        line = VMobject(color=YELLOW, stroke_width=3)
        line.set_points_as_corners(points)
        active_card = self.replace_card(active_card, self.make_card("Grafik data", "Grafik memperlihatkan pola perubahan dari waktu ke waktu.", color=YELLOW))
        self.play(Create(axes), FadeIn(dots), Create(line), run_time=0.9)
        conclusion = _box("kesimpulan", detail="suhu meningkat", width=2.0, color=GREEN, font_size=16).move_to(RIGHT * 3.9 + DOWN * 0.2)
        self.play(FadeIn(conclusion), run_time=0.45)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class MomentumImpulseCollisionTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[102]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Momentum", "Momentum bergantung pada massa dan kecepatan benda.", color=BLUE))
        track = Line(LEFT * 5.2 + DOWN * 0.4, LEFT * 0.9 + DOWN * 0.4, color=WHITE)
        ball1 = Circle(radius=0.32, color=BLUE, fill_color=BLUE, fill_opacity=0.22).move_to(LEFT * 4.6 + DOWN * 0.1)
        ball2 = Circle(radius=0.24, color=GREEN, fill_color=GREEN, fill_opacity=0.22).move_to(LEFT * 1.55 + DOWN * 0.1)
        v1 = Arrow(ball1.get_right(), ball1.get_right() + RIGHT * 0.8, buff=0.05, color=YELLOW)
        v2 = Arrow(ball2.get_left(), ball2.get_left() + LEFT * 0.45, buff=0.05, color=YELLOW)
        self.play(Create(track), FadeIn(ball1), FadeIn(ball2), Create(v1), Create(v2), run_time=0.8)
        collision = Star(n=8, outer_radius=0.26, color=RED, fill_color=RED, fill_opacity=0.75).move_to((ball1.get_center() + ball2.get_center()) / 2)
        active_card = self.replace_card(active_card, self.make_card("Tumbukan", "Pada tumbukan, gaya bekerja besar dalam waktu singkat.", color=RED))
        self.play(FadeIn(collision), run_time=0.45)
        after1 = Arrow(LEFT * 3.1 + UP * 0.8, LEFT * 3.8 + UP * 0.8, buff=0.05, color=BLUE)
        after2 = Arrow(LEFT * 2.2 + UP * 0.8, LEFT * 1.1 + UP * 0.8, buff=0.05, color=GREEN)
        formula = _formula(spec.get("impulse_latex"), 27, YELLOW).move_to(RIGHT * 3.45 + DOWN * 0.15)
        active_card = self.replace_card(active_card, self.make_card("Impuls", "Impuls sama dengan perubahan momentum.", color=YELLOW))
        self.play(Create(after1), Create(after2), FadeIn(formula), run_time=0.75)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


class CoordinateGridElementaryTemplate(WicaraTemplateScene):
    SPEC = TEMPLATE_61_107_MANIM_SPECS[106]

    def construct(self):
        spec = self.SPEC
        self.play(FadeIn(self.make_title_block(spec), shift=DOWN * 0.08), run_time=0.6)
        active_card = self.replace_card(None, self.make_card("Grid koordinat", "Posisi titik dapat dijelaskan dengan pasangan x dan y.", color=BLUE))
        axes = _axes([0, 6, 1], [0, 6, 1], x_length=4.5, y_length=3.0).move_to(LEFT * 2.85 + DOWN * 0.35)
        self.play(Create(axes), run_time=0.65)
        point_lookup = {}
        mobs = VGroup()
        for p in spec.get("points", []):
            dot = Dot(axes.c2p(p["x"], p["y"]), color=YELLOW)
            label = Text(f"{p['label']}({p['x']},{p['y']})", font_size=14, color=YELLOW).next_to(dot, UP, buff=0.06)
            mobs.add(VGroup(dot, label))
            point_lookup[p["label"]] = dot
        self.play(FadeIn(mobs), run_time=0.7)
        path_labels = spec.get("path", [])
        path_lines = VGroup()
        for a, b in zip(path_labels, path_labels[1:]):
            path_lines.add(Arrow(point_lookup[a].get_center(), point_lookup[b].get_center(), buff=0.08, color=GREEN, stroke_width=3))
        active_card = self.replace_card(active_card, self.make_card("Rute", "Urutan titik pada grid dapat membentuk jalur perjalanan.", color=GREEN))
        self.play(Create(path_lines), run_time=0.65)
        active_card = self.render_step_cards(spec, active_card=active_card)
        self.clean_summary(spec, active_card=active_card)


TEMPLATE_61_107_MANIM_REGISTRY = {
    62: {"class_name": "ChemicalEquilibriumShiftTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[62]["template_id"], "status": "new_distinct"},
    63: {"class_name": "ThermochemistryEnergyProfileTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[63]["template_id"], "status": "new_distinct"},
    64: {"class_name": "PatternSequenceGeneralizationConceptTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[64]["template_id"], "status": "wrapper_existing"},
    65: {"class_name": "ElementaryShapesIdentificationTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[65]["template_id"], "status": "new_distinct"},
    66: {"class_name": "AreaVolumeDecompositionTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[66]["template_id"], "status": "new_distinct"},
    72: {"class_name": "MatrixOperationModelTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[72]["template_id"], "status": "new_distinct"},
    73: {"class_name": "GeodesicCoordinateModelTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[73]["template_id"], "status": "new_distinct"},
    75: {"class_name": "MotionForcePressureModelTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[75]["template_id"], "status": "new_distinct"},
    76: {"class_name": "WorkEnergyPowerModelTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[76]["template_id"], "status": "new_distinct"},
    77: {"class_name": "ScalarVectorModelTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[77]["template_id"], "status": "new_distinct"},
    78: {"class_name": "ForceNewtonDiagramConceptTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[78]["template_id"], "status": "wrapper_existing"},
    79: {"class_name": "FluidPressureModelTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[79]["template_id"], "status": "new_distinct"},
    80: {"class_name": "ElectromagnetismFieldModelTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[80]["template_id"], "status": "new_distinct"},
    87: {"class_name": "InheritanceProbabilityModelTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[87]["template_id"], "status": "new_distinct"},
    90: {"class_name": "MeasurementUnitConversionTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[90]["template_id"], "status": "new_distinct"},
    91: {"class_name": "ElementaryFinanceTimelineTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[91]["template_id"], "status": "new_distinct"},
    92: {"class_name": "FactorTreeMultipleGridTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[92]["template_id"], "status": "new_distinct"},
    93: {"class_name": "ElementaryGeometryTransformTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[93]["template_id"], "status": "new_distinct"},
    94: {"class_name": "ElementaryProbabilityTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[94]["template_id"], "status": "new_distinct"},
    95: {"class_name": "RatioScaleProportionConceptTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[95]["template_id"], "status": "wrapper_existing"},
    97: {"class_name": "FactorizationDivisibilityTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[97]["template_id"], "status": "new_distinct"},
    98: {"class_name": "GraphFunctionConceptTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[98]["template_id"], "status": "wrapper_existing"},
    99: {"class_name": "SpatialNetModelTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[99]["template_id"], "status": "new_distinct"},
    100: {"class_name": "MeasurementDataProcessTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[100]["template_id"], "status": "new_distinct"},
    102: {"class_name": "MomentumImpulseCollisionTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[102]["template_id"], "status": "new_distinct"},
    106: {"class_name": "CoordinateGridElementaryTemplate", "template_id": TEMPLATE_61_107_MANIM_SPECS[106]["template_id"], "status": "new_distinct"},
}


__all__ = [
    "TEMPLATE_61_107_MANIM_SPECS",
    "TEMPLATE_61_107_MANIM_REGISTRY",
    *[item["class_name"] for item in TEMPLATE_61_107_MANIM_REGISTRY.values()],
]

# ============================================================
# END PHASE 6: TEMPLATE 61-107 MANIM BUNDLE (MERGED)
# ============================================================


# ============================================================
# OBJECT CONSTRUCTION
# ============================================================


class ObjectConstructionTemplate(WicaraTemplateScene):
    """Assemble a real-world object one part at a time.

    Every other template explains with abstract marks -- a labelled circle, a
    box, an arrow. This one draws the thing itself and builds it up, so the
    "simple to complex" reading comes from watching it assemble rather than from
    a caption saying so.

    The part order is the object's own build order (see wicara_objects), so this
    construct() never has to know whether it is drawing a house or an atom.
    """

    SPEC = {
        "eyebrow": "Membangun Objek",
        "title": "Dari Bentuk Sederhana ke Rumah",
        "subtitle": "Objek yang rumit selalu tersusun dari bentuk-bentuk dasar.",
        "object": "house",
        "part_steps": [
            {"title": "Garis tanah", "body": "Setiap bangunan mulai dari satu garis datar sebagai alas."},
            {"title": "Dinding", "body": "Sebuah persegi panjang menjadi badan rumah."},
            {"title": "Atap", "body": "Segitiga di atas dinding; dua bentuk dasar sudah membentuk rumah."},
            {"title": "Cerobong", "body": "Persegi panjang kecil menambah detail di garis atap."},
            {"title": "Pintu", "body": "Persegi panjang lagi, kali ini di dalam dinding."},
            {"title": "Gagang pintu", "body": "Satu titik kecil; detail terakhir yang membuatnya terbaca."},
            {"title": "Jendela", "body": "Persegi dengan palang membagi bidang dinding."},
            {"title": "Palang jendela", "body": "Dua garis menyilang menyelesaikan bentuknya."},
        ],
        "summary": "Rumah ini hanya persegi, segitiga, garis, dan satu titik.",
    }

    def construct(self):
        spec = self.SPEC
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)

        obj = objects.make_object(spec.get("object", "house"))
        # A single object has a lot of air around it, so it gets a bigger share
        # of the stage than a multi-row figure would.
        self.fit_stage(obj, margin=0.55, max_scale=3.2)
        parts = list(obj.submobjects) or [obj]

        steps = spec.get("part_steps") or []
        counter = Text(
            f"0 / {len(parts)}",
            font_size=theme.FS_CAPTION,
            color=theme.ON_INK_3,
            **theme.font_kwargs("medium"),
        )
        counter.move_to(
            np.array([self.stage_left + 0.55, self.stage_bottom + 0.30, 0.0])
        )
        self.play(FadeIn(counter), run_time=0.25)

        active_card = None
        for index, part in enumerate(parts):
            info = steps[index] if index < len(steps) else None
            if info:
                active_card = self.replace_card(
                    active_card,
                    self.make_card(
                        info.get("title", ""),
                        info.get("body", ""),
                        color=theme.chip(index),
                    ),
                )
            next_counter = Text(
                f"{index + 1} / {len(parts)}",
                font_size=theme.FS_CAPTION,
                color=theme.ON_INK_3,
                **theme.font_kwargs("medium"),
            ).move_to(counter)
            # FadeTransform, not Transform: morphing "3 / 8" into "4 / 8" pairs
            # glyph outlines and renders an unreadable hybrid mid-tween.
            self.play(
                FadeIn(part, shift=UP * 0.14, scale=0.94),
                FadeTransform(counter, next_counter),
                run_time=0.55,
            )
            counter = next_counter

        # One beat with the finished object before the summary sweeps it.
        glow = SurroundingRectangle(
            obj, color=theme.BLUE_ON_INK, buff=0.26, corner_radius=0.18
        )
        glow.set_stroke(width=2.0, opacity=0.55)
        self.play(Create(glow), run_time=0.5)
        self.play(FadeOut(glow), run_time=0.4)

        self.clean_summary(spec, active_card=active_card)


# ============================================================
# PROJECTILE SCENE (physics + math over a drawn world)
# ============================================================


class ProjectileSceneTemplate(WicaraTemplateScene):
    """A drawn world, a person who throws, and the maths laid over the result.

    This is the capability piece: it builds a scene rather than a diagram --
    ground, mountain, sun, cloud, house, tree, a posed human figure -- then
    animates a throw and derives the physics from the arc the viewer just
    watched. The maths is not illustrated beside the picture, it is measured
    on top of it.

    Cards run along the bottom here instead of the right rail, because a scene
    needs the whole width. The stage bounds are class attributes precisely so a
    template can retune them like this.
    """

    STAGE_LEFT = -6.85
    STAGE_RIGHT = 6.85
    STAGE_TOP = 1.55
    STAGE_BOTTOM = -2.30

    GROUND_Y = -1.62

    SPEC = {
        "eyebrow": "Fisika - Gerak Parabola",
        "title": "Lemparan yang Menjadi Persamaan",
        "subtitle": "Lintasan benda yang dilempar selalu berbentuk parabola.",
        "summary": "Satu lemparan, satu parabola: tinggi dan jangkauan keduanya jatuh dari persamaan yang sama.",
    }

    # -- scene helpers -------------------------------------------------

    def _place_on_ground(self, mobject, x, width=None):
        if width is not None:
            mobject.scale_to_fit_width(width)
        mobject.shift(
            RIGHT * (x - mobject.get_center()[0])
            + UP * (self.GROUND_Y - mobject.get_bottom()[1])
        )
        return mobject

    def _trajectory(self, start, end, arc):
        """Quadratic through start and end with `arc` of lift at the midpoint."""

        def point(t):
            x = start[0] + (end[0] - start[0]) * t
            y = start[1] + (end[1] - start[1]) * t + arc * 4 * t * (1 - t)
            return np.array([x, y, 0.0])

        return point

    # -- construct -----------------------------------------------------

    def construct(self):
        spec = self.SPEC
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=0.6)

        # ---- the world, composed relative to its own ground line ----------
        # Built at a nominal size and then fitted, rather than typed at frame
        # coordinates, so the same scene composes on 16:9, 1:1 and 9:16.
        # A panorama does not survive being squeezed into a 9:16 frame -- at
        # that aspect the stage is under four units wide, and a 13.7-unit world
        # shrinks to a quarter size. Portrait gets a shorter world with the
        # background scenery dropped, not the same world made tiny.
        wide = not self._portrait
        span = (self.NOMINAL_W if wide else 7.4) / 2
        ground = Line(np.array([-span, 0.0, 0.0]), np.array([span, 0.0, 0.0]))
        ground.set_stroke(color=theme.RULE, width=3.0)

        def stand(mobject, x, width):
            mobject.scale_to_fit_width(width)
            mobject.shift(
                RIGHT * (x - mobject.get_center()[0])
                + UP * (0.0 - mobject.get_bottom()[1])
            )
            return mobject

        if wide:
            scenery = [
                stand(objects.mountain(), -5.55, 2.55),
                stand(objects.house(), 5.85, 1.85),
                stand(objects.tree(), 4.30, 1.25),
                objects.sun().scale_to_fit_width(1.00).move_to(np.array([6.05, 2.76, 0.0])),
                objects.cloud().scale_to_fit_width(1.40).move_to(np.array([2.55, 2.82, 0.0])),
            ]
            thrower_x, land_at = -3.75, 0.686
        else:
            scenery = [
                stand(objects.house(), 3.05, 1.35),
                stand(objects.tree(), 1.95, 0.95),
                objects.sun().scale_to_fit_width(0.85).move_to(np.array([3.05, 2.60, 0.0])),
            ]
            thrower_x, land_at = -2.55, 0.70

        def stand_figure(pose):
            fig = objects.figure(pose=pose, height=1.62)
            fig.shift(
                RIGHT * (thrower_x - fig.get_center()[0])
                + UP * (0.0 - fig.get_bottom()[1])
            )
            return fig

        thrower = stand_figure("wind_up")
        launched = stand_figure("throw")

        world = VGroup(ground, *scenery, thrower, launched)
        self.fit_stage(world, margin=0.28)
        # One factor derived from the fit, used for every length below.
        k = ground.get_length() / (span * 2)

        card = self.replace_card(
            None,
            self.make_card(
                "Adegan",
                "Sebuah dunia sederhana: tanah, gunung, rumah, dan pohon.",
                color=theme.chip(0),
            ),
            zone=self.card_zone(),
        )
        self.play(Create(ground), run_time=self.beat(0.5))
        self.play(
            LaggedStart(
                *[FadeIn(item, shift=UP * 0.16) for item in scenery],
                lag_ratio=0.26,
            ),
            run_time=self.beat(1.8),
        )

        card = self.replace_card(
            card,
            self.make_card(
                "Pelempar",
                "Rangka sendi yang sama dapat dipasang dalam berbagai pose.",
                color=theme.chip(2),
            ),
            zone=self.card_zone(),
        )
        self.play(FadeIn(thrower, shift=UP * 0.14), run_time=self.beat(0.7))

        # ---- the throw ----------------------------------------------------
        launch_point = objects.hand_of(launched)
        landing = ground.point_from_proportion(land_at)
        traj = self._trajectory(launch_point, landing, arc=1.62 * k)
        path = ParametricFunction(traj, t_range=[0, 1, 0.01])
        path.set_stroke(color=theme.GOLD, width=3.0, opacity=0.9)

        projectile = objects.ball().scale_to_fit_width(0.30 * k)
        projectile.move_to(launch_point)

        card = self.replace_card(
            card,
            self.make_card(
                "Lemparan",
                "Benda dilepas dengan kecepatan awal pada sudut tertentu.",
                color=theme.GOLD,
            ),
            zone=self.card_zone(),
        )
        self.play(
            FadeTransform(thrower, launched),
            FadeIn(projectile, scale=0.6),
            run_time=0.5,
        )
        self.play(
            MoveAlongPath(projectile, path),
            Create(path),
            run_time=self.beat(1.9),
            rate_func=linear,
        )

        # ---- the maths, measured on the arc -------------------------------
        apex = traj(0.5)
        ground_y = ground.get_center()[1]
        apex_dot = Dot(apex, color=theme.BLUE_ON_INK, radius=0.06)
        height_line = DashedLine(
            np.array([apex[0], ground_y, 0.0]), apex, dash_length=0.10
        )
        height_line.set_stroke(color=theme.BLUE_ON_INK, width=2.0, opacity=0.8)
        height_label = MathTex("h_{maks}", font_size=30, color=theme.BLUE_ON_INK)
        # On the compressed portrait world the apex sits close to the thrower, so
        # the label goes on the far side of the drop-line to clear the velocity
        # arrows rather than landing on top of them.
        height_label.next_to(height_line, RIGHT if self._portrait else LEFT, buff=0.16)

        range_arrow = DoubleArrow(
            np.array([launch_point[0], ground_y - 0.34 * k, 0.0]),
            np.array([landing[0], ground_y - 0.34 * k, 0.0]),
            buff=0, color=theme.GOOD, stroke_width=3, tip_length=0.18 * k,
        )
        range_label = MathTex("R", font_size=30, color=theme.GOOD)
        range_label.next_to(range_arrow, DOWN, buff=0.10)

        card = self.replace_card(
            card,
            self.make_card(
                "Ukur lintasannya",
                "Tinggi maksimum dan jangkauan terbaca langsung dari parabola.",
                color=theme.BLUE_ON_INK,
            ),
            zone=self.card_zone(),
        )
        self.play(
            FadeIn(apex_dot, scale=0.5),
            Create(height_line),
            FadeIn(height_label, shift=RIGHT * 0.10),
            run_time=0.7,
        )
        self.play(
            Create(range_arrow), FadeIn(range_label, shift=UP * 0.10), run_time=0.6
        )

        # ---- launch velocity, decomposed ----------------------------------
        v_tip = launch_point + np.array([1.05 * k, 0.92 * k, 0.0])
        v0 = Arrow(launch_point, v_tip, buff=0, color=theme.chip(4), stroke_width=3.5, tip_length=0.20 * k)
        vx = Arrow(launch_point, launch_point + np.array([1.05 * k, 0.0, 0.0]),
                   buff=0, color=theme.GOOD, stroke_width=2.6, tip_length=0.16 * k)
        vy = Arrow(launch_point, launch_point + np.array([0.0, 0.92 * k, 0.0]),
                   buff=0, color=theme.chip(1), stroke_width=2.6, tip_length=0.16 * k)
        v0_label = MathTex("v_0", font_size=26, color=theme.chip(4)).next_to(v_tip, UR, buff=0.04)
        vx_label = MathTex("v_x", font_size=22, color=theme.GOOD).next_to(vx, DOWN, buff=0.06)
        vy_label = MathTex("v_y", font_size=22, color=theme.chip(1)).next_to(vy, LEFT, buff=0.06)

        equation = MathTex(
            r"y = x\tan\theta - \frac{g\,x^{2}}{2v_0^{2}\cos^{2}\theta}",
            font_size=31,
            color=theme.GOLD,
        )
        # Anchored to the stage rather than to a typed coordinate, and dropped
        # under the title on a narrow frame where there is no room beside it.
        if self._portrait:
            equation.scale_to_fit_width(min(equation.width, config.frame_width - 1.0))
            equation.move_to(np.array([0.0, self.stage_top + 0.42, 0.0]))
        else:
            equation.move_to(np.array([self.stage_left + 1.95, self.stage_top + 0.05, 0.0]))

        card = self.replace_card(
            card,
            self.make_card(
                "Dari gambar ke persamaan",
                "Kecepatan awal diuraikan menjadi komponen mendatar dan tegak.",
                color=theme.chip(4),
            ),
            zone=self.card_zone(),
        )
        self.play(
            LaggedStart(
                AnimationGroup(GrowArrow(v0), FadeIn(v0_label)),
                AnimationGroup(GrowArrow(vx), FadeIn(vx_label)),
                AnimationGroup(GrowArrow(vy), FadeIn(vy_label)),
                lag_ratio=0.35,
            ),
            run_time=1.3,
        )
        self.play(Write(equation), run_time=self.beat(1.1))
        self.wait(self.hold_for(spec.get("summary")))

        self.clean_summary(spec, active_card=card)


# ============================================================
# COMPLEX VISUALS
# ============================================================


class FourierEpicyclesTemplate(WicaraTemplateScene):
    """Rotating circles that draw an arbitrary shape.

    The coefficients are a real discrete Fourier transform of the target
    outline, not a scripted animation: the path is sampled, transformed, the
    terms sorted by amplitude, and the top N rebuilt as epicycles. Whatever
    shape the spec names is what the circles will draw.

    This is the "why would a sum of waves matter" lesson that a line graph
    cannot make. It is also the single most watchable thing in the pack.
    """

    SPEC = {
        "eyebrow": "Matematika - Deret Fourier",
        "title": "Lingkaran yang Menggambar Bentuk",
        "subtitle": "Setiap bentuk tertutup dapat disusun dari putaran-putaran sederhana.",
        "audience_level": "sma",
        "trace_text": "W",
        "terms": 64,
        "cycles": 2,
        "steps": [
            {"title": "Ambil lintasannya", "body": "Bentuk apa pun dapat dibaca sebagai satu lintasan tertutup."},
            {"title": "Uraikan jadi putaran", "body": "Transformasi Fourier memecah lintasan itu menjadi putaran dengan jari-jari dan kecepatan tertentu."},
            {"title": "Susun bertingkat", "body": "Setiap lingkaran berputar di ujung lingkaran sebelumnya."},
            {"title": "Ujung pena menggambar", "body": "Titik terakhir menelusuri kembali bentuk semula."},
        ],
        "summary": "Bentuk rumit hanyalah jumlah dari putaran-putaran sederhana.",
    }

    def _target_path(self, spec):
        """The outline the circles have to reproduce."""
        raw = str(spec.get("trace_text", "W"))[:1] or "W"
        glyph = Text(raw, font_size=200, **theme.font_kwargs("extrabold"))
        # A glyph with an interior counter traverses as several subpaths, and the
        # jumps between them show up as straight scars in the trace. Keep the
        # single longest contour.
        pieces = [m for m in glyph.family_members_with_points() if len(m.points) > 4]
        if not pieces:
            return Circle(radius=1.5)
        return max(pieces, key=lambda m: m.get_arc_length())

    def _coefficients(self, path, terms, samples=480):
        """Complex DFT of the path, strongest terms first."""
        pts = np.array(
            [path.point_from_proportion(i / samples) for i in range(samples)]
        )
        centre = pts.mean(axis=0)
        z = (pts[:, 0] - centre[0]) + 1j * (pts[:, 1] - centre[1])

        n = np.arange(samples)
        coeffs = []
        # Frequencies interleaved outward from zero: 0, +1, -1, +2, -2 ...
        freqs = [0]
        k = 1
        while len(freqs) < terms:
            freqs.extend([k, -k])
            k += 1
        for f in freqs[:terms]:
            c = np.sum(z * np.exp(-2j * np.pi * f * n / samples)) / samples
            coeffs.append((f, c))
        coeffs.sort(key=lambda fc: -abs(fc[1]))
        return coeffs

    def construct(self):
        spec = self.SPEC
        title_block = self.make_title_block(spec)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=self.beat(0.6))

        terms = int(spec.get("terms", 64))
        path = self._target_path(spec)
        coeffs = self._coefficients(path, terms)

        centre, box_w, box_h = self.stage_box()
        target_h = min(box_w, box_h) * 0.62

        # Size from the extent the reconstruction actually reaches, not from the
        # sum of the amplitudes. That sum is the worst case -- every circle
        # pointing the same way at once, which never happens -- and using it drew
        # the shape at a fifth of the space it had.
        probe = []
        for i in range(240):
            t = i / 240.0
            x = y = 0.0
            for freq, c in coeffs:
                angle = np.angle(c) + 2 * np.pi * freq * t
                x += abs(c) * np.cos(angle)
                y += abs(c) * np.sin(angle)
            probe.append((x, y))
        span_y = max(p[1] for p in probe) - min(p[1] for p in probe)
        span_x = max(p[0] for p in probe) - min(p[0] for p in probe)
        unit = min(
            target_h / max(span_y, 1e-6),
            (box_w * 0.72) / max(span_x, 1e-6),
        )

        card = self.replace_card(
            None,
            self.make_card(
                "Ambil lintasannya",
                "Bentuk apa pun dapat dibaca sebagai satu lintasan tertutup.",
                color=theme.chip(0),
            ),
            zone=self.card_zone(),
        )

        ghost = path.copy()
        ghost.set_stroke(color=theme.RULE, width=2.0, opacity=0.55).set_fill(opacity=0)
        ghost.scale_to_fit_height(target_h).move_to(centre)
        self.play(Create(ghost), run_time=self.beat(1.2))

        time = ValueTracker(0.0)

        def tip_at(t):
            """Just the vector sum. No mobjects -- this runs per frame, three
            times over, and building 64 circles to find one point was the
            difference between a render and a stall."""
            x = y = 0.0
            for freq, c in coeffs:
                angle = np.angle(c) + 2 * np.pi * freq * t
                x += abs(c) * np.cos(angle)
                y += abs(c) * np.sin(angle)
            return centre + np.array([x * unit, y * unit, 0.0])

        def epicycle_chain():
            """Circles and spokes rebuilt from the tracker each frame."""
            group = VGroup()
            tip = centre.copy()
            t = time.get_value()
            for index, (freq, c) in enumerate(coeffs):
                radius = abs(c) * unit
                angle = np.angle(c) + 2 * np.pi * freq * t
                nxt = tip + np.array(
                    [radius * np.cos(angle), radius * np.sin(angle), 0.0]
                )
                if radius > 0.012:
                    ring = Circle(radius=radius, arc_center=tip)
                    ring.set_stroke(
                        color=theme.BLUE_ON_INK,
                        width=1.3,
                        opacity=max(0.10, 0.62 - index * 0.012),
                    )
                    spoke = Line(tip, nxt)
                    spoke.set_stroke(
                        color=theme.ON_INK_3,
                        width=1.1,
                        opacity=max(0.10, 0.55 - index * 0.011),
                    )
                    group.add(ring, spoke)
                tip = nxt
            return group

        chain = always_redraw(epicycle_chain)
        pen = always_redraw(
            lambda: Dot(tip_at(time.get_value()), radius=0.055, color=theme.GOLD)
        )
        trace = TracedPath(
            lambda: tip_at(time.get_value()),
            stroke_color=theme.GOLD,
            stroke_width=4.4,
        )

        card = self.replace_card(
            card,
            self.make_card(
                "Uraikan jadi putaran",
                f"{len(coeffs)} putaran, masing-masing dengan jari-jari dan kecepatan sendiri.",
                color=theme.chip(2),
            ),
            zone=self.card_zone(),
        )
        self.add(chain, trace, pen)
        self.play(FadeOut(ghost), run_time=self.beat(0.4))

        card = self.replace_card(
            card,
            self.make_card(
                "Ujung pena menggambar",
                "Titik terakhir menelusuri kembali bentuk semula.",
                color=theme.GOLD,
            ),
            zone=self.card_zone(),
        )

        cycles = int(spec.get("cycles", 2))
        self.play(
            time.animate.set_value(float(cycles)),
            run_time=self.beat(5.4) * cycles,
            rate_func=linear,
        )

        self.remove(chain, pen)
        self.wait(self.hold_for(spec.get("summary")))
        self.clean_summary(spec, active_card=card)


class LinearTransformTemplate(WicaraTemplateScene):
    """A matrix bending the plane it acts on.

    Shows what a 2x2 matrix *is* rather than how to multiply one: the whole
    grid deforms, the basis vectors land on the matrix columns, the unit square
    becomes a parallelogram whose area is the determinant, and the eigenvectors
    are the only directions that merely stretch instead of turning.

    All of it is computed from the spec's matrix -- eigenvectors included -- so
    a different matrix gives a genuinely different animation.
    """

    SPEC = {
        "eyebrow": "Matematika - Aljabar Linear",
        "title": "Matriks Menekuk Ruang",
        "subtitle": "Sebuah matriks memindahkan seluruh bidang, bukan hanya satu titik.",
        "audience_level": "sma",
        "matrix": [[2.0, 1.0], [0.5, 1.5]],
        "steps": [
            {"title": "Bidang utuh ikut bergerak", "body": "Setiap garis grid dipetakan ke tempat baru, tetapi tetap lurus dan tetap sejajar."},
            {"title": "Kolom matriks adalah tujuan", "body": "Vektor basis i dan j mendarat tepat pada kolom pertama dan kedua matriks."},
            {"title": "Determinan adalah luas", "body": "Persegi satuan menjadi jajaran genjang; luasnya sama dengan nilai determinan."},
            {"title": "Eigenvektor tidak berbelok", "body": "Hanya arah-arah ini yang bertahan pada garisnya sendiri, sekadar memanjang."},
        ],
        "summary": "Matriks adalah aturan yang memindahkan seluruh ruang sekaligus.",
    }

    def construct(self):
        spec = self.SPEC
        title_block = self.make_title_block(spec)
        title_block.set_z_index(2)
        self.play(FadeIn(title_block, shift=DOWN * 0.08), run_time=self.beat(0.6))

        m = np.array(spec.get("matrix", [[2.0, 1.0], [0.5, 1.5]]), dtype=float)
        centre, box_w, box_h = self.stage_box()
        # A transform that stretches by 2.5x will push a full-size grid straight
        # off the stage and under the card, so the plane is pre-shrunk by the
        # matrix's largest singular value -- how much it can stretch anything.
        stretch = float(np.linalg.svd(m, compute_uv=False)[0])
        # A strong transform is *meant* to push the grid past the frame -- that
        # overflow is the effect. Pre-shrinking only mildly keeps the starting
        # grid readable without making the result timid.
        unit = min(box_w * 0.98 / 8.0, box_h * 0.98 / 6.0) / max(1.0, stretch * 0.42)

        plane = NumberPlane(
            x_range=[-4, 4, 1],
            y_range=[-3, 3, 1],
            x_length=8 * unit,
            y_length=6 * unit,
            # theme.GRID is the faint texture that sits *behind* a figure. Here
            # the grid IS the figure -- watching it bend is the whole lesson --
            # so it is lifted to a foreground weight.
            background_line_style={
                "stroke_color": theme.lift(theme.RULE, 0.28),
                "stroke_width": 1.8,
                "stroke_opacity": 0.85,
            },
            axis_config={
                "stroke_color": theme.ON_INK_2,
                "stroke_width": 2.8,
            },
        )
        plane.move_to(centre)
        plane.set_z_index(-10)
        origin = plane.c2p(0, 0)

        # The card is created before the plane, so without an explicit order the
        # grid draws straight over the text. A scrim holds the card's zone.
        if self.card_zone() == "bottom":
            scrim = Rectangle(width=config.frame_width, height=2.55)
            scrim.to_edge(DOWN, buff=0.0)
        else:
            scrim = Rectangle(width=config.frame_width / 2 - 1.30,
                              height=config.frame_height)
            scrim.to_edge(RIGHT, buff=0.0)
        scrim.set_fill(color=theme.INK, opacity=0.90).set_stroke(width=0)
        top_scrim = Rectangle(width=config.frame_width, height=2.05)
        top_scrim.to_edge(UP, buff=0.0)
        top_scrim.set_fill(color=theme.INK, opacity=0.90).set_stroke(width=0)
        # Above the plane and the eigen spans, below the cards and the title.
        for band in (scrim, top_scrim):
            band.set_z_index(-2)
            self.add(band)

        def vec(x, y, color):
            arrow = Arrow(origin, plane.c2p(x, y), buff=0, color=color,
                          stroke_width=5, tip_length=0.22 * unit)
            return arrow

        i_vec = vec(1, 0, theme.GOLD)
        j_vec = vec(0, 1, theme.GOOD)
        unit_square = Polygon(
            plane.c2p(0, 0), plane.c2p(1, 0), plane.c2p(1, 1), plane.c2p(0, 1),
        )
        unit_square.set_fill(color=theme.BLUE_ON_INK, opacity=0.22)
        unit_square.set_stroke(color=theme.BLUE_ON_INK, width=2.0)

        card = self.replace_card(
            None,
            self.make_card(
                "Bidang utuh ikut bergerak",
                "Setiap garis grid dipetakan ke tempat baru, tetapi tetap lurus dan tetap sejajar.",
                color=theme.chip(0),
            ),
            zone=self.card_zone(),
        )
        self.play(Create(plane), run_time=self.beat(1.0))
        self.play(
            FadeIn(unit_square),
            GrowArrow(i_vec),
            GrowArrow(j_vec),
            run_time=self.beat(0.7),
        )

        matrix_tex = MathTex(
            r"\begin{bmatrix} %.3g & %.3g \\ %.3g & %.3g \end{bmatrix}"
            % (m[0][0], m[0][1], m[1][0], m[1][1]),
            font_size=34,
            color=theme.ON_INK,
        )
        det = float(np.linalg.det(m))
        det_tex = MathTex(r"\det = %.2f" % det, font_size=30, color=theme.BLUE_ON_INK)
        readout = VGroup(matrix_tex, det_tex).arrange(DOWN, buff=0.22)
        if self._portrait:
            readout.move_to(np.array([0.0, self.stage_top + 0.55, 0.0]))
        else:
            readout.move_to(
                np.array([self.stage_left + 0.95, self.stage_top - 0.55, 0.0])
            )
        readout.set_z_index(2)
        self.play(FadeIn(readout, shift=DOWN * 0.10), run_time=self.beat(0.5))

        card = self.replace_card(
            card,
            self.make_card(
                "Kolom matriks adalah tujuan",
                "Vektor basis i dan j mendarat tepat pada kolom pertama dan kedua matriks.",
                color=theme.GOLD,
            ),
            zone=self.card_zone(),
        )

        # The transform itself, applied about the plane's own origin.
        self.play(
            ApplyMatrix(m, plane, about_point=origin),
            ApplyMatrix(m, i_vec, about_point=origin),
            ApplyMatrix(m, j_vec, about_point=origin),
            ApplyMatrix(m, unit_square, about_point=origin),
            run_time=self.beat(2.4),
            rate_func=smooth,
        )

        card = self.replace_card(
            card,
            self.make_card(
                "Determinan adalah luas",
                "Persegi satuan menjadi jajaran genjang; luasnya sama dengan nilai determinan.",
                color=theme.BLUE_ON_INK,
            ),
            zone=self.card_zone(),
        )
        area_flash = unit_square.copy().set_fill(color=theme.GOLD, opacity=0.45)
        self.play(FadeIn(area_flash), run_time=self.beat(0.35))
        self.play(FadeOut(area_flash), run_time=self.beat(0.45))

        # Eigenvectors: the directions this matrix only stretches.
        values, vectors = np.linalg.eig(m)
        if np.all(np.isreal(values)):
            card = self.replace_card(
                card,
                self.make_card(
                    "Eigenvektor tidak berbelok",
                    "Hanya arah-arah ini yang bertahan pada garisnya sendiri, sekadar memanjang.",
                    color=theme.chip(4),
                ),
                zone=self.card_zone(),
            )
            spans = VGroup()
            labels = VGroup()
            for idx in range(vectors.shape[1]):
                v = np.real(vectors[:, idx])
                norm = np.linalg.norm(v)
                if norm < 1e-9:
                    continue
                v = v / norm
                far = 3.6
                # Measured off the original basis, not via plane.c2p: the plane
                # has already been deformed by this point, so its coordinate
                # mapping no longer describes the space the eigenvectors are
                # stated in.
                line = Line(
                    origin - np.array([v[0], v[1], 0.0]) * far * unit,
                    origin + np.array([v[0], v[1], 0.0]) * far * unit,
                )
                line.set_stroke(color=theme.chip(4 - idx), width=2.6, opacity=0.9)
                line.set_z_index(-8)
                spans.add(line)
                tag = MathTex(r"\lambda = %.2f" % float(np.real(values[idx])),
                              font_size=24, color=theme.chip(4 - idx))
                # Pushed out along its own span and offset perpendicular, so the
                # label never lands on the parallelogram it is describing.
                # An eigen-span has two ends; put the label on whichever one
                # points away from the matrix readout in the top-left, so the
                # two never share the same patch of frame.
                direction = np.array([v[0], v[1], 0.0])
                if direction[0] < 0 or (abs(direction[0]) < 1e-6 and direction[1] > 0):
                    direction = -direction
                perp = np.array([-direction[1], direction[0], 0.0])
                tag.move_to(
                    origin + direction * (far * 0.88) * unit + perp * 0.34
                )
                tag.set_z_index(1)
                labels.add(tag)
            self.play(
                LaggedStart(*[Create(l) for l in spans], lag_ratio=0.3),
                run_time=self.beat(1.0),
            )
            self.play(FadeIn(labels), run_time=self.beat(0.5))

        self.wait(self.hold_for(spec.get("summary")))
        self.clean_summary(spec, active_card=card)


class MotionCompositionTemplate(WicaraTemplateScene):
    """A composition built the Remotion way, rendered by Manim.

    The timeline is declared as a Series of named segments with durations, not
    accumulated by hand; every entrance rides a spring rather than an easing
    curve; the counters are driven through interpolate(); and the segments are
    joined by a branded panel wipe.

    It doubles as the reference for how to use wicara_motion, which is why the
    segments are named after what they demonstrate.
    """

    SPEC = {
        "eyebrow": "Motion System",
        "title": "Gerak yang Terasa Nyata",
        "subtitle": "Pegas, timeline, dan transisi -- disusun sebagai data, bukan urutan perintah.",
        "audience_level": "smp",
        "fps": 30,
        "metrics": [
            {"label": "Template", "value": 80, "suffix": ""},
            {"label": "Objek", "value": 17, "suffix": ""},
            {"label": "Palet", "value": 6, "suffix": ""},
        ],
        "steps": [
            {"title": "Pegas, bukan kurva", "body": "Setiap elemen masuk dengan fisika peredam-pegas, sehingga sedikit melewati sasaran lalu mapan."},
            {"title": "Timeline sebagai data", "body": "Segmen dideklarasikan beserta durasinya; urutannya dihitung, bukan dicatat manual."},
            {"title": "Angka yang menghitung", "body": "Nilai dipetakan dari rentang ke rentang, lalu berhenti tepat di batasnya."},
        ],
        "summary": "Gerak yang baik membuat penjelasan terasa hidup tanpa mengalihkan perhatian.",
    }

    def construct(self):
        spec = self.SPEC
        fps = float(spec.get("fps", 30))
        title_block = self.make_title_block(spec)
        self.play(
            FadeIn(title_block, shift=DOWN * 0.34),
            run_time=motion.spring_seconds("gentle", fps),
            rate_func=motion.spring_rate("gentle", fps),
        )

        centre, box_w, box_h = self.stage_box()
        series = motion.Series(fps=fps)

        # -- segment 1: springs side by side ------------------------------
        def springs(scene, seg):
            scene._motion_card = scene.replace_card(
                getattr(scene, "_motion_card", None),
                scene.make_card(
                    "Pegas, bukan kurva",
                    "Setiap elemen masuk dengan fisika peredam-pegas, sehingga sedikit melewati sasaran lalu mapan.",
                    color=theme.chip(0),
                ),
                zone=scene.card_zone(),
            )
            names = ["stiff", "gentle", "snappy", "bouncy"]
            chips = VGroup()
            for index, name in enumerate(names):
                pill = theme.panel(width=1.62, height=0.86)
                label = Text(
                    name,
                    font_size=theme.FS_LABEL,
                    color=theme.chip(index),
                    **theme.font_kwargs("semibold"),
                )
                label.move_to(pill.get_center())
                chips.add(VGroup(pill, label))
            chips.arrange(DOWN, buff=0.24)
            scene.fit_stage(chips, margin=0.5, max_scale=1.35)

            # Same distance, same moment, four different springs -- the point is
            # that they arrive differently.
            scene.play(
                *[
                    FadeIn(
                        chip,
                        shift=RIGHT * 1.30,
                        rate_func=motion.spring_rate(name, fps),
                        run_time=motion.spring_seconds(name, fps),
                    )
                    for chip, name in zip(chips, names)
                ]
            )
            scene.wait(0.4)
            seg.meta["mobjects"] = chips

        # -- segment 2: interpolate driving counters ----------------------
        def counters(scene, seg):
            scene._motion_card = scene.replace_card(
                getattr(scene, "_motion_card", None),
                scene.make_card(
                    "Angka yang menghitung",
                    "Nilai dipetakan dari rentang ke rentang, lalu berhenti tepat di batasnya.",
                    color=theme.GOLD,
                ),
                zone=scene.card_zone(),
            )

            metrics = spec.get("metrics", [])
            tracker = ValueTracker(0.0)

            def numeral(text, index):
                return Text(
                    text,
                    font_size=theme.FS_METRIC,
                    color=theme.chip(index),
                    **theme.font_kwargs("extrabold"),
                )

            # Lay the finished values out first, so the columns are spaced for
            # the widest numeral each will ever show.
            columns = VGroup()
            for index, metric in enumerate(metrics):
                caption = Text(
                    str(metric.get("label", "")),
                    font_size=theme.FS_CAPTION,
                    color=theme.ON_INK_3,
                    **theme.font_kwargs("medium"),
                )
                column = VGroup(
                    numeral(str(int(metric.get("value", 0))), index), caption
                ).arrange(DOWN, buff=0.16)
                columns.add(column)
            columns.arrange(RIGHT, buff=1.05)
            scene.fit_stage(columns, margin=0.6, max_scale=1.5)

            # always_redraw rebuilds its mobject every frame, and a fresh Text is
            # born at the origin -- the arrangement above is not inherited. Each
            # counter has to be pinned to the anchor its column settled on, or
            # all three stack in the middle of the frame.
            live = VGroup()
            for index, (metric, column) in enumerate(zip(metrics, columns)):
                target = float(metric.get("value", 0))
                anchor = column[0].get_center()
                scale = column[0].height / max(numeral("0", index).height, 1e-6)
                column.remove(column[0])
                live.add(
                    always_redraw(
                        lambda t=target, i=index, a=anchor, sc=scale: numeral(
                            str(int(round(motion.interpolate(
                                tracker.get_value(), [0.0, 1.0], [0.0, t],
                                easing=motion.Easing.out_cubic,
                            )))),
                            i,
                        ).scale(sc).move_to(a)
                    )
                )

            scene.add(columns, live)
            scene.play(
                tracker.animate.set_value(1.0),
                run_time=1.6,
                rate_func=linear,
            )
            scene.wait(0.5)
            seg.meta["mobjects"] = VGroup(columns, live)

        series.add("springs", 3.2, springs)
        series.add("counters", 3.4, counters)

        previous_group = None
        for seg in series.segments:
            if previous_group is not None:
                # The wipe covers the swap, so the outgoing segment never has to
                # animate itself away.
                def swap(group=previous_group):
                    # FadeIn adds each chip to the scene individually, so the
                    # scene never held the VGroup and removing it alone left
                    # every chip on screen behind the next segment.
                    self.remove(group, *group.submobjects)

                motion.wipe(self, swap, direction=RIGHT, color=theme.VIOLET,
                            run_time=0.5, fps=fps)
            seg.build(self, seg)
            previous_group = seg.meta.get("mobjects")

        card = self.replace_card(
            getattr(self, "_motion_card", None),
            self.make_card(
                "Timeline sebagai data",
                f"Dua segmen, total {series.duration:.1f} detik, disusun sebagai daftar.",
                color=theme.chip(2),
            ),
            zone=self.card_zone(),
        )
        self.wait(self.hold_for(spec.get("summary")))
        self.clean_summary(spec, active_card=card)
