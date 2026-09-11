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
BLOCK_LANE_H = ROW_H + 100  # a second block, below the first (#321):
                            # one band, plus room for the upper block's
                            # lead bus, its 12px overhang and a ground
                            # symbol under it
# 78 until #213, which is when the arithmetic was first done rather
# than eyeballed. A lifted source hangs its value *below* its
# circle, and the element on the row beneath carries a value and a
# name stacked *above* its own: 34.75 down, 45.35 up, and GAP
# between them is 84.1 -- a stack of 78 had them overlapping by
# 0.4px since #212 gave every name a subscript, under the review
# harness's 2px tolerance and so invisible until the values gained
# subscripts too and it grew to 2.1.
OP_LANE_H = 78     # extra height per extra op-amp lane
OP_ABOVE_GAP = 60  # clear strip between an above-row body and the row
OP_ABOVE_H = 126    # the band an above-row op-amp adds over the row
OP_UNDER_H = 52    # extra height for a non-inverting input routed under
OP_INK_BANDS = 24  # staircase steps modelling the op-amp wedge's ink
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
# Roberto, 11 Sep 2026: the independent source about 10% larger. 15.0
# until then, which is what every note before this date refers to.
SRC_R = 16.5       # independent source outline radius
# Roberto, 1 Sep 2026: the resistor 20% smaller, the dependent source
# 10% larger. Both are pure scale factors on the one number each shape
# is built from, so nothing else in the geometry has to be re-derived --
# the zigzag's vertex angle, and so its mitre, is unchanged because its
# length and its amplitude scale together.
R_SCALE = 0.8      # the resistor, against the other bodies' BODY
# The dependent source's diamond is built from `SRC_R`, so growing the
# independent source would have grown it too. `DEP_R` is held at the
# 16.5 it has been since 1 Sep 2026 and the scale reads 1.0 -- which
# means the two are now the *same* size, and the 10% that used to tell
# a dependent source from an independent one at a glance is gone.
# Flagged for Roberto: 1.1 here restores the difference at 18.15.
DEP_SCALE = 1.0    # the dependent source's diamond, against SRC_R
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


