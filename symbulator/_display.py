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
