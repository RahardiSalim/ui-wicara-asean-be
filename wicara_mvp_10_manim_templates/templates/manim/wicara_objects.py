"""Real-world objects, drawn in the WICARA brand language.

Every template so far explains a concept with abstract marks -- labelled
circles, boxes, arrows. That reads as a diagram, not as a thing. This module
draws recognisable objects (a ball, a house, a rocket, a tree) out of Manim
primitives, in the deck palette, so a lesson can point at an object instead of
at a letter in a circle.

Two conventions make the whole library behave the same way:

1. Every builder returns a VGroup whose submobjects are ordered the way the
   object would actually be assembled -- ground up for a house, core outward
   for an atom. `build(obj)` animates that order, so "simple to complex" is a
   property of the data, not something each caller has to choreograph.

2. Every object is normalised into roughly a 2x2 unit box centred on the
   origin. Callers position and scale via the scene's stage helpers rather than
   guessing coordinates, which is what kept putting figures under the card.
"""

from manim import *
import numpy as np

try:  # package import when rendered from the repo
    from . import wicara_theme as theme
except ImportError:  # loose-script import inside a render workdir
    import wicara_theme as theme


__all__ = [
    "ball", "house", "tree", "rocket", "car", "person", "book", "lightbulb",
    "gear", "flask", "atom", "water_molecule", "sun", "cloud", "mountain",
    "battery", "OBJECTS", "make_object", "build", "assemble", "normalise",
]


# ----------------------------------------------------------------------
# Shared drawing helpers
# ----------------------------------------------------------------------

_STROKE = 2.0


def _solid(shape, color, opacity=0.30, stroke=None, stroke_width=_STROKE):
    """Brand fill: a translucent body with a lit outline.

    Flat opaque fills go muddy on the ink ground -- the plate's violet glow
    stops reading through them and the object looks pasted on.
    """
    shape.set_fill(color=color, opacity=opacity)
    shape.set_stroke(color=stroke or color, width=stroke_width, opacity=0.95)
    return shape


def normalise(group, size=2.0):
    """Scale into a `size` box and centre, so every object composes alike."""
    if not len(group):
        return group
    longest = max(group.width, group.height, 1e-6)
    group.scale(size / longest)
    group.move_to(ORIGIN)
    return group


def _label(text, font_size=None):
    return Text(
        str(text),
        font_size=font_size or theme.FS_CAPTION,
        color=theme.ON_INK_2,
        **theme.font_kwargs("medium"),
    )


# ----------------------------------------------------------------------
# The objects
# ----------------------------------------------------------------------


def ball(radius=1.0, color=None):
    """A sphere read as a ball: body, terminator shading, specular highlight."""
    color = color or theme.BLUE_ON_INK
    body = _solid(Circle(radius=radius), color, opacity=0.52)

    # Layered offset circles stand in for a radial gradient, which Manim will
    # not put on a filled shape.
    shade = VGroup()
    for i, (dx, dy, op, sc) in enumerate(
        [(-0.16, -0.18, 0.26, 0.84), (-0.30, -0.32, 0.24, 0.60)]
    ):
        c = Circle(radius=radius * sc).shift(RIGHT * dx * radius + UP * dy * radius)
        c.set_fill(color=theme.INK, opacity=op).set_stroke(width=0)
        shade.add(c)

    highlight = Circle(radius=radius * 0.22)
    highlight.shift(LEFT * radius * 0.34 + UP * radius * 0.36)
    highlight.set_fill(color=theme.ON_INK, opacity=0.78).set_stroke(width=0)

    seam = Arc(radius=radius * 0.96, start_angle=PI * 0.62, angle=PI * 0.76)
    seam.set_stroke(color=color, width=1.6, opacity=0.6)

    return normalise(VGroup(body, shade, highlight, seam))


