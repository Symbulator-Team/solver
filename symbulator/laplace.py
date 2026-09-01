"""
Transient (time-domain) analysis and Laplace-transform helpers: ports
`tr()`, `t2s()`, and `s2t()`.

The original called out to a separate TI-Nspire library (`lf\\ilaplace` /
`lf\\laplace`) that wasn't included in the document this was ported
from, so this port uses SymPy's own `laplace_transform` /
`inverse_laplace_transform` instead of trying to reproduce that missing
library.
"""

from __future__ import annotations

from typing import Iterable, Optional

import sympy as sp

from .analysis import Result, fd

T = sp.Symbol("t", nonnegative=True)
S = sp.Symbol("s")
#: Public names for the same two symbols. `t` is non-negative, not
#: strictly positive, and the difference is load-bearing: SymPy evaluates
#: DiracDelta of a strictly positive argument to 0, so under `positive`
#: every impulse silently disappeared -- a delta(t) source, the scalar an
#: expert-mode unknown solves to in TR, and s2t(1), which answered 0
#: instead of DiracDelta(t). t >= 0 is also what the one-sided transform
#: is actually defined on, and the origin is exactly where an impulse
#: lives.
#:
#: `positive` was chosen for tidy answers -- it lets the transforms drop
#: Heaviside(t). `_invert` below folds that away afterwards instead, so
#: the answers read the same as they always have.
#:
#: A bare sp.Symbol("t") is a *different* symbol and subs() on it
#: silently does nothing. Import these instead of re-creating them.
t = T
s = S


#: The time symbol as an expression carries it before any transform. The
#: forward transform integrates from 0 to infinity and needs no assumption
#: about t; declaring one changes what the expression *means* rather than
#: only which symbol it uses, and SymPy evaluates DiracDelta of a strictly
#: positive argument to 0 -- so an impulse source parsed against a positive
#: t is gone before the transform ever sees it.
TIME_IN = sp.Symbol("t")


def _read(expr) -> sp.Expr:
    """A transform argument, read the way every other input is read.

    t2s and s2t are public and their docstrings advertise strings --
    t2s("5") for a 5 V step. They used bare sp.sympify, which knows none
    of Symbulator's notation, so anything past a plain number was wrong:
    "5*u(t)" made an undefined function of u, "2'k" would not parse at
    all, and "1-e^(-t/2)" read the caret as XOR and e as a symbol, giving
    1 - 1/e**(t/2). Before the domain checks those failures were silent,
    coming back as unevaluated transforms.
    """
    if isinstance(expr, str):
        from .si_prefix import expand_value, safe_sympify

        return safe_sympify(expand_value(expr), reserve_imaginary=False,
                            original=str(expr))
    return sp.sympify(expr)


def _domain_of(expr: sp.Expr) -> set:
    """Which of the two domain variables this expression mentions."""
    return {x.name for x in getattr(expr, "free_symbols", set())} & {"t", "s"}


def _check_transform(expr, result, into: str, fn: str = None):
    """Both ends of a transform, or a CircuitError explaining which failed.

    `into` is the domain being transformed *into* -- "s" for t2s, "t" for
    s2t. `fn` names the function the reader called, so the message points
    at what they typed; None means the `{...}` bracket shorthand.

    It used to be `origin`, a ready-made English phrase ("between
    brackets", "as an argument to t2s()"). A phrase cannot be translated
    from inside an argument, so #199 made the two forms two codes apiece
    and reduced what crosses the boundary to a function's name, which is
    the same in every language.
    """
    from .elements import CircuitError
    from . import messages as M

    frm = "t" if into == "s" else "s"

    # -- the input end. Containing neither variable is fine: a constant is
    #    a valid expression in either domain, and {5} means a step of 5.
    if into in _domain_of(expr):
        raise CircuitError(
            M.E_ALREADY_IN_DOMAIN_CALL if fn else M.E_ALREADY_IN_DOMAIN_BRACKETS,
            into=into, frm=frm, **({"fn": fn} if fn else {}))

    # -- the output end. An unevaluated transform is SymPy saying it could
    #    not find a closed form; it must not travel any further.
    unevaluated = (sp.LaplaceTransform if into == "s"
                   else sp.InverseLaplaceTransform)
    if result.has(unevaluated) or frm in _domain_of(result):
        raise CircuitError(
            M.E_NOT_VALID_DOMAIN_CALL if fn else M.E_NOT_VALID_DOMAIN_BRACKETS,
            into=into, **({"fn": fn} if fn else {}))
    return result


