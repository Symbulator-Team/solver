"""Typesetting for the result objects, shared by `Result`,
`TheveninResult` and `PortResult` (#315).

A notebook asks an object for `_repr_latex_` and hands what it gets to
MathJax, so every result object answers with one aligned block, one
answer per row, its name on the left and its value on the right -- the
form the app's Results card uses. Outside a notebook nothing here runs:
the plain `__repr__` of each object is unchanged.

The imaginary unit is written `j`, the way the app and the tutorial
write it. SymPy's own printer says `i`.
"""

from __future__ import annotations

from typing import Iterable, Tuple

import sympy as sp


def name_latex(key: str) -> str:
    """`i_r1` -> `i_{r1}`, `pout` -> `pout`: SymPy's own rule for a
    symbol name, so a result reads as the symbol it is."""
    return sp.latex(sp.Symbol(key))


def value_latex(value) -> str:
    """A value typeset for a notebook: `j` for the imaginary unit and a
    plain ∞ for every infinity, complex infinity included -- the app's
    Results card prints one sign for all of them (#305)."""
    if isinstance(value, str):
        return rf"\text{{{value}}}"
    if not isinstance(value, sp.Basic):
        try:
            value = sp.sympify(value)
        except (sp.SympifyError, TypeError, ValueError):
            return rf"\text{{{value}}}"
    if value in (sp.oo, sp.zoo):
        return r"\infty"
    if value == -sp.oo:
        return r"-\infty"
    return sp.latex(value, imaginary_unit="j")


def aligned(rows: Iterable[Tuple[str, str]], caption: str = "") -> str:
    """One `aligned` block from (name, value) pairs already in LaTeX,
    with `caption` set in text above it when given (the pair sits in a
    `gathered`, so the caption is centred over the rows)."""
    body = r" \\ ".join(rf"{name} &= {value}" for name, value in rows)
    block = rf"\begin{{aligned}}{body}\end{{aligned}}"
    if caption:
        block = (rf"\begin{{gathered}}\text{{{caption}}} \\ "
                 rf"{block}\end{{gathered}}")
    return rf"$\displaystyle {block}$"


def round_sig(expr, digits: int):
    """`expr` with every number shown to `digits` significant figures,
    rounded in *decimal* (#318).

    `sp.N(expr, digits)` is not that: it evaluates at a binary working
    precision of about `digits` digits, so the last decimal digit can
    land either side -- the wye-delta line current's angle, -36.20493°,
    came out -36.21 at four figures, where the book and the arithmetic
    say -36.20. So: evaluate at full precision first, then round each
    Float in decimal, ties away from zero (31.25 is 31.3, as a book
    prints it -- Python's own formatting would say 31.2, ties to even,
    and that changed ten of the tutorial's printed answers when it was
    tried), and rebuild it as a Float carrying exactly `digits` digits
    so it prints as rounded. Exact integers are left alone; a rational
    becomes a decimal, as the app's Rounding setting makes it.
    """
    if not isinstance(expr, sp.Basic) or not digits:
        return expr
    if expr.is_Integer:
        return expr
    try:
        evaluated = sp.N(expr)
    except Exception:                                      # noqa: BLE001
        return expr
    if isinstance(evaluated, sp.Basic):
        rounded = {}
        for f in evaluated.atoms(sp.Float):
            rounded[f] = _round_float(f, digits)
        return evaluated.xreplace(rounded) if rounded else evaluated
    return evaluated


def _round_float(f, digits: int):
    """One Float to `digits` significant figures, decimal, half away
    from zero, as a Float that prints with exactly those digits."""
    from decimal import Decimal, ROUND_HALF_UP
    if float(f) == 0.0:
        return sp.Float(0, digits)
    # Fifteen decimal digits, a double's own precision, not the shortest
    # round-trip: a computed -0.00585 is the double just below the tie,
    # and reading it as -0.005849999999999999 turned a 0.0059 into a
    # 0.0058. Anything past the fifteenth digit is noise, not a value.
    d = Decimal(str(sp.Float(f, 15)))
    exponent = d.adjusted()                     # position of the leading digit
    quantum = Decimal(1).scaleb(exponent - digits + 1)
    q = d.quantize(quantum, rounding=ROUND_HALF_UP)
    return sp.Float(f"{q:.{digits - 1}e}", digits)