def house(color=None):
    """Ground up: walls, roof, door, window, chimney, path."""
    color = color or theme.chip(0)
    walls = _solid(Rectangle(width=2.0, height=1.5), color, opacity=0.22)
    walls.move_to(DOWN * 0.35)

    roof = _solid(
        Polygon(
            walls.get_corner(UL) + LEFT * 0.28,
            walls.get_corner(UR) + RIGHT * 0.28,
            walls.get_top() + UP * 1.0,
        ),
        theme.chip(1),
        opacity=0.30,
    )

    door = _solid(Rectangle(width=0.46, height=0.72), theme.GOLD, opacity=0.34)
    door.next_to(walls.get_bottom(), UP, buff=0).shift(LEFT * 0.42)
    knob = Dot(radius=0.045, color=theme.ON_INK).move_to(
        door.get_right() + LEFT * 0.10
    )

    window = _solid(Square(side_length=0.52), theme.BLUE_ON_INK, opacity=0.28)
    window.move_to(walls.get_center() + RIGHT * 0.48 + UP * 0.16)
    mullion = VGroup(
        Line(window.get_left(), window.get_right()),
        Line(window.get_top(), window.get_bottom()),
    ).set_stroke(color=theme.BLUE_ON_INK, width=1.4, opacity=0.8)

    chimney = _solid(Rectangle(width=0.28, height=0.62), theme.ON_INK_3, opacity=0.24)
    chimney.move_to(roof.get_top() + RIGHT * 0.62 + DOWN * 0.30)

    ground = Line(LEFT * 1.6, RIGHT * 1.6).shift(DOWN * 1.10)
    ground.set_stroke(color=theme.RULE, width=2.2)

    return normalise(
        VGroup(ground, walls, roof, chimney, door, knob, window, mullion)
    )


def tree(color=None):
    """Trunk, then three canopy tiers, then fruit."""
    color = color or theme.GOOD
    trunk = _solid(
        Rectangle(width=0.34, height=1.0), theme.chip(1), opacity=0.30
    ).shift(DOWN * 0.85)

    canopy = VGroup()
    for i, (w, y) in enumerate([(1.9, -0.10), (1.5, 0.45), (1.0, 0.95)]):
        tier = _solid(
            Polygon(
                LEFT * w / 2 + DOWN * 0.30, RIGHT * w / 2 + DOWN * 0.30, UP * 0.42
            ),
            color,
            opacity=0.22 + i * 0.04,
        ).shift(UP * y)
        canopy.add(tier)

    fruit = VGroup(
        *[
            Dot(radius=0.058, color=theme.GOLD).move_to(p)
            for p in [
                LEFT * 0.42 + UP * 0.10,
                RIGHT * 0.38 + UP * 0.52,
                LEFT * 0.10 + UP * 0.88,
                RIGHT * 0.16 + DOWN * 0.12,
            ]
        ]
    )

    ground = Line(LEFT * 1.4, RIGHT * 1.4).shift(DOWN * 1.35)
    ground.set_stroke(color=theme.RULE, width=2.2)

    return normalise(VGroup(ground, trunk, canopy, fruit))


def rocket(color=None):
    """Body, nose, fins, window, then exhaust."""
    color = color or theme.ON_INK_2
    body = _solid(
        RoundedRectangle(width=0.80, height=1.9, corner_radius=0.30),
        color,
        opacity=0.20,
    )
    nose = _solid(
        Polygon(LEFT * 0.40, RIGHT * 0.40, UP * 0.72), theme.chip(4), opacity=0.32
    ).next_to(body, UP, buff=-0.06)

    fins = VGroup()
    for sign in (-1, 1):
        fin = _solid(
            Polygon(ORIGIN, RIGHT * sign * 0.58, UP * 0.66 + RIGHT * sign * 0.06),
            theme.chip(0),
            opacity=0.30,
        )
        fin.move_to(body.get_bottom() + RIGHT * sign * 0.46 + UP * 0.22)
        fins.add(fin)

    window = _solid(Circle(radius=0.22), theme.BLUE_ON_INK, opacity=0.38)
    window.move_to(body.get_center() + UP * 0.42)

    flame = VGroup()
    for i, (h, c, op) in enumerate(
        [(0.85, theme.GOLD, 0.34), (0.55, theme.chip(1), 0.42)]
    ):
        f = _solid(
            Polygon(LEFT * (0.30 - i * 0.09), RIGHT * (0.30 - i * 0.09), DOWN * h),
            c,
            opacity=op,
            stroke_width=0,
        )
        f.next_to(body, DOWN, buff=0.02)
        flame.add(f)

    return normalise(VGroup(body, nose, fins, window, flame))