def _runs_width(runs: List[Tuple]) -> float:
    """The rendered width `_Canvas.runs` would bound this label at.

    The same 7.2px average advance, in one place, so a caller that has
    to know where a label's edge falls -- #338's op-amp name, which is
    set against a sloping edge -- cannot drift from what is drawn."""
    return sum(len(r[0]) * (7.2 * SUB_SCALE if r[1] else 7.2) for r in runs)


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
# The opening of a hop's arc, as `_hop_path` writes it. Counting these
# is how a drawing's crossings are counted (`_cost`).
_HOP_ARC = "A{0:g} {0:g} 0 0".format(HOP_R)
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
        # Where each label's ink lands, in canvas coordinates. Recorded
        # for the same reason `ink` is: a measurement that looks only at
        # symbols measures half the picture, and it is the labels that
        # collide. #212 cost three rounds to that distinction.
        self.labels: List[Tuple[float, float, float, float]] = []

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
             runs: List[Tuple], anchor: str = "middle",
             cls: str = "lbl") -> None:
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
        self.labels.append((x0, y - LABEL_ASCENT, x1, y + low))
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
            '<text class="{4}" x="{0:g}" y="{1:g}" text-anchor="{2}">{3}</text>'
            .format(x, y, anchor, "".join(body), cls))

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
                   y_top: float, y_bot: float, four: bool = False,
                   legs_to_rail: bool = True):
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
    # With all four terminals named (#314, `four`) the block does not
    # reach the rail: it stops 30px short of it, so the rail can pass
    # beneath uncut and the lower leads can leave sideways above it --
    # at 108px below the row, which is past the body of anything hanging
    # in a neighbouring column (52..98), so a lead that crosses one
    # crosses its wire, with a hop, never its symbol, and leaves room
    # under the box for the common-bottom case's return wire. Otherwise
    # the height is the whole band, as before.
    box_h = (y_bot - 30.0 - by0) if four else PORT_BOX_H
    by1 = by0 + box_h
    low = by1 - PORT_BOX_OVER          # the lower terminals' own line
    cv.raw('<rect x="{0:g}" y="{1:g}" width="{2:g}" height="{3:g}" '
           'fill="none"/>'.format(bx0, by0, PORT_BOX_W, box_h),
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
    if not four and legs_to_rail:
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
    if four or not legs_to_rail:
        # the lower terminals, on the faces, for the caller to route --
        # or, for a two-node block with another hanging below it
        # (#321), for a ground symbol each instead of legs to the rail
        return [(bx0, low), (bx1, low)]
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
# How far below the node row a four-terminal transformer's windings
# reach (#314): the coil body sits centred in that span, and the lower
# leads turn sideways at its foot -- 32px above the rail, and past the
# body of anything hanging in a neighbouring column (52..98 below the
# row), so a lead that crosses one crosses its wire, with a hop, never
# its symbol.
TRANS_FOUR_SPAN = 118.0


def _draw_transformer(cv: _Canvas, e: Element, xa: float, xb: float,
                      y_top: float, y_bot: float, four: bool = False):
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
    # With all four terminals named (#314, `four`) the windings stop
    # short of the rail, at `foot`, where their lower leads are routed
    # sideways by the caller; the rail passes beneath. Otherwise they
    # run the whole band down to it, as before.
    foot = (y_top + TRANS_FOUR_SPAN) if four else y_bot
    lead = (foot - y_top - BODY) / 2.0
    top, bot = y_top + lead, y_top + lead + BODY

    # The primary is mirrored about its own axis, so the two windings
    # face each other across the core instead of both spiralling the
    # same way. `scale(1,-1)` inside the rotated frame flips the local
    # y that the rotation has already turned into screen x -- and it
    # flips the coil's ends with it, which is what keeps the pair a true
    # mirror rather than one coil slid over.
    for x, flip in ((xp, " scale(1,-1)"), (xs, "")):
        cv.raw('<g transform="translate({0:g},{1:g}) rotate(90){3}">{2}</g>'
               .format(x, y_top, _body_l(foot - y_top), flip),
               (x - REACH["l"], top), (x + REACH["l"], bot))
        cv.ink(x - REACH["l"], top, x + REACH["l"], bot)
        cv.eseg(x, y_top, x, foot, half=BODY / 2.0)
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
    for f in e.turns:                   # bare pair or [N1,N2] (#314)
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
        shown = [_round_long_floats(str(f)) for f in e.turns]
    ratio = "{0} : {1}".format(*shown)
    # The ratio sits between the two windings' upper leads, which are
    # 2*TRANS_OFF apart: `1 : 2` fits, `80 : 80+120` does not and was
    # printed through both leads (the pixel harness caught it on AS7's
    # Example 13.11, 0.5px). A ratio wider than the gap goes above the
    # node row instead, where the leads are horizontal and the nearest
    # node name is a column away; the name follows it up.
    est_w = 6.0 * len(ratio)          # 13px sans: `1 : 2` measures ~26px
    if est_w > 2 * TRANS_OFF - 2 * _HALF - GAP:
        ratio_y = y_top - _HALF - GAP - LABEL_DESCENT
    else:
        ratio_y = top - 9
    cv.text(mid, ratio_y, ratio)
    cv.runs(mid, ratio_y - LABEL_ASCENT - LABEL_GAP - _name_below(),
            _name_runs(e.name))
    if four:
        # the lower terminals, at the windings' feet, for the caller
        return [(xp, foot), (xs, foot)]
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
            # a port element's terminals: two, or four (#314)
            for n in e.nodes:
                note(n)

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
        if e.kind in PORT_BLOCK or e.kind == "t":
            # The two tops must be adjacent, as always -- the symbol
            # spans between them -- and each bottom wants to sit beside
            # its own top, which the reordering below then enforces.
            (tl, bl), (tr, br) = e.port_nodes
            link(tl, tr)
            link(tl, bl)
            link(tr, br)
        elif e.kind not in ("m", "o"):
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

    # A four-terminal block's bottom nodes (#314) are drawn by leads
    # that leave its lower terminals sideways, rise through a clear
    # column beside the block and join their nodes on the row. That
    # only reads cleanly when each bottom node's column is the one
    # right beside its own top's, on the block's outer side -- so the
    # walk's order is amended: the left port's bottom moves to just
    # before its top, the right port's to just after. A bottom that is
    # also one of the tops, or both bottoms the same node (a common
    # terminal), takes the left side once.
    for e in elements:
        if _drawn_four(e):
            (tl, bl), (tr, br) = e.port_nodes
            if tl not in order or tr not in order:
                continue
            left, right = (tl, tr) if order.index(tl) < order.index(tr) \
                else (tr, tl)
            lb = bl if left == tl else br
            rb = br if left == tl else bl
            for node, side in ((lb, "L"), (rb, "R")):
                if node == "0" or node in (tl, tr):
                    continue
                if side == "R" and node == lb:
                    continue          # common bottom: placed once, left
                order.remove(node)
                at = order.index(left) if side == "L" else order.index(right) + 1
                order.insert(at, node)

    # A feedback divider reads output-first. When an op-amp's routed
    # input carries exactly a pair -- one element back to the output
    # node, one down to ground -- put the **output node before that
    # input**, so the feedback element runs left to right from the
    # output to the divider node and sits in the row between them.
    #
    # Ordered the other way (which is what the walk produces, the
    # inverting input being linked to the output and reached first) the
    # same element spans two adjacent columns backwards and is drawn
    # arcing *over* the node row, with the divider node and the output
    # sharing a row wire -- so the grounded half of the divider looks
    # like it hangs off the output, and the inverting input has nowhere
    # to return but a rectangle under the body.
    #
    # Roberto, 10 Sep 2026, having redrawn AS2's Practice Problem 5.5 by
    # hand: *"the trick was flipping the resistor."* It is the whole fix
    # for the non-inverting stage, and it is an ordering question, not a
    # routing one.
    for e in elements:
        if e.kind != "o":
            continue
        nout = e.fields[2]
        for dn in (e.fields[1], e.fields[0]):
            if dn == "0" or dn not in order or nout not in order:
                continue
            if not _is_feedback_divider(elements, e, dn):
                continue
            other = e.fields[0] if dn == e.fields[1] else e.fields[1]
            # ...nor when the other input is ground. That is a plain
            # inverting stage: the summing node is the *input* side and
            # belongs first, with its input resistor to the left of the
            # triangle, which is where the drawing already put it.
            # Reordering drags that resistor across to the right --
            # Bo2's Example 5.5 and Drill Exercise 5.5 (`o,0,1,o`),
            # 10 Sep 2026.
            if other == "0":
                continue
            ous = [u for u in elements if u.kind != "o" and other in u.nodes]
            if (len(ous) == 1 and ous[0].kind in ("e", "j")
                    and "0" in (ous[0].n1, ous[0].n2)):
                continue
            # ...but not when the *other* input carries a lone grounded
            # source. That is the classic non-inverting stage, whose
            # source is drawn under the triangle by the capture pass and
            # whose layout is already the textbook one -- swapping there
            # moves a drawing that was right. Seven of the book's, on
            # 10 Sep 2026: AS2's Example 5.2 and Figure 5.16, Bo2's
            # Example 3.1, TR5's Example 4-17 and kin.

            i, j = order.index(dn), order.index(nout)
            if i < j:
                order[i], order[j] = order[j], order[i]
    return order


def _ground_node(e: Element) -> str:
    """The non-ground terminal of a grounded two-terminal element."""
    return e.n2 if e.n1 == "0" else e.n1


def _port_tops(e: Element) -> Tuple[str, str]:
    """The two top terminals of a transformer or two-port block, in
    either form -- the nodes its symbol spans between."""
    (tl, _bl), (tr, _br) = e.port_nodes
    return tl, tr


def _laned(e: Element) -> bool:
    """A parameter block, in either form: the kind that goes into a
    lane when it overlaps another (#321). Transformers keep the row."""
    return e.kind in PORT_BLOCK


def _drawn_four(e: Element) -> bool:
    """Does this transformer or two-port want the four-terminal
    drawing? Only when at least one of its bottoms is a live node:
    `z,[1,0],[2,0]` is the two-node form written out and draws exactly
    as `z,1,2` does, rail cut, two ground symbols and all (#314)."""
    if not ((e.kind in PORT_BLOCK or e.kind == "t") and e.four_node):
        return False
    (_tl, bl), (_tr, br) = e.port_nodes
    return bl != "0" or br != "0"


def _op_up(e: Element) -> str:
    """The op-amp input drawn wired to the node row: the inverting
    input normally, but the non-inverting one when the inverting input
    is ground -- `o,1,0,o` is written that way round, and treating "0"
    as a column would run the input riser through whatever hangs on
    the leftmost column.

    **This is the ordering answer, and `_Layout.op_up` is the drawing
    one.** The function has two jobs: `_node_order` walks
    `_op_up -> output` before any resistor edge, which is what makes a
    cascade come out left to right, and the layout uses it to decide
    which input is wired to the row. Changing it to suit the drawing
    breaks the ordering -- tried on 10 Sep 2026, and both op-amps of a
    cascade landed in the same column. So the drawing gets its own
    decision, taken once the columns are known; see `_Layout.op_up`."""
    return e.fields[1] if e.fields[1] != "0" else e.fields[0]


def _up_of(lay: "_Layout", e: Element) -> str:
    """The input this op-amp is *drawn* with wired to the node row."""
    return lay.op_up.get(e.name) or _op_up(e)


def _is_feedback_divider(elements: List[Element], op: Element,
                         dn: str) -> bool:
    """Does `dn` -- an op-amp input -- carry nothing but a divider
    between the output node and ground?

    Every element on the node must go to one or the other, with at least
    one of each. The feedback side may be **several elements in
    parallel**: AS7's Problem 10.77 puts a resistor and a capacitor
    across it, which a strict pair test rejected, so its drawing kept the
    layout this predicate exists to fix (Roberto, 10 Sep 2026).
    """
    if dn == "0":
        return False
    nout = op.fields[2]
    users = [u for u in elements if u.kind != "o" and dn in u.nodes]
    if not users:
        return False
    to_out = [u for u in users if nout in u.nodes]
    to_gnd = [u for u in users if "0" in u.nodes]
    if not to_out or not to_gnd:
        return False
    return len(to_out) + len(to_gnd) == len(users)


def _op_under(lay: "_Layout", e: Element) -> bool:
    """Whether this op-amp's downward input takes the route *under* the
    body rather than over the node row (#337).

    It does when that input's node sits to the **right** of the input
    riser. The over-the-top route leaves the pin going left, climbs to
    16px under the node row and then runs the whole width back to the
    right -- straight across the riser it just left and across whatever
    the node row carries in between. Roberto's reading of the picture,
    8 Sep 2026: go down and right instead, and the crossings are gone.

    Asked by the layout (which has to make the drawing taller for it)
    and by the drawing itself, so it lives outside both."""
    if lay.op_src.get(e.name) is not None:
        return False
    up = _up_of(lay, e)
    flip = up != e.fields[1]
    dn = e.fields[1] if flip else e.fields[0]
    if dn == "0" or dn not in lay.node_col or up not in lay.node_col:
        return False
    return lay.node_col[dn] > lay.node_col[up]


class _Layout:
    """Column assignment and the resulting pixel geometry."""

    def __init__(self, elements: List[Element],
                 allow_raise: Optional[set] = None,
                 allow_above: Optional[set] = None,
                 row_h: Optional[float] = None,
                 gaps: Optional[List[float]] = None) -> None:
        # Which op-amps this pass may draw on the node row, and which
        # above it. `None` means every candidate; `_render` calls the
        # layout once per combination and keeps the cheapest drawing
        # (see `_cost`).
        self.allow_raise = allow_raise
        self.allow_above = allow_above
        # The band between the node row and the ground rail. `ROW_H`
        # unless a measuring pass has found the room is not needed --
        # see `_tighten_band`. The balloon deflates as well as inflates
        # (Roberto, 10 Sep 2026).
        self.row_h = float(ROW_H if row_h is None else row_h)
        # The width of each column gap, or empty for the uniform COL_W
        # every gap had before #373. Set by a measuring pass -- see
        # `_tighten_gaps`.
        self.gaps: List[float] = list(gaps or ())
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
        self.op_up: Dict[str, str] = {}
        #: op-amps whose routed input carries a feedback divider -- one
        #: element back to the output node, one down to ground. The same
        #: shape `_node_order` puts the output before, and the only one
        #: drawn with its row-wired pin on the node row.
        self.op_divider: set = set()
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

        # Which input each op-amp is *drawn* with wired to the node row.
        # Decided here, before the capture below and before `_assign`,
        # because both have to agree with it: capture looks at whatever
        # ends up being the *lower* input, and getting that from the old
        # orientation while the drawing used the new one left the source
        # uncaptured and pushed out to a column -- eight of the book's
        # op-amp drawings, 10 Sep 2026.
        #
        # `_op_up` stays the ordering answer (it feeds `_node_order`,
        # which is what makes a cascade come out left to right, and
        # changing it there put two op-amps in one column). This is the
        # drawing answer, and it can differ because the provisional order
        # already tells us which input lies left.
        #
        # Wire the **leftmost** input to the row, so the other is to the
        # right and `_op_under` takes it down-and-right under the body --
        # the route with no crossings. The alternative is a lane 16px
        # under the node row, which runs the whole width back across
        # whatever hangs between (thesis Problem 081). Lowering that lane
        # only moves the damage: the riser back up to the node then runs
        # the length of whatever hangs on it. 16px is short on purpose.
        # The capture below outranks the position rule. When the lower
        # input already carries a lone grounded source, the orientation
        # that puts it there is the one a textbook draws, and flipping
        # loses the capture and pushes the source out to a column --
        # which is what broke the classic non-inverting stage on the
        # first attempt at this.
        _users: Dict[str, List[Element]] = {}
        for e in elements:
            if e.kind == "m":
                continue
            for n in set(e.fields[:3] if e.kind == "o" else e.nodes):
                if n != "0":
                    _users.setdefault(n, []).append(e)

        def _captures(op, low):
            """Would `low` as the lower input give a source to capture?"""
            if low == "0":
                return False
            us = _users.get(low, [])
            return len(us) == 2 and op in us and any(
                u.kind in ("e", "j") and "0" in (u.n1, u.n2) for u in us)

        for e in self.opamps:
            for dn in (e.fields[1], e.fields[0]):
                if _is_feedback_divider(elements, e, dn):
                    self.op_divider.add(e.name)

        _prov = {n: i for i, n in enumerate(_node_order(elements))}
        self.op_up = {}
        for e in self.opamps:
            up = _op_up(e)
            dn = e.fields[0] if up == e.fields[1] else e.fields[1]
            if dn == "0":
                continue
            if _captures(e, dn):
                continue                    # the default already draws it
            a, b = _prov.get(up), _prov.get(dn)
            if _captures(e, up) or (a is not None and b is not None
                                    and b < a):
                self.op_up[e.name] = dn

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
            terms = e.fields[:3] if e.kind == "o" else e.nodes
            for n in set(terms):
                if n != "0":
                    usage.setdefault(n, []).append(e)
        for op in self.opamps:
            # The input routed downward -- read off the orientation the
            # drawing will actually use, not the ordering one. When the
            # pins are flipped because n- is ground the downward input
            # *is* ground and there is nothing to capture.
            up = _up_of(self, op)
            dn = op.fields[0] if up == op.fields[1] else op.fields[1]
            if dn == "0" or dn == up:
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
        self.block_lane: Dict[str, int] = {}    # four-terminal blocks (#321)
        self.max_block_lane = 0
        self._assign()

    def _assign(self) -> None:
        by_node: Dict[str, List[Element]] = {}
        for e in self.grounded:
            n = e.n2 if e.n1 == "0" else e.n1
            by_node.setdefault(n, []).append(e)

        order = [n for n in _node_order(self.elements)
                 if n not in self.captured]
        idx = {n: i for i, n in enumerate(order)}


        # The columns a body-in-the-band element occupies, in node-order
        # index space. A grounded element hanging inside one of these
        # spans would be drawn straight through the body or its wires --
        # the band between the node row and the rail is exactly where
        # such an element sits -- so it is bumped out to a column of its
        # own: before the span when it hangs off the left end (a driving
        # source belongs on the left), after it otherwise (a load
        # belongs on the right).
        #
        # Two kinds sit in the band, and for the same reason, but only
        # the op-amp was ever registered here. A four-terminal block --
        # a two-port or a transformer -- fills the band too, and a
        # resistor hanging from one of its own top nodes landed *inside*
        # the box: thesis Problem 040 drew its 50 ohm r1 within the
        # two-port, label over the block's own parameter list. The
        # spacer logic further down gives the block clear columns
        # between its nodes, but an `extra` column for a second grounded
        # element is inserted before that spacer and lands in the box.
        # Registering the block as a span is the whole fix; the bumping
        # machinery already does the right thing with it.
        spans_idx: List[Tuple[int, int]] = []
        for e in self.opamps:
            a, b = idx.get(_up_of(self, e)), idx.get(e.fields[2])
            if a is not None and b is not None:
                spans_idx.append((min(a, b), max(a, b)))
        for e in self.spanning:
            if e.kind not in PORT_BLOCK and e.kind != "t":
                continue
            tl, tr = _port_tops(e)
            a, b = idx.get(tl), idx.get(tr)
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
        # A follower -- an op-amp whose output *is* one of its inputs,
        # `o1,1,2,2` -- names one node twice, so it spans no columns at
        # all and the ordering has nowhere to put its body. The symbol
        # is drawn to the right of that single column regardless, and
        # lands in whatever gap comes next: in AS2's Practice Problem
        # 5.9 that is the very gap the second stage occupies, and the
        # two triangles were drawn 35px into each other.
        #
        # Roberto's balloon (10 Sep 2026): "every element claims its
        # real footprint, and the canvas grows to accommodate the sum
        # rather than each element being fitted into a grid decided in
        # advance." A follower's footprint is a column, so it claims
        # one -- the same spacer the four-terminal block asks for below,
        # for the same reason.
        for e in self.opamps:
            a, b = idx_of.get(_up_of(self, e)), idx_of.get(e.fields[2])
            if a is not None and a == b:
                spacer_after[a] = max(spacer_after.get(a, 0), 1)
        # A four-terminal block (#314) also wants one clear column on
        # each *outer* side: its lower terminals leave sideways and rise
        # there to the node row, and that riser must not share a column
        # with anything hanging in the band. `lead_spacer` is the case
        # where the block's left top is the first node of all.
        lead_spacer = 0
        for e in self.spanning:
            want = 1 if (e.kind in PORT_BLOCK or e.kind == "t") else 0
            if want:
                tl, tr = _port_tops(e)
                a, b = idx_of.get(tl), idx_of.get(tr)
                if a is not None and b is not None and abs(a - b) == 1:
                    k = min(a, b)
                    spacer_after[k] = max(spacer_after.get(k, 0), want)
                if _drawn_four(e) and a is not None and b is not None:
                    lo, hi = min(a, b), max(a, b)
                    if lo == 0:
                        lead_spacer = 1
                    else:
                        spacer_after[lo - 1] = max(spacer_after.get(lo - 1, 0), 1)
                    spacer_after[hi] = max(spacer_after.get(hi, 0), 1)

        col = lead_spacer
        own_col: List[Tuple[str, Element]] = []   # (node, elem) pairs
        # The spacer columns after each node index, in order -- the
        # return leads of a four-terminal block rise through these.
        spacer_cols: Dict[int, List[int]] = {}
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
            k = spacer_after.get(i, 0)
            spacer_cols[i] = list(range(col, col + k))
            col += k
        self.cols = max(col, 1)

        for n, e in at_node:
            self.elem_col[e.name] = self.node_col[n]

        stubs: List[Tuple[int, int, int]] = []
        for n, e in own_col:
            a, b = self.node_col[n], self.elem_col[e.name]
            stubs.append((min(a, b), max(a, b), 0))

        # Where each four-terminal block's return leads rise: the last
        # spacer column before its left top, the first after its right
        # top -- and the run along the node row from there to the
        # bottom node's own column is a stub, so nothing else is placed
        # on the row across it.
        self.port_return: Dict[str, Tuple[Optional[int], Optional[int]]] = {}
        for e in self.spanning:
            if not _drawn_four(e):
                continue
            (tl, bl), (tr, br) = e.port_nodes
            a, b = idx_of.get(tl), idx_of.get(tr)
            if a is None or b is None:
                continue
            if a <= b:
                lo, hi, lb, rb = a, b, bl, br
            else:
                lo, hi, lb, rb = b, a, br, bl
            left_col = (spacer_cols.get(lo - 1) or [None])[-1] if lo > 0 else 0
            right_col = (spacer_cols.get(hi) or [None])[0]
            self.port_return[e.name] = (left_col, right_col)
            for node in (lb, rb):
                c = self.return_col(e, node)
                if node != "0" and c is not None and node in self.node_col:
                    nc = self.node_col[node]
                    stubs.append((min(nc, c), max(nc, c), 0))

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
        def ends(e: Element) -> Tuple[str, str]:
            return _port_tops(e) if (e.kind in PORT_BLOCK or e.kind == "t") \
                else (e.n1, e.n2)
        spans = [(min(self.node_col[ends(e)[0]], self.node_col[ends(e)[1]]),
                  max(self.node_col[ends(e)[0]], self.node_col[ends(e)[1]]), e)
                 for e in self.spanning if not _laned(e)]
        # A four-terminal block never sits on a stacked level: two whose
        # spans overlap go into lanes down the band instead (#321), so
        # they are kept out of the colouring above and placed on the
        # row's occupancy list at level 0 by hand.
        laned = [(min(self.node_col[ends(e)[0]], self.node_col[ends(e)[1]]),
                  max(self.node_col[ends(e)[0]], self.node_col[ends(e)[1]]), e)
                 for e in self.spanning if _laned(e)]
        for lo, hi, e in laned:
            self.level[e.name] = 0
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
        placed += [(lo, hi, 0) for lo, hi, _ in laned]
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
            for n in (_up_of(self, e), e.fields[2]):
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
        # Every stacked row, not just the node row. `row0` answers "is
        # the node row clear here"; this answers "is the strip *above*
        # the node row clear here", which is what an above-row body has
        # to know and what nothing was asking (#369).
        self.lifted = [(lo, hi) for lo, hi, l in placed
                       if l > 0 and hi > lo]

        # Op-amps all sit in one horizontal band, so two whose
        # input-to-output columns overlap would be drawn through each
        # other. Identical colouring to the rows above, except the
        # colours become lanes *down* the band. A cascade keeps lane 0
        # throughout, since each stage owns its own columns.
        ops = []
        for e in self.opamps:
            a = self.node_col.get(_up_of(self, e))
            b = self.node_col.get(e.fields[2])
            if a is None or b is None:
                continue
            lo, hi = min(a, b), max(a, b)
            # A follower's output *is* its row-wired input -- `o1,1,2,2`
            # -- so both ends are one node and the span comes out zero
            # wide. A zero-wide span overlaps nothing, so the allocator
            # sees no conflict and puts both stages of AS2's Practice
            # Problem 5.9 in lane 0, 35px on top of each other.
            #
            # The triangle occupies real width whatever its nodes say:
            # `_draw_opamp` places it at `x_in + 26` and it is 50px
            # across, which runs into the next column. So the span
            # claims that column. An element declares the room it takes
            # up, and the layout makes room for it (Roberto's balloon,
            # 10 Sep 2026).
            if lo == hi:
                hi = lo + 1
            ops.append((lo, hi, e))
        ops.sort(key=lambda s: (s[0], s[1]))

        # Raised op-amps and lane op-amps are measured from different
        # places, so they are allocated in separate pools. A raised body
        # hangs from `y_top` and reaches 29px either side of the node
        # row; a lane body hangs from `y_top + ROW_H/2` and so starts
        # 46px below the row. Those two never meet -- but a *lane*
        # offset of 78px does, which is how AS2's Practice Problem 5.9
        # drew a raised second stage 3px from an unraised first one and
        # why the raise had to be refused outright whenever a drawing
        # had more than one op-amp.
        #
        # It need not be. The row is one band and the lanes are
        # another: raise what may be raised onto the row, and let the
        # rest take lanes underneath. Two raised bodies would collide
        # with each other, so they are coloured among themselves and
        # only colour 0 keeps the raise; the loser falls back into the
        # lane pool, where it is drawn as it always was.
        self.op_raised: set = set()
        self.op_above: set = set()
        rows: List[Tuple[int, int, int]] = []
        aboves: List[Tuple[int, int, int]] = []
        lanes = []
        for lo, hi, e in ops:
            if self.allow_above is not None and e.name in self.allow_above \
                    and self._above_ok(e) \
                    and not any(min(hi, h) > max(lo, o) for o, h, _l in aboves):
                self.op_above.add(e.name)
                self.op_lane[e.name] = 0
                aboves.append((lo, hi, 0))
                continue
            if self.allow_raise is not None and e.name not in self.allow_raise:
                lanes.append((lo, hi, e))
                continue
            if not self._raise_ok(e):
                lanes.append((lo, hi, e))
                continue
            if any(min(hi, h) > max(lo, o) for o, h, _l in rows):
                lanes.append((lo, hi, e))
                continue
            self.op_raised.add(e.name)
            self.op_lane[e.name] = 0
            rows.append((lo, hi, 0))

        taken: List[Tuple[int, int, int]] = []
        for lo, hi, e in lanes:
            lane = 0
            while any(l == lane and min(hi, h) > max(lo, o)
                      for o, h, l in taken):
                lane += 1
            self.op_lane[e.name] = lane
            taken.append((lo, hi, lane))
        self.max_op_lane = max(
            [l for n, l in self.op_lane.items()
             if n not in self.op_raised and n not in self.op_above],
            default=0)

        # Two four-terminal blocks sharing a port, or overlapping at
        # all, are drawn one below the other (#321): the same greedy
        # colouring as the op-amp lanes. A parallel-series connection
        # of two z blocks -- AS7's Problem 19.70, the case that found
        # this -- had both boxes centred on the same columns, one drawn
        # through the other's parameters.
        # A block whose lower terminal is another block's upper terminal
        # is wired above it -- the series connection -- and is drawn
        # above it: its lane is below (numerically above) the other's.
        # Kahn's walk over those edges, the (lo, hi) sort as tie-break.
        laned.sort(key=lambda t: (t[0], t[1]))
        tops = {e.name: {t for t, _ in e.port_nodes} for _, _, e in laned}
        bottoms = {e.name: {b for _, b in e.port_nodes if b != "0"}
                   for _, _, e in laned}
        above_b: Dict[str, set] = {}      # name -> names that sit above it
        for _, _, e in laned:
            for _, _, f in laned:
                if e.name != f.name and bottoms[e.name] & tops[f.name]:
                    above_b.setdefault(f.name, set()).add(e.name)
        pending_b = list(laned)
        done_b: set = set()
        taken_b: List[Tuple[int, int, int]] = []
        while pending_b:
            pick = next((t for t in pending_b
                         if above_b.get(t[2].name, set()) <= done_b),
                        pending_b[0])
            pending_b.remove(pick)
            lo, hi, e = pick
            lane = max((self.block_lane[a] + 1 for a in above_b.get(e.name, ())
                        if a in self.block_lane), default=0)
            while any(l == lane and min(hi, h) > max(lo, o)
                      for o, h, l in taken_b):
                lane += 1
            self.block_lane[e.name] = lane
            done_b.add(e.name)
            taken_b.append((lo, hi, lane))
        self.max_block_lane = max(self.block_lane.values(), default=0)

    def return_col(self, e: Element, node: str) -> Optional[int]:
        """The spacer column through which a four-terminal block's lead
        to bottom node `node` rises: the one on the side of the block
        the node's own column lies on. Normally that is the side of the
        port the node belongs to, since `_node_order` put it there --
        but a bottom that is the *other* port's top (`t,[1,0],[2,1]`,
        the autotransformer as one tapped winding) lies across the
        block, and its lead goes round the far side rather than along
        the block's own top."""
        lc, rc = self.port_return.get(e.name, (None, None))
        if node == "0" or node not in self.node_col:
            return None
        tl, tr = _port_tops(e)
        lo = min(self.node_col[tl], self.node_col[tr])
        hi = max(self.node_col[tl], self.node_col[tr])
        nc = self.node_col[node]
        if nc <= lo:
            return lc
        if nc >= hi:
            return rc
        return lc if (nc - lo) <= (hi - nc) else rc

    def _raise_ok(self, e: Element) -> bool:
        """May this op-amp be drawn on the node row at all?

        Three conditions, each measured (see `_draw_opamp`): the
        feedback-divider shape, the orientation flipped, and the stretch
        of row it would occupy clear. Whether it *is* raised is decided
        by the allocator, which will not put two of them on one stretch
        of row -- ask `raised()`, not this."""
        if e.name not in self.op_divider or e.name not in self.op_up:
            return False
        a = self.node_col.get(_up_of(self, e))
        b = self.node_col.get(e.fields[2])
        if a is None or b is None:
            return False
        return all(self.gap_free(c) for c in range(min(a, b), max(a, b)))

    def _above_ok(self, e: Element) -> bool:
        """May this op-amp stand in the band above the node row?

        Offered when its lower input would otherwise take the long way
        east under the body (#337) -- that is the expensive route the
        placement exists to replace, and every other op-amp is better
        served where it already is. All three of its nodes must have
        columns, since all three leads become drops onto the row."""
        if not _op_under(self, e):
            return False
        nodes = [e.fields[0], e.fields[1], e.fields[2]]
        if not all(n in self.node_col for n in nodes):
            return False
        # And the strip it would stand in has to be free. A lifted
        # element runs one `stack_h` above the node row for each level,
        # which is exactly where the band goes: `r1` of Bo2's Drill
        # Exercise 3.4 spans node 2 to node o on level 1 and was drawn
        # straight through the body. `_raise_ok` asks this of the node
        # row through `gap_free`; the same question, one storey up.
        a = self.node_col[_up_of(self, e)]
        b = self.node_col[e.fields[2]]
        c = self.node_col[e.fields[0] if e.fields[1] == _up_of(self, e)
                          else e.fields[1]]
        lo, hi = min(a, b, c), max(a, b, c)
        return not any(lo < h and l < hi for l, h in self.lifted)

    def above(self, e: Element) -> bool:
        """Is this op-amp drawn in the band above the node row?"""
        return e.name in self.op_above

    @property
    def has_above(self) -> bool:
        return bool(self.op_above)

    def raised(self, e: Element) -> bool:
        """Is this op-amp drawn with its output tip **on** the node row?

        Lives here rather than in the drawing because the node names
        have to know it too -- a raised op-amp's input runs *above* the
        row, through the strip the names are lettered in, so those names
        move aside (#367)."""
        return e.name in self.op_raised

    def raised_input_nodes(self) -> set:
        """The row-row nodes whose name a raised op-amp's input passes."""
        return {_up_of(self, e) for e in self.opamps if self.raised(e)}

    def gap_free(self, c: int) -> bool:
        """True when the node row between column c and column c+1
        carries nothing -- no element, no stub -- so a wire may run
        along it and join a node at its own corner."""
        return not any(lo <= c < hi for lo, hi in self.row0)

    # pixel helpers
    def px(self, col: int) -> float:
        """Where column `col` stands.

        `MARGIN + col * COL_W` until #373: every gap was 132px whether
        it carried a stretched resistor with a two-line label or nothing
        but a wire. `self.gaps` holds a width per gap, and a gap is
        never wider than `COL_W`, so a drawing can only ever narrow."""
        if not self.gaps:
            return MARGIN + col * COL_W
        x = MARGIN
        for i in range(col):
            x += self.gaps[i] if i < len(self.gaps) else COL_W
        return x

    @property
    def stack_h(self) -> float:
        """Height of one stacked row. `STACK_H` unless a lifted branch
        carries a current arrow, which hangs `_mark_stack()` further
        down than anything the plain number was measured against."""
        lifted = any(self.level.get(n, 0) > 0 for n in self.i_ref)
        return STACK_H + (_mark_stack() if lifted else 0.0)

    @property
    def y_top(self) -> float:
        # The band an above-row body stands in is added the same way
        # `op_under` adds one below: the drawing grows rather than the
        # body being squeezed into space that belongs to something else
        # (Roberto's balloon, 10 Sep 2026).
        return (MARGIN + self.max_level * self.stack_h
                + (OP_ABOVE_H if self.op_above else 0.0))

    @property
    def op_under(self) -> bool:
        """Whether any op-amp here routes its lower input underneath."""
        return any(_op_under(self, e) for e in self.opamps)

    @property
    def y_bot(self) -> float:
        # `op_under` is Roberto's "make the circuit taller": the band it
        # adds is what the under-run travels in, clear of every body.
        return (self.y_top + self.row_h + self.max_op_lane * OP_LANE_H
                + self.max_block_lane * BLOCK_LANE_H
                + (OP_UNDER_H if self.op_under else 0.0))

    @property
    def y_under(self) -> float:
        """The lane an under-run travels in: half the added band above
        the ground rail, which puts it clear of the deepest triangle
        (46px above the rail before the band was added) and clear of
        every stretched body, which stays centred higher up."""
        return self.y_bot - OP_UNDER_H / 2.0


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
    up_node = _up_of(lay, e)                        # wired to the top row
    # Standing above the row, the input whose node lies further right
    # takes the *upper* pin: the upper pin is the one with a clear run
    # over the body's top, and the near input then drops straight down.
    # In AS2's Practice Problem 5.8 the far input is node 6, which is
    # the non-inverting one, so the symbol comes out with `+` uppermost
    # -- which is the "flipping it" Roberto asked for. It is not a
    # separate instruction; it falls out of which lead has room.
    if lay.above(e):
        ca = lay.node_col.get(e.fields[0], -1)
        cb = lay.node_col.get(e.fields[1], -1)
        up_node = e.fields[0] if ca > cb else e.fields[1]
    flip = up_node != e.fields[1]                   # n- grounded, pins swap
    dn_node = e.fields[1] if flip else e.fields[0]  # rail, or its own row
    up_sign, dn_sign = ("+", "−") if flip else ("−", "+")
    x_in = lay.px(lay.node_col[up_node]) if up_node in lay.node_col \
        else lay.px(0)
    x_out = lay.px(lay.node_col[n_out]) if n_out in lay.node_col \
        else x_in + COL_W

    lane = lay.op_lane.get(e.name, 0)
    h_tri = 58.0
    # A non-inverting stage -- row-wired input, feedback divider on the
    # routed one -- is drawn with its row-wired pin *on* the node row, so
    # the input runs straight in and the output straight out. Roberto,
    # 10 Sep 2026: raise it and "you will avoid the two bends in the
    # lines around the op amp."
    #
    # Only that shape. Raising every op-amp lifts the triangle above the
    # node row, where the row is generally occupied, and takes the review
    # harness from 3 findings to 57.
    # The tip goes **on** the node row, so the output runs straight out
    # through its node and into the feedback resistor -- "perfectly in
    # line with the resistor and the joining point behind it" (Roberto,
    # 10 Sep 2026). The symbol puts its output at the triangle's centre
    # and the inputs 14.5px off it, so only one of the two can be on the
    # row; the output side is the one that reads as a line.
    if lay.above(e):
        # Clear of the row by `OP_ABOVE_GAP`, which is the strip the node
        # names are lettered in and the strip a near lead turns in.
        mid = lay.y_top - OP_ABOVE_GAP - h_tri / 2.0
    elif lay.raised(e):
        mid = lay.y_top + lane * OP_LANE_H
    else:
        mid = lay.y_top + lay.row_h / 2.0 + lane * OP_LANE_H
    # The triangle is a fixed equilateral symbol, centred in the gap
    # between the input and output columns. Widening it to span whatever
    # gap it happens to sit in would be the easy way to make the wires
    # meet, but it distorts the symbol; the leads stretch instead.
    h = h_tri
    w = h * 3 ** 0.5 / 2.0
    if lay.above(e):
        # Centred over the gap it spans -- between the *near* input and
        # the output -- so it stands over the feedback resistor rather
        # than being dragged out to the far input's column.
        x_near = lay.px(lay.node_col[dn_node])
        tx = max(min(x_near, x_out) + (abs(x_out - x_near) - w) / 2.0,
                 min(x_near, x_out) + 26)
    elif x_out - x_in > COL_W * 1.5:
        # A span stretched by a spacer column: stand at the output end
        # and let the input lead take the slack. Centring here leaves
        # the output hanging (107px in Practice Problem 5.9) and parks
        # the body against its neighbour.
        tx = max(x_out - 26 - w, x_in + 26)
    else:
        tx = max((x_in + x_out) / 2.0 - w / 2.0, x_in + 26)
    y_minus, y_plus = mid - h / 4.0, mid + h / 4.0

    cv.raw('<path d="M{0:g} {1:g} L{0:g} {2:g} L{3:g} {4:g} Z" fill="none"/>'
           .format(tx, mid - h / 2, mid + h / 2, tx + w, mid),
           (tx, mid - h / 2), (tx + w, mid + h / 2))
    cv.obstacle(tx, mid - h / 2, tx + w, mid + h / 2)
    # The ink is the wedge, banded (#338). A band's width is the
    # triangle's width at whichever of its two edges is nearer the
    # middle -- the widest point the band covers -- so the staircase
    # encloses the symbol rather than cutting into it.
    band = h / OP_INK_BANDS
    for k in range(OP_INK_BANDS):
        ya, yb = mid - h / 2 + k * band, mid - h / 2 + (k + 1) * band
        near = min(abs(ya - mid), abs(yb - mid))
        cv.ink(tx, ya, tx + w * (1.0 - 2.0 * near / h), yb)
    # The pin signs are stroked marks, not text glyphs, so they match
    # the voltage source's polarity marks in weight and size (#130).
    _sign_mark(cv, tx + 13, y_minus, up_sign == "+")
    _sign_mark(cv, tx + 13, y_plus, dn_sign == "+")
    # The feedback loop drawn when the output cannot go right (below)
    # passes over the triangle's top, where the name normally sits, so
    # the name yields the spot and moves under the body instead.
    loop = x_out < tx + w + 12
    # #338, Roberto: the name closer to the symbol. Above a *triangle*
    # the nearest ink is not the top vertex but the hypotenuse, which
    # at the body's horizontal centre has already fallen h/4 -- so a
    # name cleared from the top vertex reads as floating a quarter of
    # the symbol's height away from anything. Clear it from the sloping
    # edge instead, measured at the label's own left corner, which is
    # the corner that comes closest to the slope.
    nm = _name_runs(e.name)
    x_name = tx + w / 2
    edge_x = max(tx, x_name - _runs_width(nm) / 2.0)
    # One band of slack, so the label clears the staircase the ink is
    # recorded as and not merely the ideal slope underneath it.
    y_slope = (mid - h / 2.0 + (edge_x - tx) / w * (h / 2.0)
               - h / OP_INK_BANDS)
    y_name = (mid + h / 2 + GAP + LABEL_ASCENT if loop
              else y_slope - GAP - _name_below())
    cv.runs(x_name, y_name, nm)

    if lay.above(e):
        tip_a = tx + w
        x_up = lay.px(lay.node_col[up_node])
        x_dn = lay.px(lay.node_col[dn_node])
        # The far input: out of the pin, up over the body, across, and
        # down onto its node. Over the top rather than under, because
        # under is where the output's own drop is.
        #
        # It has to clear the **name**, not the triangle. Since #338 the
        # name is set against the hypotenuse, so it already stands above
        # the top vertex: a lead 12px over the vertex passed 1.6px over
        # the name, in every drawing that stands a body above the row.
        # Roberto, 10 Sep 2026: "Names should not be touching the lines."
        # Measured from the same baseline the name was placed at, so the
        # two cannot drift apart.
        y_over = min(mid - h / 2.0, y_name - LABEL_ASCENT) - GAP - 8
        # **Both legs bend at the same distance from the body** (#375).
        # Roberto, 11 Sep 2026: *"both legs are bending in opposite
        # directions. Both have bends already. All I ask is that they
        # bend at the same distance."*
        #
        # And it costs nothing, which is the point. The near input's
        # turn is not a free choice -- it is its node's own column, so
        # the lead drops straight onto the node. The far input's was the
        # constant 12, chosen for no reason at all. Matching the
        # arbitrary one to the determined one leaves the bend count
        # exactly as it was: Bo2's Drill Exercise 3.2 had a 12px leg
        # bending up and a 38px leg bending down.
        #
        # The two risers then share a column, one climbing from the
        # upper pin and one dropping from the lower, with the pin gap
        # between them. They cannot merge (`_flush_wires` merges only
        # overlapping collinear runs) and neither earns a dot or a hop.
        # Kept to the left of the body, since a node lying right of the
        # triangle has no column here to bend at.
        x_far = x_dn if x_dn < tx - 1 else tx - 12
        cv.wire(tx, y_minus, x_far, y_minus)
        cv.wire(x_far, y_minus, x_far, y_over)
        cv.wire(x_far, y_over, x_up, y_over)
        cv.wire(x_up, y_over, x_up, lay.y_top)
        # The near input: straight out of the pin and down.
        cv.wire(tx, y_plus, x_dn, y_plus)
        cv.wire(x_dn, y_plus, x_dn, lay.y_top)
        # The output: straight out of the tip and down.
        cv.wire(tip_a, mid, x_out, mid)
        cv.wire(x_out, mid, x_out, lay.y_top)
        return None
    # upper input: straight down from its node, then in
    cv.wire(x_in, lay.y_top, x_in, y_minus)
    cv.wire(x_in, y_minus, tx, y_minus)
    # When several op-amps hang off one input node they share that
    # vertical wire, so the branch to this one is a T-junction and needs
    # a dot -- but only if another op-amp continues on past it.
    if any(o.name != e.name and _up_of(lay, o) == up_node
           and lay.op_lane.get(o.name, 0) > lane for o in lay.opamps):
        cv.dot(x_in, y_minus)
    # lower input: out of the pin, then down to the rail (or up to its
    # own node row if it is not grounded).
    #
    # Which way it leaves is decided by where it is going. Leaving left
    # is right for a grounded input -- the rail is below -- and for a
    # captured source, which is drawn in the drop itself. But when the
    # node lies to the right and the route is the under-the-body one of
    # #337, the lead used to walk 30px past the *input* column first,
    # which in a cascade means past the previous stage and its feedback,
    # and then turn and cross all of it again going east. In AS2's
    # Practice Problem 5.9 that is 137px the wrong way for 396px back,
    # and both of the drawing's crossings are on the westward leg.
    #
    # Roberto's rule, 10 Sep 2026, and it is meant to outlive this
    # drawing: **a lead leaves toward its destination.** Nothing can be
    # crossed on the way to somewhere you were already going.
    if _op_under(lay, e):
        # Both legs bend at the same distance (#375). The upper input
        # turns at `x_in`, its own node's column, which is not a free
        # choice; this one's `tx - 12` was. Matching the arbitrary turn
        # to the determined one adds no bend -- both legs already bend,
        # in opposite directions -- and the two risers share a column
        # with the pin gap between them, which is the symbol's own
        # geometry rather than a join that missed.
        x_p = min(x_in, tx - 12) - lane * 8
    else:
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
        if _op_under(lay, e):
            # Down and right, under the body (#337). The node is to the
            # right, so the old route back over the top crossed the
            # input riser and the node row; this one crosses nothing
            # the circuit did not already put in the way.
            #
            # Where it rises is the whole correctness question. Teeing
            # onto the node's column low down would join whatever wire
            # is there -- and if something hangs from that node to the
            # rail, the wire down there is on the *ground* side of it.
            # Node 3's column in Lesson 5a's Drill Exercise 3.2 carries
            # r30, so a tee 26px above the rail is node 0, not node 3
            # (Roberto caught exactly this). The riser goes all the way
            # to the node row, on the node's own column when that
            # column is clear and in the free gap beside it when not.
            blocked = any(lay.elem_col.get(g.name) == dn_col
                          for g in lay.grounded)
            x_rise = xp_node - 30 if blocked else xp_node
            cv.wire(x_p, y_plus, x_p, lay.y_under)
            cv.wire(x_p, lay.y_under, x_rise, lay.y_under)
            if blocked:
                # Up beside the column and into the node from the left,
                # 16px under the row -- the same clearance the route
                # over the top uses, and the last 30px of it. Crossing
                # the column itself to rise on its far side would cost
                # a hop over the very wire whose lower half is the
                # wrong node, and 66px of width for the privilege.
                cv.wire(x_rise, lay.y_under, x_rise, lay.y_top + 16)
                cv.wire(x_rise, lay.y_top + 16, xp_node, lay.y_top + 16)
                # A tee, not a run up to the row: the column's own wire
                # is already there, and 16px under the row is above any
                # hanging body (the shortest lead a vertical element
                # leaves is 55px), so this lands on the node's side of
                # it. Running up to y_top instead would lay a second
                # wire along the first.
                cv.dot(xp_node, lay.y_top + 16)
            else:
                cv.wire(x_rise, lay.y_under, x_rise, lay.y_top)
        elif up_col is not None and dn_col == up_col - 1 \
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


def _cost(svg: str) -> Tuple[int, int, int]:
    """Roberto's price list, read off the finished drawing.

    "Think of this as an optimisation problem, where every bend costs
    money, and every cross costs a lot of money, and where straight
    lines and elements in the same row are rewarded" (10 Sep 2026). The
    tuple is compared lexicographically, which is what "a lot" means:
    no number of saved bends buys one more crossing.

    A crossing is counted as the drawing's own no-connection hop rather
    than by intersecting the wire list. A symbol draws its leads inside
    its own group, so a wire-list count misses every crossing over a
    lead: it had AS2's Practice Problem 5.9 at one crossing where the
    picture -- and the drawer -- had three."""
    body = re.sub(r"<g transform=.*?</g>", "", svg, flags=re.S)
    hor: List[Tuple[float, float, float]] = []
    ver: List[Tuple[float, float, float]] = []
    for x1, y1, x2, y2 in re.findall(
            r'<line x1="([-\d.]+)" y1="([-\d.]+)" '
            r'x2="([-\d.]+)" y2="([-\d.]+)"/>', body):
        _seg(hor, ver, float(x1), float(y1), float(x2), float(y2))
    for d in re.findall(r'<path d="([^"]+)"', body):
        pts = [(float(x), float(y)) for x, y in
               re.findall(r"[ML]\s*([-\d.]+)[ ,]\s*([-\d.]+)", d)]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            _seg(hor, ver, x1, y1, x2, y2)
    bends = 0
    for x0, x1, y in hor:
        for x, y0, y1 in ver:
            if (abs(x - x0) < 0.5 or abs(x - x1) < 0.5) \
                    and (abs(y - y0) < 0.5 or abs(y - y1) < 0.5):
                bends += 1
    return (svg.count(_HOP_ARC), bends, len(hor) + len(ver))


def _seg(hor, ver, x1: float, y1: float, x2: float, y2: float) -> None:
    if abs(y1 - y2) < 0.01 and abs(x1 - x2) > 0.01:
        hor.append((min(x1, x2), max(x1, x2), y1))
    elif abs(x1 - x2) < 0.01 and abs(y1 - y2) > 0.01:
        ver.append((x1, min(y1, y2), max(y1, y2)))


def _render(elements: List[Element], marks=None) -> str:
    """Draw it, and where the drawer has a choice, draw it both ways and
    keep the cheaper picture.

    The only choice it has is which op-amps sit on the node row (#367).
    A hand-written predicate for that took most of a day and still got
    AS2's Practice Problem 5.9 wrong in both directions; the price list
    above gets all seven candidates in the example book right, agreeing
    with every drawing Roberto has ruled on -- raise the two lone
    non-inverting stages, leave the three cascades in the band, and
    raise Practice Problem 5.9, which is the one he asked for by name.

    Subsets, not all-or-nothing, because two candidates in one drawing
    need not agree; capped, because the count is exponential and no
    example in the book has more than one."""
    lay = _Layout(elements)
    rows = sorted(lay.op_raised)
    ups = sorted(_Layout(elements, allow_above=set(
        e.name for e in lay.opamps)).op_above)
    if rows or ups:
        best, best_cost = None, None
        for choice in _placements(rows, ups):
            c = _cost(_render_once(elements, marks, *choice))
            if best_cost is None or c < best_cost:
                best, best_cost = choice, c
        # Drawn again, so the winner is the last thing drawn. The review
        # harness and the editable-drawing exporter both read the canvas
        # by hooking `_Canvas._flush_wires`, and a hook sees the last
        # pass, not the returned one: choosing without this put four
        # label findings on drawings whose labels sit nowhere near a
        # wire, naming wires that belong to the rejected layout.
        return _final(elements, marks, best)
    return _final(elements, marks, (set(), set()))


def _final(elements: List[Element], marks, choice) -> str:
    """Draw the chosen layout, with the band and the column gaps closed
    to what this particular drawing needs (#373).

    The tightening is geometry only -- the columns, the levels and every
    op-amp's placement are already decided, so no wire changes which
    side of anything it passes. The price list is checked all the same
    and the full size kept if it ever disagrees: a cost that moves here
    would mean the tightening had changed the drawing's topology, which
    is a bug, not a trade.

    Roberto's balloon, both ways round. It only ever inflated -- `ROW_H`
    and `COL_W` are constants, so a drawing of one source and two
    resistors was laid out on the same grid as a three-phase network.
    Measured over the 356 built-in drawings, the median one carried
    113px of air in its band and 95px in every column gap."""
    base: dict = {}
    full = _render_once(elements, marks, *choice, out=base)
    budget = _collisions(base["cv"])
    cost = _cost(full)
    row_h = _tighten_band(elements, marks, choice)
    gaps = _tighten_gaps(elements, marks, choice, row_h)

    # Both relaxations, then each alone, then neither. The band and the
    # gaps are checked *together* because they interact: closing the
    # band moves every body and its labels up, which is how `iCO` came
    # to sit on `8∠-40°` in AS7's Example 10.13 with the gaps left
    # untouched. A tightening that is checked only in the dimension it
    # moves is not checked.
    for try_h, try_g in ((row_h, gaps), (row_h, None), (None, gaps)):
        if try_h is None and try_g is None:
            continue
        probe: dict = {}
        svg = _render_once(elements, marks, *choice,
                           row_h=try_h, gaps=try_g, out=probe)
        h, sf = _collisions(probe["cv"])
        if _cost(svg) == cost and h <= budget[0] and sf <= budget[1]:
            return svg
    return _render_once(elements, marks, *choice)


# The shortest bare lead the band will leave between a body (or its
# label) and the node row or the ground rail.
#
# **This is a design choice, not a derived number, and it is Roberto's
# to make.** What measurement can say is the range it may live in, and
# both ends were measured rather than guessed:
#
#   * below about 5 the review harness goes red -- squeezed to 2, it
#     reports 26 drawings with a label on a symbol or an element drawn
#     through an op-amp's body;
#   * the book as shipped sits at 56.6 on half its drawings, and that
#     number is not a clearance anyone chose either -- it is what
#     `(ROW_H - 36.8) / 2` happens to leave beside a resistor.
#
# So the honest statement is that anything from ~10 to ~57 draws a legal
# picture and the choice between them is a matter of how a schematic
# should look. 34 is the midpoint, offered as a starting point for him
# to move. `tools/sweep_lead.py` renders the same circuits at several
# values side by side.
LEAD_MIN = 34.0
ROW_H_MIN = 96.0   # the band never deflates past this, whatever is in
# it. A drawing with nothing hanging in the band still has to look like
# a circuit rather than two lines close together.


def _band_content(cv: "_Canvas", lay: "_Layout"):
    """The separate things standing in the band, one box each.

    Symbols **and labels**: a measurement that looks only at symbols
    measures half the picture, and it is the labels that collide.

    Two collapses, both of them one object reported twice. An op-amp
    draws its wedge as `OP_INK_BANDS` horizontal strips, so consecutive
    strips of one triangle sit 2.4px apart -- an unguarded pair test
    calls that the tightest gap in the drawing, on every op-amp drawing
    in the book. And a body that is also a keep-out reports both, over
    the same rectangle. Anything inside an obstacle or a block belongs
    to it; what is left is merged where the boxes touch.

    A body that straddles the node row is not in the band at all -- a
    raised op-amp reaches 29px either side of the row (#367) -- so it
    is dropped rather than pinning the band at its own clearance."""
    keep = list(cv.obstacles) + [b for b in getattr(cv, "_blocks", ())]
    loose = []
    for x0, y0, x1, y1 in list(cv.inks) + list(cv.labels):
        if any(bx0 - 1 <= x0 and x1 <= bx1 + 1
               and by0 - 1 <= y0 and y1 <= by1 + 1
               for bx0, by0, bx1, by1 in keep):
            continue
        loose.append([x0, y0, x1, y1])
    merged: List[List[float]] = []
    for b in sorted(loose, key=lambda b: (b[0], b[1])):
        for m in merged:
            if b[0] <= m[2] + 1 and m[0] <= b[2] + 1 \
                    and b[1] <= m[3] + 1 and m[1] <= b[3] + 1:
                m[0], m[1] = min(m[0], b[0]), min(m[1], b[1])
                m[2], m[3] = max(m[2], b[2]), max(m[3], b[3])
                break
        else:
            merged.append(list(b))
    out = []
    for x0, y0, x1, y1 in [tuple(m) for m in merged] + keep:
        if y0 < lay.y_top + 0.5 or y1 > lay.y_bot - 0.5:
            continue
        out.append((x0, y0, x1, y1))
    return out


def _tighten_band(elements: List[Element], marks, choice) -> Optional[float]:
    """How short the band may be for *this* drawing, or None to leave it.

    Roberto's balloon has only ever inflated: `ROW_H` is a constant, so
    the band between the node row and the ground rail is 150px whether
    it holds a stacked pair of resistors or one 30px source. Bo2's
    Drill Exercise 3.2 is his example -- a 30px source with about 156px
    of bare lead wrapped round it -- and across the book the median
    drawing carries **113px of air** in that band.

    Measured, not modelled. The drawing is rendered once at the full
    band and asked what actually stands in it; the band is then closed
    until the tightest thing in it is `LEAD_MIN` from the row and from
    the rail.

    One number is enough for the whole band because every vertical gap
    in it closes at the same rate. A body hangs at the band's midpoint
    (or at a lane offset below the row) and the rail sits at the foot,
    so shortening the band by `d` moves each body up by `d/2` and the
    rail up by `d` -- which takes `d/2` off the gap above *and* the gap
    below. So the shortest gap anywhere governs, and the answer is
    `2 * (shortest - LEAD_MIN)`.

    Refused outright where the arithmetic above does not hold:

      * a four-terminal block sizes its own box against `ROW_H` through
        `PORT_BOX_H` and draws its band from the constant, so a layout
        whose `row_h` disagreed with it would tear;
      * an under-run travels at `y_under`, half the added band above the
        rail, so bodies must clear *that* rather than the rail -- which
        is measured here rather than being a reason to refuse."""
    if not elements:
        return None
    if any(e.kind == "t" or e.kind in PORT_BLOCK for e in elements):
        return None                 # sizes its own box against ROW_H
    probe: dict = {}
    _render_once(elements, marks, choice[0], choice[1], out=probe)
    lay, cv = probe["lay"], probe["cv"]
    floor = lay.y_under if lay.op_under else lay.y_bot
    boxes = _band_content(cv, lay)
    if not boxes:
        return None
    slack = min(min(y0 - lay.y_top, floor - y1) for _x0, y0, _x1, y1 in boxes)
    drop = 2.0 * (slack - LEAD_MIN)
    row_h = max(ROW_H_MIN, lay.row_h - drop)
    return row_h if row_h < lay.row_h - 0.5 else None


H_CLEAR = 20.0     # clear air either side of the widest thing standing
# in a column gap. Like `LEAD_MIN`, a design choice with a measured
# range rather than a derived number.
LABEL_APART = 18.0  # two labels on one line stay at least this far
# apart. Like LEAD_MIN and H_CLEAR, a look-and-feel number with a
# measured range: the book as shipped has a median of 45 and a
# minimum of 8.9, so 18 is tighter than today and legible.
COL_W_MIN = 60.0   # a gap never narrows past this, so a column of bare
# wire still reads as a span rather than a kink.


def _collisions(cv: "_Canvas") -> Tuple[int, float]:
    """What is on top of what, as (hard, soft).

    **hard** is a fault: two labels overlapping, a label on a
    symbol's ink or on a wire, a wire or an element through a body.
    **soft** is only tight: two labels on one line closer than
    `LABEL_APART`.

    Two numbers rather than one, because a relaxation that compares
    totals will happily spend a near-miss it started with on a real
    overlap. That is exactly what it did: at the full width `iCO`
    sat 17.5px from `8∠-40°` in AS7's Example 10.13 -- one soft --
    and the narrowed drawing had them overlapping -- one hard --
    so the count matched and the fault shipped past a green check.

    Two labels overlapping, or a label on a symbol's ink, or a wire
    through an op-amp's body -- the three faults the review harness
    reports, computed here so the drawer can check its own work before
    it narrows anything.

    A label sitting inside a block's box is not a fault: a two-port's
    parameters are drawn there on purpose. Nor is a label over the ink
    of the element it belongs to, which is why ink is compared only
    against labels that overhang it by more than `GAP`."""
    hard, soft = 0, 0.0
    # The canvas sizes a label from a character count at 7.2px an
    # advance; the review harness uses 7.3 and calls any overlap a
    # finding where `GAP` would wave it through. Both are estimates of
    # the same ink, and **a check looser than the guard downstream of it
    # is a check that reports clean and ships a finding** -- which is
    # what happened when the independent source grew 10% on 11 Sep 2026:
    # `is1` came to rest exactly on an op-amp's input lead in AS2's
    # Practice Problem 5.4a, at a measured overlap of 0.0px, and this
    # waved it through while the harness did not.
    #
    # So: widen by 3px a side and count any overlap at all. Being too
    # strict here costs a little compression on a few drawings; being
    # too loose costs a fault in the book.
    labels = [(x0 - 3.0, y0, x1 + 3.0, y1) for x0, y0, x1, y1 in cv.labels]
    touch = 0.0
    # Rule 7 -- *lines should not be together if they can be apart* --
    # applies to labels, and "do they overlap?" is not that question.
    # Narrowing the gaps to the point where nothing quite touches took
    # the closest pair of labels in the book from 45px apart (median) to
    # 2.0px, between `50Ω` and `iRX` on TR5's Figure 4-4. The review
    # harness passed every one of them.
    for i in range(len(labels)):
        ax0, ay0, ax1, ay1 = labels[i]
        for j in range(i + 1, len(labels)):
            bx0, by0, bx1, by1 = labels[j]
            if min(ay1, by1) - max(ay0, by0) <= 0:
                continue                    # not on the same line
            d = max(bx0 - ax1, ax0 - bx1)
            if 0 <= d < LABEL_APART:
                # How far short of `LABEL_APART` it falls, not that it
                # falls short. A count lets an already-tight pair be
                # tightened further for free -- 17.5px to 4.7px is one
                # near-miss before and one after -- and the deficit
                # does not.
                soft += LABEL_APART - d
    for i in range(len(labels)):
        ax0, ay0, ax1, ay1 = labels[i]
        for j in range(i + 1, len(labels)):
            bx0, by0, bx1, by1 = labels[j]
            if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                hard += 1
    for lx0, ly0, lx1, ly1 in labels:
        for ix0, iy0, ix1, iy1 in cv.inks:
            ox = min(lx1, ix1) - max(lx0, ix0)
            oy = min(ly1, iy1) - max(ly0, iy0)
            if ox > GAP and oy > GAP:
                hard += 1
    hor = [w for w in cv.wires if abs(w[1] - w[3]) < _EPS]
    ver = [w for w in cv.wires if abs(w[0] - w[2]) < _EPS]
    for lx0, ly0, lx1, ly1 in labels:
        for x0, y, x1, _y in hor:
            if x0 < lx1 - touch and lx0 + touch < x1 \
                    and ly0 + touch < y < ly1 - touch:
                hard += 1
        for x, y0, _x, y1 in ver:
            if y0 < ly1 - touch and ly0 + touch < y1 \
                    and lx0 + touch < x < lx1 - touch:
                hard += 1
    # A wire through an element's own body -- the `half` zone either
    # side of its midpoint, which is the part of an element's axis a
    # wire may never cross (its leads may be, with a hop).
    for x1, y, x2, _y in hor:
        for sx1, sy1, sx2, sy2, half in cv.esegs:
            if abs(sx1 - sx2) > _EPS or half <= 0:
                continue
            mid = (sy1 + sy2) / 2.0
            if x1 + 0.5 < sx1 < x2 - 0.5 and sy1 + 0.5 < y < sy2 - 0.5 \
                    and abs(y - mid) < half + 2:
                hard += 1
    for x, y1, _x, y2 in ver:
        for sx1, sy1, sx2, sy2, half in cv.esegs:
            if abs(sy1 - sy2) > _EPS or half <= 0:
                continue
            mid = (sx1 + sx2) / 2.0
            if y1 + 0.5 < sy1 < y2 - 0.5 and sx1 + 0.5 < x < sx2 - 0.5 \
                    and abs(x - mid) < half + 2:
                hard += 1
    # A wire entering an op-amp triangle anywhere but its three pins.
    # The pin connections on the two faces, and the output rising from
    # the tip, are how the symbol is wired and are not faults.
    m = 4.0
    for ox0, oy0, ox1, oy1 in cv.obstacles:
        bx0, by0, bx1, by1 = ox0 - m, oy0 - m, ox1 + m, oy1 + m
        for x1, y1, x2, y2 in cv.wires:
            if abs(y1 - y2) < _EPS:
                if not (by0 < y1 < by1 and x1 < bx1 and x2 > bx0):
                    continue
                if abs(x2 - ox0) < 1 or abs(x1 - ox1) < 1:
                    continue
                if abs(x1 - ox0) < 1 or abs(x2 - ox1) < 1:
                    continue
                hard += 1
            elif bx0 < x1 < bx1 and y1 < by1 and y2 > by0:
                tipy = (oy0 + oy1) / 2.0
                if abs(x1 - ox1) < 1 and abs(max(y1, y2) - tipy) < 1:
                    continue
                hard += 1
    # An *element* inside a body, or crossing one. A different fault
    # from a wire doing it, and the one #372's guard was widened for:
    # a line can cross a body without its midpoint being inside it, and
    # a horizontal segment has zero height, so an "overlap on both axes"
    # test reports nothing until the segment is given its thickness.
    for bx0, by0, bx1, by1 in cv.obstacles:
        for sx1, sy1, sx2, sy2, half in cv.esegs:
            if half <= 0:
                continue
            mx, my = (sx1 + sx2) / 2.0, (sy1 + sy2) / 2.0
            if bx0 + 1 < mx < bx1 - 1 and by0 + 1 < my < by1 - 1:
                hard += 1
                continue
            lox, hix = min(sx1, sx2) - half, max(sx1, sx2) + half
            loy, hiy = min(sy1, sy2) - half, max(sy1, sy2) + half
            if min(hix, bx1) - max(lox, bx0) > 1 \
                    and min(hiy, by1) - max(loy, by0) > 1:
                hard += 1
    return hard, soft


def _tighten_gaps(elements: List[Element], marks, choice,
                  row_h: Optional[float]) -> Optional[List[float]]:
    """How wide each column gap needs to be for *this* drawing.

    `px(col)` was `MARGIN + col * COL_W`, so every gap was 132px whether
    it carried a stretched resistor with a two-line label or nothing but
    a wire. Measured over the book, **the median gap has 95px of air in
    it** and 881 of 919 gaps carry nothing wider than 60px. That is the
    horizontal half of Roberto's balloon, and the half his rule 5 --
    *keep as small a relative figure size compared to the size of the
    elements* -- is mostly about.

    The rule is deliberately conservative, because two labels in
    neighbouring gaps are the thing that breaks when a drawing narrows:

      * a gap is sized by the **full width** of the widest object whose
        x-extent touches it, not by the part that lies inside it. An
        object centred in the gap then keeps `H_CLEAR` to the columns
        either side, and two objects half-overhanging from opposite
        columns still clear each other by `H_CLEAR`, since the gap is at
        least twice either half-width plus the clearance.
      * a gap is **never wider than `COL_W`**. A drawing can only
        narrow, so nothing that fits today can stop fitting.

    Labels count, not just symbols. A measurement that looks only at
    bodies measures half the picture and it is the labels that collide
    -- the same distinction that cost #212 three rounds."""
    probe: dict = {}
    _render_once(elements, marks, choice[0], choice[1],
                 row_h=row_h, out=probe)
    lay, cv = probe["lay"], probe["cv"]
    cols = set(lay.node_col.values()) | set(lay.elem_col.values())
    if len(cols) < 2:
        return None
    top = max(cols)
    budget = _collisions(cv)
    boxes = list(cv.inks) + list(cv.labels) + list(cv.obstacles)
    gaps = []
    for c in range(top):
        lo, hi = lay.px(c), lay.px(c + 1)
        need = 0.0
        for x0, _y0, x1, _y1 in boxes:
            if x1 <= lo + 0.5 or x0 >= hi - 0.5:
                continue
            need = max(need, x1 - x0)
        gaps.append(min(COL_W, max(COL_W_MIN, need + 2 * H_CLEAR)))

    # The estimate above sizes a gap by the *widest* thing in it, and
    # two things at the same height need the sum of their widths, not
    # the larger. Rather than model that -- the labels are drawn by a
    # dozen different call sites, each with its own offsets -- draw it
    # and look, then let the gaps that are still too tight push back
    # out. Springs settling, in the one dimension the drawing has.
    #
    # It stops when the narrowed drawing is no worse than the full-width
    # one, so a fault that is already in the book at 132px is not a
    # reason to refuse to narrow. Monotone and capped, so the worst case
    # is the uniform COL_W it started from.
    for _ in range(8):
        step: dict = {}
        _render_once(elements, marks, choice[0], choice[1],
                     row_h=row_h, gaps=gaps, out=step)
        h, sf = _collisions(step["cv"])
        if h <= budget[0] and sf <= budget[1]:
            break
        wide = [min(COL_W, g + 18.0) for g in gaps]
        if wide == gaps:
            return None                  # back at COL_W and still bad
        gaps = wide
    else:
        return None
    return gaps if any(g < COL_W - 0.5 for g in gaps) else None


def _placements(rows: List[str], ups: List[str]):
    """Every combination of "on the row" and "above the row" worth
    drawing, the plain banded layout first.

    An op-amp cannot be both, so the two subsets are kept disjoint.
    Capped: the count is exponential and no example in the book offers
    more than two candidates, so beyond that only the extremes are
    tried rather than a partial search that would look thorough."""
    names = sorted(set(rows) | set(ups))
    if len(names) > 3:
        return [(set(), set()), (set(rows), set()), (set(), set(ups))]
    out = []
    for k in range(3 ** len(names)):
        r, a, n = set(), set(), k
        for name in names:
            pick, n = n % 3, n // 3
            if pick == 1 and name in rows:
                r.add(name)
            elif pick == 2 and name in ups:
                a.add(name)
        if (r, a) not in [(x, y) for x, y in out]:
            out.append((r, a))
    return out


def _render_once(elements: List[Element], marks=None,
                 allow_raise: Optional[set] = None,
                 allow_above: Optional[set] = None,
                 row_h: Optional[float] = None,
                 gaps: Optional[List[float]] = None,
                 out: Optional[dict] = None) -> str:
    lay = _Layout(elements, allow_raise=allow_raise,
                  allow_above=allow_above, row_h=row_h, gaps=gaps)
    cv = _Canvas()
    if out is not None:
        out["lay"], out["cv"] = lay, cv
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
        if e.kind == "t" or e.kind in PORT_BLOCK:
            tl, tr = _port_tops(e)
            xa, xb = lay.px(lay.node_col[tl]), lay.px(lay.node_col[tr])
            if _drawn_four(e):
                # All four terminals named (#314). The symbol still
                # spans between its two tops, but its lower terminals
                # no longer reach the rail: each leaves its face
                # sideways, rises through the clear column the layout
                # kept beside the block, and joins its own node on the
                # row. A bottom that is ground still drops to the rail
                # as it always did. The two-node ground logic below is
                # untouched -- this branch never cuts the rail and
                # never asks for a symbol on it.
                # The block's own band: lane 0 is the node row's, a
                # later lane one BLOCK_LANE_H lower (#321). Its upper
                # terminals then rise to the node row at their own
                # columns, which is where the book joins the blocks.
                lane = lay.block_lane.get(e.name, 0)
                y_lane = y_top + lane * BLOCK_LANE_H
                y_band = y_lane + ROW_H
                if e.kind == "t":
                    lows = _draw_transformer(cv, e, xa, xb, y_lane, y_band,
                                             four=True)
                else:
                    lows = _draw_port_box(cv, e, xa, xb, y_lane, y_band,
                                          four=True)
                if lane:
                    for xn in (xa, xb):
                        cv.wire(xn, y_top, xn, y_lane)
                below = lane < lay.max_block_lane
                (tl, bl), (tr, br) = e.port_nodes
                left_first = lay.node_col[tl] <= lay.node_col[tr]
                lb, rb = (bl, br) if left_first else (br, bl)
                lc, rc = lay.port_return.get(e.name, (None, None))
                (xl, low), (xr, low_r) = lows
                low = max(low, low_r)
                # Each lead rises through the spacer column on the side
                # its node lies -- its own side normally, the far side
                # when the node is across the block (a bottom that is
                # the other port's top). A lead bound for the far side,
                # and both leads when they share a side (a common
                # bottom), travel along one line under the block: the
                # transformer's is the line of its own feet, so a common
                # bottom simply joins them, which is what an
                # autotransformer looks like in a book; the box hangs
                # lower than its terminals, so its line runs under it.
                # One shared line, so whatever hangs beside the block is
                # crossed once rather than twice.
                # A far-side lead runs *below* the feet, since a foot on
                # its way to the rail would otherwise read as joined to
                # it; the crossing is then a hop on the foot's drop.
                bus_shared = low if e.kind == "t" else low + 22.0
                bus_far = low + 22.0
                targets = {"L": lay.return_col(e, lb), "R": lay.return_col(e, rb)}
                shared = (lb != "0" and rb != "0"
                          and targets["L"] is not None
                          and targets["L"] == targets["R"])
                risen = set()
                for node, x, side in ((lb, xl, "L"), (rb, xr, "R")):
                    if node == "0":
                        if below:
                            # another block hangs under this one, so a
                            # drop to the rail would cut through it:
                            # a stub and the symbol, right here
                            cv.wire(x, low, x, low + 16.0)
                            _ground_symbol(cv, x, low + 16.0)
                            continue
                        cv.wire(x, low, x, y_bot)
                        ground_x.append(x)
                        ground_marks.append(x)
                        continue
                    c = targets[side]
                    if c is None or node not in lay.node_col:
                        continue
                    xs = lay.px(c)
                    xn = lay.px(lay.node_col[node])
                    far = (c == lc) != (side == "L")
                    if far or shared:
                        bus = bus_far if far else bus_shared
                        cv.wire(x, low, x, bus)
                        cv.wire(min(x, xs), bus, max(x, xs), bus)
                        if c not in risen:
                            cv.wire(xs, bus, xs, y_top)
                    else:
                        cv.wire(min(x, xs), low, max(x, xs), low)
                        if c not in risen:
                            cv.wire(xs, low, xs, y_top)
                    if c not in risen:
                        cv.wire(min(xs, xn), y_top, max(xs, xn), y_top)
                        risen.add(c)
                segs[e.name] = (xa, y_top, xb, y_top)
                continue
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
                # A two-node block in a lane (#321): drawn one
                # BLOCK_LANE_H lower per lane, its upper terminals rising
                # to the node row; when another block hangs below it,
                # its legs stop at its own band's foot with a ground
                # symbol there, since a drop to the rail would cut the
                # lower block. Lane 0 with nothing below is the drawing
                # as it always was.
                lane = lay.block_lane.get(e.name, 0)
                y_lane = y_top + lane * BLOCK_LANE_H
                below = lane < lay.max_block_lane
                if lane:
                    for xn in (xa, xb):
                        cv.wire(xn, y_top, xn, y_lane)
                if below:
                    faces = _draw_port_box(cv, e, xa, xb, y_lane, y_lane + ROW_H,
                                           legs_to_rail=False)
                    for x, low in faces:
                        # past the box's own 12px overhang, then the symbol
                        cv.wire(x, low, x, low + PORT_BOX_OVER + 16.0)
                        _ground_symbol(cv, x, low + PORT_BOX_OVER + 16.0)
                    segs[e.name] = (xa, y_top, xb, y_top)
                    continue
                legs = _draw_port_box(cv, e, xa, xb, y_lane, y_bot)
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
        xa, xb = lay.px(lay.node_col[e.n1]), lay.px(lay.node_col[e.n2])
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
        # keep n1 at the end the element was declared from. When block
        # lanes have made the band taller (#321), the body stays in the
        # first band, level with the top block, and plain wire runs on
        # to the rail -- centred on the whole band it landed on the
        # top block's ground symbols.
        y_foot = y_bot
        if lay.max_block_lane:
            y_foot = y_top + lay.row_h
            cv.wire(x, y_foot, x, y_bot)
        if e.n1 == "0":
            segs[e.name] = (x, y_foot, x, y_top)
        else:
            segs[e.name] = (x, y_top, x, y_foot)
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
        terms = e.fields[:3] if e.kind == "o" else e.nodes
        for n in terms:
            touching[n] = touching.get(n, 0) + 1
    for n, count in touching.items():
        if n != "0" and count >= 3 and n in lay.node_col:
            cv.dot(lay.px(lay.node_col[n]), y_top)

    # 6. node names, tucked just above the row -- clear of the wire by
    #    GAP like everything else. A node can be called `ag` or `bg`
    #    (the three-phase books do), and those hang below the baseline.
    # A raised op-amp's input leaves its node *above* the row -- which
    # is the strip these names are lettered in -- and runs right to the
    # triangle. Those names go to the left of their dot instead; every
    # other name keeps its place (#367).
    _raised_in = lay.raised_input_nodes()
    for n, col in lay.node_col.items():
        if n in _raised_in:
            cv.text(lay.px(col) - 6,
                    y_top - _HALF - GAP - LABEL_DESCENT, n, "end")
        else:
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

    # 7b. X14 (version X only): the by-hand overlay, when a caller
    #     asked for one. Before the flush so it is ordinary parts.
    if marks:
        _byhand_marks(cv, lay, segs, y_top, marks)

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
        '.symbulator-schematic .sub{{font-size:{5:g}em}}'
        '.symbulator-schematic .mesh{{stroke:var(--accent,currentColor);'
        'opacity:.75}}'
        '.symbulator-schematic .mesh-head{{fill:var(--accent,currentColor);'
        'stroke:none;opacity:.75}}'
        '.symbulator-schematic .bh-node{{stroke:var(--accent,currentColor);'
        'fill:none;opacity:.85}}'
        '.symbulator-schematic .bh-encl{{stroke:var(--accent,currentColor);'
        'fill:none;opacity:.8;stroke-dasharray:5 4}}'
        '.symbulator-schematic .mesh-lbl{{font:12px/1 ui-sans-serif,'
        'system-ui,sans-serif;fill:var(--accent,currentColor);stroke:none;'
        'opacity:.9}}'
        '.symbulator-schematic .bh-cap{{font:11px/1 ui-sans-serif,'
        'system-ui,sans-serif;fill:var(--accent,currentColor);stroke:none;'
        'opacity:.9;letter-spacing:.04em}}</style>'
        '{4}</svg>'
    ).format(x0, y0, w, h, "".join(cv.parts), SUB_SCALE)


