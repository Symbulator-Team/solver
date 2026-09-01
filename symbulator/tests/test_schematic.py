"""
Schematic drawing: what the SVG must contain for each element type, and
what the documented limitations actually do.

These are structural rather than pixel tests. Asserting exact
coordinates would freeze the layout and break on every spacing tweak;
asserting that a resistor produces a resistor body, that a source
carries its polarity the way the engine stamps it, and that a parallel
pair does not overlap, is what a later refactor must not break.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import re

import pytest

from symbulator.schematic import to_svg, draw, _split_name
from symbulator.elements import CircuitError


DIVIDER = "e1,1,0,12:r1,1,2,4:r2,2,0,4"


def texts(svg):
    """The label strings in a drawing, in document order, with each
    label's runs flattened -- an element name is one <text> holding a
    full-height <tspan> and a subscript one (#212)."""
    return [re.sub(r"<[^>]*>", "", inner)
            for inner in re.findall(r"<text[^>]*>(.*?)</text>", svg)]


def shown(name):
    """How an element name reads once drawn: `r1` -> `R1`, `rin` ->
    `RIN`. The kind letter stands full height and the rest is a
    capitalised subscript, so the flattened label is the name upper
    cased with its underscores closed up."""
    head, sub = _split_name(name)
    return head + sub


# --- the shape of the output ------------------------------------------

def test_output_is_a_standalone_svg():
    svg = to_svg(DIVIDER)
    assert svg.lstrip().startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert "viewBox" in svg


def test_every_stroke_defers_to_css():
    """One drawing has to work in both themes, so nothing may name a
    colour of its own."""
    svg = to_svg(DIVIDER)
    assert "currentColor" in svg
    for attr in re.findall(r'(?:stroke|fill)="([^"]+)"', svg):
        assert attr in ("currentColor", "none"), attr


def test_labels_name_every_element():
    svg = to_svg(DIVIDER)
    joined = " ".join(texts(svg))
    for name in ("e1", "r1", "r2"):
        assert shown(name) in joined


# --- element bodies ----------------------------------------------------

@pytest.mark.parametrize("desc,name", [
    ("e1,1,0,5:r1,1,0,100", "r1"),
    ("e1,1,0,5:c1,1,0,1e-6", "c1"),
    ("e1,1,0,5:l1,1,0,1e-3", "l1"),
    ("j1,0,1,1:r1,1,0,100", "j1"),
    ("e1,1,0,5:s1,1,0", "s1"),
])
def test_each_element_type_draws_and_is_labelled(desc, name):
    svg = to_svg(desc)
    assert shown(name) in " ".join(texts(svg))
    assert svg.count("<path") + svg.count("<line") + svg.count("<circle") > 1


def test_two_port_blocks_draw_as_labelled_boxes():
    """A documented limitation: they render, without port parameters."""
    svg = to_svg("e1,1,0,5:z1,1,2:r1,2,0,50")
    assert shown("z1") in " ".join(texts(svg))


# --- things the engine decides, not the drawing ------------------------

def test_source_polarity_is_drawn_and_follows_terminal_order():
    """engine._stamp_e stamps v(n1) - v(n2), so + belongs on the n1 end.
    The marks are strokes, not glyphs, so the test is that they exist and
    that reversing the terminals moves them -- if orientation ever stopped
    mattering, the drawing would be lying about the sign of every answer."""
    svg = to_svg("e1,1,0,12:r1,1,0,4")
    two_stroke = [d for d in re.findall(r'<path d="([^"]+)"', svg)
                  if d.count("M") == 2]
    assert two_stroke, "no + mark drawn on the source"
    assert svg != to_svg("e1,0,1,12:r1,1,0,4")


def test_coupled_inductors_are_captioned_not_wired_together():
    """m couples two elements, not two nodes; a dashed tie between coils
    just reads as another wire, so the coupling is written above."""
    svg = to_svg("e1,1,0,5:l1,1,0,1e-3:l2,2,0,1e-3:r1,2,0,50:m1,l1,l2,1e-4")
    joined = " ".join(texts(svg))
    assert shown("l1") in joined and shown("l2") in joined
    assert shown("m1") in joined


def test_negative_coupling_keeps_the_dots_and_shows_its_sign():
    """Reversed coupling is a negative M -- engine.py adds +M*i_other with
    no orientation term -- so the dots never move and the sign has to be
    visible in the caption instead."""
    svg = to_svg("e1,1,0,5:l1,1,0,1e-3:l2,2,0,1e-3:r1,2,0,50:m1,l1,l2,-1e-4")
    assert "−" in " ".join(texts(svg)) or "-" in " ".join(texts(svg))


# --- layout properties, not coordinates --------------------------------

def test_parallel_elements_do_not_share_a_row():
    """The interval colouring exists for this case: two elements across
    the same pair of nodes must be drawn on different rows."""
    svg = to_svg("e1,1,0,5:r1,1,0,100:r2,1,0,200")
    ys = [float(m) for m in re.findall(r'<line[^>]*y1="([-\d.]+)"', svg)]
    assert len(set(round(y) for y in ys)) > 1


def test_an_element_spanning_an_intermediate_node_is_lifted():
    """A bridge draws as a ladder with a jumper over the top -- a
    documented limitation, and the reason the stacking exists at all."""
    svg = to_svg("e1,1,0,5:r1,1,2,1:r2,2,0,1:r3,1,0,1")
    # Wires are merged where collinear, so count strokes of any kind:
    # the lifted branch still needs risers beyond a flat ladder's wiring.
    assert svg.count("<line") + svg.count("<path") > 6


def test_a_chain_comes_out_as_a_chain():
    ladder = to_svg("e1,1,0,5:r1,1,2,1:r2,2,3,1:r3,3,0,1")
    assert all(shown(n) in " ".join(texts(ladder))
               for n in ("r1", "r2", "r3"))


def test_opamp_stage_draws():
    svg = to_svg("e1,1,0,1:r1,1,2,1'k:r2,2,3,4'k:o1,0,2,3")
    assert shown("o1") in " ".join(texts(svg))


# --- values are shown the way they were typed --------------------------

def test_phasor_values_stay_in_angle_notation():
    """expand_shorthand turns 110∠0° into a 17-digit rectangular
    number even with expand_si=False; the label must show what the
    reader wrote."""
    svg = to_svg("ea,1,0,(110∠0°):r1,1,0,10")
    joined = " ".join(texts(svg))
    assert "∠" in joined
    assert "110" in joined
    assert "16829419696157" not in joined   # no float dust


def test_long_float_literals_are_rounded_for_display():
    svg = to_svg("e1,1,0,173.20508075688772:r1,1,0,10")
    joined = " ".join(texts(svg))
    assert "173.20508075688772" not in joined
    assert "173.2" in joined


def test_degree_angles_display_in_degrees():
    """`30*pi/180` is degrees spelt in radians; the label shows the
    degrees back. Display only -- the solver still gets radians."""
    svg = to_svg("j,0,2,10*sin(2*t+30*pi/180):r1,2,0,10")
    joined = " ".join(texts(svg))
    assert "30°" in joined
    assert "pi/180" not in joined


def test_long_values_move_to_a_caption_below_the_drawing():
    """A value too long to letter at the element keeps only the name
    there; `name = value` appears once, below everything else."""
    import re as _re
    desc = "e,1,0,12:r1,1,0,4+20j+[16,-14j+25j]"
    svg = to_svg(desc)
    labels = [(float(m.group(1)), _re.sub(r"<[^>]*>", "", m.group(2)))
              for m in _re.finditer(
                  r'<text[^>]*y="([-\d.]+)"[^>]*>(.*?)</text>', svg)]
    caption = [y for y, s in labels if s.startswith(shown("r1") + " = ")]
    assert len(caption) == 1
    # below every other label and every wire
    assert caption[0] > max(y for y, s in labels
                            if not s.startswith(shown("r1") + " ="))
    # and the value is not also lettered at the element
    assert sum(1 for _, s in labels if "[16," in s) == 1


def test_short_values_stay_at_the_element():
    svg = to_svg(DIVIDER)
    assert not any(" = " in s for s in texts(svg))


# --- crossings and junctions -------------------------------------------

def test_unconnected_crossings_are_drawn_as_hops():
    """Two op-amp stages that force a wire past another wire must mark
    the crossing with the semicircular no-connection hop (an arc in the
    path data), never a bare X crossing."""
    svg = to_svg("e,1,0,vs:r1,1,2,rr1:r2,2,0,rr2:o,2,3,o:"
                 "r3,o,3,rr3:r4,3,0,rr4")
    # The + input routes from the triangle back to node 2 past the
    # bumped r4 -- that crossing must carry an arc.
    assert "A5 5 0 0" in svg


def test_a_wire_never_crosses_an_element_body():
    """Layout invariant, checked over a gallery of layouts that used to
    fail: cascade, follower, transresistance, grounded inverting
    input."""
    from symbulator.schematic import _Canvas
    cases = [
        "e,1,0,vs:r12,1,2,1:r14,1,4,1:r4o,4,o,1:r2o,2,o,1:r23,2,3,1:"
        "r34,3,4,1:o1,0,2,3:o2,0,4,o",
        "e,1,0,1.5:rs,1,2,2'k:o,2,0,o:rl,o,0,1'k",
        "j,0,1,is1:r,1,o,r:o,0,1,o",
        "e,2,0,vs:r1,2,1,r1:r2,1,o,r2:o,1,0,o",
        "e,1,0,28:r1,1,2,6:r2,2,3,6:r3,1,2,2:r4,2,3,8:r5,1,3,12",
    ]
    crossings = []
    orig = _Canvas._flush_wires

    def spy(self):
        for x1, y, x2, _y in [w for w in self.wires
                              if abs(w[1] - w[3]) < 0.01]:
            for sx1, sy1, sx2, sy2, half in self.esegs:
                if abs(sx1 - sx2) > 0.01 or half <= 0:
                    continue
                mid = (sy1 + sy2) / 2.0
                if x1 + 0.5 < sx1 < x2 - 0.5 and sy1 + 0.5 < y < sy2 - 0.5 \
                        and abs(y - mid) < half + 2:
                    crossings.append((x1, y))
        for x, y1, _x, y2 in [w for w in self.wires
                              if abs(w[0] - w[2]) < 0.01]:
            for sx1, sy1, sx2, sy2, half in self.esegs:
                if abs(sy1 - sy2) > 0.01 or half <= 0:
                    continue
                mid = (sx1 + sx2) / 2.0
                if y1 + 0.5 < sy1 < y2 - 0.5 and sx1 + 0.5 < x < sx2 - 0.5 \
                        and abs(x - mid) < half + 2:
                    crossings.append((x, sy1))
        orig(self)

    _Canvas._flush_wires = spy
    try:
        for desc in cases:
            to_svg(desc)
    finally:
        _Canvas._flush_wires = orig
    assert not crossings


# --- op-amp layout -----------------------------------------------------

def test_cascade_outputs_land_right_of_their_inputs():
    """Both stages of an adder cascade must point forward -- the output
    wire never retraces through the triangle."""
    from symbulator.schematic import _Layout, _op_up
    from symbulator.elements import parse_circuit
    els = parse_circuit(
        "e,1,0,vs:r12,1,2,1:r14,1,4,1:r4o,4,o,1:r2o,2,o,1:r23,2,3,1:"
        "r34,3,4,1:o1,0,2,3:o2,0,4,o", expand_si=False)
    lay = _Layout(els)
    for op in lay.opamps:
        assert lay.node_col[op.fields[2]] > lay.node_col[_op_up(op)]


def test_noninverting_stage_draws_its_source_in_the_input_drop():
    """A grounded source that is the only thing on the + input hangs
    under the triangle instead of taking a top-row column."""
    from symbulator.schematic import _Layout
    from symbulator.elements import parse_circuit
    els = parse_circuit("e,p,0,v2:r1,1,0,r1:r2,1,o,r2:o,p,1,o",
                        expand_si=False)
    lay = _Layout(els)
    assert "p" not in lay.node_col
    assert lay.op_src["o"].name == "e"
    # and the drawing still names the node and the source
    joined = " ".join(texts(to_svg("e,p,0,v2:r1,1,0,r1:r2,1,o,r2:o,p,1,o")))
    assert "p" in joined.split() and shown("e") in joined.split()


def test_grounded_inverting_input_flips_the_pins():
    """`o,1,0,o` (inverting input on ground) must not treat ground as
    the leftmost column; the non-inverting input takes the node row."""
    svg = to_svg("e,2,0,vs:r1,2,1,r1:r2,1,o,r2:o,1,0,o")
    assert "<svg" in svg   # and the body-crossing test above covers it


def test_follower_output_loops_over_the_triangle():
    """`o1,1,2,2` -- output tied to the inverting input -- draws a
    feedback loop, not a wire back through the body."""
    svg = to_svg("e,1,0,4:o1,1,2,2:o2,2,3,o:ro,3,0,4'k:r6,3,o,6'k")
    assert "<svg" in svg


def test_no_wire_grazes_an_opamp_triangle():
    """Bo2's Figure 6.23: the feedback loop used to run 1.5px above
    the triangle's top vertex -- visually touching its corner. Every
    wire must clear the triangle by a margin, except the three pin
    connections on its faces."""
    import re as _re
    svg = to_svg("e,3,0,1:r1,3,1,1:r2,1,2,1:ca,2,0,1/5,0:cb,1,o,1,0:o,2,o,o")
    tri = _re.search(r'<path d="M([\d.]+) ([\d.]+) L\1 ([\d.]+) '
                     r'L([\d.]+) ([\d.]+) Z"', svg)
    assert tri, "no triangle found"
    ox0, oy0, oy1, ox1 = (float(tri.group(1)), float(tri.group(2)),
                          float(tri.group(3)), float(tri.group(4)))
    for m in _re.finditer(r'<line x1="([-\d.]+)" y1="([-\d.]+)" '
                          r'x2="([-\d.]+)" y2="([-\d.]+)"', svg):
        x1, y1, x2, y2 = (float(m.group(i)) for i in (1, 2, 3, 4))
        if abs(y1 - y2) < 0.01:      # horizontal
            if x2 - 1 < ox0 or x1 + 1 > ox1:
                continue             # entirely left/right, or a pin stub
            assert not (oy0 - 4 < y1 < oy1 + 4), (
                "horizontal wire at y=%g grazes the triangle" % y1)
        else:                        # vertical
            if abs(x1 - ox1) < 1 and abs(max(y1, y2) - (oy0 + oy1) / 2) < 1:
                continue             # rises straight from the tip corner
            if oy0 - 4 < max(y1, y2) and min(y1, y2) < oy1 + 4:
                assert not (ox0 - 4 < x1 < ox1 + 4), (
                    "vertical wire at x=%g grazes the triangle" % x1)


# --- input handling ----------------------------------------------------

def test_si_shorthand_is_not_negotiated():
    """to_svg parses with expand_si=False, so a bare 1k draws where the
    solver would stop and ask which it meant. This is what lets the card
    render a circuit that does not solve."""
    assert "<svg" in to_svg("e1,1,0,5:r1,1,0,1k")


def test_newline_separated_description_is_accepted():
    assert to_svg(DIVIDER) == to_svg(DIVIDER.replace(":", "\n"))


def test_a_circuit_without_ground_is_refused():
    with pytest.raises(CircuitError):
        to_svg("r1,1,2,100")


def test_draw_returns_something_renderable():
    out = draw(DIVIDER)
    assert out is not None


# --- textbook style (#212) ---------------------------------------------

def test_element_names_are_set_with_a_capitalised_subscript():
    """`rin` draws as R with a subscript IN: one full-height tspan for
    the kind letter, one `class="sub"` tspan for the rest."""
    svg = to_svg("e1,1,0,5:rin,1,2,1'k:r_a,2,0,1'k")
    assert '<tspan class="sub" dy="' in svg
    joined = " ".join(texts(svg))
    for typed, drawn in (("rin", "RIN"), ("r_a", "RA"), ("e1", "E1")):
        assert shown(typed) == drawn
        assert drawn in joined
    # the underscore is a separator, not a character to print
    assert "r_a" not in joined and "_A" not in joined


def test_subscripts_are_smaller_than_the_name_they_hang_off():
    from symbulator.schematic import SUB_SCALE, SUB_DY
    assert 0.5 < SUB_SCALE < 1.0
    assert SUB_DY > 0
    assert ".sub{font-size:" in to_svg(DIVIDER)


def test_inductor_turns_are_loops_not_humps():
    """A textbook coil is a row of loops -- a cursive `l` repeated --
    which in SVG means each turn is an arc of more than 180 degrees
    (large-arc-flag 1) across a chord shorter than its own diameter.
    The large-arc flag with a chord of exactly 2r is the hump."""
    from symbulator.schematic import IND_LOOPS, IND_R, IND_STEP
    assert 2 * IND_R > IND_STEP, "chord too long for the arc to loop"
    svg = to_svg("e1,1,0,5:l1,1,2,1e-3:r1,2,0,10")
    turn = "a{0:g} {0:g} 0 1 1 {1:g} 0".format(IND_R, IND_STEP)
    assert svg.count(turn) == IND_LOOPS


def test_a_controlled_source_is_a_diamond_and_an_independent_one_a_circle():
    """Sadiku & Alexander, Fundamentals of Electric Circuits, Fig. 1.13:
    dependent sources are drawn as diamonds. `_controlled` reads the
    value the way the solver does, so `2*i_r1` is a reference and a
    bare symbol is not."""
    from symbulator.schematic import _source_outline
    circle, diamond = _source_outline(0.0, False), _source_outline(0.0, True)
    assert circle.startswith("<circle") and diamond.startswith("<path")

    # two independent sources: two circles, no diamond
    plain = to_svg("e1,1,0,12:r1,1,2,100:j2,2,0,1")
    assert plain.count("<circle") == 2 and diamond[:12] not in plain
    # one independent, one controlled: one of each
    dep = to_svg("e1,1,0,12:r1,1,2,100:ed,2,0,2*i_r1")
    assert dep.count("<circle") == 1
    import re as _re
    diamonds = [d for d in _re.findall(r'<path d="([^"]*)"[^>]*>', dep)
                if d.endswith(" Z") and d.count(" L") == 3]
    assert len(diamonds) == 1, diamonds
    # a symbolic value that refers to nothing in the circuit is not a
    # control -- it is simply an unknown -- and keeps its circle
    sym = to_svg("e1,1,0,vs:r1,1,0,100")
    assert sym.count("<circle") == 1


def test_a_controlled_source_follows_the_solver_s_spelling_rules():
    """`i_r1`, `ir1` and `IR1` are one name to the solver (0.5.19), so
    the diamond cannot depend on the underscore being typed."""
    from symbulator.schematic import _controlled
    from symbulator.elements import parse_circuit
    for spelling in ("2*i_r1", "2*ir1", "2*IR1"):
        els = parse_circuit("e1,1,0,12:r1,1,2,100:ed,2,0," + spelling,
                            expand_si=False)
        assert "ed" in _controlled(els), spelling


def test_no_label_lands_on_a_symbol():
    """Every label clears every body's ink. The canvas records where
    each symbol draws (`_Canvas.ink`) precisely so this can be checked;
    tools/review_schematics.py runs the same test over all 330 example
    circuits."""
    from symbulator.schematic import _Canvas, _render
    from symbulator.elements import parse_circuit
    seen = {}
    orig = _Canvas._flush_wires

    def spy(self):
        seen["inks"] = list(self.inks)
        seen["parts"] = list(self.parts)
        orig(self)

    _Canvas._flush_wires = spy
    try:
        for desc in ("e1,1,0,10:r1,1,2,50:l1,2,3,0.1:c1,3,0,1e-6",
                     "j1,0,1,2:l1,1,0,0.5:c1,1,0,1e-3:ra,1,0,10",
                     "e1,1,0,10:r1,1,2,1000:jd,2,0,0.05*v_1:r2,2,0,2000",
                     # ...and with the reference marks a control adds
                     # (#213), which put ink and a label of their own
                     # on the side the value did not take
                     "e1,1,0,10:r1,1,2,2:r2,2,0,3:ed,3,0,4*i_r1:r3,3,0,4",
                     "e1,1,0,10:r1,1,2,2:rx,2,0,3:ed,3,0,2*v_rx:r3,3,0,4",
                     "e1,1,0,10:r1,1,2,2:r2,2,0,3:"
                     "ed,3,0,4*i_r1+2*v_r1:r3,3,0,4"):
            svg = _render(parse_circuit(desc, expand_si=False))
            for x0, y0, x1, y1, s in _text_boxes(svg):
                if not s.strip():
                    continue
                for ix0, iy0, ix1, iy1 in seen["inks"]:
                    ox = min(x1, ix1) - max(x0, ix0)
                    oy = min(y1, iy1) - max(y0, iy0)
                    assert not (ox > 1 and oy > 1), (desc, s)
    finally:
        _Canvas._flush_wires = orig


def _text_boxes(svg):
    """Estimated boxes for every label, the subscript runs counted at
    their own width and depth."""
    from symbulator.schematic import (SUB_SCALE, SUB_DY, LABEL_ASCENT,
                                      LABEL_DESCENT, CAP_DESCENT)
    out = []
    for m in re.finditer(
            r'<text[^>]*x="([-\d.]+)" y="([-\d.]+)" '
            r'text-anchor="(\w+)">(.*?)</text>', svg):
        x, y, anchor, inner = (float(m.group(1)), float(m.group(2)),
                               m.group(3), m.group(4))
        runs = re.findall(r'<tspan([^>]*)>([^<]*)</tspan>', inner) \
            or [("", inner)]
        w = sum(len(t) * (7.3 * SUB_SCALE if "sub" in a else 7.3)
                for a, t in runs)
        x0 = x - w / 2 if anchor == "middle" else (
            x - w if anchor == "end" else x)
        low = y + (SUB_DY + CAP_DESCENT
                   if any("sub" in a for a, _ in runs) else LABEL_DESCENT)
        out.append((x0, y - LABEL_ASCENT, x0 + w,
                    low, "".join(t for _, t in runs)))
    return out


# --- #213: the element a dependent source reads ----------------------

REF_V = "e1,1,0,10:r1,1,2,2:r2,2,0,3:ed,3,0,2*v_r1:r3,3,0,4"
REF_I = "e1,1,0,10:r1,1,2,2:r2,2,0,3:ed,3,0,4*i_r1:r3,3,0,4"


def _marks(desc, name, x1, y1, x2, y2, mark_v=False, mark_i=False):
    """`_reference_marks` on one element, drawn along a segment we
    choose, so the assertions are about the marks and not about where
    the layout happened to put the element."""
    from symbulator.schematic import _Canvas, _reference_marks, REACH
    from symbulator.elements import parse_circuit
    e = [el for el in parse_circuit(desc, expand_si=False)
         if el.name == name][0]
    cv = _Canvas()
    _reference_marks(cv, e, x1, y1, x2, y2, REACH[e.kind],
                     mark_v=mark_v, mark_i=mark_i)
    return cv


def _points(d):
    """Every coordinate pair in a path's `d`, as (x, y)."""
    nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", d)]
    return list(zip(nums[0::2], nums[1::2]))


def test_a_voltage_control_puts_a_plus_at_n1_and_a_minus_at_n2():
    """`ed,3,0,2*v_r1` tells the reader to measure the drop across r1,
    and v_r1 is v(n1) - v(n2) (engine.stamp_all). So the + belongs at
    r1's first node and the - at its second, whichever way round the
    layout drew it."""
    for (x1, x2), plus_left in (((100.0, 300.0), True),
                                ((300.0, 100.0), False)):
        cv = _marks(REF_V, "r1", x1, 50.0, x2, 50.0, mark_v=True)
        paths = [re.search(r'd="([^"]*)"', p).group(1) for p in cv.parts]
        plus = [p for p in paths if p.count("M") == 2]
        minus = [p for p in paths if p.count("M") == 1]
        assert len(plus) == 1 and len(minus) == 1, paths
        px = sum(x for x, _ in _points(plus[0])) / 4.0
        mx = sum(x for x, _ in _points(minus[0])) / 2.0
        assert (px < mx) is plus_left, (px, mx)
        # ...and both below the wire, where the name and value are not
        assert all(y > 50.0 for _, y in
                   _points(plus[0]) + _points(minus[0]))


def test_a_current_control_draws_an_arrow_from_n1_to_n2():
    """`ed,3,0,4*i_r1` reads the current through r1, and the solver's
    positive i_r1 flows n1 -> n2 (engine._stamp_r), so the head goes to
    the n2 end -- reversed when the layout drew the element the other
    way round."""
    for (x1, x2), head_right in (((100.0, 300.0), True),
                                 ((300.0, 100.0), False)):
        cv = _marks(REF_I, "r1", x1, 50.0, x2, 50.0, mark_i=True)
        head = [m.group(1) for m in re.finditer(
            r'<path d="([^"]*)" fill="currentColor"/>', "".join(cv.parts))]
        assert len(head) == 1, cv.parts
        pts = _points(head[0])
        assert len(pts) == 3
        tip = pts[1]                     # M corner, L tip, L corner
        assert (tip[0] > 200.0) is head_right, pts


def test_the_current_reference_is_labelled_i_with_the_name_beneath():
    """The label is the *i* of every textbook -- lower case, sloped --
    with the element's whole name as its subscript."""
    cv = _marks(REF_I, "r1", 100.0, 50.0, 300.0, 50.0, mark_i=True)
    label = [p for p in cv.parts if p.startswith("<text")]
    assert len(label) == 1
    assert re.sub(r"<[^>]*>", "", label[0]) == "iR1"
    assert '<tspan font-style="italic" dy="0">i</tspan>' in label[0]
    assert '<tspan class="sub"' in label[0]


def test_a_reference_reads_every_spelling_the_solver_reads():
    """`5*vr1`, `5v_R1` and `5*V_r1` are one reference to the solver
    (0.5.19), so they must be one reference to the drawing."""
    from symbulator.schematic import _references
    from symbulator.elements import parse_circuit
    for spelling in ("5*vr1", "5v_R1", "5*V_r1", "5vR1"):
        els = parse_circuit(
            "e1,1,0,10:r1,1,2,2:r2,2,0,3:ed,3,0," + spelling + ":r3,3,0,4",
            expand_si=False)
        assert _references(els)[0] == frozenset({"r1"}), spelling
    for spelling in ("3*ir2", "3i_R2", "3*I_r2"):
        els = parse_circuit(
            "e1,1,0,10:r1,1,2,2:r2,2,0,3:ed,3,0," + spelling + ":r3,3,0,4",
            expand_si=False)
        assert _references(els)[1] == frozenset({"r2"}), spelling


def test_a_node_voltage_is_not_an_element_drop():
    """`engine._alias_map` claims node voltages first, so in a circuit
    with a node called `x` the token `vx` is that node's voltage and
    not the drop across an element called `x`. The drawing has to mark
    what the solver will actually solve, so it marks nothing here."""
    from symbulator.schematic import _references
    from symbulator.elements import parse_circuit
    els = parse_circuit("e1,1,0,10:rx,1,x,2:r2,x,0,3:ed,3,0,2*vx:r3,3,0,4",
                        expand_si=False)
    assert _references(els) == (frozenset(), frozenset())


def test_an_unreferenced_element_wears_no_marks():
    """The marks say "a source is reading this". An ordinary divider
    has nothing reading anything, and must come out exactly as before."""
    assert "iR1" not in texts(to_svg(DIVIDER))
    assert 'font-style="italic"' not in to_svg(DIVIDER)
    assert "iR1" in texts(to_svg(REF_I))


def _runs_of(svg, flat):
    """The <tspan> runs of the one label whose flattened text is
    `flat`, as (text, is_subscript, is_italic)."""
    for inner in re.findall(r"<text[^>]*>(.*?)</text>", svg):
        if re.sub(r"<[^>]*>", "", inner) != flat:
            continue
        return [(m.group("txt"),
                 'class="sub"' in m.group("attrs"),
                 "italic" in m.group("attrs"))
                for m in re.finditer(
                    r'<tspan(?P<attrs>[^>]*)>(?P<txt>[^<]*)</tspan>', inner)]
    raise AssertionError("no label %r in %s" % (flat, texts(svg)))


def test_a_value_implies_its_multiplication():
    """No book prints the star. `2*x*ir2` is `2x` and then the
    current -- and the styling is what keeps the two apart."""
    svg = to_svg("e1,1,0,10:r1,1,2,2:r2,2,0,3:ed,3,0,2*x*ir2:r3,3,0,4")
    assert _runs_of(svg, "2xiR2")[0] == ("2x", False, False)


def test_multiplication_between_two_numbers_keeps_its_star():
    """`2*3` is not 23. The star only goes where what follows it
    cannot be read as a continuation of what precedes it."""
    assert "2*3" in texts(to_svg("e1,1,0,10:r1,1,2,2*3:r2,2,0,3"))


def test_a_referenced_quantity_is_set_as_a_book_sets_it():
    """Lower-case sloped letter, capitalised subscript -- and the same
    three runs however the reader spelled it."""
    for spelling in ("2*v_r1", "2vr1", "2*VR1"):
        svg = to_svg("e1,1,0,10:r1,1,2,2:r2,2,0,3:ed,3,0,"
                     + spelling + ":r3,3,0,4")
        assert _runs_of(svg, "2vR1") == [("2", False, False),
                                         ("v", False, True),
                                         ("R1", True, False)], spelling
    svg = to_svg("e1,1,0,10:r1,1,2,2:r2,2,0,3:ed,3,0,ir1/2:r3,3,0,4")
    assert _runs_of(svg, "iR1/2") == [("i", False, True),
                                      ("R1", True, False),
                                      ("/2", False, False)]


def test_a_node_voltage_in_a_value_is_set_the_same_way():
    """`v_2` is a voltage like any other; the subscript is the node."""
    svg = to_svg("e1,1,0,10:r1,1,2,2:r2,2,0,3:ed,3,0,2*v_2:r3,3,0,4")
    assert _runs_of(svg, "2v2") == [("2", False, False),
                                    ("v", False, True),
                                    ("2", True, False)]


def test_a_free_parameter_is_left_exactly_as_typed():
    """`vs` in a circuit with no node or element called `s` is a
    parameter, not a voltage -- the same reading the solver gives it,
    and the same one that keeps the source a circle."""
    svg = to_svg("e1,1,0,vs:r1,1,0,100")
    assert "vs" in texts(svg)
    assert 'font-style="italic"' not in svg


def test_pi_is_printed_as_the_letter():
    """Once multiplication is implied, `100+24*pi*j` would run together
    into the word `24pij`. The degrees rewrite goes first, so what is
    left is a real pi -- and a name that merely contains the two
    letters is not one."""
    assert "100+24\u03c0j" in texts(to_svg("e1,1,0,10:r1,1,0,100+24*pi*j"))
    assert "30\u00b0" in "".join(
        texts(to_svg("j,0,2,10e^(-t)*sin(2t+30*pi/180):r1,2,0,5")))
    assert "pin" in texts(to_svg("e1,1,0,10:r1,1,0,pin"))