def car(color=None):
    """Chassis, cabin, windows, wheels, headlight."""
    color = color or theme.chip(0)
    chassis = _solid(
        RoundedRectangle(width=2.6, height=0.72, corner_radius=0.20),
        color,
        opacity=0.26,
    )
    cabin = _solid(
        RoundedRectangle(width=1.5, height=0.62, corner_radius=0.18),
        color,
        opacity=0.20,
    ).next_to(chassis, UP, buff=-0.10).shift(LEFT * 0.10)

    glass = VGroup()
    for dx in (-0.34, 0.34):
        g = _solid(
            RoundedRectangle(width=0.56, height=0.36, corner_radius=0.08),
            theme.BLUE_ON_INK,
            opacity=0.34,
            stroke_width=1.2,
        ).move_to(cabin.get_center() + RIGHT * dx)
        glass.add(g)

    wheels = VGroup()
    for dx in (-0.78, 0.82):
        hub = _solid(Circle(radius=0.30), theme.ON_INK_3, opacity=0.22)
        hub.move_to(chassis.get_bottom() + RIGHT * dx + UP * 0.04)
        rim = Circle(radius=0.13).move_to(hub.get_center())
        rim.set_fill(color=theme.ON_INK_2, opacity=0.5).set_stroke(width=0)
        wheels.add(VGroup(hub, rim))

    lamp = Dot(radius=0.075, color=theme.GOLD)
    lamp.move_to(chassis.get_right() + LEFT * 0.10 + UP * 0.06)
    beam = VGroup(
        *[
            Line(
                lamp.get_center() + RIGHT * 0.10 + UP * dy * 0.5,
                lamp.get_center() + RIGHT * 0.62 + UP * dy,
            ).set_stroke(color=theme.GOLD, width=2.0, opacity=op)
            for dy, op in ((0.16, 0.45), (0.0, 0.70), (-0.16, 0.45))
        ]
    )

    road = Line(LEFT * 1.7, RIGHT * 1.7).shift(DOWN * 0.72)
    road.set_stroke(color=theme.RULE, width=2.4)

    return normalise(VGroup(road, chassis, cabin, glass, wheels, lamp, beam))


def person(color=None):
    """Head, torso, arms, legs -- a figure, for scale and for agency."""
    color = color or theme.BLUE_ON_INK
    head = _solid(Circle(radius=0.30), color, opacity=0.30).shift(UP * 1.05)
    torso = _solid(
        RoundedRectangle(width=0.66, height=0.90, corner_radius=0.24),
        color,
        opacity=0.22,
    ).shift(UP * 0.22)

    arms = VGroup()
    for sign in (-1, 1):
        arm = Line(
            torso.get_center() + UP * 0.28 + RIGHT * sign * 0.30,
            torso.get_center() + UP * 0.02 + RIGHT * sign * 0.72,
        )
        arm.set_stroke(color=color, width=5, opacity=0.85)
        arms.add(arm)

    legs = VGroup()
    for sign in (-1, 1):
        leg = Line(
            torso.get_bottom() + RIGHT * sign * 0.14,
            torso.get_bottom() + RIGHT * sign * 0.34 + DOWN * 0.78,
        )
        leg.set_stroke(color=color, width=5, opacity=0.85)
        legs.add(leg)

    return normalise(VGroup(torso, head, arms, legs))


def book(color=None):
    """Cover, spine, pages, then a title rule."""
    color = color or theme.chip(2)
    cover = _solid(
        RoundedRectangle(width=1.6, height=2.1, corner_radius=0.10),
        color,
        opacity=0.28,
    )
    spine = _solid(
        Rectangle(width=0.20, height=2.1), theme.VIOLET, opacity=0.42, stroke_width=0
    ).move_to(cover.get_left() + RIGHT * 0.10)

    pages = VGroup()
    for i in range(3):
        p = Rectangle(width=0.08, height=1.9).move_to(
            cover.get_right() + LEFT * (0.04 + i * 0.03)
        )
        p.set_fill(color=theme.ON_INK, opacity=0.10 + i * 0.04).set_stroke(width=0)
        pages.add(p)

    rules = VGroup(
        *[
            Line(LEFT * 0.42, RIGHT * 0.42)
            .set_stroke(color=theme.ON_INK_2, width=2.4, opacity=0.55)
            .move_to(cover.get_center() + UP * (0.42 - i * 0.30) + RIGHT * 0.10)
            for i in range(2)
        ]
    )

    return normalise(VGroup(pages, cover, spine, rules))


