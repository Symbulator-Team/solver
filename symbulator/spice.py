"""
SPICE netlist translation, both directions (#160).

`to_spice(desc)` turns a Symbulator circuit description into a generic
ngspice-compatible netlist; `from_spice(text)` reads the linear subset of
a SPICE netlist back into Symbulator notation. Both return
`(text, warnings)`: the translation never fails on an untranslatable
element -- it leaves it out (as a `*` comment on export, dropped on
import) and says so in the warnings, so the reader always gets the part
that does translate.

The single most dangerous difference between the notations is the mega/
milli trap: Symbulator's `1'M` is mega, but in SPICE `1M` (any case) is
*milli* and mega is `1MEG`. Both directions here spell it out explicitly.

What translates:

    Symbulator -> SPICE          SPICE -> Symbulator
    r/l/c  -> R/L/C (+IC=)       R/L/C (+IC=) -> r/l/c
    e      -> V                  V (bare or DC value) -> e
    j      -> I                  I (bare or DC value) -> j
    s      -> V...0 (0V source)  E/G (VCVS/VCCS) -> e/j with v_* value
    m      -> K (numeric L only) F/H (CCCS/CCVS) -> j/e with i_* value
    dependent e/j -> E/G/F/H     K -> m (numeric L only)
    o      -> E with gain 1e9 (finite-gain stand-in, ~1e-9 rel. error)
    t      -> E + 0V sense + F (the exact ideal-transformer pair)
    z/y/h/g/a/b with a numeric [p11,p12,p21,p22] term
           -> up to 4 grounded VCCS, via the engine's own admittance
              reduction -- so every Symbulator element type exports

Dependent sources (#161): a value that is affine in node voltages,
two-terminal element drops (`v_r1`) and element currents (`i_r1`), with
numeric coefficients, becomes one SPICE element per term -- E/G for a
voltage control, H/F for a current control, an independent V/I for a
constant -- in series for a voltage source, in parallel for a current
source. A current control on anything that is not already a voltage
source gets a 0 V sensing source spliced into that element's branch
(SPICE's own ammeter idiom); the current of an independent current
source is its value and folds into the constant. All emitted elements
are plain linear SPICE -- never behavioral/dialect-specific ones.

What still warns instead of translating: symbolic values anywhere
(SPICE needs numbers), dependent values that are not affine with
numeric coefficients, two-ports without a numeric parameter term or
with a set that is singular in admittance form, and -- on import only --
SPICE's diodes/transistors/subcircuits and waveform sources
(SIN/PULSE/PWL/...). Nothing is ever mistranslated silently.

Node names pass through unchanged apart from case folding; ground is `0`
on both sides.
"""
from __future__ import annotations

import decimal
import math
import re
from typing import List, Optional, Tuple

from . import messages as M
from .elements import (parse_circuit, CircuitError, TWO_PORT_KINDS,
                       two_port_param_texts)
from .si_prefix import safe_sympify

# ---------------------------------------------------------------------------
# Shared: number formatting and suffix tables
# ---------------------------------------------------------------------------

# Symbulator prefix -> SPICE suffix. Note mega: 'M -> MEG, never M.
# Peta and atto have no SPICE suffix and fall back to e-notation.
_SPICE_SUFFIX = {
    12: "T", 9: "G", 6: "MEG", 3: "K",
    -3: "M", -6: "U", -9: "N", -12: "P", -15: "F",
}

# SPICE suffix -> power of ten. SPICE is case-insensitive: M and m are
# both milli; mega must be spelled MEG. MIL is 25.4e-6 (a thousandth of
# an inch) and converts to a plain number.
_SPICE_SUFFIX_IN = {
    "t": 12, "g": 9, "meg": 6, "k": 3,
    "m": -3, "u": -6, "µ": -6, "n": -9, "p": -12, "f": -15,
}

# Symbulator's own suffix letters for from-SPICE output (exponent -> 'x).
_SYMB_PREFIX = {
    12: "T", 9: "G", 6: "M", 3: "k",
    -3: "m", -6: "u", -9: "n", -12: "p", -15: "f",
}

_NUM_RE = re.compile(
    r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)(meg|mil|[tgkmunpfµ])?"
    r"[a-zµ]*$",
    re.IGNORECASE,
)


def _exact(value: float, spec: str = "g") -> str:
    """The shortest decimal that reads back as exactly `value`: try 6
    then 12 significant digits, fall back to repr (which Python already
    guarantees round-trips). Six digits are plenty for values a person
    typed; a *computed* gain (an admittance coefficient, a turns ratio)
    truncated to six would shift every solved voltage at the 1e-6 level
    -- measured, not hypothetical."""
    for digits in (6, 12):
        text = f"{value:.{digits}{spec}}"
        if float(text) == value:
            return text
    return repr(value)