def _rounded(x0: float, y0: float, x1: float, y1: float,
             r: float = 12.0, cls: str = "bh-encl") -> str:
    """A rounded rectangle as a path -- the dashed enclosure a textbook
    draws round a supernode or a supermesh."""
    r = min(r, (x1 - x0) / 2.0, (y1 - y0) / 2.0)
    return (
        '<path class="{6}" d="M{0:g} {1:g} H{2:g} A{4:g} {4:g} 0 0 1 {3:g} '
        '{5:g} V{7:g} A{4:g} {4:g} 0 0 1 {2:g} {8:g} H{0:g} '
        'A{4:g} {4:g} 0 0 1 {9:g} {7:g} V{5:g} A{4:g} {4:g} 0 0 1 {0:g} '
        '{1:g} Z"/>'
    ).format(x0 + r, y0, x1 - r, x1, r, y0 + r, cls, y1 - r, y1, x0)


def _byhand_marks(cv: _Canvas, lay: "_Layout",
                  segs: Dict[str, Tuple[float, float, float, float]],
                  y_top: float, marks) -> None:
    """X14: the by-hand overlay -- what a student would draw on the
    circuit before writing the equations.

    `marks` is what `byhand.nodal()` / `byhand.mesh()` hand back:

        nodes        the nodes whose KCL is written, ringed
        supernodes   [[node, node, ...], ...] enclosed together
        loops        {"I1": [(element, +1|-1), ...]} in traversal order
        supermeshes  [["I1", "I2"], ...] enclosed together

    Everything here is opt-in and additive: with no `marks` the drawing
    is byte for byte the one this module has always produced, so both
    schematic harnesses see exactly what they saw before.

    Drawn in `var(--accent, currentColor)` so the overlay reads as one
    layer distinct from the circuit, follows whichever of the app's
    themes is on, and still renders standalone where that variable does
    not exist."""
    marks = marks or {}
    node_x = {}
    for n, col in lay.node_col.items():
        node_x[n] = lay.px(col)

    # --- the nodes whose equation is being written ----------------------
    for n in marks.get("nodes") or ():
        if n not in node_x:
            continue
        cv.raw('<circle class="bh-node" cx="{0:g}" cy="{1:g}" r="7"/>'
               .format(node_x[n], y_top),
               (node_x[n] - 9, y_top - 9), (node_x[n] + 9, y_top + 9))

    # --- supernodes -----------------------------------------------------
    #
    # A supernode encloses two or more nodes *and* the source between
    # them. The source is a spanning element drawn above the row, so the
    # enclosure has to reach up to it -- which is why the box is grown
    # over every element whose both ends are inside the group, not just
    # over the node points.
    #
    # One case must not be drawn as a box: a group whose two nodes have
    # some *other* node between them on the row. A box there silently
    # swallows a node that is not in the supernode, which is worse than
    # not drawing one. Those get a ring each and a dashed tie instead.
    for group in marks.get("supernodes") or ():
        inside = [n for n in group if n in node_x]
        if len(inside) < 2:
            continue
        xs = [node_x[n] for n in inside]
        lo, hi = min(xs), max(xs)
        strays = [n for n, x in node_x.items()
                  if n not in group and lo < x < hi]
        if strays:
            for n in inside:
                cv.raw('<circle class="bh-encl" cx="{0:g}" cy="{1:g}" '
                       'r="13"/>'.format(node_x[n], y_top),
                       (node_x[n] - 15, y_top - 15),
                       (node_x[n] + 15, y_top + 15))
            cv.raw('<path class="bh-encl" d="M{0:g} {1:g} H{2:g}"/>'
                   .format(lo + 13, y_top, hi - 13),
                   (lo, y_top - 2), (hi, y_top + 2))
            cap_x, cap_y = lo, y_top - 20
        else:
            y0, y1 = y_top - 16, y_top + 16
            by_name = {el.name: el for el in lay.elements}
            for name, seg in segs.items():
                el = by_name.get(name)
                if el is None:
                    continue
                ends = [t for t in _terminals(el) if t != "0"]
                if ends and all(t in group for t in ends):
                    y0 = min(y0, seg[1] - 20, seg[3] - 20)
                    y1 = max(y1, seg[1] + 20, seg[3] + 20)
            cv.raw(_rounded(lo - 20, y0, hi + 20, y1),
                   (lo - 22, y0 - 2), (hi + 22, y1 + 2))
            cap_x, cap_y = lo - 20, y0 - 5
        cv.runs(cap_x, cap_y, [("supernode", False)], "start", cls="bh-cap")

    # --- the mesh currents ---------------------------------------------
    centres = {}
    for name, walk in (marks.get("loops") or {}).items():
        points = []
        for element, _sign in walk:
            seg = segs.get(element)
            if seg is None:
                continue
            points.append(((seg[0] + seg[2]) / 2.0, (seg[1] + seg[3]) / 2.0))
        if len(points) < 2:
            continue
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)

        area = 0.0
        for i, (px, py) in enumerate(points):
            qx, qy = points[(i + 1) % len(points)]
            area += px * qy - qx * py
        clockwise = area > 0

        reach = min(math.hypot(px - cx, py - cy) for px, py in points)
        r = max(13.0, min(30.0, reach * 0.45))

        # The centroid of a mesh's elements is usually the middle of the
        # loop, but on a thin mesh -- two elements nearly in line, a pair
        # in parallel -- it lands on a symbol, and a label over a
        # resistor is worse than a label slightly off centre. Measured
        # over the example book, that was 24 of 251. So try the centroid
        # first and then a widening ring, and take the first placement
        # clear of every symbol's ink. Ink is what the harness measures
        # for the drawing's own labels (#212); the same rule applies to
        # these.
        half_w, half_h = 11.0, 9.0

        def clear(px, py):
            for ix0, iy0, ix1, iy1 in cv.inks:
                if (ix0 - half_w <= px <= ix1 + half_w
                        and iy0 - half_h <= py <= iy1 + half_h):
                    return False
            return True

        if not clear(cx, cy):
            best = None
            for step in (14.0, 24.0, 34.0):
                for k in range(8):
                    angle = k * math.pi / 4.0
                    tx = cx + step * math.cos(angle)
                    ty = cy + step * math.sin(angle)
                    if clear(tx, ty):
                        best = (tx, ty)
                        break
                if best:
                    break
            if best:
                cx, cy = best
                r = max(12.0, min(r, 20.0))

        centres[name] = (cx, cy, r, points)

        span = 1.5 * math.pi                 # 270 degrees: a clear circulation
        t0 = -0.35 * math.pi
        t1 = t0 + span if clockwise else t0 - span
        x0, y0 = cx + r * math.cos(t0), cy + r * math.sin(t0)
        xe, ye = cx + r * math.cos(t1), cy + r * math.sin(t1)
        sweep = 1 if clockwise else 0
        arc = ('<path class="mesh" d="M{0:g} {1:g} A{2:g} {2:g} 0 1 {3:d} '
               '{4:g} {5:g}"/>').format(x0, y0, r, sweep, xe, ye)

        # The head, on the tangent at the far end of the arc.
        tx, ty = (-math.sin(t1), math.cos(t1))
        if not clockwise:
            tx, ty = -tx, -ty
        nx, ny = -ty, tx
        head = ('<path class="mesh-head" d="M{0:g} {1:g} L{2:g} {3:g} '
                'L{4:g} {5:g} Z"/>').format(
            xe + tx * 7.0, ye + ty * 7.0,
            xe - tx * 2.0 + nx * 4.0, ye - ty * 2.0 + ny * 4.0,
            xe - tx * 2.0 - nx * 4.0, ye - ty * 2.0 - ny * 4.0)

        cv.raw(arc + head, (cx - r - 8, cy - r - 8), (cx + r + 8, cy + r + 8))
        cv.runs(cx, cy + 4.5, _name_runs(name), "middle", cls="mesh-lbl")

    # --- supermeshes ----------------------------------------------------
    #
    # The two loops a shared current source merged into one equation,
    # enclosed together. Drawn over the *elements* of both loops rather
    # than over their two arrows, since that is the region the one KVL
    # is written around.
    for group in marks.get("supermeshes") or ():
        pts = []
        for name in group:
            if name in centres:
                cx, cy, r, points = centres[name]
                pts.extend(points)
                pts.append((cx - r, cy - r))
                pts.append((cx + r, cy + r))
        if len(pts) < 3:
            continue
        x0 = min(p[0] for p in pts) - 16
        x1 = max(p[0] for p in pts) + 16
        y0 = min(p[1] for p in pts) - 16
        y1 = max(p[1] for p in pts) + 16
        cv.raw(_rounded(x0, y0, x1, y1),
               (x0 - 2, y0 - 2), (x1 + 2, y1 + 2))
        cv.runs(x0, y0 - 5, [("supermesh", False)], "start", cls="bh-cap")


