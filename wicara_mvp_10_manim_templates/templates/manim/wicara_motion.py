"""Remotion's motion model, ported to Manim.

Manim thinks in animations: you hand `play()` an object and a rate function and
it interpolates for you. Remotion thinks in frames: every value on screen is a
pure function of the current frame, so timing is data you can compose, offset
and nest rather than a sequence of imperative calls.

The two ideas are compatible, and the frame-driven half is what this module
adds:

  spring()       damped-harmonic motion, the same solution Remotion solves.
                 This is what makes its output feel modern -- an entrance that
                 overshoots slightly and settles reads as physical, where an
                 ease-out curve reads as a slide.
  interpolate()  map a range onto a range, with easing and edge clamping.
  Series         declare segments with durations and let the timeline lay them
                 out, instead of tracking elapsed seconds by hand.
  transitions    a branded panel that sweeps the frame between segments.

Nothing here replaces Manim's animation system; it feeds it. spring_rate()
hands back a plain rate function, so `self.play(..., rate_func=spring_rate())`
is all a template needs to change how everything feels.
"""

from __future__ import annotations

import math

import numpy as np

try:
    from . import wicara_theme as theme
except ImportError:  # loose-script import inside a render workdir
    import wicara_theme as theme


__all__ = [
    "SpringConfig", "spring", "measure_spring", "spring_rate",
    "interpolate", "Easing", "Series", "Segment",
    "slide_in", "draw_on", "wipe_transition", "wipe", "pop_in", "rise_in",
]


# ----------------------------------------------------------------------
# spring
# ----------------------------------------------------------------------


class SpringConfig:
    """Remotion's spring parameters, with its defaults.

    damping    how fast the oscillation dies. Below critical it overshoots.
    mass       inertia; heavier overshoots further and settles slower.
    stiffness  how hard it is pulled toward the target.
    """

    __slots__ = ("damping", "mass", "stiffness", "overshoot_clamping")

    def __init__(self, damping=10.0, mass=1.0, stiffness=100.0,
                 overshoot_clamping=False):
        self.damping = float(damping)
        self.mass = float(mass)
        self.stiffness = float(stiffness)
        self.overshoot_clamping = bool(overshoot_clamping)


#: Named presets, tuned against this brand's pacing rather than copied.
PRESETS = {
    "default": SpringConfig(),
    # A card arriving: a little life, no wobble.
    "gentle": SpringConfig(damping=14, mass=1.0, stiffness=110),
    # A number or a result landing: visible overshoot.
    "snappy": SpringConfig(damping=9, mass=0.8, stiffness=170),
    # Deliberately springy, for one accent per video and no more.
    "bouncy": SpringConfig(damping=6.5, mass=1.0, stiffness=150),
    # No overshoot at all; for anything measuring a real quantity, where an
    # overshoot would state a value that is not true even for a few frames.
    "stiff": SpringConfig(damping=26, mass=1.0, stiffness=220),
}


def spring(frame, fps=30.0, config=None, from_value=0.0, to_value=1.0,
           velocity=0.0):
    """Position of a damped harmonic oscillator at `frame`.

    The analytic solution, matching Remotion's: under-damped systems get the
    oscillating form, critically and over-damped get the exponential one.
    """
    cfg = _resolve(config)
    t = max(0.0, float(frame)) / max(float(fps), 1e-6)

    delta = to_value - from_value
    if abs(delta) < 1e-12:
        return to_value

    omega0 = math.sqrt(cfg.stiffness / cfg.mass)
    zeta = cfg.damping / (2.0 * math.sqrt(cfg.stiffness * cfg.mass))

    x0 = -delta          # displacement from the target
    v0 = -float(velocity)

    if zeta < 1.0:
        omega1 = omega0 * math.sqrt(1.0 - zeta * zeta)
        envelope = math.exp(-zeta * omega0 * t)
        position = to_value - envelope * (
            ((v0 + zeta * omega0 * x0) / omega1) * math.sin(omega1 * t)
            + x0 * math.cos(omega1 * t)
        )
    else:
        envelope = math.exp(-omega0 * t)
        position = to_value - envelope * (x0 + (v0 + omega0 * x0) * t)

    if cfg.overshoot_clamping:
        low, high = (from_value, to_value) if to_value >= from_value else (to_value, from_value)
        position = max(low, min(high, position))
    return position