def lightbulb(color=None):
    """Glass, filament, base, then the glow -- the classic 'idea' object."""
    color = color or theme.GOLD
    glass = _solid(Circle(radius=0.72), color, opacity=0.16)
    neck = _solid(
        Rectangle(width=0.46, height=0.30), theme.ON_INK_3, opacity=0.24
    ).next_to(glass, DOWN, buff=-0.06)
    base = _solid(
        RoundedRectangle(width=0.52, height=0.44, corner_radius=0.10),
        theme.ON_INK_3,
        opacity=0.30,
    ).next_to(neck, DOWN, buff=0.0)
    threads = VGroup(
        *[
            Line(LEFT * 0.26, RIGHT * 0.26)
            .set_stroke(color=theme.ON_INK_3, width=1.6, opacity=0.8)
            .move_to(base.get_center() + UP * (0.10 - i * 0.14))
            for i in range(3)
        ]
    )

    filament = VMobject()
    filament.set_points_smoothly(
        [
            glass.get_center() + LEFT * 0.22 + DOWN * 0.28,
            glass.get_center() + LEFT * 0.12 + UP * 0.10,
            glass.get_center() + RIGHT * 0.02 + DOWN * 0.06,
            glass.get_center() + RIGHT * 0.16 + UP * 0.14,
            glass.get_center() + RIGHT * 0.22 + DOWN * 0.28,
        ]
    )
    filament.set_stroke(color=color, width=3.2)

    rays = VGroup()
    for i in range(8):
        angle = i * TAU / 8 + TAU / 16
        d = np.array([np.cos(angle), np.sin(angle), 0.0])
        ray = Line(glass.get_center() + d * 0.86, glass.get_center() + d * 1.12)
        ray.set_stroke(color=color, width=2.4, opacity=0.55)
        rays.add(ray)

    return normalise(VGroup(base, threads, neck, glass, filament, rays))


def gear(teeth=10, color=None):
    """A mechanism -- reads as 'process' without a single arrow."""
    color = color or theme.ON_INK_2
    r_out, r_in = 0.95, 0.70
    pts = []
    for i in range(teeth):
        a0 = i * TAU / teeth
        a1 = a0 + TAU / teeth * 0.42
        a2 = a0 + TAU / teeth * 0.58
        a3 = a0 + TAU / teeth
        for r, a in ((r_out, a0), (r_out, a1), (r_in, a2), (r_in, a3)):
            pts.append(np.array([r * np.cos(a), r * np.sin(a), 0.0]))
    body = _solid(Polygon(*pts), color, opacity=0.18)

    hub = _solid(Circle(radius=0.34), theme.VIOLET, opacity=0.30)
    bore = Circle(radius=0.16)
    bore.set_fill(color=theme.INK, opacity=1.0).set_stroke(
        color=color, width=1.6, opacity=0.8
    )

    return normalise(VGroup(body, hub, bore))


def flask(color=None):
    """Neck, body, liquid, bubbles -- the lab object."""
    color = color or theme.GOOD
    outline = Polygon(
        LEFT * 0.22 + UP * 1.05,
        LEFT * 0.22 + UP * 0.30,
        LEFT * 0.85 + DOWN * 0.95,
        RIGHT * 0.85 + DOWN * 0.95,
        RIGHT * 0.22 + UP * 0.30,
        RIGHT * 0.22 + UP * 1.05,
    )
    glass = _solid(outline, theme.ON_INK_2, opacity=0.08)

    liquid = Polygon(
        LEFT * 0.60 + DOWN * 0.30,
        LEFT * 0.85 + DOWN * 0.95,
        RIGHT * 0.85 + DOWN * 0.95,
        RIGHT * 0.60 + DOWN * 0.30,
    )
    liquid.set_fill(color=color, opacity=0.38).set_stroke(color=color, width=1.6)

    lip = Line(LEFT * 0.30 + UP * 1.05, RIGHT * 0.30 + UP * 1.05)
    lip.set_stroke(color=theme.ON_INK_2, width=3.0)

    bubbles = VGroup(
        *[
            Circle(radius=r)
            .set_fill(color=theme.ON_INK, opacity=0.30)
            .set_stroke(width=0)
            .move_to(p)
            for r, p in [
                (0.07, LEFT * 0.28 + DOWN * 0.62),
                (0.05, RIGHT * 0.16 + DOWN * 0.48),
                (0.06, RIGHT * 0.42 + DOWN * 0.70),
            ]
        ]
    )

    return normalise(VGroup(glass, liquid, bubbles, lip))