def t2s(expr_t, t: sp.Symbol = None, s: sp.Symbol = S,
        validate: bool = True) -> sp.Expr:
    """Laplace transform of a time-domain expression -- ports `t2s()`.
    Use this to prepare a source value for `fd()`/`tr()`, e.g.
    `t2s("5")` for a 5 V step, or hand-write it directly ("5/s").

    With `t` left unset the time symbol is taken from the expression, so
    it works whichever `t` the caller's expression happens to carry. Pass
    one explicitly to override."""
    expr = _read(expr_t)
    if t is None:
        named = sorted((x for x in expr.free_symbols if x.name == "t"),
                       key=str)
        t = named[0] if named else TIME_IN
    result = sp.simplify(sp.laplace_transform(expr, t, s, noconds=True))
    if not validate:
        # The caller checks it themselves, so that a failure can be
        # reported against what the reader actually typed. The bracket
        # form does this: blaming t2s() for brackets names the wrong
        # thing.
        return result
    return _check_transform(expr, result, "s", "t2s")


def _invert(expr: sp.Expr, s: sp.Symbol = S, t: sp.Symbol = T) -> sp.Expr:
    """Inverse Laplace transform, with `Heaviside(t)` folded away.

    Every answer here exists only for t >= 0, so a Heaviside(t) factor
    multiplying the whole thing says nothing and makes it harder to read.
    Folding it is what lets the time symbol be non-negative -- which is
    what lets an impulse survive -- without every answer growing a factor
    it never used to carry.

    Only `Heaviside(t)` exactly. `Heaviside(t - 1)` is a step delayed to
    t = 1 and is genuinely zero before then; folding that would turn a
    delayed source into an immediate one.
    """
    got = sp.inverse_laplace_transform(expr, s, t)
    got = got.replace(
        lambda e: isinstance(e, sp.Heaviside) and e.args[0] == t,
        lambda e: sp.Integer(1))
    return sp.simplify(got)


def s2t(expr_s, s: sp.Symbol = S, t: sp.Symbol = T) -> sp.Expr:
    """Inverse Laplace transform of an s-domain expression -- ports
    `s2t()`."""
    expr = _read(expr_s)
    return _check_transform(expr, _invert(expr, s, t), "t", "s2t")


# --------------------------------------------------------------------------
# Time-domain source values, on the way into an s-domain solve
# --------------------------------------------------------------------------
#
# `tr()` reads its sources in the time domain; `fd()` reads them in s. That
# is the original's design, and this port lost half of it: the answers were
# transformed back but the sources were never transformed in, so every
# transient result was one integration short -- a plain `12` gave the
# impulse response where the step response was wanted.
#
# This is symbv8s5's `betatool="tr"` branch, restored. Read out of the
# version 8 document itself rather than inferred:
#
#     If betatool="tr" and inString("ej",kind) Then
#       If value <> value|t=0 Then      t2s(value)      a waveform
#       Else If type="NUM" Then         value/s         a step
#            Else if it depends on another answer:  leave it alone
#                 otherwise:                        value/s
#
# The last branch is the one worth reading twice. A controlled source's
# value is a *relation* -- `2*i_r1` says "twice whatever that current is"
# -- not a waveform, so transforming it would be meaningless. The original
# decides by substituting each node voltage and element current in turn and
# seeing whether the value changes; this does the same by looking at which
# symbols the value actually contains.

_ANSWER_PREFIXES = ("v", "i", "p", "ap", "s", "z", "r")


