"""
Small standalone helpers ported from Symbulator: `pr()` (parallel
combination) and `gain()` (two-port gain figures). `pf()` (power factor)
is a simplified adaptation -- the original read implicit per-element
`v<name>`/`i<name>` calculator variables and a fixed sign convention
per element type; here it just takes an explicit voltage/current phasor
pair, which is the part of the original logic that generalizes cleanly.
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


def pf(voltage: Union[str, sp.Expr], current: Union[str, sp.Expr]):
    """Power factor magnitude and leading/lagging direction for a
    voltage/current phasor pair, using the |cos(angle(V) - angle(I))|
    convention from the original `pf()`."""
    v = sp.sympify(voltage)
    i = sp.sympify(current)
    angle_diff = sp.arg(v) - sp.arg(i)
    value = sp.re(sp.cos(angle_diff))
    side = sp.sign(angle_diff)
    magnitude = round(float(sp.Abs(value)), 5)
    if side < 0:
        direction = "leading"
    elif side > 0:
        direction = "lagging"
    else:
        direction = "in phase"
    return f"pf: {magnitude} {direction}"


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