def atom(electrons=3, color=None):
    """Nucleus first, then each orbital shell -- literally simple to complex."""
    color = color or theme.chip(2)
    nucleus = _solid(Circle(radius=0.24), theme.chip(4), opacity=0.55)
    glow = Circle(radius=0.38)
    glow.set_fill(color=theme.chip(4), opacity=0.16).set_stroke(width=0)

    shells = VGroup()
    for i in range(electrons):
        orbit = Ellipse(width=2.0, height=0.80)
        orbit.rotate(i * PI / electrons)
        orbit.set_stroke(color=color, width=2.0, opacity=0.65)
        dot = Dot(radius=0.075, color=theme.BLUE_ON_INK)
        dot.move_to(orbit.point_from_proportion((i * 0.27) % 1.0))
        shells.add(VGroup(orbit, dot))

    return normalise(VGroup(glow, nucleus, shells))


def water_molecule():
    """H2O -- two bonds and three atoms, the smallest real 'thing'."""
    o = _solid(Circle(radius=0.44), theme.chip(4), opacity=0.42)
    o_label = Text("O", font_size=22, color=theme.ON_INK, **theme.font_kwargs("bold"))
    o_label.move_to(o.get_center())

    hydrogens = VGroup()
    bonds = VGroup()
    for angle in (PI * 0.72, PI * 0.28):
        pos = np.array([np.cos(angle), np.sin(angle), 0.0]) * 1.02
        h = _solid(Circle(radius=0.26), theme.BLUE_ON_INK, opacity=0.34).move_to(pos)
        h_label = Text(
            "H", font_size=16, color=theme.ON_INK, **theme.font_kwargs("semibold")
        ).move_to(pos)
        bond = Line(ORIGIN, pos)
        bond.set_stroke(color=theme.ON_INK_3, width=4, opacity=0.7)
        bonds.add(bond)
        hydrogens.add(VGroup(h, h_label))

    return normalise(VGroup(bonds, o, o_label, hydrogens))


def sun(color=None):
    color = color or theme.GOLD
    core = _solid(Circle(radius=0.62), color, opacity=0.82)
    halo = Circle(radius=0.86)
    halo.set_fill(color=color, opacity=0.12).set_stroke(width=0)
    rays = VGroup()
    for i in range(12):
        a = i * TAU / 12
        d = np.array([np.cos(a), np.sin(a), 0.0])
        r = Line(d * 0.95, d * (1.30 if i % 2 == 0 else 1.14))
        r.set_stroke(color=color, width=3.0 if i % 2 == 0 else 2.0, opacity=0.7)
        rays.add(r)
    return normalise(VGroup(halo, core, rays))


def cloud(color=None):
    color = color or theme.ON_INK_2
    lobes = VGroup()
    for r, p in [
        (0.46, LEFT * 0.52), (0.62, ORIGIN), (0.42, RIGHT * 0.62), (0.34, RIGHT * 0.20 + DOWN * 0.18)
    ]:
        lobes.add(_solid(Circle(radius=r), color, opacity=0.16, stroke_width=0).move_to(p))
    base = _solid(
        RoundedRectangle(width=1.9, height=0.44, corner_radius=0.22),
        color,
        opacity=0.16,
        stroke_width=0,
    ).shift(DOWN * 0.30)
    return normalise(VGroup(base, lobes))


def mountain(color=None):
    color = color or theme.chip(0)
    far = _solid(
        Polygon(LEFT * 1.9 + DOWN * 0.9, RIGHT * 0.1 + DOWN * 0.9, LEFT * 0.85 + UP * 0.75),
        color,
        opacity=0.16,
    )
    near = _solid(
        Polygon(LEFT * 0.6 + DOWN * 0.9, RIGHT * 1.9 + DOWN * 0.9, RIGHT * 0.7 + UP * 1.05),
        color,
        opacity=0.26,
    )
    cap = Polygon(
        RIGHT * 0.7 + UP * 1.05,
        RIGHT * 0.38 + UP * 0.52,
        RIGHT * 0.56 + UP * 0.58,
        RIGHT * 0.72 + UP * 0.46,
        RIGHT * 0.88 + UP * 0.60,
        RIGHT * 1.02 + UP * 0.52,
    )
    cap.set_fill(color=theme.ON_INK, opacity=0.55).set_stroke(width=0)
    ground = Line(LEFT * 2.0, RIGHT * 2.0).shift(DOWN * 0.9)
    ground.set_stroke(color=theme.RULE, width=2.2)
    return normalise(VGroup(ground, far, near, cap))