def _mantissa(value: float, exp: int) -> Optional[str]:
    """`(2.2e-9, -9)` -> `"2.2"`: the value with a suffix's decimal
    shift taken out of it.

    In base ten, because `2.2e-9 / 10**-9` in binary is
    2.1999999999999997 and that noise went straight into the netlist as
    `2.1999999999999997N` (Roberto, 1 Sep 2026). A suffix *is* a decimal
    shift, so shifting in decimal is both simpler and right.

    None when the mantissa cannot be written as a plain decimal that
    reads back as `value`, and the caller falls back to e-notation. The
    read-back goes through the decimal-to-double conversion rather than
    a float multiplication, so the check is as exact as the shift."""
    try:
        # `f`, not `str`: Decimal keeps whatever form it was given, so
        # 1e-10 shifted up by twelve is `1E+2` and would lose its suffix
        # to the e-notation check below. A mantissa is always in
        # [1, 1000) here, so fixed-point is always short.
        text = format(decimal.Decimal(_exact(value)).scaleb(-exp), "f")
    except (decimal.InvalidOperation, decimal.Overflow, ValueError):
        return None
    if "e" in text or "E" in text:
        return None
    # A decimal shift keeps its digit count, so 1100 scaled down by
    # three is `1.100`. Trim the zeros it grew -- but only past a
    # decimal point, since `Decimal.normalize()` would take `100` to
    # `1E+2` and cost the suffix that a mantissa of 100 is entitled to.
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    try:
        if float(f"{text}e{exp}") != value:
            return None
    except (ValueError, OverflowError):
        return None
    return text


def _spice_number(value: float) -> str:
    """Format a float the way a SPICE reader expects: a suffix when one
    fits cleanly, plain decimal for near-unit values, e-notation
    otherwise. Milli is always written as a plain decimal -- this
    module never emits `M`, so its output cannot feed the mega/milli
    confusion."""
    if value == 0:
        return "0"
    if 0.001 <= abs(value) < 1000:
        return _exact(value)
    exp = math.floor(math.log10(abs(value)) / 3) * 3
    if exp in _SPICE_SUFFIX and exp != -3:
        mant = _mantissa(value, exp)
        if mant is not None:
            return f"{mant}{_SPICE_SUFFIX[exp]}"
    return _exact(value)


def _parse_spice_number(token: str) -> Optional[float]:
    """Read a SPICE value token -- number, optional suffix, optional
    trailing unit letters (`10kOhm`). None if it isn't one."""
    m = _NUM_RE.match(token.strip())
    if not m:
        return None
    number, suffix = m.group(1), (m.group(2) or "").lower()
    if suffix == "mil":
        return float(number) * 25.4e-6
    if not suffix:
        return float(number)
    # Scaled in base ten, not by multiplying two doubles: `397.3m` is
    # 0.3973, and `float("397.3") * 10**-3` is 0.39730000000000004, a
    # different double. The same fix as `si_prefix._scaled_literal`, and
    # for the same reason -- a suffix is a decimal shift, so shifting it
    # in decimal is both simpler and right.
    try:
        return float(decimal.Decimal(number).scaleb(
            _SPICE_SUFFIX_IN[suffix]))
    except (decimal.InvalidOperation, decimal.Overflow, ValueError):
        return float(number) * 10 ** _SPICE_SUFFIX_IN[suffix]


def _symb_number(value: float) -> str:
    """Format a float in Symbulator notation: `12'm`-style when a prefix
    fits cleanly, plain decimal for near-unit values."""
    if value == 0:
        return "0"
    if 0.01 <= abs(value) < 1000:
        return _exact(value)
    exp = math.floor(math.log10(abs(value)) / 3) * 3
    if exp in _SYMB_PREFIX:
        mant = _mantissa(value, exp)
        if mant is not None:
            return f"{mant}'{_SYMB_PREFIX[exp]}"
    return _exact(value)


def _numeric(expr_text: str) -> Optional[float]:
    """The value as a float if it is a plain number (no free symbols),
    else None. Runs through the solver's own guarded sympify, so the
    same expressions the solver accepts are accepted here."""
    try:
        expr = safe_sympify(expr_text, reserve_imaginary=False)
    except Exception:
        return None
    if expr.free_symbols:
        return None
    try:
        return float(expr)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Symbulator -> SPICE
# ---------------------------------------------------------------------------