def to_svg(desc: str, marks=None) -> str:
    """Render a Symbulator circuit description as a standalone SVG
    string.

    `marks` (X14, version X only) overlays what a by-hand run would
    draw on the circuit -- ringed nodes, dashed supernode and supermesh
    enclosures, and a circulating arrow per mesh. Pass
    `byhand.nodal(...).marks` or `byhand.mesh(...).marks`. Omitted, the
    drawing is byte for byte the one this function has always produced.

    >>> "svg" in to_svg("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k")
    True
    """
    return _render(parse_circuit(desc, expand_si=False), marks=marks)


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
#   Considered again on 6 Sep 2026 -- deltas, wyes and diamonds as
#   drawn motifs -- and declined by Roberto as not worth the effort.
#   What was measured, for whenever it comes up again: every element
#   here is horizontal or vertical, so a triangle needs angled bodies
#   and label placement first; and the shape is not the signal. Of the
#   330 built-in entries, 181 contain a three-element loop when ground
#   may be a corner (every divider is one), 34 contain one avoiding
#   ground, and only the 7 three-phase entries (Lesson 9 and the
#   monograph) are drawn as a delta in their source -- the other 27
#   are ordinary meshes the books draw as rectangles. A recogniser
#   keyed on topology alone is right about one time in five. A
#   signature of three grounded sources each feeding one corner of a
#   triangle matches all 7 and none of the 27, but 7 is a sample. If
#   it is ever built: fire only on that narrow signature and the exact
#   five-element diamond, let a `.cir` key override, keep today's
#   drawing as the fallback, and prove every unclaimed entry renders
#   byte-identically. Scoring candidates by crossings and hops rejects
#   bad pictures but cannot choose the meaningful one -- Example 12.11
#   scores perfectly as a ladder and still does not read as three-phase.
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
#   (The four-terminal box of #314 does draw the parameters, and since
#   #321 two such boxes that overlap stack in lanes down the band, the
#   lower one's upper terminals rising to the node row -- the shape a
#   book gives an interconnection of two-ports.)
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