def battery(color=None):
    color = color or theme.GOOD
    shell = _solid(
        RoundedRectangle(width=2.0, height=0.95, corner_radius=0.14),
        theme.ON_INK_3,
        opacity=0.14,
    )
    cap = _solid(
        RoundedRectangle(width=0.16, height=0.40, corner_radius=0.05),
        theme.ON_INK_3,
        opacity=0.30,
    ).next_to(shell, RIGHT, buff=-0.02)
    cells = VGroup()
    for i in range(3):
        c = _solid(
            RoundedRectangle(width=0.48, height=0.62, corner_radius=0.08),
            color,
            opacity=0.34,
            stroke_width=0,
        )
        c.move_to(shell.get_left() + RIGHT * (0.42 + i * 0.56))
        cells.add(c)
    bolt = Polygon(
        UP * 0.26 + LEFT * 0.10,
        DOWN * 0.02 + LEFT * 0.02,
        DOWN * 0.02 + RIGHT * 0.12,
        DOWN * 0.28 + RIGHT * 0.04,
        DOWN * 0.02 + RIGHT * 0.14,
        UP * 0.02 + RIGHT * 0.02,
    )
    bolt.set_fill(color=theme.GOLD, opacity=0.9).set_stroke(width=0)
    bolt.move_to(shell.get_center())
    return normalise(VGroup(shell, cap, cells, bolt))


# ----------------------------------------------------------------------
# Registry and animation
# ----------------------------------------------------------------------

OBJECTS = {
    "ball": ball,
    "house": house,
    "tree": tree,
    "rocket": rocket,
    "car": car,
    "person": person,
    "book": book,
    "lightbulb": lightbulb,
    "gear": gear,
    "flask": flask,
    "atom": atom,
    "water": water_molecule,
    "sun": sun,
    "cloud": cloud,
    "mountain": mountain,
    "battery": battery,
}


def make_object(name, **kwargs):
    """Look an object up by spec name; unknown names fall back to a ball."""
    builder = OBJECTS.get(str(name).strip().lower())
    if builder is None:
        builder = ball
    try:
        return builder(**kwargs)
    except TypeError:
        # Builders take different kwargs; never fail a render over styling.
        return builder()


def build(obj, run_time=1.6, lag_ratio=0.32):
    """Animate an object assembling itself, part by part, in build order.

    This is the whole point of the ordering convention: the same call reads as
    'simple to complex' for every object in the library.
    """
    parts = list(obj.submobjects) or [obj]
    return LaggedStart(
        *[
            FadeIn(part, shift=UP * 0.12, scale=0.94)
            for part in parts
        ],
        lag_ratio=lag_ratio,
        run_time=run_time,
    )


def assemble(obj, run_time=2.0):
    """A more deliberate build: outlines draw on, then fills arrive."""
    parts = list(obj.submobjects) or [obj]
    anims = []
    for part in parts:
        if isinstance(part, VMobject) and len(part.get_points()):
            anims.append(Create(part))
        else:
            anims.append(FadeIn(part))
    return LaggedStart(*anims, lag_ratio=0.28, run_time=run_time)


# ----------------------------------------------------------------------
# A posable human figure
# ----------------------------------------------------------------------
#
# `person()` above is a static icon -- fine for scale, useless for a scene where
# somebody has to actually do something. This builds the figure from a skeleton
# of joint angles instead, so the same code draws a person standing, throwing or
# running, and the scene can pose them mid-lesson.

_LIMB_W = 6.5
_JOINT_R = 0.055

# Angles in degrees, measured from the positive x-axis. Each limb is
# (upper segment, lower segment).
POSES = {
    # Limbs leaving the shoulder near -90 run straight down the spine and
    # disappear into the torso, so a resting arm still needs to be angled out.
    "stand": dict(
        l_arm=(-118, -104), r_arm=(-62, -76),
        l_leg=(-107, -97), r_leg=(-73, -83), lean=0.0,
    ),
    "throw": dict(
        l_arm=(-160, -205), r_arm=(72, 34),
        l_leg=(-128, -104), r_leg=(-52, -74), lean=-7.0,
    ),
    "wind_up": dict(
        l_arm=(-150, -190), r_arm=(128, 168),
        l_leg=(-120, -100), r_leg=(-58, -78), lean=9.0,
    ),
    "point": dict(
        l_arm=(-118, -104), r_arm=(22, 10),
        l_leg=(-107, -97), r_leg=(-73, -83), lean=0.0,
    ),
    # Both arms up. The old values were negative, i.e. pointing at the floor.
    "cheer": dict(
        l_arm=(132, 156), r_arm=(48, 24),
        l_leg=(-112, -100), r_leg=(-68, -80), lean=0.0,
    ),
}