def _is_controlled(expr: sp.Expr, elements) -> bool:
    """Does this value refer to another element's answer?"""
    names = {str(sym) for sym in expr.free_symbols}
    if not names:
        return False
    known = set()
    for el in elements:
        for prefix in _ANSWER_PREFIXES:
            known.add(f"{prefix}_{el.name}")
            known.add(f"{prefix}{el.name}")
        for idx in (0, 1):
            if idx < len(el.fields):
                known.add(f"v_{el.fields[idx]}")
                known.add(f"v{el.fields[idx]}")
    return bool(names & known)


def _source_to_s(value: str, elements, s: sp.Symbol = S) -> str:
    """One `e`/`j` value, moved from the time domain into the s-domain."""
    from .si_prefix import expand_value, safe_sympify

    try:
        # reserve_imaginary=False: a transient circuit has no complex
        # values, so `i` in a source value is an ordinary symbol --
        # `i*delta(t)` means i amperes of impulse, not the unit. And
        # through the ordinary shorthand, or `u(t)` is still an undefined
        # function and the transform quietly declines it.
        expr = safe_sympify(expand_value(value), reserve_imaginary=False,
                            original=str(value))
    except Exception:                                         # noqa: BLE001
        return value          # not an expression we can read; leave it

    time = [x for x in expr.free_symbols if x.name == "t"]

    # A waveform: anything written in terms of t. The transform picks the
    # expression's own time symbol -- forcing one it does not contain
    # makes laplace_transform treat the whole thing as a constant and
    # divide by s, which is how `t` came back as t/s instead of 1/s**2.
    if time:
        try:
            return str(t2s(expr, s=s))
        except Exception:                                     # noqa: BLE001
            return value

    # Already written in s: the reader has done the conversion, or is
    # working from an s-domain source as `fd()` would take. Leave it.
    if expr.has(s):
        return value

    if _is_controlled(expr, elements):
        return value

    # A constant, numeric or symbolic: a step of that amplitude.
    return str(expr / s)


def _sources_to_s(desc: str, s: sp.Symbol = S) -> str:
    """`desc` with every independent source value moved into the s-domain."""
    from .elements import parse_circuit

    try:
        elements = parse_circuit(desc, expand_si=False)
    except Exception:                                         # noqa: BLE001
        return desc           # let the real parse report the problem

    out, changed = [], False
    for el in elements:
        fields = list(el.fields)
        if el.kind in ("e", "j") and len(fields) >= 3:
            moved = _source_to_s(fields[2], elements, s)
            if moved != fields[2]:
                fields[2] = moved
                changed = True
        out.append(el.name + "," + ",".join(fields))
    return ":".join(out) if changed else desc


def _side_to_s(expr: sp.Expr, elements, s: sp.Symbol = S) -> sp.Expr:
    """One side of a time-domain relation, moved into the s-domain.

    The same four cases as `_source_to_s`, for the same reasons."""
    if any(x.name == "t" for x in expr.free_symbols):
        if _is_controlled(expr, elements):
            # Both a waveform and an answer name -- there is no single
            # right reading, so leave it and let the solver complain
            # rather than transform half of it.
            return expr
        try:
            return t2s(expr, s=s)
        except Exception:                                     # noqa: BLE001
            return expr
    if expr.has(s):
        return expr
    if _is_controlled(expr, elements):
        return expr
    return expr / s


def _relation_to_s(text: str, elements, s: sp.Symbol = S) -> str:
    """An expert equation or condition, read as a statement about time.

    Returns it in the s-domain, which is where tr() actually solves.
    A relation naming no answer at all -- `x = 3`, fixing a symbol in
    the circuit -- is about a parameter rather than a waveform and is
    returned unchanged; dividing that by s would be nonsense.
    """
    from .si_prefix import expand_value, safe_sympify

    if "=" not in str(text):
        return text
    lhs_text, rhs_text = str(text).split("=", 1)
    try:
        lhs = safe_sympify(expand_value(lhs_text), reserve_imaginary=False,
                           original=lhs_text)
        rhs = safe_sympify(expand_value(rhs_text), reserve_imaginary=False,
                           original=rhs_text)
    except Exception:                                         # noqa: BLE001
        return text

    if not (_is_controlled(lhs, elements) or _is_controlled(rhs, elements)):
        return text

    return f"{_side_to_s(lhs, elements, s)} = {_side_to_s(rhs, elements, s)}"


