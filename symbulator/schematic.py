"""SVG schematic drawing for Symbulator circuit descriptions.

Turns a circuit description -- the same string `dc()`, `ac()`, `fd()`
and `tr()` already take -- into a standalone SVG drawing. Pure standard
library: no matplotlib, no LaTeX, no external toolchain, so it runs
unchanged in CPython and under Pyodide in the browser builds.

The layout is deliberately *not* a general graph-drawing algorithm.
Force-directed placement (Kamada-Kawai and friends, what most netlist
viewers reach for) gives a physics-plausible blob rather than something
that reads like a schematic. This instead assumes the shape that nearly
every linear teaching circuit already has:

  * ground is one horizontal rail along the bottom;
  * an element with one terminal on ground is drawn vertically, between
    the top row of nodes and that rail;
  * an element between two non-ground nodes is drawn horizontally along
    the top row;
  * nodes are ordered left to right by a depth-first walk from the first
    node mentioned, so a chain of elements comes out as a chain;
  * anything that would collide on the top row -- a parallel element, or
    one reaching over an intermediate node -- is lifted onto its own row
    above, with risers back down at each end.

Dividers, ladders, series RLC, T networks and single-op-amp stages land
in their textbook form. See LIMITATIONS at the bottom of this module for
what doesn't.

A dependent source's *control* is drawn too, on the element it reads:
a + at that element's first node and a - at its second when the drop is
the control, and an arrow running first node to second, labelled with
the current's own name, when the current is. Which element and which
way round is read off the value the way the solver reads it, so the
picture and the equations cannot disagree. The value in the source is
typeset to match, whatever the reader typed: multiplication implied
rather than starred, a voltage or a current as its own lower-case
sloped letter, and what it names as a capitalised subscript.

The symbols follow the books the tutorial teaches from -- Sadiku &
Alexander's *Fundamentals of Electric Circuits* and Boylestad's
*Introductory Circuit Analysis*: a zigzag resistor with sharp peaks, a
coil of loops for an inductor, a circle for an independent source and a
diamond for a controlled one, and element names set the way those books
set them, as a kind letter with a capitalised subscript (`rin` -> R_IN).

Colours are left to CSS: every stroke is `currentColor`, so one drawing
works in both the light and dark themes of the site.
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

from . import messages as M
from .elements import Element, CircuitError, parse_circuit

__all__ = ["to_svg", "draw"]

# Element kinds laid out as a plain two-terminal box. `o` (op-amp) is
# three-terminal and handled separately; `m` (mutual inductance) couples
# two *elements* rather than two nodes and so is written above the
# drawing instead of placed in it; everything else falls back to a
# labelled rectangle, so an unrecognised kind still draws something
# honest.
TWO_TERMINAL = frozenset("rlcejs")

# The two-port parameter families. They take two node names and ground
# their other two terminals themselves, so they are drawn as a block
# filling the band between the node row and the rail rather than as an
# element in a branch -- see `_draw_port_box`.
PORT_BLOCK = frozenset("zyhgab")

_UNIT = {"r": "Ω", "l": "H", "c": "F", "e": "V", "j": "A",
         "m": "H"}

# A value we are willing to append a unit to: a plain number with an
# optional SI-prefix letter. Anything else (vin, r_a, 5/s, 2*v_3) is a
# symbolic expression and is shown bare.
_NUMERIC = re.compile(
    r"^([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)([a-zA-Zµμ]?)$")

# Prefix letters as the *input* shorthand defines them (si_prefix), and
# the subset used for *display*. These differ: input accepts K and both
# mu characters, output picks one spelling and sticks to it.
_IN_PREFIX = {"k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
              "P": 1e15, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
              "n": 1e-9, "p": 1e-12, "f": 1e-15, "a": 1e-18}
_OUT_PREFIX = [(1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
               (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"),
               (1e-15, "f")]


def _engineering(text: str) -> Optional[str]:
    """`1e-6` -> `1µ`, `1000` -> `1k`, `1'u` -> `1µ`. Returns None when
    the value is not a plain number, which is the signal to show it
    verbatim: symbolic values like `r_a` or `5/s` must never be
    reformatted."""
    m = _NUMERIC.match(text)
    if not m:
        return None
    mantissa, letter = m.group(1), m.group(2)
    if letter and letter not in _IN_PREFIX:
        return None
    try:
        num = float(mantissa) * (_IN_PREFIX[letter] if letter else 1.0)
    except (ValueError, OverflowError):
        return None
    if num == 0:
        return "0"
    factor, prefix = 1.0, ""
    for f, p in _OUT_PREFIX:
        if abs(num) >= f:
            factor, prefix = f, p
            break
    else:
        return "{0:g}".format(num)
    scaled = "{0:.4g}".format(num / factor)
    if "." in scaled:
        scaled = scaled.rstrip("0").rstrip(".")
    return scaled + prefix

# --- geometry -------------------------------------------------------
COL_W = 132        # horizontal distance between adjacent node columns
ROW_H = 150        # top row of nodes down to the ground rail
STACK_H = 88       # extra height per stacked parallel branch
# 78 until #213, which is when the arithmetic was first done rather
# than eyeballed. A lifted source hangs its value *below* its
# circle, and the element on the row beneath carries a value and a
# name stacked *above* its own: 34.75 down, 45.35 up, and GAP
# between them is 84.1 -- a stack of 78 had them overlapping by
# 0.4px since #212 gave every name a subscript, under the review
# harness's 2px tolerance and so invisible until the values gained
# subscripts too and it grew to 2.1.
OP_LANE_H = 78     # extra height per extra op-amp lane
MARGIN = 58
GAP = 4.0          # clear air between a symbol's ink and a label's

# How far the label font's ink reaches from its own baseline. Measured,
# not assumed -- 13px ui-sans-serif renders at ascent 9.75 and descent
# 3.12, and capitals alone still descend 1.25 (Q's tail). The descent is
# the number that matters: a value like `-4j`, `1/gx` or a node called
# `ag` hangs below its baseline, and placing labels as though glyphs sat
# *on* the baseline is what left twenty-one of the 330 example drawings
# with 1-2px of air above a symbol instead of GAP. Re-measure with
# `tools/pixel_clearance.py`'s method if the font or size ever changes.
LABEL_ASCENT = 10.0
LABEL_DESCENT = 3.25
CAP_DESCENT = 1.5    # capitals only, which is all a name or subscript is
LABEL_GAP = 2.0      # between two stacked labels
BODY = 46          # length of the symbol body itself, leads excluded
DOT_R = 3.4

# The marks a *reference* wears: the element a dependent source names in
# its value gets the sign of the drop, or the direction of the current,
# that the source is reading (#213). Both sit on the element's free
# side -- below a horizontal one, left of a vertical one -- because the
# name and the value already own the other.
REF_SIGN_OFF = 10.0    # a reference + / - sign, off the element's axis
REF_ARROW_W = 4.0      # the reference arrow head's half-width
REF_ARROW_HEAD = 6.5   # and its length
REF_ARROW_MIN = 14.0   # shortest half-length the shaft is drawn at

# The inductor's coil: four turns spanning BODY, so its leads line up
# with the resistor's. IND_R > IND_STEP/2 is what makes each turn a
# *loop* rather than a hump -- see `_body_l`.
SRC_R = 15.0       # independent source outline radius
# Roberto, 1 Sep 2026: the resistor 20% smaller, the dependent source
# 10% larger. Both are pure scale factors on the one number each shape
# is built from, so nothing else in the geometry has to be re-derived --
# the zigzag's vertex angle, and so its mitre, is unchanged because its
# length and its amplitude scale together.
R_SCALE = 0.8      # the resistor, against the other bodies' BODY
DEP_SCALE = 1.1    # the dependent source's diamond, against SRC_R
R_BODY = BODY * R_SCALE
DEP_R = SRC_R * DEP_SCALE
# The coil: a projected helix, drawn as one line that loops (see
# `_body_l`). IND_RATIO is B/A, and it is the only number that decides
# whether the line crosses itself -- above 1 it loops, at 1 it is a sine
# wave, below 1 a ripple. IND_H is kept at the height the old four-arc
# coil reached, 10.99, so the symbol's vertical footprint and every
# label placed from it stay exactly where they were.
IND_TURNS = 3
IND_RATIO = 2.6
IND_H = 11.0
IND_SEGMENTS = 8          # cubic Beziers per turn; the fit is analytic
IND_REACH = IND_H
# Where the curve starts, and how far it runs. Both ends land on y = 0
# whatever the phase, so the leads always meet it level -- what the
# phase decides is which end gets the extra half turn's arch. At +pi/2
# the curve dives into a loop at the near end and rises out of an arch
# at the far one; at -pi/2 it is the mirror image. Roberto wanted the
# arch at the far end (1 Sep 2026), which is +pi/2.
IND_PHASE = math.pi / 2.0
_IND_SPAN = 2.0 * math.pi * IND_TURNS + math.pi

# The drawn span is a half turn longer than the turns alone, and the B
# term moves the two ends relative to each other, so the advance that
# puts the lead attachment points exactly BODY apart is not BODY over
# the turns. Solving x(t1) - x(t0) = BODY for A:
_IND_SIN_DIFF = math.sin(IND_PHASE + _IND_SPAN) - math.sin(IND_PHASE)


def _ind_advance() -> float:
    return BODY / (_IND_SPAN - IND_RATIO * _IND_SIN_DIFF)


def _ind_overhang() -> float:
    """How far the loops reach past the lead attachment points.

    The curve doubles back, so its extreme x is not at an endpoint: the
    stationary points are where cos t = A/B, and past them the line has
    already swung outside the span its ends define. Scanned rather than
    solved -- it is a handful of microseconds once, and a closed form
    here would have to know which stationary point is the outermost."""
    a = _ind_advance()
    b = IND_RATIO * a
    xs = [a * (IND_PHASE + _IND_SPAN * k / 400.0)
          - b * math.sin(IND_PHASE + _IND_SPAN * k / 400.0)
          for k in range(401)]
    return max(xs[0] - min(xs), max(xs) - xs[-1], 0.0)


IND_OVERHANG = _ind_overhang()

# How far each symbol's **ink** reaches either side of its own axis.
# Labels are placed from this rather than from one number for every
# kind: the bodies are not the same height (a capacitor's plates stand
# 13 out, a resistor's zigzag 9, a coil IND_REACH), and a fixed offset
# that clears the shallowest runs through the tallest.
#
# Ink, not path. Each number below starts as a *centreline* distance,
# and the stroke puts another half-width outside it. The resistor used
# to reach much further than that: its peaks were mitred to a point, and
# a mitre runs past its own vertex by half the stroke over the sine of
# half the vertex angle -- 2.2px here. Measured against rendered pixels,
# a label the path geometry called 3px clear of the zigzag was 1px clear
# of its ink, which is what a reader sees as touching.
# `tools/pixel_clearance.py` is that measurement, kept.
STROKE = 1.7                 # the drawing's stroke-width
_HALF = STROKE / 2.0
ZIG_AMP = 9.0 * R_SCALE      # the zigzag's half-height, centreline

# The peaks are rounded rather than pointed (Roberto, 1 Sep 2026). Each
# corner becomes a quadratic whose control point is the old vertex,
# starting ZIG_ROUND back along each arm -- so the curve leaves and
# rejoins the straight run along its own direction and there is no join
# to see. `stroke-linejoin="round"` was the cheap alternative and is not
# the same thing: its radius is fixed at half a stroke, 0.85px, which is
# the blob #212 rejected.
#
# **Rounding a corner cuts it off**, so the drawn peak is lower than the
# amplitude the geometry asks for: 6.51px against 7.20. That is the
# number a label has to clear, so it -- not ZIG_AMP -- is what REACH is
# built from, and the resistor's labels sit 2px closer than they did.
ZIG_ROUND = 1.5

_ZIG_ARM = math.hypot(R_BODY / 6.0, 2.0 * ZIG_AMP)   # peak to peak
_ZIG_CUT = min(ZIG_ROUND, _ZIG_ARM / 2.0)
# The quadratic's midpoint, for a symmetric interior peak: with the ends
# a fraction c = cut/arm along each arm, it lands at amp * (1 - c).
ZIG_PEAK = ZIG_AMP * (1.0 - _ZIG_CUT / _ZIG_ARM)

REACH = {"r": ZIG_PEAK + _HALF,
         "l": IND_REACH + _HALF,
         "c": 13.0 + _HALF,
         "e": SRC_R + _HALF, "j": SRC_R + _HALF,
         "s": _HALF}
REACH_BOX = 13.0 + _HALF   # the fallback labelled rectangle


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _mark_stack() -> float:
    """How much deeper a current arrow and its label hang below an
    element than the element alone: a gap, the arrow's full width plus
    its stroke, another gap, and a line of label with a subscript.

    A stacked row has to carry that, or the arrow under a lifted branch
    lands on the labels of the row beneath. `_Layout.stack_h` adds it,
    once, only to drawings that have such a branch -- the 330 example
    circuits have none, and none of them changed size for this."""
    return (GAP + 2.0 * REF_ARROW_W + STROKE + GAP
            + LABEL_ASCENT + _name_below())


# --- element names, set the way a textbook sets them ----------------
# `r1` is drawn R with a subscript 1; `rin` as R with a subscript IN.
# The first letter is the element's kind and stands full height;
# everything after it says *which* one, and every book this drawing is
# trying to look like sets that as a capitalised subscript -- Sadiku's
# R_1 and v_s, Boylestad's C_1 and X_2.
#
# An underscore is the reader's own way of writing the same thing
# (`r_a` means R sub a), so it is a separator here rather than a
# character to print, and `r_a_b` comes out as R sub AB. That makes the
# display many-to-one -- `rab`, `rAB` and `r_a_b` all draw alike -- but
# it already was: the subscript is capitalised, so `rin` and `rIn`
# were never distinguishable either. The name in the caption block,
# the answers and the description stays exactly as typed.
SUB_SCALE = 0.72     # subscript size, as a fraction of the label font
SUB_DY = 3.4         # how far its baseline drops, px


def _split_name(name: str) -> Tuple[str, str]:
    """`r1` -> ('R', '1'), `vin` -> ('V', 'IN'), `r_a` -> ('R', 'A')."""
    if not name:
        return "", ""
    return name[0].upper(), name[1:].lstrip("_").replace("_", "").upper()


def _name_below(subscripted: bool = True) -> float:
    """How far a *name* label's ink falls below its baseline. A name is
    a capital and a capitalised subscript, so it never has a true
    descender -- but the subscript sits SUB_DY lower and capitals still
    drop CAP_DESCENT."""
    return (SUB_DY if subscripted else 0.0) + CAP_DESCENT


def _name_runs(name: str) -> List[Tuple[str, bool]]:
    """The name as text runs for `_Canvas.runs`: the kind letter at full
    height, the rest subscripted."""
    head, sub = _split_name(name)
    return [(head, False), (sub, True)]


def _i_runs(name: str) -> List[Tuple]:
    """`r2` -> the label *i*_R2 that marks a referenced current.

    The quantity is the symbol -- a lower-case sloped *i*, the way every
    book sets a current -- and the element it belongs to is the whole of
    its subscript, upright: `R2`, not `R` with a `2` under it. That is
    one level of subscript, which is all a subscript can carry; the
    element's own label beside the symbol still reads R with a
    subscripted 2, and the two are meant to be read together."""
    head, sub = _split_name(name)
    return [("i", False, True), (head + sub, True, False)]


# A float literal long enough to be floating-point dust rather than a
# number anyone typed: eight or more significant digits.
_LONG_FLOAT = re.compile(r"\d*\.\d{7,}(?:[eE][+-]?\d+)?")

# `30*pi/180` is how an angle in degrees is written where the solver
# needs radians; the schematic shows it back as the degrees it means.
_DEG_RE = re.compile(r"(\d+(?:\.\d+)?)\s*\*\s*pi\s*/\s*180(?![\d.])")

# Whatever `pi` survives that is a real pi, and a book prints the
# letter. It has to be the letter once multiplication is implied
# (#213): spelled out, `100+24*pi*j` becomes the unreadable word
# `24pij`, where `24πj` reads at a glance.
_PI_RE = re.compile(r"(?<![A-Za-z0-9_])pi(?![A-Za-z0-9_])")

# A value longer than this is not lettered at the element -- the
# element keeps its name and the value moves to a caption line below
# the drawing (same block the mutual inductances already use).
CAPTION_LEN = 16


def _round_long_floats(text: str) -> str:
    """`173.20508075688772` -> `173.21`. Only rewrites literals long
    enough that no one wrote them by hand -- they are the residue of an
    expansion (a phasor turned rectangular, a computed coefficient) and
    carry no five-decimal information a schematic reader could use."""
    def shorten(m):
        try:
            return "{0:.5g}".format(float(m.group(0)))
        except (ValueError, OverflowError):
            return m.group(0)
    return _LONG_FLOAT.sub(shorten, text)


def _strip_outer_parens(text: str) -> str:
    """Drop redundant enclosing parentheses: `((110∠0°))` -> `110∠0°`.
    Only when the outermost pair actually matches around the whole
    string, so `(a)+(b)` keeps its parens."""
    while len(text) >= 2 and text[0] == "(" and text[-1] == ")":
        depth = 0
        for i, ch in enumerate(text):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i < len(text) - 1:
                    return text
        text = text[1:-1].strip()
    return text


def _pretty(e: Element) -> str:
    """Label text for an element value: shown the way the reader typed
    it (raw_fields survives shorthand expansion -- a phasor stays
    `110∠0°` instead of its 17-digit rectangular expansion), with the
    SI-prefix quote dropped (1'k -> 1k), redundant outer parentheses
    removed, long float literals rounded, and a unit appended when the
    value is a bare number rather than a symbol."""
    raw = e.value
    if raw is None:
        return ""
    if e.raw_fields and len(e.raw_fields) > 2:
        raw = e.raw_fields[2]
    val = raw.replace("'", "").strip()
    val = _strip_outer_parens(val)
    val = _round_long_floats(val)
    val = _DEG_RE.sub("\\1\u00b0", val)
    val = _PI_RE.sub("π", val)
    # The parallel-impedance shortcut back in the reader's own
    # notation: `[a,b]` was rewritten to `pr(a,b)` before the fields
    # were split (raw_fields cannot preserve it -- the bracket's inner
    # comma makes the typed text split differently), so it is restored
    # here, innermost first when they nest.
    while "pr(" in val:
        swapped = re.sub(r"pr\(([^()]*)\)", r"[\1]", val)
        if swapped == val:
            break
        val = swapped
    eng = _engineering(val)
    if eng is None:
        return val
    return eng + _UNIT.get(e.kind, "")


# A `*` no book prints. Multiplication is implied wherever the thing
# after it cannot be mistaken for a continuation of the thing before --
# a letter, an opening bracket, a Greek coefficient. It is kept in front
# of a digit or a sign, where dropping it would rewrite the arithmetic:
# `2*3` is not `23` and `2*-3` is not `2-3`.
_IMPLIED = re.compile(r"\s*\*\s*(?=[^\s\d.+\-*/^,)\]=<>])")


def _value_runs(e: Element, refs: Optional[Dict] = None) -> List[Tuple]:
    """An element's value as text runs, set the way a book sets it.

    Three things happen to the string `_pretty` produced, and they are
    the same three whichever way the reader typed it (#213):

      * multiplication is implied, not starred -- `2*x*ir2` is `2x`
        followed by the current;
      * a voltage or a current is its own lower-case sloped letter --
        *v*, *i* -- never an upright `v` run together with a name;
      * what it names hangs off it as a capitalised subscript, so
        `vr1`, `v_R1` and `5vR1` all read *v*_R1, exactly as the
        element beside it reads R_1.

    Only spellings this circuit actually defines are typeset (`refs`,
    from `_ref_keys`). A bare symbol that happens to start with a v is
    a parameter, not a voltage, and is left as it was typed -- the same
    reading the solver gives it."""
    text = _pretty(e)
    if not text:
        return []
    # Names first, stars second. Dropping the stars up front would fuse
    # `2*x*ir2` into the single word `xir2`, and the current reference
    # inside it would never be seen again -- which is exactly what the
    # first cut of this did.
    hits = [(m.start(), m.end(), refs.get(_fold(m.group(0))))
            for m in _IDENT.finditer(text)] if refs else []
    hits = [h for h in hits if h[2] is not None]

    def gap(a: int, b: int, joined: bool) -> str:
        """The plain text between two names, with its multiplication
        implied. `joined` says a name follows, so a trailing `*` has
        something to imply against. The lookahead cannot see past the
        slice, so a `*` at its end is stripped separately."""
        chunk = _IMPLIED.sub("", text[a:b])
        return re.sub(r"\s*\*\s*$", "", chunk) if joined else chunk

    runs: List[Tuple] = []
    pos = 0
    for start, end, hit in hits:
        lead = gap(pos, start, True)
        if lead:
            runs.append((lead, False, False))
        runs.append((hit[0], False, True))
        runs.append((hit[1], True, False))
        pos = end
    tail = gap(pos, len(text), False)
    if tail:
        runs.append((tail, False, False))
    return runs


def _flat(runs: List[Tuple]) -> str:
    """The runs as one plain string -- what the width and caption-length
    decisions are still taken on."""
    return "".join(r[0] for r in runs)


HOP_R = 5.0    # radius of the semicircular hop where one wire crosses another
_EPS = 0.5


def _hop_path(a: float, b: float, c: float, pts: List[float],
              horizontal: bool) -> str:
    """One wire from a to b (at cross-coordinate c) with a semicircular
    hop at each of `pts` -- the standard notation for a crossing that
    is not a connection. Plain wires stay `<line>` elements."""
    # A hop too close to the wire's end (or to the previous hop) has no
    # room for its arc; drop it rather than draw a mangled bump.
    usable: List[float] = []
    for p in pts:
        if p - HOP_R < a + 1 or p + HOP_R > b - 1:
            continue
        if usable and p - HOP_R < usable[-1] + HOP_R + 1:
            continue
        usable.append(p)
    if not usable:
        if horizontal:
            return ('<line x1="{0:g}" y1="{1:g}" x2="{2:g}" y2="{1:g}"/>'
                    .format(a, c, b))
        return ('<line x1="{0:g}" y1="{1:g}" x2="{0:g}" y2="{2:g}"/>'
                .format(c, a, b))
    d: List[str] = []
    if horizontal:
        d.append("M{0:g} {1:g}".format(a, c))
        for p in usable:
            d.append("L{0:g} {1:g}".format(p - HOP_R, c))
            d.append("A{0:g} {0:g} 0 0 1 {1:g} {2:g}".format(
                HOP_R, p + HOP_R, c))
        d.append("L{0:g} {1:g}".format(b, c))
    else:
        d.append("M{0:g} {1:g}".format(c, a))
        for p in usable:
            d.append("L{0:g} {1:g}".format(c, p - HOP_R))
            d.append("A{0:g} {0:g} 0 0 0 {1:g} {2:g}".format(
                HOP_R, c, p + HOP_R))
        d.append("L{0:g} {1:g}".format(c, b))
    return '<path d="{0}"/>'.format(" ".join(d))


class _Canvas:
    """Collects SVG fragments and tracks the bounding box, so the
    viewBox is computed from what was actually drawn rather than
    predicted up front.

    Wires and junction dots are *collected* rather than emitted: at
    flush time every horizontal-vertical crossing that is interior to
    both wires -- a place two unconnected wires pass each other -- is
    drawn as a small semicircular hop on the horizontal wire, the
    standard no-connection notation, and every endpoint that lands on
    the interior of a perpendicular wire gets a junction dot, so a
    T-connection and a crossing can never be confused."""

    def __init__(self) -> None:
        self.parts: List[str] = []
        self.inks: List[Tuple[float, float, float, float]] = []
        self.x0 = self.y0 = 1e9
        self.x1 = self.y1 = -1e9
        self.wires: List[Tuple[float, float, float, float]] = []
        # Element segments: the axis line an element body sits on, kept
        # so a wire crossing an element's *lead* still gets its hop.
        # (Crossing the body itself is a layout failure the column and
        # level assignment is responsible for preventing.)
        self.esegs: List[Tuple[float, float, float, float]] = []
        # Solid regions no wire may enter (op-amp triangle bodies):
        # nothing routes around them at draw time -- the layout is
        # responsible for never sending a wire there -- but keeping
        # them lets a verification harness prove that it didn't.
        self.obstacles: List[Tuple[float, float, float, float]] = []
        self.dots: List[Tuple[float, float]] = []

    def _bound(self, *pts: Tuple[float, float]) -> None:
        for x, y in pts:
            self.x0, self.x1 = min(self.x0, x), max(self.x1, x)
            self.y0, self.y1 = min(self.y0, y), max(self.y1, y)

    def wire(self, x1: float, y1: float, x2: float, y2: float) -> None:
        if abs(x1 - x2) < 0.01 and abs(y1 - y2) < 0.01:
            return
        self._bound((x1, y1), (x2, y2))
        self.wires.append((min(x1, x2), min(y1, y2),
                           max(x1, x2), max(y1, y2)))

    def eseg(self, x1: float, y1: float, x2: float, y2: float,
             half: float = 23.0) -> None:
        """Register an element's axis segment. `half` is the body's
        half-length along the axis, centred on the midpoint -- the zone
        a wire must never cross (the leads either side of it may be
        crossed, with a hop)."""
        self.esegs.append((min(x1, x2), min(y1, y2),
                           max(x1, x2), max(y1, y2), half))

    def ink(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """Record where a symbol actually puts ink, so a harness can
        prove no label lands on it (`tools/review_schematics.py`).

        Not the same thing as `obstacle`, which is the wider keep-out a
        *wire* has to respect: a label may sit inside a keep-out (every
        value label does, 3px above its own body) and must still stay
        off the ink."""
        self.inks.append((min(x0, x1), min(y0, y1),
                          max(x0, x1), max(y0, y1)))

    def obstacle(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.obstacles.append((x0, y0, x1, y1))

    def dot(self, x: float, y: float) -> None:
        self._bound((x, y))
        self.dots.append((x, y))

    def _flush_wires(self) -> None:
        """Emit the collected wires: merged where collinear runs
        overlap, with a hop wherever one wire crosses another (or an
        element's lead) without connecting, and a junction dot wherever
        a wire or element endpoint tees into a passing wire."""
        hor = [w for w in self.wires if abs(w[1] - w[3]) < 0.01]
        ver = [w for w in self.wires if abs(w[0] - w[2]) < 0.01]
        eh = [s for s in self.esegs if abs(s[1] - s[3]) < 0.01]
        ev = [s for s in self.esegs if abs(s[0] - s[2]) < 0.01]

        def merge(runs, key_i, lo_i, hi_i):
            """Overlapping collinear runs (double-drawn risers) as one."""
            merged: List[List[float]] = []
            for r in sorted(runs, key=lambda r: (r[key_i], r[lo_i])):
                for m in merged:
                    if abs(m[key_i] - r[key_i]) < 0.01 \
                            and r[lo_i] <= m[hi_i] + 0.01 \
                            and m[lo_i] <= r[hi_i] + 0.01:
                        m[lo_i] = min(m[lo_i], r[lo_i])
                        m[hi_i] = max(m[hi_i], r[hi_i])
                        break
                else:
                    merged.append(list(r))
            return [tuple(m) for m in merged]

        hor = merge(hor, 1, 0, 2)
        ver = merge(ver, 0, 1, 3)

        # Junction dots at T-joints: an endpoint on the interior of a
        # perpendicular wire is a connection, and drawing its dot is
        # what lets the hops below carry the opposite meaning.
        for xa, ya, xb, yb in [w[:4] for w in hor] + [s[:4] for s in eh]:
            for x, y1, _, y2 in ver:
                for px_ in (xa, xb):
                    if abs(px_ - x) < _EPS and y1 + _EPS < ya < y2 - _EPS:
                        self.dot(px_, ya)
        for x, ya, _, yb in [w[:4] for w in ver] + [s[:4] for s in ev]:
            for x1, y, x2, _ in hor:
                for py in (ya, yb):
                    if abs(py - y) < _EPS and x1 + _EPS < x < x2 - _EPS:
                        self.dot(x, py)

        # Hops: the horizontal wire jumps over vertical wires and
        # vertical element leads; a vertical wire only ever needs to
        # jump where a horizontal *element* is in its way, since
        # wire-wire crossings already got their hop on the horizontal.
        for x1, y, x2, _y in hor:
            pts = sorted(
                x for x, ya, _, yb in [w[:4] for w in ver]
                + [s[:4] for s in ev]
                if x1 + _EPS < x < x2 - _EPS and ya + _EPS < y < yb - _EPS)
            self.parts.append(_hop_path(x1, x2, y, pts, horizontal=True))
        for x, y1, _x, y2 in ver:
            pts = sorted(
                y for xa, y, xb, _ in [s[:4] for s in eh]
                if y1 + _EPS < y < y2 - _EPS and xa + _EPS < x < xb - _EPS)
            self.parts.append(_hop_path(y1, y2, x, pts, horizontal=False))

        seen = set()
        for x, y in self.dots:
            key = (round(x), round(y))
            if key not in seen:
                seen.add(key)
                self.parts.append(
                    '<circle cx="{0:g}" cy="{1:g}" r="{2:g}" '
                    'fill="currentColor" stroke="none"/>'
                    .format(x, y, DOT_R))

    def flush(self) -> None:
        self._flush_wires()

    def text(self, x: float, y: float, s: str, anchor: str = "middle") -> None:
        self.runs(x, y, [(s, False)], anchor)

    def runs(self, x: float, y: float,
             runs: List[Tuple], anchor: str = "middle") -> None:
        """One label built of full-size and subscript runs -- `[("R",
        False), ("1", True)]` is the R_1 an element name is drawn as.

        A run is `(text, subscript)`, or `(text, subscript, italic)`
        where the quantity itself has to be set in italics: a current
        reference is the *i* of every textbook, sloped, with the
        element's name upright beneath it (see `_i_runs`).

        Emitted as a single <text> with a <tspan> per run, so the runs
        flow with no positioning arithmetic here; each tspan carries the
        baseline shift *relative to the previous one*, which is what
        lets a label come back up to full size after a subscript."""
        runs = [(r[0], r[1], r[2] if len(r) > 2 else False)
                for r in runs if r[0]]
        if not runs:
            return
        # Bound by an estimate of the rendered width (13px UI font,
        # ~7.2px average advance), so a long label widens the viewBox
        # instead of being clipped at its edge.
        w = sum(len(t) * (7.2 * SUB_SCALE if sub else 7.2)
                for t, sub, _it in runs)
        if anchor == "middle":
            x0, x1 = x - w / 2.0, x + w / 2.0
        elif anchor == "end":
            x0, x1 = x - w, x
        else:
            x0, x1 = x, x + w
        low = (_name_below() if any(sub for _t, sub, _it in runs)
               else LABEL_DESCENT)
        self._bound((x0, y - LABEL_ASCENT), (x1, y + low))
        body, shift = [], 0.0
        for t, sub, ital in runs:
            want = SUB_DY if sub else 0.0
            # `font-style` as a presentation attribute rather than a
            # class: the harness that reads these labels back keys on
            # `class="sub"` being the whole class attribute, and the
            # <text>'s own `font` shorthand cannot reset a child's.
            body.append('<tspan{0}{1} dy="{2:g}">{3}</tspan>'.format(
                ' class="sub"' if sub else "",
                ' font-style="italic"' if ital else "",
                want - shift, _esc(t)))
            shift = want
        self.parts.append(
            '<text class="lbl" x="{0:g}" y="{1:g}" text-anchor="{2}">{3}</text>'
            .format(x, y, anchor, "".join(body)))

    def raw(self, svg: str, *corners: Tuple[float, float]) -> None:
        self._bound(*corners)
        self.parts.append(svg)


# --- symbol bodies --------------------------------------------------
# Each returns SVG drawn along the +x axis starting at (0,0), with the
# body centred on a segment of the given length. Keeping them in local
# coordinates means a vertical element is the same code plus a rotate()
# on the enclosing group.

def _body_r(length: float) -> str:
    """Six segments -- three full cycles -- with every corner rounded.

    A corner becomes a quadratic whose control point is the corner
    itself, leaving the straight run ZIG_ROUND back along one arm and
    rejoining it ZIG_ROUND along the next. Because a quadratic leaves
    its first control point along the line to the second, the curve is
    tangent to both arms and there is no join to see: the whole zigzag
    is one continuous stroke that never comes to a point.

    The cut is clamped to half the shorter arm, which matters at the two
    ends, where a long lead meets a half-length first arm -- an
    unclamped cut would run the curve past the corner it is rounding."""
    lead = (length - R_BODY) / 2.0
    step, amp = R_BODY / 6.0, ZIG_AMP
    pts = [(0.0, 0.0), (lead, 0.0)]
    for i in range(6):
        pts.append((lead + step * (i + 0.5), amp if i % 2 == 0 else -amp))
    pts += [(lead + R_BODY, 0.0), (length, 0.0)]

    d = ["M0 0"]
    for i in range(1, len(pts) - 1):
        (ax, ay), (vx, vy), (bx, by) = pts[i - 1], pts[i], pts[i + 1]
        la = math.hypot(ax - vx, ay - vy)
        lb = math.hypot(bx - vx, by - vy)
        cut = min(ZIG_ROUND, la / 2.0, lb / 2.0)
        d.append("L{0:g} {1:g}".format(vx + (ax - vx) / la * cut,
                                       vy + (ay - vy) / la * cut))
        d.append("Q{0:g} {1:g} {2:g} {3:g}".format(
            vx, vy, vx + (bx - vx) / lb * cut, vy + (by - vy) / lb * cut))
    d.append("L{0:g} {1:g}".format(*pts[-1]))
    return '<path d="{0}"/>'.format(" ".join(d))


def _body_c(length: float) -> str:
    """Two straight plates.

    A bowed plate was drawn for a few hours on 1 Sep 2026 and taken back
    out the same day, and the reason is worth keeping: **a curved plate
    conventionally marks a polarised capacitor**, and Symbulator's are
    not polarised -- `c1,2,0,1'u` has no + end and the engine never
    treats one terminal differently from the other. Drawn on every
    capacitor the curve says something about the component that is not
    true. It is a nice-looking symbol for a different part."""
    mid, gap, h = length / 2.0, 5.5, 13.0
    return ('<path d="M0 0 L{0:g} 0 M{1:g} 0 L{2:g} 0"/>'
            '<path d="M{0:g} {3:g} L{0:g} {4:g}"/>'
            '<path d="M{1:g} {3:g} L{1:g} {4:g}"/>'
            .format(mid - gap, mid + gap, length, -h, h))


def _body_l(length: float) -> str:
    """A coil drawn as one line that loops -- a projected helix
    (Roberto, 1 Sep 2026).

    Not a row of arcs and not a set of ellipses: a single continuous
    curve that crosses itself, which is what a coil seen slightly off
    its own axis actually looks like. The curve is a prolate trochoid,

        x(t) = A*t - B*sin(t)      advance A per radian, loop reach B
        y(t) = -IND_H*cos(t)       loop half-height

    and it loops exactly when B > A, because that is when dx/dt =
    A - B*cos(t) changes sign and the line doubles back on itself.
    B == A is a plain sine wave with no crossings at all, B < A a gentle
    ripple -- so IND_RATIO alone decides whether this reads as a spring
    or as a wave, and it is the number to move.

    Two shapes were tried and measured before this one. Arcs between two
    points on a line cannot do it: with both ends on the same line the
    large-arc flag takes the *major* arc, over the top, and the far side
    of the circle lies on the minor arc, so a turn can never cross its
    own chord -- every lifted variant measured 0.00 below the leads. A
    row of whole ellipses does cross, but reads as separate rings, not
    as one wire.

    Emitted as cubic Beziers fitted to the analytic derivative (Hermite
    segments converted to Bezier control points), so a few segments per
    turn are exact rather than merely close.

    **The half turn is what makes the ends read right.** Over a whole
    number of turns both ends leave in the same direction; the extra pi
    gives the coil one end that dives straight into a loop and one that
    rises out of an arch, which is what Roberto's references show. Which
    end gets which is `IND_PHASE` and nothing else -- both land on
    y = 0 either way, so the leads meet the curve level whichever it
    is."""
    a = _ind_advance()
    b = IND_RATIO * a
    t0 = IND_PHASE
    span = _IND_SPAN

    def at(t):
        return (a * t - b * math.sin(t), -IND_H * math.cos(t))

    def deriv(t):
        return (a - b * math.cos(t), IND_H * math.sin(t))

    shift = (length - BODY) / 2.0 - at(t0)[0]
    # Per *turn*, so the half turn gets its share too -- counting whole
    # turns would quietly thin the fit by an eighth.
    n = max(int(round(IND_SEGMENTS * span / (2.0 * math.pi))), 2)
    x0, y0 = at(t0)
    d = ["M0 0 L{0:g} 0".format(x0 + shift)]
    for i in range(n):
        u0, u1 = t0 + span * i / n, t0 + span * (i + 1) / n
        h = (u1 - u0) / 3.0
        (px, py), (qx, qy) = at(u0), at(u1)
        (dx0, dy0), (dx1, dy1) = deriv(u0), deriv(u1)
        d.append("C{0:g} {1:g} {2:g} {3:g} {4:g} {5:g}".format(
            px + shift + dx0 * h, py + dy0 * h,
            qx + shift - dx1 * h, qy - dy1 * h, qx + shift, qy))
    d.append("L{0:g} 0".format(length))
    return '<path d="{0}"/>'.format(" ".join(d))


def _source_outline(mid: float, dependent: bool) -> str:
    """The source's body: a circle, or a diamond when it is controlled.

    "Dependent sources are usually designated by diamond-shaped
    symbols" -- Sadiku & Alexander, *Fundamentals of Electric
    Circuits*, Fig. 1.13. The two are no longer drawn to the same radius:
    a dependent source is DEP_SCALE larger (Roberto, 1 Sep 2026), so
    every caller that needs to know how far a source reaches has to be
    told which one it is -- `_body_extent`, the label reach and the
    wire keep-out all take `dependent` for that reason."""
    r = DEP_R if dependent else SRC_R
    if not dependent:
        return '<circle cx="{0:g}" cy="0" r="{1:g}" fill="none"/>'.format(
            mid, r)
    return ('<path d="M{0:g} 0 L{1:g} {2:g} L{3:g} 0 L{1:g} {4:g} Z" '
            'fill="none" stroke-linejoin="miter"/>'
            .format(mid - r, mid, -r, mid + r, r))


def _body_e(length: float, dependent: bool = False) -> str:
    """Independent or dependent voltage source: leads and the outline
    only. The + and - polarity marks are added afterwards by
    `_polarity`, in absolute coordinates -- drawn here they would be
    caught by the group's rotate() and a vertical source would end up
    with a minus sign standing on end."""
    mid, r = length / 2.0, (DEP_R if dependent else SRC_R)
    return ('<path d="M0 0 L{0:g} 0 M{1:g} 0 L{2:g} 0"/>{3}'
            .format(mid - r, mid + r, length,
                    _source_outline(mid, dependent)))


def _body_j(length: float, dependent: bool = False) -> str:
    """Current source, arrow pointing n1 -> n2: the solver's positive
    i_<name> leaves n1 through the element (engine.add_current)."""
    mid, r = length / 2.0, (DEP_R if dependent else SRC_R)
    return ('<path d="M0 0 L{0:g} 0 M{1:g} 0 L{2:g} 0"/>{3}'
            '<path d="M{4:g} 0 L{5:g} 0"/>'
            '<path d="M{6:g} -4 L{5:g} 0 L{6:g} 4" fill="currentColor"/>'
            .format(mid - r, mid + r, length,
                    _source_outline(mid, dependent),
                    mid - 9, mid + 9, mid + 3))


def _body_s(length: float) -> str:
    return '<path d="M0 0 L{0:g} 0"/>'.format(length)


def _body_box(length: float, letter: str) -> str:
    lead, h = (length - BODY) / 2.0, 26.0
    return ('<path d="M0 0 L{0:g} 0 M{1:g} 0 L{2:g} 0"/>'
            '<rect x="{0:g}" y="{3:g}" width="{4:g}" height="{5:g}" '
            'fill="none"/>'
            '<text class="lbl" x="{6:g}" y="4" text-anchor="middle">{7}</text>'
            .format(lead, lead + BODY, length, -h / 2, BODY, h,
                    lead + BODY / 2, _esc(letter)))


_BODIES = {"r": _body_r, "c": _body_c, "l": _body_l,
           "e": _body_e, "j": _body_j, "s": _body_s}


def _body_extent(kind: str, length: float,
                 dependent: bool = False) -> Optional[Tuple[float, float]]:
    """How far along the segment the symbol actually draws, measured
    from the (x1,y1) end, leads excluded -- so a label beside a lead is
    not mistaken for a label on a symbol. None for a short, which is
    lead all the way across.

    Symmetric about the midpoint for every kind, which is why the
    caller can apply it without knowing which way round the element was
    drawn."""
    mid = length / 2.0
    if kind == "s":
        return None
    if kind in ("e", "j"):
        r = DEP_R if dependent else SRC_R
        return mid - r, mid + r
    if kind == "c":
        return mid - 7.0, mid + 7.0        # the two plates and their gap
    if kind == "r":
        return mid - R_BODY / 2.0, mid + R_BODY / 2.0
    if kind == "l":
        # The coil's loops swing past the points its leads attach at, so
        # its ink is wider than its span. Reporting the span alone would
        # let a label sit on the outermost loop.
        half = BODY / 2.0 + IND_OVERHANG
        return mid - half, mid + half
    return mid - BODY / 2.0, mid + BODY / 2.0


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _fold(token: str) -> str:
    """The solver's spelling-equivalence key: `i_r1`, `ir1` and `IR1`
    are one name to it since 0.5.19 (`engine._norm_name`), so anything
    reading a value has to fold the same way."""
    return token.replace("_", "").lower()


def _terminals(e: Element) -> List[str]:
    """The node names an element attaches to. `m` couples two elements
    rather than two nodes, so it has none; `o` has three."""
    if e.kind == "m":
        return []
    return list(e.fields[:3]) if e.kind == "o" else [e.n1, e.n2]


def _controlled(elements: List[Element]) -> frozenset:
    """Names of the e/j sources whose value refers to another quantity
    in the circuit -- a node voltage, an element drop, an element
    current. Those are the *controlled* (dependent) sources, and a
    textbook draws them as diamonds rather than circles.

    The spellings are the solver's own, folded the way `spice._fold`
    folds them: `i_r1`, `ir1` and `IR1` are one name to the solver
    since 0.5.19, so the test cannot be a search for an underscore.
    Building the key set from the circuit is what keeps a plain
    symbolic value apart from a reference: `e1,1,0,vs` is a dependent
    source exactly when the circuit has something called `s` for it to
    refer to -- which is the same reading the solver gives it."""
    keys = set()
    for e in elements:
        for n in _terminals(e):
            keys.add(_fold("v" + n))
        keys.add(_fold("v" + e.name))
        keys.add(_fold("i" + e.name))
    out = set()
    for e in elements:
        if e.kind not in ("e", "j"):
            continue
        val = (e.value or "").replace("'", "").strip()
        if not val or _NUMERIC.match(val):
            continue
        if any(_fold(t) in keys for t in _IDENT.findall(val)):
            out.add(e.name)
    return frozenset(out)


def _ref_keys(elements: List[Element]) -> Dict[str, Tuple[str, str, str]]:
    """Every spelling a value can use to name a voltage or a current in
    this circuit, folded, mapped to how the drawing sets it and what it
    refers to: `("v", "R1", "r1")` for the drop across r1, `("i", "R1",
    "r1")` for the current through it, `("v", "2", "")` for node 2's
    voltage.

    Resolution follows `engine._alias_map` exactly, and it has to: node
    voltages are claimed first, so in a circuit with a node called `s`
    the token `vs` is that node's voltage and *not* the drop across an
    element called `s`. The drawing must say what the solver will
    actually solve. `i` has no node spelling, so a current is only ever
    an element's.

    Elements only if they are two-terminal. An op-amp, a transformer or
    a two-port block has no single drop or current a mark could name,
    and a value mentioning one is left exactly as it was typed."""
    keys: Dict[str, Tuple[str, str, str]] = {}
    for e in elements:
        if e.kind == "m":
            continue
        for n in _terminals(e):
            if n != "0":
                keys.setdefault(_fold("v" + n), ("v", n.upper(), ""))
    for e in elements:
        if e.kind not in TWO_TERMINAL:
            continue
        head, tail = _split_name(e.name)
        shown = head + tail
        keys.setdefault(_fold("v" + e.name), ("v", shown, e.name))
        keys.setdefault(_fold("i" + e.name), ("i", shown, e.name))
    return keys


def _references(elements: List[Element]) -> Tuple[frozenset, frozenset]:
    """The elements a dependent source *reads*: the names whose voltage
    drop one refers to, and the names whose current one refers to.

    A source written `ed,3,2,4*ir1` is telling the reader to look at r1
    and measure the current through it; `ed,2,3,2*vra` says to look at
    ra and measure the drop across it. Neither instruction is anywhere
    on the drawing unless it is drawn, so a schematic that omits them
    leaves the reader to work out from the netlist text which way round
    the control was meant -- which is the one thing a schematic exists
    to save them. Hence the polarity pair and the labelled arrow (#213).

    A node voltage is a real control and makes a real diamond, but it
    is not a mark: there is no element to put it on."""
    keys = _ref_keys(elements)
    vref, iref = set(), set()
    for e in elements:
        if e.kind not in ("e", "j"):
            continue
        val = (e.value or "").replace("'", "").strip()
        if not val or _NUMERIC.match(val):
            continue
        for tok in _IDENT.findall(val):
            hit = keys.get(_fold(tok))
            if hit is None or not hit[2]:
                continue
            (vref if hit[0] == "v" else iref).add(hit[2])
    return frozenset(vref), frozenset(iref)


def _coupling_dot(cv: _Canvas, x1: float, y1: float, x2: float,
                  y2: float) -> None:
    """Polarity dot for a coupled inductor, at its n1 terminal.

    Which terminal gets the dot is not a choice: engine._stamp_l adds
    the coupling as +M*i_other with no orientation term, and the
    solver's positive current enters at n1 (engine.add_current). So
    current into n1 of one coil raises v(n1) - v(n2) of the other, which
    is precisely what the dot convention marks -- n1, on every coupled
    inductor, every time. A winding wound the other way is expressed by
    a negative M, not by moving the dot.

    The dot is offset to the side the value labels do not occupy: above
    a horizontal coil, left of a vertical one."""
    dx, dy = x2 - x1, y2 - y1
    span = (dx * dx + dy * dy) ** 0.5
    if span < BODY:
        return
    ux, uy = dx / span, dy / span
    along = max((span - BODY) / 2.0 - 6.0, 8.0)
    ox, oy = (0.0, -10.0) if abs(dx) > abs(dy) else (-10.0, 0.0)
    cv.dot(x1 + ux * along + ox, y1 + uy * along + oy)


def _sign_mark(cv: _Canvas, x: float, y: float, plus: bool) -> None:
    """A stroked + or - centred on (x, y): 3.5px arms at the page's own
    stroke width. One drawing style for every sign in a schematic --
    the voltage source's polarity and the op-amp's input pins draw
    through here, so they cannot fall out of step (#130: the op-amp's
    used to be 13px text glyphs, visibly heavier than the source's
    marks beside them)."""
    arm = 3.5
    if plus:
        cv.raw('<path d="M{0:g} {1:g} L{2:g} {1:g} M{3:g} {4:g} L{3:g} '
               '{5:g}"/>'
               .format(x - arm, y, x + arm, x, y - arm, y + arm),
               (x - arm, y - arm), (x + arm, y + arm))
    else:
        cv.raw('<path d="M{0:g} {1:g} L{2:g} {1:g}"/>'
               .format(x - arm, y, x + arm),
               (x - arm, y), (x + arm, y))


def _polarity(cv: _Canvas, x1: float, y1: float, x2: float,
              y2: float) -> None:
    """Mark a voltage source + at the n1 end and - at the n2 end, which
    is the sign the solver uses: v(n1) - v(n2) = value (engine._stamp_e).

    Both marks are drawn in absolute screen coordinates rather than
    along the element's own axis, so the minus stays a horizontal bar
    whichever way the source is oriented."""
    dx, dy = x2 - x1, y2 - y1
    span = (dx * dx + dy * dy) ** 0.5
    if span < 1:
        return
    ux, uy = dx / span, dy / span          # n1 -> n2, unit length
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    off = 7.0
    _sign_mark(cv, cx - ux * off, cy - uy * off, True)    # + toward n1
    _sign_mark(cv, cx + ux * off, cy + uy * off, False)   # - toward n2


def _reference_marks(cv: _Canvas, e: Element, x1: float, y1: float,
                     x2: float, y2: float, base: float,
                     mark_v: bool, mark_i: bool,
                     dependent: bool = False) -> None:
    """Draw what a dependent source is reading off this element: a + at
    its n1 terminal and a - at its n2 terminal when the drop is the
    control, and an arrow running n1 -> n2 with the current's own label
    when the current is.

    Both directions are the solver's, not a choice: `v_<name>` is
    v(n1) - v(n2) (engine.stamp_all) and the positive `i_<name>` flows
    from n1 to n2 through the element (engine._stamp_r), so the + goes
    to the n1 end and the arrow head to the n2 end, always.

    (x1,y1) is the n1 end -- `_draw_element`'s guarantee -- but *which*
    end that is on screen is not fixed, so every offset below is taken
    along the element's own axis and then pushed to one screen side.
    That side is always the same one -- below a horizontal element,
    left of a vertical one -- and `base` says how far the element's own
    ink and labels already reach on it, so a source with its value
    hanging under its circle simply hands over a larger number.

    One side, always, is what lets the layout size the stacked rows for
    the marks: they can only ever grow a drawing downward, and only by
    `MARK_STACK`. Putting them over the name instead, which the first
    cut did for a source, grew it *upward* into the row above and made
    the row spacing depend on which side each element had chosen."""
    dx, dy = x2 - x1, y2 - y1
    span = math.hypot(dx, dy)
    if span < 1:
        return
    ux, uy = dx / span, dy / span              # n1 -> n2, unit length
    nx, ny = -uy, ux                           # and its perpendicular
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    ext = _body_extent(e.kind, span, dependent)
    half = (ext[1] - ext[0]) / 2.0 if ext else 16.0

    px, py = (-1.0, 0.0) if abs(dx) < 0.5 else (0.0, 1.0)

    if mark_v:
        # Just outside the body, on the lead, where a book puts them --
        # unless the leads are too short for that, in which case the
        # pair moves out past the body instead of onto it.
        along = min(half + 9.0, max(span / 2.0 - 5.0, 0.0))
        off = REF_SIGN_OFF if along >= half + 2.0 else base + GAP + 3.5
        for sign, s_ in ((True, -1.0), (False, 1.0)):
            sx = cx + ux * along * s_ + px * off
            sy = cy + uy * along * s_ + py * off
            _sign_mark(cv, sx, sy, sign)
            cv.ink(sx - 3.5 - _HALF, sy - 3.5 - _HALF,
                   sx + 3.5 + _HALF, sy + 3.5 + _HALF)

    if not mark_i:
        return
    # Ink, not path, on both sides of the arrow: the head is half a
    # stroke wider than the triangle its points describe, and `base`
    # already carries the symbol's own half-stroke.
    off = base + GAP + REF_ARROW_W + _HALF
    a = min(max(half, REF_ARROW_MIN), max(span / 2.0 - 6.0, 8.0))
    tx, ty = cx - ux * a + px * off, cy - uy * a + py * off   # tail
    hx, hy = cx + ux * a + px * off, cy + uy * a + py * off   # head
    bx, by = hx - ux * REF_ARROW_HEAD, hy - uy * REF_ARROW_HEAD
    cv.raw('<path d="M{0:g} {1:g} L{2:g} {3:g}"/>'
           '<path d="M{4:g} {5:g} L{2:g} {3:g} L{6:g} {7:g}" '
           'fill="currentColor"/>'
           .format(tx, ty, hx, hy,
                   bx + nx * REF_ARROW_W, by + ny * REF_ARROW_W,
                   bx - nx * REF_ARROW_W, by - ny * REF_ARROW_W),
           (min(tx, hx) - REF_ARROW_W, min(ty, hy) - REF_ARROW_W),
           (max(tx, hx) + REF_ARROW_W, max(ty, hy) + REF_ARROW_W))
    cv.ink(min(tx, hx) - REF_ARROW_W - _HALF,
           min(ty, hy) - REF_ARROW_W - _HALF,
           max(tx, hx) + REF_ARROW_W + _HALF,
           max(ty, hy) + REF_ARROW_W + _HALF)

    # The label, one clear gap beyond the arrow's own widest point --
    # its ink edge, not its baseline, on the side it was pushed to.
    edge = off + REF_ARROW_W + _HALF + GAP
    if px:
        cv.runs(cx - edge, cy + 4.5, _i_runs(e.name), "end")
    else:
        cv.runs(cx, cy + edge + LABEL_ASCENT, _i_runs(e.name))


PORT_BOX_W = 150.0    # the block's width, and its height. The height is
                      # not free: the lower terminals sit PORT_BOX_OVER
                      # above the bottom edge, so for them to land *on*
                      # the rail -- and the lower leads to run straight
                      # out with no bend -- the box has to be the band
                      # plus twice the overhang. ROW_H + 2*12 = 174.
                      # A taller box drops them below the rail and the
                      # bend comes back the other way.
PORT_BOX_H = ROW_H + 2 * 12.0
PORT_BOX_OVER = 12.0  # how far its top and bottom edges stand past the
                      # lines that enter it, so the terminals meet the
                      # left and right faces rather than the corners
PORT_BOX_LINE = 17.0  # line spacing for the name and parameters inside
PORT_BOX_MARK = 30.0  # and how far past the box each ground symbol sits:
                      # clear of the lower edge the box overhangs by, and
                      # far enough that the node's name still fits to the
                      # right of the bars, where every other one is


def _draw_port_box(cv: _Canvas, e: Element, xa: float, xb: float,
                   y_top: float, y_bot: float) -> List[float]:
    """A two-port block: one box filling the band between the node row
    and the ground rail, with a terminal at each of its four corners.

    Not an element in a branch. The reader names two nodes and the other
    two terminals are ground, so the block has a port either side and
    they share a return: `engine._stamp_two_port` reads `v1 = v(n1)` and
    `v2 = v(n2)`, both against ground. Drawn in line between two nodes it
    implied a single series current, which the element does not carry --
    in AS7's Example 13.8 the two port currents differ by the turns
    ratio, the difference going to ground.

    The box is **shorter than the band**, so it stops above the rail and
    its two lower terminals run out sideways and then down to it. All
    four terminals meet the left and right faces, inset from the corners
    by the overhang. Returns the x positions those lower leads put on
    the rail."""
    mid = (xa + xb) / 2.0
    bx0, bx1 = mid - PORT_BOX_W / 2.0, mid + PORT_BOX_W / 2.0
    by0 = y_top - PORT_BOX_OVER
    by1 = by0 + PORT_BOX_H
    low = by1 - PORT_BOX_OVER          # the lower terminals' own line
    cv.raw('<rect x="{0:g}" y="{1:g}" width="{2:g}" height="{3:g}" '
           'fill="none"/>'.format(bx0, by0, PORT_BOX_W, PORT_BOX_H),
           (bx0, by0), (bx1, by1))
    # The ink is the outline, not the area: the rect is `fill="none"`,
    # and its inside is the one piece of clear space on the drawing --
    # which is where the name goes. Registering the filled area instead
    # made the harness report the block's own name as sitting on it.
    h = _HALF
    cv.ink(bx0 - h, by0 - h, bx1 + h, by0 + h)          # top edge
    cv.ink(bx0 - h, by1 - h, bx1 + h, by1 + h)          # bottom
    cv.ink(bx0 - h, by0 - h, bx0 + h, by1 + h)          # left
    cv.ink(bx1 - h, by0 - h, bx1 + h, by1 + h)          # right
    cv.wire(min(xa, bx0), y_top, bx0, y_top)
    cv.wire(bx1, y_top, max(xb, bx1), y_top)
    # ...and the lower pair, out of the faces to the rail. Each stops
    # halfway between the block's face and the column its own upper lead
    # came from -- which is where the rail bends up into whatever is
    # there. A fixed offset put the symbol hard against the box on a
    # tight drawing and adrift on a wide one; the midpoint is the same
    # gap on both sides however far apart the columns fall.
    legs = []
    for x, node_x in ((bx0, min(xa, bx0)), (bx1, max(xb, bx1))):
        out = (x + node_x) / 2.0
        cv.wire(min(x, out), low, max(x, out), low)
        cv.wire(out, low, out, y_bot)
        legs.append(out)

    # The name, and then the four parameters under it -- the block's
    # whole behaviour, written where there is room for it. Nothing else
    # on the drawing says what a `z` or an `h` block actually does, and
    # the reader would otherwise have to go back to the description for
    # `[40,20j,30j,50]` and remember that the order is 11, 12, 21, 22.
    params = _port_params(e)
    lines = 1 + len(params)
    top = (by0 + by1) / 2.0 - (lines - 1) * PORT_BOX_LINE / 2.0 + 4.0
    cv.runs(mid, top, _name_runs(e.name))
    for i, text in enumerate(params):
        cv.text(mid, top + (i + 1) * PORT_BOX_LINE, text)
    return legs


def _port_params(e: Element) -> List[str]:
    """`zp,1,2,[4,5,6,7]` -> ['zp11 = 4', 'zp12 = 5', ...].

    Empty when the reader gave none: each parameter is then a free
    symbol of that very name, and writing `z11 = z11` four times says
    nothing the box's own letter has not said already."""
    raw = e.raw_fields[2] if len(e.raw_fields) > 2 else ""
    raw = (raw or "").strip()
    if not (raw.startswith("[") and raw.endswith("]")):
        return []
    body, parts, depth, cur = raw[1:-1], [], 0, ""
    for ch in body:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    parts.append(cur.strip())
    if len(parts) != 4:
        return []
    return ["{0}{1} = {2}".format(e.name, n, _round_long_floats(v))
            for n, v in zip(("11", "12", "21", "22"), parts)]


TRANS_OFF = 19.0     # each winding's axis, either side of the core
TRANS_CORE = 2.5     # half the gap between the two core bars


def _draw_transformer(cv: _Canvas, e: Element, xa: float, xb: float,
                      y_top: float, y_bot: float) -> List[float]:
    """An ideal transformer: two windings facing a core, with the
    polarity dots and the turns ratio.

    Four terminals like the two-port, and for the same reason -- the
    reader names two nodes and the other two are ground -- so each
    winding runs from its node down to the rail and the two share that
    return. Returns the x positions its windings put on the rail.

    **The dots carry the polarity and the printed ratio shows
    magnitudes**, which is how the books set it: `t,2,3,1,-2` draws as
    `1 : 2` with the secondary's dot at the foot of its winding.

    The two are alternative notations, never both. `engine._stamp_t`
    sets `v(n1)/turns1 = v(n2)/turns2`, so a negative turn count already
    says the secondary is inverted; move the dot as well and a reader
    applies the reversal twice and reads AS7's Example 13.8 as `+2`.
    This drawing did exactly that for a few hours on 1 Sep 2026, until
    Roberto asked whether the inversion was deliberate or a double
    count. It was a double count.

    A ratio that is not a plain number -- `1 : n` -- has no sign to
    read, so the dots stay together and it is printed as typed. That is
    not a fallback but the honest answer: nothing in the description
    says which way an unknown `n` runs.

    Two core bars, not one. A single line between two windings at this
    stroke reads as a wire joining them, which is precisely what an
    ideal transformer does not have."""
    mid = (xa + xb) / 2.0
    xp, xs = mid - TRANS_OFF, mid + TRANS_OFF
    lead = (y_bot - y_top - BODY) / 2.0
    top, bot = y_top + lead, y_top + lead + BODY

    # The primary is mirrored about its own axis, so the two windings
    # face each other across the core instead of both spiralling the
    # same way. `scale(1,-1)` inside the rotated frame flips the local
    # y that the rotation has already turned into screen x -- and it
    # flips the coil's ends with it, which is what keeps the pair a true
    # mirror rather than one coil slid over.
    for x, flip in ((xp, " scale(1,-1)"), (xs, "")):
        cv.raw('<g transform="translate({0:g},{1:g}) rotate(90){3}">{2}</g>'
               .format(x, y_top, _body_l(y_bot - y_top), flip),
               (x - REACH["l"], top), (x + REACH["l"], bot))
        cv.ink(x - REACH["l"], top, x + REACH["l"], bot)
        cv.eseg(x, y_top, x, y_bot, half=BODY / 2.0)
    cv.wire(min(xa, xp), y_top, xp, y_top)
    cv.wire(xs, y_top, max(xb, xs), y_top)

    bars = []
    for bx in (mid - TRANS_CORE, mid + TRANS_CORE):
        bars.append('<path d="M{0:g} {1:g} L{0:g} {2:g}"/>'
                    .format(bx, top - 3, bot + 3))
    cv.raw("".join(bars), (mid - TRANS_CORE, top - 3),
           (mid + TRANS_CORE, bot + 3))
    cv.ink(mid - TRANS_CORE, top - 3, mid + TRANS_CORE, bot + 3)

    # Signs agree -> same polarity, dots level. Only a pair of plain
    # numbers can be compared; anything symbolic keeps them together.
    turns, same = [], True
    for f in (e.fields[2], e.fields[3]):
        try:
            turns.append(float(f))
        except (TypeError, ValueError):
            turns = []
            break
    if turns:
        same = (turns[0] < 0) == (turns[1] < 0)
    cv.dot(xp - 9, top + 3)
    cv.dot(xs + 9, top + 3 if same else bot - 3)

    # The sign has gone into the dots, so the ratio shows magnitudes --
    # printing both would say the reversal twice.
    if turns:
        shown = ["{0:g}".format(abs(t)) for t in turns]
    else:
        shown = [_round_long_floats(str(e.fields[2])),
                 _round_long_floats(str(e.fields[3]))]
    ratio = "{0} : {1}".format(*shown)
    cv.text(mid, top - 9, ratio)
    cv.runs(mid, top - 9 - LABEL_ASCENT - LABEL_GAP - _name_below(),
            _name_runs(e.name))
    return [xp, xs]


def _draw_element(cv: _Canvas, e: Element, x1: float, y1: float,
                  x2: float, y2: float,
                  dependent: bool = False,
                  mark_v: bool = False,
                  mark_i: bool = False,
                  refs: Optional[Dict] = None) -> Tuple[float, float]:
    """Draw `e` along the axis-aligned segment (x1,y1)-(x2,y2), oriented
    so that its n1 terminal is the (x1,y1) end. Returns the midpoint, so
    a later pass can tie two element bodies together (mutual
    inductance).

    `mark_v` / `mark_i` say that some dependent source names this
    element's drop or its current in its value, and the reference wants
    drawing -- see `_reference_marks`. `refs` is the circuit's own name
    map, which is what lets this element's *value* be set as a book
    would set it (`_value_runs`)."""
    vertical = abs(x2 - x1) < 0.5
    length = abs(y2 - y1) if vertical else abs(x2 - x1)
    if e.kind in ("e", "j"):
        body = _BODIES[e.kind](length, dependent)
    else:
        maker = _BODIES.get(e.kind)
        body = maker(length) if maker else _body_box(length, e.kind.upper())
    src_r = DEP_R if dependent else SRC_R
    cv.eseg(x1, y1, x2, y2,
            half=src_r if e.kind in ("e", "j") else
            0.0 if e.kind == "s" else BODY / 2.0)

    # Labels are emitted outside the rotated group, in absolute
    # coordinates, so that a vertical element's text stays horizontal.
    # A source's circle (r = 15) is taller and wider than the other
    # bodies, so its labels sit further out -- and a horizontal source
    # puts the value *below* the circle, where a resistor-height offset
    # would run the text straight through the stroke.
    round_body = e.kind in ("e", "j")
    # Set by the horizontal branch below: the reference marks take the
    # side the value label did not.
    val_below = False
    val_runs = _value_runs(e, refs)
    val = _flat(val_runs)
    if len(val) > CAPTION_LEN:
        # Too long to letter at the element: the name stays, the value
        # goes to the caption block below the drawing (see _render).
        val_runs, val = [], ""
    reach = REACH.get(e.kind, REACH_BOX)
    if e.kind in ("e", "j"):
        reach = src_r + _HALF
    if vertical:
        top, bot = min(y1, y2), max(y1, y2)
        if y1 < y2:
            tf = "translate({0:g},{1:g}) rotate(90)".format(x1, top)
        else:
            tf = "translate({0:g},{1:g}) rotate(-90)".format(x1, bot)
        cv.raw('<g transform="{0}">{1}</g>'.format(tf, body),
               (x1 - 22, top), (x1 + 22, bot))
        span = _body_extent(e.kind, length, dependent)
        if span:
            cv.ink(x1 - reach, top + span[0], x1 + reach, top + span[1])
        mx, my = x1, (top + bot) / 2.0
        # Out to the side by the same clear air the horizontal case
        # leaves above and below, rather than two numbers per kind.
        dx = reach + GAP + 1.5
        # Name above the midpoint, value below it. The gap has to
        # carry a line of text plus the name's subscript descent, or
        # the two labels touch (they did, until the clearance check in
        # review_schematics.py was able to see it).
        cv.runs(mx + dx, my - 6, _name_runs(e.name), "start")
        cv.runs(mx + dx, my + 13, val_runs, "start")
    else:
        left, right = min(x1, x2), max(x1, x2)
        if x1 < x2:
            tf = "translate({0:g},{1:g})".format(left, y1)
        else:
            tf = "translate({0:g},{1:g}) rotate(180)".format(right, y1)
        cv.raw('<g transform="{0}">{1}</g>'.format(tf, body),
               (left, y1 - 22), (right, y1 + 22))
        span = _body_extent(e.kind, length, dependent)
        if span:
            cv.ink(left + span[0], y1 - reach, left + span[1], y1 + reach)
        mx, my = (x1 + x2) / 2.0, y1
        if e.kind == "s" and length > COL_W * 1.5:
            # A long short is a plain wire whose midpoint is exactly
            # where another element's riser tends to cross it (shorts
            # jumper over things by nature); label it off-centre.
            mx = min(x1, x2) + length / 4.0
        # Every offset below is "the ink clears the ink by GAP", never
        # a baseline distance: a label's baseline is not its edge.
        name_up = my - reach - GAP - _name_below()
        if round_body:
            # A source is round and tall: the value goes below it, the
            # name above, both clear of the outline by GAP.
            val_below = True
            cv.runs(mx, name_up, _name_runs(e.name))
            cv.runs(mx, my + reach + GAP + LABEL_ASCENT, val_runs)
        elif len(val) * 7.2 > 70:
            # A long value centred above the body would run into the
            # neighbouring node's name; below the wire is open.
            val_below = True
            cv.runs(mx, name_up, _name_runs(e.name))
            cv.runs(mx, my + reach + GAP + LABEL_ASCENT, val_runs)
        else:
            # Value just above the body, name above the value.
            vy = my - reach - GAP - LABEL_DESCENT
            cv.runs(mx, vy, val_runs)
            cv.runs(mx, vy - LABEL_ASCENT - LABEL_GAP - _name_below(),
                    _name_runs(e.name))
    if e.kind == "e":
        _polarity(cv, x1, y1, x2, y2)
    if (mark_v or mark_i) and e.kind in TWO_TERMINAL:
        # How far the element has already claimed on the mark side: its
        # own ink, plus the value label when that is what hangs below.
        base = reach
        if not vertical and val_below:
            base += GAP + LABEL_ASCENT + _name_below()
        _reference_marks(cv, e, x1, y1, x2, y2, base, mark_v, mark_i,
                         dependent)
    return mx, my


# --- layout ---------------------------------------------------------

def _node_order(elements: List[Element]) -> List[str]:
    """Left-to-right ordering of the non-ground nodes: a depth-first
    walk over the element graph, started from each node in the order it
    first appears in the description. DFS rather than BFS because a
    chain of elements must come out as a chain -- BFS distance ties
    would collapse two branches into one column."""
    adj: Dict[str, List[str]] = {}
    seen: List[str] = []

    def note(n: str) -> None:
        if n != "0" and n not in adj:
            adj[n] = []
            seen.append(n)

    for e in elements:
        if e.kind in TWO_TERMINAL:
            note(e.n1)
            note(e.n2)
        elif e.kind == "o":
            for n in e.fields[:3]:
                note(n)
        elif e.kind != "m":
            note(e.n1)
            note(e.n2)

    def link(a: str, b: str) -> None:
        if a != "0" and b != "0" and a != b:
            adj[a].append(b)
            adj[b].append(a)

    # Op-amp links first: an op-amp's inverting input and output are not
    # a two-terminal edge, but they do have to end up adjacent -- and
    # walking that link *before* any resistor edge is what makes a
    # cascade come out left to right. An adder's skip resistor (input
    # straight to the second stage's summing node) is declared early and
    # would otherwise drag the far output node into an early column,
    # leaving the first op-amp pointing backwards through its own
    # output wire.
    for e in elements:
        if e.kind == "o":
            link(_op_up(e), e.fields[2])
    # A weaker link second: non-inverting input to inverting input. For
    # a non-inverting stage the feedback divider hangs off n- and often
    # touches nothing else, so without this the only route to n- is
    # *through* the output node and the stage comes out backwards. n+
    # is usually ground, where link() is a no-op.
    for e in elements:
        if e.kind == "o":
            link(e.fields[0], e.fields[1])
    for e in elements:
        if e.kind not in ("m", "o"):
            link(e.n1, e.n2)

    # An op-amp's output node must land to the *right* of its inverting
    # input, or the triangle is drawn backwards with its output wire
    # retracing through the body. The link edges above make the two
    # adjacent, but another element (an adder's skip resistor, a shared
    # feedback network) can still hand the output node to the walk
    # early -- so an output node whose inverting input has not been
    # placed yet is deferred rather than visited. The forced pop when
    # the stack runs dry keeps a pathological circuit (an output node
    # that is the only path to its own input) from deadlocking; it just
    # falls back to the old order there.
    inv_input_of: Dict[str, str] = {}
    for e in elements:
        if e.kind == "o":
            inv_input_of.setdefault(e.fields[2], _op_up(e))

    order: List[str] = []
    visited = set()
    for start in seen:
        if start in visited:
            continue
        stack = [start]
        deferred: List[str] = []
        while stack or deferred:
            if not stack:
                stack.append(deferred.pop(0))
                forced = True
            else:
                forced = False
            n = stack.pop()
            if n in visited:
                continue
            minus = inv_input_of.get(n)
            if not forced and minus is not None and minus not in visited \
                    and minus in adj:
                deferred.append(n)
                continue
            visited.add(n)
            order.append(n)
            for m in reversed(adj[n]):
                if m not in visited:
                    stack.append(m)
    return order


def _ground_node(e: Element) -> str:
    """The non-ground terminal of a grounded two-terminal element."""
    return e.n2 if e.n1 == "0" else e.n1


def _op_up(e: Element) -> str:
    """The op-amp input drawn wired to the node row: the inverting
    input normally, but the non-inverting one when the inverting input
    is ground -- `o,1,0,o` is written that way round, and treating "0"
    as a column would run the input riser through whatever hangs on
    the leftmost column."""
    return e.fields[1] if e.fields[1] != "0" else e.fields[0]


class _Layout:
    """Column assignment and the resulting pixel geometry."""

    def __init__(self, elements: List[Element]) -> None:
        # Which sources are controlled -- computed once, from the whole
        # circuit, because the answer for one source depends on what
        # names the *others* introduced (see `_controlled`).
        self.controlled = _controlled(elements)
        # ...and which elements those sources are reading, so each can
        # be drawn wearing the reference it is read through (#213).
        self.v_ref, self.i_ref = _references(elements)
        # ...and the same map, kept, so every value can be set the way
        # a book sets it (`_value_runs`).
        self.refs = _ref_keys(elements)
        self.elements = elements
        self.grounded: List[Element] = []
        self.spanning: List[Element] = []
        self.opamps: List[Element] = []
        self.mutuals: List[Element] = []

        for e in elements:
            if e.kind == "m":
                self.mutuals.append(e)
            elif e.kind == "o":
                self.opamps.append(e)
            elif e.n1 == e.n2:
                continue          # degenerate self-loop: nothing to draw
            elif e.n1 == "0" or e.n2 == "0":
                self.grounded.append(e)
            else:
                self.spanning.append(e)

        # A non-inverting stage's driving source -- a grounded e/j that
        # is the *only* thing on the op-amp's lower input -- is drawn in
        # the input drop itself, under the triangle, the way a textbook
        # draws it. Giving that node a top-row column of its own would
        # push the source out to the edge of the drawing and route its
        # wire across everything in between.
        self.op_src: Dict[str, Element] = {}
        self.captured: set = set()
        usage: Dict[str, List[Element]] = {}
        for e in elements:
            if e.kind == "m":
                continue
            terms = e.fields[:3] if e.kind == "o" else [e.n1, e.n2]
            for n in set(terms):
                if n != "0":
                    usage.setdefault(n, []).append(e)
        for op in self.opamps:
            # The input routed downward: n+ normally; when the pins are
            # flipped because n- is ground (see _op_up), the downward
            # input *is* ground and there is nothing to capture.
            dn = op.fields[0] if op.fields[1] != "0" else "0"
            if dn == "0" or dn == _op_up(op):
                continue
            users = usage.get(dn, [])
            if len(users) != 2 or op not in users:
                continue
            src = next((u for u in users
                        if u.kind in ("e", "j") and "0" in (u.n1, u.n2)),
                       None)
            if src is not None and src in self.grounded:
                self.op_src[op.name] = src
                self.captured.add(dn)
                self.grounded.remove(src)

        self.node_col: Dict[str, int] = {}
        self.elem_col: Dict[str, int] = {}      # grounded elements
        self.level: Dict[str, int] = {}         # spanning elements
        self.op_lane: Dict[str, int] = {}       # op-amps
        self._assign()

    def _assign(self) -> None:
        by_node: Dict[str, List[Element]] = {}
        for e in self.grounded:
            n = e.n2 if e.n1 == "0" else e.n1
            by_node.setdefault(n, []).append(e)

        order = [n for n in _node_order(self.elements)
                 if n not in self.captured]
        idx = {n: i for i, n in enumerate(order)}

        # The columns an op-amp occupies, in node-order index space. A
        # grounded element hanging inside one of these spans would be
        # drawn straight through the triangle or its wires -- the band
        # between the node row and the rail is exactly where the op-amp
        # sits -- so such elements are bumped out to a column of their
        # own: before the span when they hang off the inverting-input
        # end (a driving source belongs on the left), after it
        # otherwise (a load belongs on the right).
        spans_idx: List[Tuple[int, int]] = []
        for e in self.opamps:
            a, b = idx.get(_op_up(e)), idx.get(e.fields[2])
            if a is not None and b is not None:
                spans_idx.append((min(a, b), max(a, b)))

        def span_of(i: int) -> Optional[Tuple[int, int]]:
            for lo, hi in spans_idx:
                if lo <= i <= hi:
                    return (lo, hi)
            return None

        def bump_target(i: int) -> Tuple[str, int]:
            """Where a grounded element on node index `i` (inside a
            span) gets its own column: ("L", j) = just before node j,
            ("R", j) = just after node j -- walked outward until the
            insertion gap is inside no span at all (cascades chain
            spans end to end)."""
            lo, hi = span_of(i)
            if i == lo:
                j, moved = lo, True
                while moved:
                    moved = False
                    for lo2, hi2 in spans_idx:
                        if lo2 < j <= hi2:
                            j, moved = lo2, True
                return ("L", j)
            j, moved = hi, True
            while moved:
                moved = False
                for lo2, hi2 in spans_idx:
                    if lo2 <= j < hi2:
                        j, moved = hi2, True
            return ("R", j)

        pre: Dict[int, List[Element]] = {}
        post: Dict[int, List[Element]] = {}
        at_node: List[Tuple[str, Element]] = []
        extra: Dict[int, List[Element]] = {}
        for n, elems in by_node.items():
            i = idx.get(n)
            if i is None:
                continue
            if span_of(i) is not None:
                side, j = bump_target(i)
                for e in elems:
                    (pre if side == "L" else post).setdefault(j, []).append(e)
            else:
                # The first element to ground hangs straight down from
                # the node; any further ones are in parallel with it and
                # need their own column, reached by a stub along the top
                # row.
                at_node.append((n, elems[0]))
                for e in elems[1:]:
                    extra.setdefault(i, []).append(e)

        # A four-terminal block (two-port, transformer) drops two legs to
        # the rail from between its two nodes, and the gutter beside a
        # node column is where the *neighbouring* element's labels live
        # -- so on adjacent columns a leg lands straight through them.
        # Measured, not guessed: it put the `100V` of AS7's Example 19.2
        # on a wire. Give the block a column of clear space to hang in.
        # A two-port takes two columns of it rather than one: the block
        # is wider than the transformer's pair of coils, and at one
        # column its left face landed on the neighbouring source's value
        # label -- the harness caught `100V` on the box. Two also puts
        # the leads at about half the block's width, which is the
        # proportion Roberto's reference has.
        idx_of = {n: k for k, n in enumerate(order)}
        spacer_after: Dict[int, int] = {}
        for e in self.spanning:
            want = 1 if (e.kind in PORT_BLOCK or e.kind == "t") else 0
            if want:
                a, b = idx_of.get(e.n1), idx_of.get(e.n2)
                if a is not None and b is not None and abs(a - b) == 1:
                    k = min(a, b)
                    spacer_after[k] = max(spacer_after.get(k, 0), want)

        col = 0
        own_col: List[Tuple[str, Element]] = []   # (node, elem) pairs
        for i, n in enumerate(order):
            for e in pre.get(i, []):
                self.elem_col[e.name] = col
                own_col.append((_ground_node(e), e))
                col += 1
            self.node_col[n] = col
            col += 1
            for e in extra.get(i, []):
                self.elem_col[e.name] = col
                own_col.append((_ground_node(e), e))
                col += 1
            for e in post.get(i, []):
                self.elem_col[e.name] = col
                own_col.append((_ground_node(e), e))
                col += 1
            col += spacer_after.get(i, 0)
        self.cols = max(col, 1)

        for n, e in at_node:
            self.elem_col[e.name] = self.node_col[n]

        stubs: List[Tuple[int, int, int]] = []
        for n, e in own_col:
            a, b = self.node_col[n], self.elem_col[e.name]
            stubs.append((min(a, b), max(a, b), 0))

        # Each element between two live nodes occupies the interval
        # between their columns on whichever row it is drawn. Two
        # intervals that share more than an endpoint cannot both sit on
        # the node row, so this colours the interval graph greedily from
        # the left and stacks the losers above, with risers back down at
        # each end.
        #
        # Bumping only exact duplicates (which is all "in parallel"
        # means) is not enough: in a bridge the element from node 1 to
        # node 3 spans *over* node 2, and would otherwise be drawn
        # straight through the two elements either side of it. Parallel
        # elements are just the special case where the two intervals are
        # identical, so one rule covers both. Stubs out to a parallel
        # ground column are pre-placed on row 0 for the same reason.
        spans = [(min(self.node_col[e.n1], self.node_col[e.n2]),
                  max(self.node_col[e.n1], self.node_col[e.n2]), e)
                 for e in self.spanning]
        # Narrow before wide: an interval nested inside another must end
        # up *below* it, so the outer element's risers drop past the
        # inner one's endpoints (a shared node -- a junction) instead of
        # the inner element's risers slicing up through the outer one's
        # body.
        spans.sort(key=lambda s: (s[1] - s[0], s[0]))

        # Width alone is not enough: an element whose endpoint column
        # sits exactly at another element's centre would send its riser
        # straight through that element's body -- the one crossing a
        # hop cannot express. Such a pair is ordered explicitly: the
        # element in the way must go above, so the riser never reaches
        # it. (Off-centre crossings land on leads and get hops.)
        above: Dict[str, set] = {}   # name -> names it must sit above
        for lo_a, hi_a, ea in spans:
            for lo_b, hi_b, eb in spans:
                if ea.name == eb.name:
                    continue
                for c in (lo_a, hi_a):
                    if lo_b < c < hi_b and 2 * c == lo_b + hi_b:
                        above.setdefault(eb.name, set()).add(ea.name)

        # Kahn's walk over those constraints, keeping the width sort as
        # the tie-break; a cycle (mutual centre hits) falls back to the
        # sorted order for whatever remains.
        ordered: List[Tuple[int, int, Element]] = []
        pending = list(spans)
        placed_names: set = set()
        while pending:
            pick = next(
                (s for s in pending
                 if above.get(s[2].name, set()) <= placed_names),
                pending[0])
            pending.remove(pick)
            placed_names.add(pick[2].name)
            ordered.append(pick)
        spans = ordered

        placed: List[Tuple[int, int, int]] = list(stubs)
        # A column that carries something down into the band below the
        # node row -- a grounded element, an op-amp's input or output
        # riser -- blocks the row above it too: an element spanning
        # straight over it on the node row would sit on the descending
        # wire's junction and read as connected to it. A zero-width
        # interval conflicts only with spans that contain the column
        # strictly, which is exactly the case to push up.
        for c in self.elem_col.values():
            placed.append((c, c, 0))
        for e in self.opamps:
            for n in (_op_up(e), e.fields[2]):
                c = self.node_col.get(n)
                if c is not None:
                    placed.append((c, c, 0))
        for lo, hi, e in spans:
            lvl = max((self.level[a] + 1 for a in above.get(e.name, ())
                       if a in self.level), default=0)
            while any(l == lvl and min(hi, h) > max(lo, o)
                      for o, h, l in placed):
                lvl += 1
            self.level[e.name] = lvl
            placed.append((lo, hi, lvl))
        self.max_level = max(self.level.values(), default=0)
        # What actually occupies the node row, for gap_free below.
        self.row0 = [(lo, hi) for lo, hi, l in placed
                     if l == 0 and hi > lo]

        # Op-amps all sit in one horizontal band, so two whose
        # input-to-output columns overlap would be drawn through each
        # other. Identical colouring to the rows above, except the
        # colours become lanes *down* the band. A cascade keeps lane 0
        # throughout, since each stage owns its own columns.
        ops = []
        for e in self.opamps:
            a = self.node_col.get(_op_up(e))
            b = self.node_col.get(e.fields[2])
            if a is not None and b is not None:
                ops.append((min(a, b), max(a, b), e))
        ops.sort(key=lambda s: (s[0], s[1]))
        taken: List[Tuple[int, int, int]] = []
        for lo, hi, e in ops:
            lane = 0
            while any(l == lane and min(hi, h) > max(lo, o)
                      for o, h, l in taken):
                lane += 1
            self.op_lane[e.name] = lane
            taken.append((lo, hi, lane))
        self.max_op_lane = max(self.op_lane.values(), default=0)

    def gap_free(self, c: int) -> bool:
        """True when the node row between column c and column c+1
        carries nothing -- no element, no stub -- so a wire may run
        along it and join a node at its own corner."""
        return not any(lo <= c < hi for lo, hi in self.row0)

    # pixel helpers
    def px(self, col: int) -> float:
        return MARGIN + col * COL_W

    @property
    def stack_h(self) -> float:
        """Height of one stacked row. `STACK_H` unless a lifted branch
        carries a current arrow, which hangs `_mark_stack()` further
        down than anything the plain number was measured against."""
        lifted = any(self.level.get(n, 0) > 0 for n in self.i_ref)
        return STACK_H + (_mark_stack() if lifted else 0.0)

    @property
    def y_top(self) -> float:
        return MARGIN + self.max_level * self.stack_h

    @property
    def y_bot(self) -> float:
        return self.y_top + ROW_H + self.max_op_lane * OP_LANE_H


# --- rendering ------------------------------------------------------

def _draw_opamp(cv: _Canvas, lay: _Layout, e: Element) -> Optional[float]:
    """Ideal op-amp (nullor): a triangle between the inverting input's
    column and the output's column, sitting below the top row.

    The inverting input is drawn as the *upper* pin and the
    non-inverting as the lower one -- the opposite of the usual
    convention, but it is what keeps the two input wires from crossing
    in the common case, where the inverting input comes down from the
    node row and the non-inverting one goes to ground.

    Returns the x at which its non-inverting input meets the ground
    rail, or None if that input is not grounded -- the caller needs it
    to size the rail, or the wire drawn here would dangle.

    When the *inverting* input is the grounded one (`o,1,0,o`), the
    pins swap: the non-inverting input takes the upper position and
    its node's column, and the inverting one drops to the rail."""
    n_out = e.fields[2]
    up_node = _op_up(e)                             # wired to the top row
    flip = up_node != e.fields[1]                   # n- grounded, pins swap
    dn_node = e.fields[1] if flip else e.fields[0]  # rail, or its own row
    up_sign, dn_sign = ("+", "−") if flip else ("−", "+")
    x_in = lay.px(lay.node_col[up_node]) if up_node in lay.node_col \
        else lay.px(0)
    x_out = lay.px(lay.node_col[n_out]) if n_out in lay.node_col \
        else x_in + COL_W

    lane = lay.op_lane.get(e.name, 0)
    mid = lay.y_top + ROW_H / 2.0 + lane * OP_LANE_H
    # The triangle is a fixed equilateral symbol, centred in the gap
    # between the input and output columns. Widening it to span whatever
    # gap it happens to sit in would be the easy way to make the wires
    # meet, but it distorts the symbol; the leads stretch instead.
    h = 58.0
    w = h * 3 ** 0.5 / 2.0
    tx = max((x_in + x_out) / 2.0 - w / 2.0, x_in + 26)
    y_minus, y_plus = mid - h / 4.0, mid + h / 4.0

    cv.raw('<path d="M{0:g} {1:g} L{0:g} {2:g} L{3:g} {4:g} Z" fill="none"/>'
           .format(tx, mid - h / 2, mid + h / 2, tx + w, mid),
           (tx, mid - h / 2), (tx + w, mid + h / 2))
    cv.obstacle(tx, mid - h / 2, tx + w, mid + h / 2)
    cv.ink(tx, mid - h / 2, tx + w, mid + h / 2)
    # The pin signs are stroked marks, not text glyphs, so they match
    # the voltage source's polarity marks in weight and size (#130).
    _sign_mark(cv, tx + 13, y_minus, up_sign == "+")
    _sign_mark(cv, tx + 13, y_plus, dn_sign == "+")
    # The feedback loop drawn when the output cannot go right (below)
    # passes over the triangle's top, where the name normally sits, so
    # the name yields the spot and moves under the body instead.
    loop = x_out < tx + w + 12
    cv.runs(tx + w / 2,
            mid + h / 2 + GAP + LABEL_ASCENT if loop
            else mid - h / 2 - GAP - _name_below(),
            _name_runs(e.name))

    # upper input: straight down from its node, then in
    cv.wire(x_in, lay.y_top, x_in, y_minus)
    cv.wire(x_in, y_minus, tx, y_minus)
    # When several op-amps hang off one input node they share that
    # vertical wire, so the branch to this one is a T-junction and needs
    # a dot -- but only if another op-amp continues on past it.
    if any(o.name != e.name and _op_up(o) == up_node
           and lay.op_lane.get(o.name, 0) > lane for o in lay.opamps):
        cv.dot(x_in, y_minus)
    # lower input: out to the left, then down to the rail (or up to its
    # own node row if it is not grounded)
    x_p = x_in - 30 - lane * 16
    cv.wire(tx, y_plus, x_p, y_plus)
    src = lay.op_src.get(e.name)
    if src is not None:
        # The stage's driving source, drawn in the input drop itself:
        # nothing else touches this input node, so the textbook picture
        # -- input straight down through the source to ground -- is
        # available. The node still gets its name, beside the drop.
        dep = src.name in lay.controlled
        mv, mi = src.name in lay.v_ref, src.name in lay.i_ref
        if _ground_node(src) == src.n1:
            _draw_element(cv, src, x_p, y_plus, x_p, lay.y_bot, dep, mv, mi,
                          lay.refs)
        else:
            _draw_element(cv, src, x_p, lay.y_bot, x_p, y_plus, dep, mv, mi,
                          lay.refs)
        # Same clearance rule as every other node name: the wire this
        # sits over is at y_plus, and a node can be called `p`.
        cv.text(x_p + 6, y_plus - _HALF - GAP - LABEL_DESCENT,
                dn_node, "start")
        grounded_at: Optional[float] = x_p
    elif dn_node == "0":
        cv.wire(x_p, y_plus, x_p, lay.y_bot)
        grounded_at = x_p
    else:
        grounded_at = None
        xp_node = lay.px(lay.node_col[dn_node])
        dn_col, up_col = lay.node_col[dn_node], lay.node_col.get(up_node)
        if up_col is not None and dn_col == up_col - 1 \
                and lay.gap_free(dn_col):
            # The column to the left is the input's own node and the
            # row between them is empty: rise to the node row and join
            # the node at its corner -- one junction point, no tee.
            cv.wire(x_p, y_plus, x_p, lay.y_top)
            cv.wire(x_p, lay.y_top, xp_node, lay.y_top)
        else:
            # 16px below the node row: far enough under a row-0
            # element's body (zigzags reach 9px down) that the parallel
            # run reads as a separate wire rather than a graze.
            cv.wire(x_p, y_plus, x_p, lay.y_top + 16)
            cv.wire(x_p, lay.y_top + 16, xp_node, lay.y_top + 16)
            cv.wire(xp_node, lay.y_top + 16, xp_node, lay.y_top)
    # output: up to its node on the top row. When the output node's
    # column is not comfortably right of the triangle -- above all the
    # follower written `o1,1,2,2`, whose output *is* its inverting
    # input -- the straight run would slice back through the body, so
    # the wire loops over the top instead: out of the tip, up past the
    # inverting lead, and back to the column it belongs to.
    tip = tx + w
    out_col = lay.node_col.get(n_out)
    if not loop:
        cv.wire(tip, mid, x_out, mid)
        cv.wire(x_out, mid, x_out, lay.y_top)
    elif abs(x_out - x_in) < 0.5 and out_col is not None \
            and lay.gap_free(out_col):
        # Nothing on the node row to the right of the output node: the
        # feedback leaves the tip the way the triangle points, turns
        # up, and joins the node at its own corner, so the corner's
        # junction dot is the only dot.
        xl = tip + 16
        cv.wire(tip, mid, xl, mid)
        cv.wire(xl, mid, xl, lay.y_top)
        cv.wire(xl, lay.y_top, x_out, lay.y_top)
    else:
        # The row is occupied: out of the tip, up, and join the input
        # riser just above the triangle instead -- 12px above the top
        # vertex, measured from the body, not the inverting lead, or
        # the wire grazes the corner.
        xl, yl = tip + 16, mid - h / 2 - 12
        cv.wire(tip, mid, xl, mid)
        cv.wire(xl, mid, xl, yl)
        cv.wire(xl, yl, x_out, yl)
        if abs(x_out - x_in) < 0.5:
            # Joins the inverting input's own riser: a real junction.
            cv.dot(x_out, yl)
        else:
            cv.wire(x_out, yl, x_out, lay.y_top)
    return grounded_at


def _ground_symbol(cv: _Canvas, x: float, y: float) -> None:
    """The stem, the three bars and the node's name. Drawn wherever the
    rail deserves saying so out loud rather than being traced.

    The name sits centred *under* the bars (Roberto, 1 Sep 2026). Beside
    them it had to know which side it had room on -- a symbol set left of
    a two-port has the block immediately to its right -- and underneath
    there is never anything to collide with."""
    parts = ['<path d="M{0:g} {1:g} L{0:g} {2:g}"/>'.format(x, y, y + 12)]
    for i, half in enumerate((11.0, 7.0, 3.0)):
        yy = y + 12 + i * 4
        parts.append('<path d="M{0:g} {1:g} L{2:g} {1:g}"/>'
                     .format(x - half, yy, x + half))
    cv.raw("".join(parts), (x - 11, y), (x + 11, y + 20))
    cv.ink(x - 11, y, x + 11, y + 20)
    # Name the reference node, same as every other node is named -- "0"
    # is a node in the description like any other, and readers tracing
    # v_2 back to its reference need to see it.
    cv.text(x, y + 20 + LABEL_ASCENT + GAP, "0")


def _render(elements: List[Element]) -> str:
    lay = _Layout(elements)
    cv = _Canvas()
    y_top, y_bot = lay.y_top, lay.y_bot
    # Oriented segment per element, n1 end first: the coupling dots need
    # to know which way round each coil was actually drawn.
    segs: Dict[str, Tuple[float, float, float, float]] = {}

    # 1. elements between two non-ground nodes, along the top row
    ground_x: List[float] = []
    # extra places to draw the symbol, each with the side its name
    # has room on
    ground_marks: List[float] = []
    # a two-port's left and right faces, which the rail stops at
    port_spans: List[Tuple[float, float]] = []
    for e in lay.spanning:
        lvl = lay.level[e.name]
        y = y_top - lvl * lay.stack_h
        xa, xb = lay.px(lay.node_col[e.n1]), lay.px(lay.node_col[e.n2])
        if e.kind == "t" or e.kind in PORT_BLOCK:
            # Four terminals: these ground their own lower pair, so they
            # never sit on a stacked level and never carry a single
            # series current the way a two-terminal element does.
            if e.kind == "t":
                legs = _draw_transformer(cv, e, xa, xb, y_top, y_bot)
                ground_x += legs
                # Both windings return to the rail, so one symbol
                # between their two feet says it once for the pair.
                ground_marks.append(sum(legs) / len(legs))
            else:
                legs = _draw_port_box(cv, e, xa, xb, y_top, y_bot)
                # Both lower terminals are ground, and each says so
                # where it is rather than making the reader trace the
                # rail to a symbol at the far end of the drawing
                # (Roberto, 1 Sep 2026). Set just past the box, since
                # its lower edge now hangs below the rail and a symbol
                # on the terminal itself would be drawn inside it.
                # The symbol goes at the foot of each leg, which is
                # already the midpoint of its gap -- offsetting it from
                # there is what put it back against the node column.
                ground_x += legs
                ground_marks += legs
                port_spans.append((legs[0], legs[1]))
            segs[e.name] = (xa, y_top, xb, y_top)
            continue
        if lvl:
            # a stacked parallel branch: risers at each end back down to
            # the row the node actually lives on
            cv.wire(xa, y_top, xa, y)
            cv.wire(xb, y_top, xb, y)
        _draw_element(cv, e, xa, y, xb, y, e.name in lay.controlled,
                      e.name in lay.v_ref, e.name in lay.i_ref, lay.refs)
        segs[e.name] = (xa, y, xb, y)

    # 2. elements with one terminal on ground, hanging down to the rail
    for e in lay.grounded:
        n = e.n2 if e.n1 == "0" else e.n1
        col_e, col_n = lay.elem_col[e.name], lay.node_col[n]
        x, xn = lay.px(col_e), lay.px(col_n)
        if col_e != col_n:
            cv.wire(xn, y_top, x, y_top)   # stub to a parallel column
        # keep n1 at the end the element was declared from
        if e.n1 == "0":
            segs[e.name] = (x, y_bot, x, y_top)
        else:
            segs[e.name] = (x, y_top, x, y_bot)
        _draw_element(cv, e, *segs[e.name],
                      dependent=e.name in lay.controlled,
                      mark_v=e.name in lay.v_ref,
                      mark_i=e.name in lay.i_ref,
                      refs=lay.refs)
        ground_x.append(x)

    # 3. op-amps, before the rail is sized: a grounded non-inverting
    #    input is one more thing the rail has to reach.
    for e in lay.opamps:
        gx = _draw_opamp(cv, lay, e)
        if gx is not None:
            ground_x.append(gx)

    # 4. the ground rail, plus a symbol wherever it is worth naming
    if ground_x:
        gx0, gx1 = min(ground_x), max(ground_x)
        # The rail stops at a two-port's left face and picks up again at
        # its right one: it must not be drawn *through* the block, which
        # would read as a wire crossing the box (Roberto, 1 Sep 2026).
        # The block's own two lower terminals are where the rail ends
        # and begins, and they are ground in their own right.
        cut = sorted(port_spans)
        segments, run = [], gx0
        for a, b in cut:
            if a > run:
                segments.append((run, a))
            run = max(run, b)
        if gx1 > run:
            segments.append((run, gx1))
        if not segments:
            segments = [(gx0, gx1)]
        for lo, hi in segments:
            cv.wire(lo, y_bot, hi, y_bot)
        edges = {x for span in cut for x in span}
        for x in sorted(set(ground_x)):
            # No dot where the rail merely arrives at a block's terminal
            # -- nothing branches there, the wire simply ends.
            if gx0 < x < gx1 and x not in edges:
                cv.dot(x, y_bot)
        # The rail's own symbol, and one at each extra place a block
        # asked for. `_ground_symbol` draws it raw rather than as wires:
        # the bars are decoration, and the wire pass would otherwise
        # mistake the stem meeting the first bar for a T-junction and
        # dot it.
        # **One symbol per run of rail**, and no more (Roberto,
        # 1 Sep 2026: "there should not be two ground nodes on the same
        # line"). Where a block asked for one, that is the one -- it
        # sits in the middle of its own gap, which is nearer to what the
        # reader is looking at than the far-left end of the drawing.
        # Otherwise the run is named at its left end, as it always was.
        # A two-port gets two symbols not by exception but because it
        # cuts the rail in two, and each half is a run of its own.
        for lo, hi in segments:
            inside = [m for m in ground_marks if lo - 0.5 <= m <= hi + 0.5]
            _ground_symbol(cv, inside[0] if inside else lo, y_bot)
        cv.raw("", (gx0 - 14, y_bot + 26))

    # 5. junction dots on the top row wherever three or more things meet
    touching: Dict[str, int] = {}
    for e in elements:
        terms = e.fields[:3] if e.kind == "o" else (
            [] if e.kind == "m" else [e.n1, e.n2])
        for n in terms:
            touching[n] = touching.get(n, 0) + 1
    for n, count in touching.items():
        if n != "0" and count >= 3 and n in lay.node_col:
            cv.dot(lay.px(lay.node_col[n]), y_top)

    # 6. node names, tucked just above the row -- clear of the wire by
    #    GAP like everything else. A node can be called `ag` or `bg`
    #    (the three-phase books do), and those hang below the baseline.
    for n, col in lay.node_col.items():
        cv.text(lay.px(col) + 6,
                y_top - _HALF - GAP - LABEL_DESCENT, n, "start")

    # 7. the caption block, below the drawing: values too long to
    #    letter at their element (the element keeps its name, see
    #    _draw_element), then the mutual inductances -- `m` couples two
    #    *elements*, not two nodes, so there is no honest place to put
    #    it on the circuit itself; a dashed tie between the two coils
    #    just reads as another wire.
    #    The coupling polarity, though, does belong on the circuit: a
    #    dot at each coupled coil's n1 terminal, which is what the
    #    solver's sign convention means in schematic notation (see
    #    `_coupling_dot`).
    for e in lay.mutuals:
        for coil in (e.n1, e.n2):
            if coil in segs:
                _coupling_dot(cv, *segs[coil])
    # Captions are runs too, so `r1 = ...` reads R_1 there as well as
    # at the element -- a caption exists only because the value would
    # not fit beside the symbol, and the two have to name the same
    # thing in the same hand.
    captions: List[List[Tuple[str, bool]]] = []
    drawn = set(segs) | {src.name for src in lay.op_src.values()}
    for e in elements:
        if e.kind == "m" or e.name not in drawn:
            continue
        val_runs = _value_runs(e, lay.refs)
        if len(_flat(val_runs)) > CAPTION_LEN:
            captions.append(_name_runs(e.name) + [(" = ", False)] + val_runs)
    for e in lay.mutuals:
        # `m`'s two fields are coupled *coils*, not nodes, so they are
        # element names and get the same treatment.
        captions.append(
            _name_runs(e.name) + [(" = ", False)] + _value_runs(e, lay.refs)
            + [("  (couples ", False)]
            + _name_runs(e.n1) + [(" and ", False)]
            + _name_runs(e.n2) + [(")", False)])
    if captions:
        cx, cy = cv.x0, cv.y1 + 26
        for i, line in enumerate(captions):
            cv.runs(cx, cy + i * 17, line, "start")

    # 8. emit the collected wires -- merged, with junction dots at every
    #    T-joint and a semicircular hop wherever two wires cross without
    #    connecting.
    cv.flush()

    x0, y0 = cv.x0 - 26, cv.y0 - 26
    w, h = (cv.x1 - cv.x0) + 52, (cv.y1 - cv.y0) + 52
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'viewBox="{0:g} {1:g} {2:g} {3:g}" width="{2:g}" height="{3:g}" '
        'fill="none" stroke="currentColor" stroke-width="1.7" '
        'stroke-linecap="round" stroke-linejoin="round" '
        'class="symbulator-schematic">'
        '<style>.symbulator-schematic .lbl{{font:13px/1 ui-sans-serif,'
        'system-ui,sans-serif;fill:currentColor;stroke:none}}'
        '.symbulator-schematic .sub{{font-size:{5:g}em}}</style>'
        '{4}</svg>'
    ).format(x0, y0, w, h, "".join(cv.parts), SUB_SCALE)


def to_svg(desc: str) -> str:
    """Render a Symbulator circuit description as a standalone SVG
    string.

    >>> "svg" in to_svg("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k")
    True
    """
    return _render(parse_circuit(desc, expand_si=False))


def draw(desc: str):
    """Same as `to_svg`, wrapped so a notebook or the browser build can
    display it directly."""
    svg = to_svg(desc)
    try:
        from IPython.display import SVG      # type: ignore
        return SVG(svg)
    except ImportError:
        return svg


# LIMITATIONS (prototype)
# -----------------------
# * A bridge draws correctly -- the element spanning over an
#   intermediate node is lifted to its own row -- but as a ladder with a
#   jumper over the top, not as the diamond most textbooks print.
#   Recognising a bridge as a diamond means special-casing the topology,
#   which the interval stacking deliberately avoids.
# * A coupled inductor carries one polarity dot, so an inductor coupled
#   to two others with opposite signs cannot be drawn faithfully -- the
#   dot convention itself has no notation for it. The caption still
#   shows each M with its sign.
# * A non-grounded op-amp `+` input is routed just under the node row;
#   where it crosses another wire the crossing is drawn as a hop, but a
#   dense multi-amp circuit can still accumulate several hops.
# * A two-port block (z/y/h/g/a/b) draws as a labelled box in line
#   between its two nodes, without its port parameters. Richer symbols
#   were built on 1 Sep 2026 -- four terminals, the lower pair to the
#   rail, which is what `engine._stamp_two_port` actually models, since
#   it reads both port voltages against ground -- and Roberto's ruling
#   was that they cluttered the drawing more than they informed it. The
#   box does not show the ground return and does not show that the two
#   port currents differ; the parameters and the answers do.
# * The transformer `t` does have a symbol: two windings facing a core,
#   with the polarity dots and the turns ratio. Its lower terminals go
#   to the rail, which is not a stylistic choice -- `engine._stamp_t`
#   reads both winding voltages against ground.
# * A name is drawn upper-cased with its underscores closed up, so the
#   display is many-to-one: `rab`, `rAB` and `r_a_b` all read R_AB. The
#   drawing is the only place this happens -- the answers, the caption
#   text of an export and the description itself keep the name as it
#   was typed -- and a circuit that distinguishes two elements by case
#   or by an underscore alone is already hard to read on the page.
# * The reference marks a control adds (a polarity pair, a labelled
#   arrow) are only drawn on the two-terminal kinds. An op-amp, a
#   transformer or a two-port block has no single pair of terminals for
#   a sign to sit at, so a source reading one of those is a diamond with
#   nothing on the far end of the sentence.
# * A controlled source is recognised from its value referring to some
#   other quantity in the circuit, which is exactly how the solver
#   reads it. A source written with a symbolic value that happens to
#   collide with a node name (`e1,1,0,vs` in a circuit with a node `s`)
#   is therefore drawn as a diamond -- correctly, since that is what it
#   solves as, but it can surprise someone who meant `vs` as a free
#   parameter and did not notice the collision.