def measure_spring(fps=30.0, config=None, threshold=0.005, max_frames=600):
    """Frames until the spring has settled.

    Remotion exposes this as measureSpring() precisely because a spring has no
    natural end -- it approaches its target forever. Without it you either cut
    the motion off mid-bounce or hold a still frame waiting for it.
    """
    cfg = _resolve(config)
    settled = 0
    for frame in range(int(max_frames)):
        if abs(1.0 - spring(frame, fps, cfg)) < threshold:
            settled += 1
            # Require a few consecutive frames inside the threshold, or a spring
            # crossing its target mid-bounce reads as settled.
            if settled >= 3:
                return max(1, frame - 2)
        else:
            settled = 0
    return int(max_frames)


def spring_rate(config="gentle", fps=30.0, threshold=0.005):
    """A Manim rate function driven by the spring.

    Manim calls a rate function with t in 0..1 and expects the eased position,
    so the spring is sampled across its own settle time and normalised.
    """
    cfg = _resolve(config)
    duration = max(1, measure_spring(fps, cfg, threshold))

    def rate(t):
        return spring(t * duration, fps, cfg)

    rate.wicara_duration = duration / float(fps)
    return rate


def spring_seconds(config="gentle", fps=30.0):
    """How long `config` needs to settle, in seconds -- a run_time."""
    return measure_spring(fps, _resolve(config)) / float(fps)


def _resolve(config):
    if config is None:
        return PRESETS["default"]
    if isinstance(config, SpringConfig):
        return config
    if isinstance(config, str):
        return PRESETS.get(config, PRESETS["default"])
    if isinstance(config, dict):
        return SpringConfig(**config)
    return PRESETS["default"]


# ----------------------------------------------------------------------
# interpolate
# ----------------------------------------------------------------------


class Easing:
    """Remotion's easing set, as plain callables on 0..1."""

    @staticmethod
    def linear(t):
        return t

    @staticmethod
    def in_quad(t):
        return t * t

    @staticmethod
    def out_quad(t):
        return 1.0 - (1.0 - t) ** 2

    @staticmethod
    def in_out_quad(t):
        return 2 * t * t if t < 0.5 else 1 - ((-2 * t + 2) ** 2) / 2

    @staticmethod
    def in_cubic(t):
        return t ** 3

    @staticmethod
    def out_cubic(t):
        return 1.0 - (1.0 - t) ** 3

    @staticmethod
    def in_out_cubic(t):
        return 4 * t ** 3 if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2

    @staticmethod
    def out_back(t, overshoot=1.70158):
        c3 = overshoot + 1
        return 1 + c3 * (t - 1) ** 3 + overshoot * (t - 1) ** 2

    @staticmethod
    def out_expo(t):
        return 1.0 if t >= 1.0 else 1 - (2 ** (-10 * t))


def interpolate(value, input_range, output_range, easing=None,
                extrapolate_left="clamp", extrapolate_right="clamp"):
    """Map `value` from one range onto another.

    Mirrors Remotion's interpolate(), including its default of clamping at both
    ends -- the behaviour that stops an entrance from continuing past its target
    just because the frame counter kept going.
    """
    xs = [float(v) for v in input_range]
    ys = [float(v) for v in output_range]
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("input_range and output_range must pair up, 2+ long")

    v = float(value)
    if v <= xs[0]:
        if extrapolate_left == "clamp":
            return ys[0]
        if extrapolate_left == "identity":
            return v
    if v >= xs[-1]:
        if extrapolate_right == "clamp":
            return ys[-1]
        if extrapolate_right == "identity":
            return v

    for i in range(len(xs) - 1):
        if xs[i] <= v <= xs[i + 1] or (i == len(xs) - 2):
            span = xs[i + 1] - xs[i]
            t = 0.0 if abs(span) < 1e-12 else (v - xs[i]) / span
            if easing is not None:
                t = easing(max(0.0, min(1.0, t)))
            return ys[i] + (ys[i + 1] - ys[i]) * t
    return ys[-1]


# ----------------------------------------------------------------------
# Series -- a declarative timeline
# ----------------------------------------------------------------------


class Segment:
    """One entry on a Series timeline."""

    __slots__ = ("name", "duration", "build", "transition", "meta")

    def __init__(self, name, duration, build=None, transition=None, **meta):
        self.name = str(name)
        self.duration = float(duration)
        self.build = build
        self.transition = transition
        self.meta = meta


