"""
Small standalone helpers ported from Symbulator: `pr()` (parallel
combination), `pf()` (power factor, version 8's two forms: a complex
value, or an element's name with the solve it came from -- #430) and
`gain()` (two-port gain figures).
"""

from __future__ import annotations

from typing import Optional, Sequence, Union

import sympy as sp


def pr(*impedances: Union[str, sp.Expr]) -> sp.Expr:
    """Parallel combination of any number of impedances (or admittances'
    reciprocals) -- ports `pr(alpha_z)`. If any argument is exactly 0,
    the combination is 0 (a short circuit dominates a parallel network),
    matching the original's explicit zero-check."""
    if len(impedances) == 1 and isinstance(impedances[0], (list, tuple)):
        impedances = tuple(impedances[0])
    values = [sp.sympify(z) for z in impedances]
    if not values:
        raise ValueError("pr() requires at least one impedance.")
    if len(values) == 1:
        return values[0]
    if any(v == 0 for v in values):
        return sp.Integer(0)
    total = sp.Integer(0)
    for v in values:
        total += 1 / v
    return sp.simplify(1 / total)


#: The element kinds `pf("<name>", res)` knows a sign convention for.
#: Version 8's `pf` recognised e, j and r; version 9 writes a coil or a
#: capacitor as `l` or `c` where the calculator wrote an impedance under
#: `r`, so those two take the load convention with `r`.
PF_SOURCE_KINDS = "ej"
PF_LOAD_KINDS = "rlc"


def pf(value, result=None):
    """Power factor -- version 8's `pf`, ported as it was (#430).

    Two forms, told apart by what is given:

    * **A complex value** -- a complex power such as `res["s_e"]`, an
      impedance, a number in rectangular or polar form, or any expression,
      symbolic or numerical::

          pf(res["s_e"])          # 0.973417...
          pf(3 + 4*sp.I)          # 0.6
          pf(sp.Symbol("x") + 2*sp.I)   # Abs(x)/sqrt(x**2 + 4)

      The answer is |Re| / |S|, the calculator's
      `abs(real(x))/abs(x)`: a float when it evaluates, an expression
      otherwise. **It says nothing about leading or lagging**, because a
      number cannot: the same complex power is consumed by one side of a
      branch and delivered by the other.

    * **The name of an element**, with the `Result` of the AC solve it
      belongs to::

          pf("e", res)            # 'pf: 0.97342 leading'

      The answer is a sentence -- the value to five decimals and the
      word -- and this form works only when the element's voltage and
      current evaluate to numbers. The word is decided the way the
      calculator decided it, and the convention differs by kind:

      - for a load (`r`, and in version 9 `l` and `c`) the angle is
        between the element's voltage and the current it *consumes*, so
        the reading is the element's own: an inductive load reads
        lagging;
      - for a source (`e`, `j`) the current is negated first -- the
        angle is between the source's voltage and the current it
        *delivers* -- so the reading is that of the circuit the source
        sees. A source feeding an inductive load reads lagging, the same
        word as the load.

      The package's own `s_<name>` is always the power *consumed*, sources
      included, so a source's reading is the power factor of `-s_e`. The
      magnitude is the same either way; only the word depends on it, and
      a source read on its consumed power would say the opposite word.

    Raises ValueError for a name that is not among the answers, an element
    kind with no convention, or a voltage and current that still hold
    symbols.
    """
    if isinstance(value, str) and result is not None:
        return _pf_of_element(value, result)
    z = sp.sympify(value)
    if z == 0:
        raise ValueError("pf() of zero is undefined: no power, no angle.")
    if not z.free_symbols:
        return sp.N(sp.Abs(sp.re(z)) / sp.Abs(z))
    # The calculator takes a symbol as real; SymPy does not, and
    # Abs(re(x))/Abs(x) with x complex-unknown is a page of conjugates.
    # Compute with real twins and put the caller's own symbols back, so
    # the answer is `Abs(x)/sqrt(x**2 + 4)` in the symbols passed in and
    # `.subs(x, 3)` on it still works.
    twins = {x: sp.Symbol(x.name, real=True) for x in z.free_symbols
             if x.is_real is not True}
    zr = z.xreplace(twins)
    ratio = sp.simplify(sp.Abs(sp.re(zr)) / sp.Abs(zr))
    return ratio.xreplace({twin: x for x, twin in twins.items()})