# Symbulator kind letter -> SPICE element letter, for renaming. `e1`
# becomes `V1`: the kind letter is replaced, the rest of the name rides
# along. Kinds absent here have no SPICE counterpart.
_KIND_TO_SPICE = {"r": "R", "l": "L", "c": "C", "e": "V", "j": "I"}

#: Element kinds with two plain terminals -- the ones whose voltage drop
#: (`v_r1`) can be rewritten as a node difference on export.
_TWO_TERMINAL = ("r", "l", "c", "e", "j", "s")


def _fold(name: str) -> str:
    """The spelling-equivalence key: `i_r1`, `ir1` and `IR1` are one
    name to the solver (0.5.19), so the exporter folds the same way."""
    return name.replace("_", "").lower()



def _typed(el) -> str:
    """The element as the user wrote it: raw_fields when a rewrite
    happened (so comments quote `[100,...]`, not the internal
    `pr(100,...)`), fields otherwise."""
    return el.name + "," + ",".join(el.raw_fields or el.fields)

class _Skip(Exception):
    """A dependent source that cannot be translated; str() says why."""


def _two_port_admittance(kind, p11, p12, p21, p22):
    """The admittance-form coefficients (A, B, C, D) of a two-port
    parameter set: i1 = A*v1 + B*v2, i2 = C*v1 + D*v2, with i1/i2 the
    currents leaving each port node into the block. The i1/i2 formulas
    are the engine's own (`engine._stamp_two_port`), transcribed
    verbatim, so the exporter cannot disagree with the solver about
    what a parameter set means. Raises _Skip for a set that is
    singular in admittance form -- the same sets the solver itself
    cannot substitute numbers into."""
    import sympy as sp
    from sympy.solvers.solveset import linear_coeffs

    v1, v2 = sp.Dummy("v1"), sp.Dummy("v2")
    p11, p12, p21, p22 = (sp.Float(p) for p in (p11, p12, p21, p22))
    try:
        if kind == "z":
            det = p11 * p22 - p12 * p21
            i1 = (p22 * v1 - p12 * v2) / det
            i2 = (p11 * v2 - p21 * v1) / det
        elif kind == "y":
            i1 = p11 * v1 + p12 * v2
            i2 = p21 * v1 + p22 * v2
        elif kind == "h":
            i1 = (v1 - p12 * v2) / p11
            i2 = p22 * v2 - p21 * ((p12 * v2 - v1) / p11)
        elif kind == "g":
            det = p11 * p22 - p12 * p21
            i1 = (det / p22) * v1 + (p12 / p22) * v2
            i2 = (-p21 / p22) * v1 + (1 / p22) * v2
        elif kind == "a":
            i1 = (-p11 * p22 * v2 + p12 * p21 * v2 + p22 * v1) / p12
            i2 = (p11 * v2 - v1) / p12
        else:  # "b"
            i1 = (p11 / p12) * v1 + (-1 / p12) * v2
            i2 = (-(p11 * p22 - p12 * p21) / p12) * v1 + (p22 / p12) * v2
        a, b, _c0 = linear_coeffs(sp.expand(i1), v1, v2)
        c, d, _c1 = linear_coeffs(sp.expand(i2), v1, v2)
        return float(a), float(b), float(c), float(d)
    except (ZeroDivisionError, TypeError, ValueError):
        raise _Skip(f"this {kind}-parameter set is singular in "
                    f"admittance form and cannot be realised as "
                    f"conductances (the solver cannot substitute "
                    f"numbers into it either)")