def _seg(start, angle_deg, length):
    """End point of a limb segment leaving `start` at `angle_deg`."""
    a = angle_deg * DEGREES
    return start + np.array([np.cos(a), np.sin(a), 0.0]) * length


def _limb(root, angles, lengths, color):
    """Two-segment limb plus a joint dot, as one VGroup."""
    upper_end = _seg(root, angles[0], lengths[0])
    lower_end = _seg(upper_end, angles[1], lengths[1])
    upper = Line(root, upper_end)
    lower = Line(upper_end, lower_end)
    for part in (upper, lower):
        part.set_stroke(color=color, width=_LIMB_W, opacity=0.95)
    joint = Dot(radius=_JOINT_R, color=color).move_to(upper_end)
    limb = VGroup(upper, lower, joint)
    limb.wicara_tip = lower_end
    return limb


def figure(pose="stand", color=None, height=2.0):
    """A human figure in a named pose.

    Returns a VGroup carrying `wicara_hand` (the throwing hand's position) so a
    scene can attach a ball, a tool or a pointer to it without guessing.
    """
    color = color or theme.BLUE_ON_INK
    conf = POSES.get(str(pose).lower(), POSES["stand"])

    hip = ORIGIN.copy()
    shoulder = hip + UP * 0.72
    neck = shoulder + UP * 0.10

    spine = Line(hip, shoulder)
    spine.set_stroke(color=color, width=_LIMB_W + 2.0, opacity=0.95)

    head = Circle(radius=0.24)
    head.set_fill(color=color, opacity=0.32).set_stroke(color=color, width=2.4)
    head.move_to(neck + UP * 0.26)

    l_arm = _limb(shoulder, conf["l_arm"], (0.42, 0.40), color)
    r_arm = _limb(shoulder, conf["r_arm"], (0.42, 0.40), color)
    l_leg = _limb(hip, conf["l_leg"], (0.48, 0.46), color)
    r_leg = _limb(hip, conf["r_leg"], (0.48, 0.46), color)

    body = VGroup(l_leg, r_leg, spine, l_arm, head, r_arm)
    if conf.get("lean"):
        body.rotate(conf["lean"] * DEGREES, about_point=hip)
    body.scale(height / max(body.height, 1e-6), about_point=hip)

    # Keep a reference to the limb, not a copy of its coordinates. An earlier
    # version cached the hand position as a vector, which went stale the moment
    # the caller moved the figure onto the ground -- the ball then launched from
    # empty air a metre in front of the thrower.
    body.wicara_r_arm = r_arm
    body.wicara_l_arm = l_arm
    return body


def hand_of(fig):
    """World-space position of the throwing (right) hand.

    Read off the forearm mobject, so it stays correct through any shift, scale
    or rotation the caller applies afterwards.
    """
    arm = getattr(fig, "wicara_r_arm", None)
    if arm is not None and len(arm) > 1:
        return arm[1].get_end()
    return fig.get_center()


OBJECTS["figure"] = figure
__all__.extend(["figure", "POSES", "hand_of"])


# ----------------------------------------------------------------------
# Story properties
# ----------------------------------------------------------------------
#
# A lesson needs a diagram; a tale needs a place. These are the pieces a
# narrative scene is built from -- sea, boat, moon, stars, rock -- drawn to the
# same conventions as everything above so they compose with the objects and the
# figure without any special handling.


def sea(width=8.0, color=None, rows=4):
    """Water as stacked wave rules, darkest at the horizon."""
    color = color or theme.BLUE_ON_INK
    band = VGroup()
    for i in range(rows):
        line = VMobject()
        span = width * (0.72 + 0.09 * i)
        pts = []
        n = 26
        for j in range(n + 1):
            x = -span / 2 + span * j / n
            y = -i * 0.30 + 0.055 * np.sin(j * 1.35 + i * 0.8)
            pts.append(np.array([x, y, 0.0]))
        line.set_points_smoothly(pts)
        line.set_stroke(color=color, width=2.2, opacity=0.28 + 0.13 * i)
        band.add(line)
    return band