class Series:
    """Segments laid end to end, each knowing where it starts.

    Remotion's <Series> exists so a timeline can be read as a list rather than
    reconstructed from accumulated offsets. Same idea: append segments, then ask
    the series when anything happens.
    """

    def __init__(self, fps=30.0):
        self.fps = float(fps)
        self.segments: list[Segment] = []

    def add(self, name, duration, build=None, transition=None, **meta):
        self.segments.append(Segment(name, duration, build, transition, **meta))
        return self

    def start_of(self, name):
        elapsed = 0.0
        for seg in self.segments:
            if seg.name == name:
                return elapsed
            elapsed += seg.duration
        raise KeyError(name)

    @property
    def duration(self):
        return sum(seg.duration for seg in self.segments)

    @property
    def frames(self):
        return int(round(self.duration * self.fps))

    def play_on(self, scene):
        """Run every segment in order, applying its transition on entry."""
        previous = None
        for seg in self.segments:
            if seg.transition and previous is not None:
                seg.transition(scene, previous)
            if callable(seg.build):
                seg.build(scene, seg)
            previous = seg
        return self


# ----------------------------------------------------------------------
# Entrances and transitions
# ----------------------------------------------------------------------


def pop_in(mobject, config_name="snappy", fps=30.0, scale_from=0.72):
    """Scale up into place on a spring. Remotion's signature entrance."""
    from manim import FadeIn

    return FadeIn(
        mobject,
        scale=scale_from,
        rate_func=spring_rate(config_name, fps),
        run_time=spring_seconds(config_name, fps),
    )


def rise_in(mobject, config_name="gentle", fps=30.0, distance=0.42):
    """Travel up into place on a spring."""
    from manim import FadeIn, UP

    return FadeIn(
        mobject,
        shift=UP * distance,
        rate_func=spring_rate(config_name, fps),
        run_time=spring_seconds(config_name, fps),
    )


def slide_in(mobject, direction=None, config_name="gentle", fps=30.0,
             distance=1.1):
    """Slide in from off the edge it points at."""
    from manim import FadeIn, RIGHT

    direction = RIGHT if direction is None else direction
    return FadeIn(
        mobject,
        shift=-np.array(direction) * distance,
        rate_func=spring_rate(config_name, fps),
        run_time=spring_seconds(config_name, fps),
    )


def draw_on(mobject, run_time=0.7):
    """Reveal a path by drawing it.

    This is deliberately not called a wipe. Manim has no stencil, so masking an
    arbitrary mobject is not available; drawing it on is the honest directional
    reveal for a path. Segment-level wipes are a different thing -- see below,
    where a full-frame panel really can sweep.
    """
    from manim import Create

    return Create(mobject, run_time=run_time)


def wipe_transition(scene, direction=None, color=None, run_time=0.62,
                    config_name="stiff", fps=30.0):
    """Sweep a branded panel across the frame, Remotion's wipe.

    At segment scale this is real: a full-bleed panel enters from one edge,
    covers everything, and leaves by the opposite one. Whatever the scene
    changes while it is covered simply appears to have always been there.

    Yields control back to the caller at full cover, so the caller can swap the
    scene contents, then completes the exit.
    """
    from manim import Rectangle, config, RIGHT

    direction = RIGHT if direction is None else np.array(direction)
    color = color or theme.VIOLET

    panel = Rectangle(
        width=config.frame_width * 1.15,
        height=config.frame_height * 1.15,
    )
    panel.set_fill(color=color, opacity=1.0).set_stroke(width=0)
    panel.set_z_index(50)

    span = (
        config.frame_width * 1.15
        if abs(direction[0]) > abs(direction[1])
        else config.frame_height * 1.15
    )
    panel.shift(-direction * span)
    scene.add(panel)

    rate = spring_rate(config_name, fps)
    scene.play(panel.animate.shift(direction * span), run_time=run_time, rate_func=rate)
    yield  # caller swaps the scene here, hidden behind the panel
    scene.play(panel.animate.shift(direction * span), run_time=run_time, rate_func=rate)
    scene.remove(panel)


def wipe(scene, swap, **kwargs):
    """wipe_transition as a single call: sweep, run `swap`, sweep away."""
    gen = wipe_transition(scene, **kwargs)
    next(gen)
    if callable(swap):
        swap()
    try:
        next(gen)
    except StopIteration:
        pass