def _decompose(el, keys, by_name, numeric_vals):
    """Read a dependent source's value as an affine expression over node
    voltages and element currents.

    Returns `(terms_v, terms_i, const)`: terms_v is a list of
    `(node_a, node_b, coeff)` -- a `+k`/`-k` pair of node coefficients is
    paired into one textbook difference, a lone node gets ground as its
    partner -- terms_i is `(element_name, coeff)`, and const is the
    plain-number remainder. The current of an independent current source
    is its own value, so such a reference folds into const here rather
    than becoming a term. Raises _Skip, with the reason, for anything
    that is not affine with numeric coefficients."""
    import sympy as sp
    from sympy.solvers.solveset import linear_coeffs

    expr = safe_sympify(el.value, reserve_imaginary=False)
    vsyms: dict = {}   # node name -> Dummy
    isyms: dict = {}   # element name -> Dummy
    subs: dict = {}
    # Sorted, so term order (and internal-node numbering) is stable
    # from run to run -- free_symbols is a set.
    for s in sorted(expr.free_symbols, key=lambda x: x.name):
        meaning = keys.get(_fold(s.name))
        if meaning is None:
            raise _Skip(f"references '{s}', which names neither a node "
                        f"voltage nor an element current; SPICE needs "
                        f"numeric coefficients")
        what, target = meaning
        if what == "vnode":
            subs[s] = (sp.Integer(0) if target == "0"
                       else vsyms.setdefault(target, sp.Dummy(target)))
        elif what == "vel":
            el2 = by_name[target]
            if el2.kind not in _TWO_TERMINAL:
                raise _Skip(f"references the voltage of '{target}', "
                            f"which is not a two-terminal element")
            va = (sp.Integer(0) if el2.n1 == "0"
                  else vsyms.setdefault(el2.n1, sp.Dummy(el2.n1)))
            vb = (sp.Integer(0) if el2.n2 == "0"
                  else vsyms.setdefault(el2.n2, sp.Dummy(el2.n2)))
            subs[s] = va - vb
        else:  # "iel"
            el2 = by_name[target]
            if el2.kind == "j" and numeric_vals.get(target) is not None:
                # An independent current source's current IS its value.
                subs[s] = sp.Float(numeric_vals[target])
            else:
                subs[s] = isyms.setdefault(target, sp.Dummy("i_" + target))

    expr = expr.subs(subs)
    dummies = list(vsyms.values()) + list(isyms.values())
    try:
        coeffs = linear_coeffs(expr, *dummies) if dummies else [expr]
    except Exception:
        raise _Skip("is not linear in its node voltages and element "
                    "currents; SPICE's controlled sources are linear "
                    "(E/G/F/H)")
    const = coeffs[-1]
    cv = list(zip(vsyms.keys(), coeffs[:len(vsyms)]))
    ci = list(zip(isyms.keys(), coeffs[len(vsyms):len(vsyms) + len(isyms)]))
    if not (const.is_number and all(c.is_number for _, c in cv + ci)):
        raise _Skip("has a symbolic coefficient; SPICE needs numbers")

    # Pair +k / -k node coefficients into one difference-controlled term.
    live = [(n, c) for n, c in cv if c != 0]
    terms_v, taken = [], set()
    for i, (a, ca) in enumerate(live):
        if i in taken:
            continue
        for j in range(i + 1, len(live)):
            if j not in taken and sp.simplify(live[j][1] + ca) == 0:
                # Orient the difference so the gain is positive.
                if ca.is_negative:
                    terms_v.append((live[j][0], a, -ca))
                else:
                    terms_v.append((a, live[j][0], ca))
                taken.update((i, j))
                break
        else:
            terms_v.append((a, "0", ca))
            taken.add(i)
    terms_i = [(n, c) for n, c in ci if c != 0]
    return terms_v, terms_i, const