def _pf_of_element(name: str, result) -> str:
    """The element form of `pf()`: see there for the sign convention."""
    name = name.strip().strip("\"'")
    # A Result, or a plain {name: expression} dict -- and a dict's own
    # `.values` is a method, so the test is on the type, not the attribute.
    values = result if isinstance(result, dict) else result.values
    try:
        v = values[f"v_{name}"]
        i = values[f"i_{name}"]
    except KeyError:
        raise ValueError(f"pf: {name!r} is not an element of this solve "
                         f"(it needs v_{name} and i_{name} among the "
                         f"answers).") from None
    kind = name[:1].lower()
    if kind in PF_SOURCE_KINDS:
        s = v * sp.conjugate(-i)          # the power the source delivers
    elif kind in PF_LOAD_KINDS:
        s = v * sp.conjugate(i)           # the power the load consumes
    else:
        raise ValueError(f"pf: no sign convention for an element of kind "
                         f"{kind!r}; it knows sources (e, j) and loads "
                         f"(r, l, c).")
    s = sp.N(sp.simplify(s))
    if s.free_symbols:
        raise ValueError(f"pf: v_{name} and i_{name} did not evaluate "
                         f"numerically; give pf() the complex power "
                         f"s_{name} instead for the value alone.")
    s = complex(s)
    if s == 0:
        raise ValueError(f"pf: {name} carries no power.")
    magnitude, direction = pf_reading(s)
    text = f"pf: {magnitude}"
    return f"{text} {direction}" if direction else text


def pf_reading(s: complex):
    """The value and the word for a numerical complex power `s`, angle
    taken as the calculator took it: |cos| to five decimals; `lagging`
    when the angle is positive, `leading` when negative, and no word at
    all when the power is purely real, as version 8 printed it. A
    reactive part that is float noise beside the real one -- below one
    part in 10^9 -- counts as zero rather than as a word."""
    s = complex(s)
    magnitude = round(abs(s.real) / abs(s), 5)
    im = s.imag
    if abs(im) <= 1e-9 * abs(s):
        im = 0.0
    direction = "lagging" if im > 0 else "leading" if im < 0 else ""
    return magnitude, direction


def gain(v1: Union[str, sp.Expr], i1: Union[str, sp.Expr],
         v2: Union[str, sp.Expr], i2: Union[str, sp.Expr]) -> dict:
    """Voltage/current/power gain and input impedance from in/out
    voltage-current pairs -- ports `gain()`."""
    v1, i1, v2, i2 = (sp.sympify(x) for x in (v1, i1, v2, i2))
    av = sp.simplify(v2 / v1)
    ai = sp.simplify(i2 / i1)
    ap = sp.simplify(sp.re(-av * sp.conjugate(ai)))
    zi = sp.simplify(v1 / i1)
    return {"Av": av, "Ai": ai, "Ap": ap, "Zi": zi}


class Phasor:
    """A complex number as a magnitude and an angle in degrees: what
    `polar()` returns. Prints as `5∠53.13°` at a terminal and typeset in
    a notebook; `.magnitude` and `.angle` are the two numbers, and
    `complex(p)` gives the value back."""

    def __init__(self, magnitude, angle):
        self.magnitude = magnitude
        self.angle = angle

    def __iter__(self):
        yield self.magnitude
        yield self.angle

    def __complex__(self):
        return complex(sp.N(self.magnitude * sp.exp(sp.I * sp.rad(self.angle))))

    def __repr__(self) -> str:
        return f"{self.magnitude}\u2220{self.angle}\u00b0"

    def _repr_latex_(self) -> str:
        return (rf"$\displaystyle {sp.latex(self.magnitude)} \angle "
                rf"{sp.latex(self.angle)}^\circ$")


def polar(value: Union[str, sp.Expr, complex], digits: Optional[int] = 4) -> Phasor:
    """A phasor as magnitude and angle in degrees -- the app's `aa`
    mini-tool and version 7's `aa` (#315): `polar(3+4j)` is `5∠53.13°`.

    `digits` rounds both numbers to that many significant figures, the
    app's Rounding setting; pass None for full precision. A real value
    still gets an angle, 0 or 180, and zero is 0∠0°. Raises ValueError
    for a value that still holds free symbols -- the angle of
    `r_b*vin/(r_a + r_b)` is not a number."""
    z = value if isinstance(value, sp.Basic) else sp.sympify(value)
    z = sp.simplify(z)
    if z.free_symbols:
        raise ValueError(f"polar() needs a number; {z} still has "
                         f"{', '.join(sorted(map(str, z.free_symbols)))} in it")

    from ._display import round_sig

    def num(x):
        # Evaluating from float inputs can leave a crumb of imaginary
        # part behind ("19.36 + 0.e-13*I"): take the real part after.
        # Full precision first, then decimal rounding -- `sp.N(x, 4)`
        # alone put this circuit's -36.20493° at -36.21 (#318).
        x = sp.re(sp.N(x))
        return round_sig(x, digits) if digits else x

    z = sp.N(z)
    magnitude = num(sp.Abs(z))
    angle = sp.Integer(0) if z == 0 else num(sp.deg(sp.arg(z)))
    return Phasor(magnitude, angle)