def _relations_to_s(items, desc: str, s: sp.Symbol = S):
    """Every equation or condition in `items`, moved into the s-domain."""
    if not items:
        return items
    from .elements import parse_circuit

    try:
        elements = parse_circuit(desc)
    except Exception:                                         # noqa: BLE001
        return items
    return [_relation_to_s(item, elements, s) for item in items]


def tr(desc: str, params: Optional[dict] = None,
       variables: Optional[Iterable[str]] = None,
       t: sp.Symbol = T, equations=None, unknowns=None,
       conditions=None, suffix: str = "ask") -> Result:
    """Transient (time-domain) analysis -- ports `tr()`. Runs `fd()`
    (s-domain analysis) and then inverse-Laplace-transforms the result
    back to the time domain.

    By default every solved node voltage and element current is
    transformed; pass `variables` (an iterable of keys like "v_2" or
    "i_r1") to transform only specific ones -- useful since inverse
    Laplace transforms of complicated expressions can be slow or fail
    to find a closed form. A variable that can't be transformed is left
    out of the result rather than raising, since the rest of the
    circuit's answers are usually still valid and useful."""
    # Everything the reader typed is in the time domain -- the domain
    # the answers are shown in -- so the added equations and conditions
    # are converted the same way the sources are. Without this they were
    # read in s while the answers around them were in t.
    s_domain = fd(_sources_to_s(desc), params=params,
                  equations=_relations_to_s(equations, desc),
                  unknowns=unknowns,
                  conditions=_relations_to_s(conditions, desc),
                  suffix=suffix)
    keys = list(variables) if variables is not None else list(s_domain.values.keys())

    # Which keys hold solved expert-mode unknowns: those values are
    # scalars (a source's amplitude, a component's size), not waveforms,
    # and must never be transformed. Everything else in `values` is a
    # circuit answer -- a node voltage or element current -- and IS a
    # waveform, whatever its expression looks like.
    scalar_keys = {str(u) for u in (unknowns or [])}
    try:
        from .elements import parse_circuit
        _elements = parse_circuit(desc)
    except Exception:                                         # noqa: BLE001
        _elements = []

    time_domain = {}
    for key in keys:
        expr = s_domain.values.get(key)
        if expr is None:
            continue
        # A value with no `s` in it is one of three things, and only one
        # of them is finished:
        #
        # - A solved expert-mode unknown: a scalar. `k = 5` means the
        #   amplitude is 5, and multiplying it by delta(t) would turn a
        #   number into an impulse. Recognised by its key.
        # - A reference to another answer (`i_j = 2*ir3`, a dependent
        #   source echoing its controlling current): the symbols name
        #   *functions*, so the relation reads identically in s and in
        #   t, and passing it through unchanged is the transform.
        #   Recognised by _is_controlled.
        # - A genuine circuit answer that is constant in s. That IS an
        #   impulse: a step arrives as k/s and a waveform brings its own
        #   s, so a bare constant has nowhere else to come from --
        #   inverse_laplace_transform(k, s, t) = k*DiracDelta(t). Until
        #   0.5.15 this case was passed through with the other two, so
        #   `e,1,0,10*delta(t)` into a resistor reported v_1 = 10: a
        #   10 V*s impulse printed identically to a 10 V step. (Zero
        #   stays zero either way -- 0*delta(t) is 0.)
        if not getattr(expr, "has", None) or not expr.has(S):
            if key in scalar_keys or _is_controlled(expr, _elements):
                time_domain[key] = expr
            else:
                time_domain[key] = expr * sp.DiracDelta(t)
            continue
        try:
            time_domain[key] = _invert(expr, S, t)
        except Exception:
            continue  # leave it out rather than fail the whole analysis

    return Result(domain="tr", values=time_domain)