def to_spice(desc: str) -> Tuple[str, List[str]]:
    """Translate a Symbulator circuit description to a SPICE netlist.

    Returns `(netlist, warnings)`. Untranslatable elements are emitted
    as `*` comment lines and reported; the netlist always carries a
    title line and `.end`, so what does translate is ready to paste
    into ngspice or LTspice.

    Dependent sources translate whenever their value is affine in node
    voltages (`v_2`, or a two-terminal element's drop `v_r1`) and element
    currents (`i_r1`), with numeric coefficients: each term becomes one
    SPICE element -- E/G for a voltage control, H/F for a current
    control, an independent V/I for a constant -- chained in series for
    a voltage source and in parallel for a current source. A current
    control on anything that is not already a voltage source gets a 0 V
    sensing source spliced into that element's branch, which is SPICE's
    own ammeter idiom. All of it stays plain linear SPICE -- no
    behavioral/dialect-specific elements are ever emitted."""
    elements = parse_circuit(desc)  # raises CircuitError on bad input
    lines: List[str] = ["* Translated from Symbulator notation"]
    warnings: List[str] = []
    used_names = set()
    by_name = {el.name: el for el in elements}

    node_set = set()
    for el in elements:
        if el.kind in _TWO_TERMINAL:
            node_set.update((el.n1, el.n2))
        elif el.kind == "o":
            node_set.update(el.fields[0:3])
        elif el.kind == "t" or el.kind in TWO_PORT_KINDS:
            node_set.update(el.fields[0:2])

    numeric_vals = {el.name: _numeric(el.value) for el in elements
                    if el.kind in ("r", "l", "c", "e", "j", "m")}
    l_values = {el.name: numeric_vals[el.name] for el in elements
                if el.kind == "l"}

    # What each spelling refers to: node voltages first (the primitive),
    # then element drops and element currents.
    keys: dict = {}
    for node in node_set:
        keys.setdefault(_fold("v" + node), ("vnode", node))
    for el in elements:
        keys.setdefault(_fold("v" + el.name), ("vel", el.name))
        keys.setdefault(_fold("i" + el.name), ("iel", el.name))

    # Decompose every dependent source up front.
    deps: dict = {}   # name -> (terms_v, terms_i, const) | str reason
    for el in elements:
        if el.kind in ("e", "j") and numeric_vals[el.name] is None:
            try:
                deps[el.name] = _decompose(el, keys, by_name, numeric_vals)
            except _Skip as why:
                deps[el.name] = str(why)

    # A current control is available when its element exports as (or
    # can carry) a voltage source. Anything else poisons the source
    # that references it -- and that can cascade.
    def current_available(target: str) -> bool:
        el2 = by_name[target]
        if el2.kind == "s":
            return True
        if el2.kind in ("r", "l", "c"):
            return numeric_vals[target] is not None
        if el2.kind in ("e", "j"):
            return (numeric_vals[target] is not None
                    or isinstance(deps.get(target), tuple))
        return False

    changed = True
    while changed:
        changed = False
        for name, d in deps.items():
            if isinstance(d, str):
                continue
            for target, _c in d[1]:
                if not current_available(target):
                    deps[name] = (f"references the current of '{target}', "
                                  f"which does not translate to SPICE")
                    changed = True
                    break

    # Which elements need a 0 V sensing source in their branch: any
    # current-controlled target that is not already a voltage source.
    sensed = set()
    for name, d in deps.items():
        if isinstance(d, tuple):
            for target, _c in d[1]:
                el2 = by_name[target]
                if el2.kind in ("r", "l", "c", "j") or (
                        el2.kind == "e" and numeric_vals[target] is None):
                    sensed.add(target)

    def unique(name: str) -> str:
        base = name
        n = 2
        while name.lower() in used_names:
            name = f"{base}{n}"
            n += 1
        used_names.add(name.lower())
        return name

    # Names must exist before emission: an early H part may reference
    # the sensing source of a later element. Sense names first, then
    # each element's primary name, in circuit order.
    sense_name = {t: unique("Vi_" + t) for t in sorted(sensed)}
    primary: dict = {}
    for el in elements:
        if el.kind in _KIND_TO_SPICE and numeric_vals.get(el.name) is not None:
            primary[el.name] = unique(_KIND_TO_SPICE[el.kind] + el.name[1:])
        elif el.kind == "s":
            primary[el.name] = unique("V" + el.name)
        elif el.kind == "m":
            primary[el.name] = unique("K" + el.name[1:])

    def current_ref(target: str) -> str:
        if target in sense_name:
            return sense_name[target]
        return primary[target]   # an independent e, or a short

    def inner_node(base: str, tag) -> str:
        cand = f"{base}_{tag}"
        while cand in node_set:
            cand += "x"
        node_set.add(cand)
        return cand

    def skip(el, why: str) -> None:
        lines.append(f"* {_typed(el)}  <- {why}")
        warnings.append(f"{el.name}: {why}")

    def emit_dependent(el, terms_v, terms_i, const) -> None:
        volts = el.kind == "e"          # series stack; else parallel set
        base = el.name[1:]
        parts = []                       # (letter, control_text, gain_or_value)
        for a, b, c in terms_v:
            parts.append(("E" if volts else "G", f"{a} {b}", float(c)))
        for target, c in terms_i:
            parts.append(("H" if volts else "F", current_ref(target),
                          float(c)))
        if const != 0 or not parts:
            parts.append(("V" if volts else "I", None, float(const)))

        sense = sense_name.get(el.name)
        multi = len(parts) > 1 or sense is not None
        if multi:
            lines.append(f"* {_typed(el)}  expands to:")
            what = (f"{len(parts)} SPICE elements in "
                    + ("series" if volts else "parallel"))
            warnings.append(f"{el.name}: translated as {what}"
                            + (" plus its own current sense" if sense else ""))

        def part_name(letter, i):
            if len(parts) == 1 and sense is None:
                return unique(letter + base)
            return unique(letter + base + chr(97 + i))

        if volts:
            here = el.n1
            stops = [inner_node(el.name, f"x{i + 1}")
                     for i in range(len(parts) - 1)]
            stops.append(inner_node(el.name, "s") if sense else el.n2)
            for i, (letter, ctrl, val) in enumerate(parts):
                nxt = stops[i]
                mid = f"{ctrl} " if ctrl else ""
                lines.append(f"{part_name(letter, i)} {here} {nxt} "
                             f"{mid}{_spice_number(val)}")
                here = nxt
            if sense:
                lines.append(f"{sense} {here} {el.n2} 0")
        else:
            far = inner_node(el.name, "s") if sense else el.n2
            for i, (letter, ctrl, val) in enumerate(parts):
                mid = f"{ctrl} " if ctrl else ""
                lines.append(f"{part_name(letter, i)} {el.n1} {far} "
                             f"{mid}{_spice_number(val)}")
            if sense:
                lines.append(f"{sense} {far} {el.n2} 0")

    for el in elements:
        num = numeric_vals.get(el.name)
        if el.kind in ("r", "l", "c") and num is None:
            skip(el, f"value '{el.value}' is not a plain number; SPICE "
                     f"needs numeric component values")
        elif el.kind in _KIND_TO_SPICE and num is not None:
            line_end = el.n2
            if el.name in sensed:
                line_end = inner_node(el.name, "s")
            line = f"{primary[el.name]} {el.n1} {line_end} {_spice_number(num)}"
            if el.kind in ("l", "c"):
                ic = _numeric(el.ic)
                if ic:
                    line += f" IC={_spice_number(ic)}"
            lines.append(line)
            if el.name in sensed:
                lines.append(f"{sense_name[el.name]} {line_end} {el.n2} 0")
        elif el.kind in ("e", "j"):    # dependent (num is None)
            d = deps[el.name]
            if isinstance(d, str):
                skip(el, f"value '{el.value}' {d}" if d.startswith(("is", "has"))
                     else d)
            else:
                emit_dependent(el, *d)
        elif el.kind == "s":
            # A 0 V source is SPICE's own idiom for a short (and its
            # ammeter). The name keeps the `s`: `s1` -> `Vs1`.
            lines.append(f"{primary[el.name]} {el.n1} {el.n2} 0")
        elif el.kind == "m":
            l1, l2 = el.fields[0], el.fields[1]
            v1, v2 = l_values.get(l1), l_values.get(l2)
            mval = numeric_vals.get(el.name)
            if v1 and v2 and mval is not None:
                k = mval / math.sqrt(v1 * v2)
                lines.append(f"{primary[el.name]} {primary[l1]} {primary[l2]} "
                             f"{_exact(k)}")
                if k > 1:
                    warnings.append(
                        f"{el.name}: coupling factor came out {k:.4g} > 1 "
                        f"(M larger than sqrt(L1*L2)); SPICE will reject it")
            else:
                skip(el, "mutual inductance needs numeric values for M "
                         "and both inductors to compute SPICE's coupling "
                         "factor k")
        elif el.kind == "o":
            # SPICE has no ideal op-amp; the universal idiom is a huge-
            # gain VCVS. At 1e9 the finite-gain error is parts-per-
            # billion -- but it is an approximation, so say so.
            nplus, nminus, nout = el.fields[0], el.fields[1], el.fields[2]
            lines.append(f"{unique('E' + el.name)} {nout} 0 "
                         f"{nplus} {nminus} 1G")
            warnings.append(
                f"{el.name}: translated as a voltage-controlled source "
                f"with gain 1e9 -- a finite-gain stand-in for the ideal "
                f"op-amp")
        elif el.kind == "t":
            # The exact ideal transformer (grounded ports, as on the
            # calculator): a VCVS forces the secondary voltage at the
            # turns ratio, a 0 V source senses the secondary current,
            # and a CCCS reflects it into the primary. Exact at DC, AC
            # and transient alike -- unlike the coupled-inductor
            # approximation, which shorts out at DC.
            t1 = _numeric(el.fields[2])
            t2 = _numeric(el.fields[3])
            if t1 and t2:
                ratio = _spice_number(t2 / t1)
                mid = inner_node(el.name, "s")
                sense = unique("Vi_" + el.name)
                lines.append(f"* {_typed(el)}  "
                             f"expands to:")
                lines.append(f"{unique('E' + el.name)} {mid} 0 "
                             f"{el.n1} 0 {ratio}")
                lines.append(f"{sense} {mid} {el.n2} 0")
                lines.append(f"{unique('F' + el.name)} {el.n1} 0 "
                             f"{sense} {ratio}")
                warnings.append(
                    f"{el.name}: translated exactly as a controlled-"
                    f"source pair with a current sense (3 elements)")
            else:
                skip(el, "ideal transformer needs numeric turns to "
                         "compute the ratio")
        else:  # two-port blocks z/y/h/g/a/b
            texts = two_port_param_texts(el)
            vals = [_numeric(t) for t in texts] if texts else None
            if vals is None:
                skip(el, "two-port block without numeric parameters; give "
                         "them in the description as a fourth term, "
                         "[p11,p12,p21,p22]")
            elif any(v is None for v in vals):
                bad = [t for t, v in zip(texts, vals) if v is None]
                skip(el, f"two-port parameter '{bad[0]}' is not a plain "
                         f"number; SPICE needs numeric parameters")
            else:
                # Any parameter set reduces to admittance form -- the
                # engine's own reduction -- so the block is (up to) four
                # grounded VCCS elements, two per port.
                try:
                    coeffs = _two_port_admittance(el.kind, *vals)
                except _Skip as why:
                    skip(el, str(why))
                else:
                    lines.append(f"* {_typed(el)}  "
                                 f"expands to:")
                    ports = [(el.n1, el.n1), (el.n1, el.n2),
                             (el.n2, el.n1), (el.n2, el.n2)]
                    count = 0
                    for (out, ctrl), coeff, suf in zip(ports, coeffs,
                                                       "abcd"):
                        if coeff == 0:
                            continue
                        lines.append(f"{unique('G' + el.name + suf)} "
                                     f"{out} 0 {ctrl} 0 "
                                     f"{_spice_number(coeff)}")
                        count += 1
                    warnings.append(
                        f"{el.name}: translated as {count} grounded "
                        f"conductance-form controlled sources")

    lines.append(".end")
    return "\n".join(lines), warnings