def boat(color=None):
    """Hull, mast, sail -- the smallest thing that reads as a voyage."""
    color = color or theme.chip(1)
    hull = _solid(
        Polygon(
            LEFT * 1.05 + UP * 0.22, RIGHT * 1.05 + UP * 0.22,
            RIGHT * 0.72 + DOWN * 0.28, LEFT * 0.72 + DOWN * 0.28,
        ),
        color,
        opacity=0.34,
    )
    mast = Line(UP * 0.22, UP * 1.42).set_stroke(color=theme.ON_INK_2, width=3.0)
    sail = _solid(
        Polygon(UP * 1.36, UP * 0.30, RIGHT * 0.82 + UP * 0.52),
        theme.ON_INK,
        opacity=0.20,
    )
    pennant = _solid(
        Polygon(UP * 1.42, UP * 1.18, RIGHT * 0.34 + UP * 1.30),
        theme.GOLD,
        opacity=0.75,
        stroke_width=0,
    )
    return normalise(VGroup(hull, mast, sail, pennant))


def moon(phase=0.62, color=None):
    """A crescent, cut by a second disc rather than drawn as an arc."""
    color = color or theme.GOLD
    disc = Circle(radius=0.58)
    disc.set_fill(color=color, opacity=0.80).set_stroke(color=color, width=1.4)
    bite = Circle(radius=0.58)
    bite.shift(RIGHT * 0.58 * phase)
    bite.set_fill(color=theme.INK, opacity=1.0).set_stroke(width=0)
    halo = Circle(radius=0.86)
    halo.set_fill(color=color, opacity=0.10).set_stroke(width=0)
    return VGroup(halo, disc, bite)


def stars(count=26, spread=(6.4, 2.2), seed=7):
    """A scatter of points. Deterministic: the same tale must render alike."""
    field = VGroup()
    # A small LCG rather than random, so a re-render is byte-identical.
    state = int(seed)
    def nxt():
        nonlocal state
        state = (1103515245 * state + 12345) % 2147483648
        return state / 2147483648.0

    for i in range(count):
        x = (nxt() - 0.5) * spread[0]
        y = (nxt() - 0.5) * spread[1]
        r = 0.018 + nxt() * 0.030
        dot = Dot(radius=r, color=theme.ON_INK)
        dot.set_opacity(0.35 + nxt() * 0.55)
        dot.move_to(np.array([x, y, 0.0]))
        field.add(dot)
    return field


def bird(color=None):
    """Two strokes. Anything more and it stops reading as distance."""
    color = color or theme.ON_INK_3
    wing = VMobject()
    wing.set_points_smoothly([
        LEFT * 0.26, LEFT * 0.10 + UP * 0.10, ORIGIN,
        RIGHT * 0.10 + UP * 0.10, RIGHT * 0.26,
    ])
    wing.set_stroke(color=color, width=2.2, opacity=0.85)
    return VGroup(wing)


def rock(color=None):
    """A boulder. Also what a certain ungrateful son ends up as."""
    color = color or theme.ON_INK_3
    body = _solid(
        Polygon(
            LEFT * 0.95 + DOWN * 0.55, LEFT * 0.70 + UP * 0.30,
            LEFT * 0.10 + UP * 0.62, RIGHT * 0.52 + UP * 0.40,
            RIGHT * 0.92 + DOWN * 0.20, RIGHT * 0.75 + DOWN * 0.55,
        ),
        color,
        opacity=0.30,
    )
    crack = VMobject()
    crack.set_points_smoothly([
        LEFT * 0.30 + UP * 0.50, LEFT * 0.12 + UP * 0.10,
        RIGHT * 0.05 + DOWN * 0.12, LEFT * 0.02 + DOWN * 0.48,
    ])
    crack.set_stroke(color=theme.INK, width=2.0, opacity=0.7)
    return normalise(VGroup(body, crack))


OBJECTS.update({
    "sea": sea, "boat": boat, "moon": moon, "stars": stars,
    "bird": bird, "rock": rock,
})
__all__.extend(["sea", "boat", "moon", "stars", "bird", "rock"])