# ---------------------------------------------------------------------------
# SPICE -> Symbulator
# ---------------------------------------------------------------------------

_NAME_OK = re.compile(r"[^a-z0-9_]")


def _symb_name(spice_name: str, kind: str) -> str:
    """Map a SPICE element name to a Symbulator one of the given kind:
    replace the type letter, keep the rest, fold case, and replace any
    character the parser would refuse (#159)."""
    rest = _NAME_OK.sub("_", spice_name[1:].lower())
    return kind + rest


def _v_of(node: str) -> str:
    """The node-voltage term for a controlling node; ground contributes
    nothing."""
    return "0" if node == "0" else f"v_{node}"


def _diff(np: str, nn: str) -> str:
    """`(v_a - v_b)` with ground folded away."""
    a, b = _v_of(np), _v_of(nn)
    if b == "0":
        return a
    if a == "0":
        return f"(-{b})"
    return f"({a} - {b})"


def from_spice(text: str) -> Tuple[str, List[str]]:
    """Translate the linear subset of a SPICE netlist into Symbulator
    notation, one element per line.

    Returns `(description, warnings)`. Elements and directives outside
    the subset are dropped and reported, never silently mistranslated."""
    warnings: List[str] = []
    out: List[str] = []

    # Join continuation lines, strip comments, keep order.
    raw_lines: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("*"):
            continue
        if stripped.startswith("+") and raw_lines:
            raw_lines[-1] += " " + stripped[1:].strip()
        else:
            raw_lines.append(stripped)

    if not raw_lines:
        raise CircuitError(M.E_SPICE_EMPTY)

    # Two passes: inductor values first, so a K line can be resolved no
    # matter where it appears.
    l_values = {}
    for line in raw_lines:
        parts = line.split()
        if parts and parts[0][0] in "lL" and len(parts) >= 4:
            val = _parse_spice_number(parts[3])
            if val is not None:
                l_values[parts[0].lower()] = val

    in_subckt = False
    first_line = True
    for line in raw_lines:
        tokens = line.split()
        head = tokens[0]
        kind = head[0].lower()

        # Directives.
        if head.startswith("."):
            word = head.lower()
            if word == ".subckt":
                in_subckt = True
                warnings.append(f"'{line}': subcircuits are not translated; "
                                f"its contents were skipped")
            elif word == ".ends":
                in_subckt = False
            elif word not in (".end",):
                warnings.append(f"'{line}': directive ignored (Symbulator "
                                f"picks the analysis in the app instead)")
            first_line = False
            continue
        if in_subckt:
            continue

        if first_line:
            # Classic netlists open with a free-text title line. Try the
            # first line as an element quietly; if it isn't one, call it
            # the title and say only that.
            trial: List[str] = []
            parsed = _element_from_spice(tokens, l_values, trial)
            if parsed is None:
                warnings.append(f"'{line}': read as the netlist's title line")
            else:
                out.append(parsed)
                warnings.extend(trial)
            first_line = False
            continue
        parsed = _element_from_spice(tokens, l_values, warnings)
        if parsed:
            out.append(parsed)

    if not out:
        raise CircuitError(M.E_SPICE_NOTHING,
                           warnings=" ".join(warnings) if warnings else "")
    return "\n".join(out), warnings


def _element_from_spice(tokens: List[str], l_values: dict,
                        warnings: List[str]) -> Optional[str]:
    """One SPICE element line -> one Symbulator element line, or None
    (with a warning appended) when it cannot be translated."""
    head = tokens[0]
    kind = head[0].lower()
    line = " ".join(tokens)

    if kind in ("r", "l", "c") and len(tokens) >= 4:
        n1, n2 = tokens[1].lower(), tokens[2].lower()
        val = _parse_spice_number(tokens[3])
        if val is None:
            warnings.append(f"'{line}': value '{tokens[3]}' not understood; "
                            f"element dropped")
            return None
        name = _symb_name(head, kind)
        fields = [name, n1, n2, _symb_number(val)]
        for tok in tokens[4:]:
            if tok.upper().startswith("IC="):
                ic = _parse_spice_number(tok[3:])
                if ic is not None:
                    fields.append(_symb_number(ic))
        return ",".join(fields)

    if kind in ("v", "i") and len(tokens) >= 3:
        n1, n2 = tokens[1].lower(), tokens[2].lower()
        rest = tokens[3:]
        upper = [t.upper() for t in rest]
        value = None
        if rest and _parse_spice_number(rest[0]) is not None:
            value = _parse_spice_number(rest[0])
        elif "DC" in upper and upper.index("DC") + 1 < len(rest):
            value = _parse_spice_number(rest[upper.index("DC") + 1])
        waveform = next((t.split("(")[0] for t in upper
                         if t.split("(")[0] in ("SIN", "PULSE", "PWL",
                                                "EXP", "SFFM", "AM")), None)
        if value is None:
            warnings.append(f"'{line}': no plain or DC value found"
                            + (f" (waveform sources like {waveform} are not "
                               f"translated)" if waveform else "")
                            + "; element dropped")
            return None
        if waveform:
            warnings.append(f"'{line}': only the DC value was kept; the "
                            f"{waveform} waveform was not translated")
        name = _symb_name(head, "e" if kind == "v" else "j")
        return f"{name},{n1},{n2},{_symb_number(value)}"

    if kind in ("e", "g") and len(tokens) >= 6:
        # VCVS / VCCS: name n+ n- nc+ nc- gain. The Symbulator name
        # keeps the SPICE type letter (`E1` -> `ee1`), so it can never
        # collide with an independent source (`V1` -> `e1`).
        gain = _parse_spice_number(tokens[5])
        if gain is None:
            warnings.append(f"'{line}': gain '{tokens[5]}' not understood; "
                            f"element dropped")
            return None
        name = ("e" if kind == "e" else "j") + _NAME_OK.sub("_", head.lower())
        ctrl = _diff(tokens[3].lower(), tokens[4].lower())
        return f"{name},{tokens[1].lower()},{tokens[2].lower()},{_exact(gain)}*{ctrl}"

    if kind in ("f", "h") and len(tokens) >= 5:
        # CCCS / CCVS: name n+ n- Vsource gain. Same naming rule:
        # `F1` -> `jf1`, `H1` -> `eh1`.
        gain = _parse_spice_number(tokens[4])
        vname = tokens[3]
        if gain is None or vname[0].lower() != "v":
            warnings.append(f"'{line}': not in the 'name n+ n- Vsource "
                            f"gain' form; element dropped")
            return None
        name = ("j" if kind == "f" else "e") + _NAME_OK.sub("_", head.lower())
        ctrl = "i_" + _symb_name(vname, "e")
        return f"{name},{tokens[1].lower()},{tokens[2].lower()},{_exact(gain)}*{ctrl}"

    if kind == "k" and len(tokens) >= 4:
        kval = _parse_spice_number(tokens[3])
        l1, l2 = tokens[1].lower(), tokens[2].lower()
        if kval is None or l1 not in l_values or l2 not in l_values:
            warnings.append(f"'{line}': coupling needs a numeric k and "
                            f"both inductors present; element dropped")
            return None
        m = kval * math.sqrt(l_values[l1] * l_values[l2])
        name = _symb_name(head, "m")
        return f"{name},{_symb_name(l1, 'l')},{_symb_name(l2, 'l')},{_symb_number(m)}"

    described = {
        "d": "diodes", "q": "bipolar transistors", "m": "MOSFETs",
        "j": "JFETs", "x": "subcircuit calls", "b": "behavioral sources",
        "t": "transmission lines", "s": "switches", "w": "switches",
        "u": "uniform RC lines", "o": "lossy lines",
    }.get(kind)
    if described:
        warnings.append(f"'{line}': {described} have no Symbulator "
                        f"equivalent; element dropped")
    else:
        warnings.append(f"'{line}': not recognised as a SPICE element; "
                        f"line dropped")
    return None
