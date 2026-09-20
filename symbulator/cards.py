"""`evaluate()` and `solve()`: the app's Evaluate and Solve cards (#466).

On the calculator, once a circuit was solved, its answers sat in the
machine's own variables and the next line could use them: `vo/vs`,
`solve(im(ze)=0,w)`, `vc|t=2`. The app gives that back as two cards; this
module gives it to a notebook:

    res = dc(circuit)
    evaluate(res, "vo/vs")
    evaluate(res, "vc", conditions=["t = 2"])
    solve(res, ["im(ze) = 0"], unknowns=["w"], conditions=["w > 0"],
          real_only=True)

Both take the result of any analysis -- `dc`/`ac`/`fd`/`tr`, `th()`, whose
answers carry the load formulas `irl`, `vrl` and `prl` in the variable
`load` as the app's do, or `port()` -- or a plain mapping of names to
values, which is how `er()`'s single expression is handed over:
`solve({"zeq": z}, ["im(zeq) = 0"], ["c"])`.

**This is a port, and deliberately a faithful one.** The logic is
`symbulator_ui.py`'s `evaluate_ui` and `solveq_ui` with the formatting
taken off, and the reasons each step is there are written up beside the
originals in the app; the comments here say what, and point there for why.
`notebooks/check_books.py` runs every built-in entry that uses either card
through both and compares the answers, so the two copies cannot drift
without a red run.

Answer names match however they are spelled -- `i_r1`, `iR1` and `IR1` are
one answer -- and everything a circuit value may use works here too:
`2'k`, `^`, implied multiplication, `u(t)`, `{...}` in FD.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Mapping, Optional

import sympy as sp

from .elements import CircuitError

__all__ = ["evaluate", "solve"]


# ---------------------------------------------------------------------------
# The answers, by name
# ---------------------------------------------------------------------------

def _load_answers(ino, z, domain: str, use_rms: bool) -> dict:
    """The th tool's load formulas, version 8's own (app: `_load_answers`).
    None when the Norton current is unbounded."""
    if ino in (sp.oo, -sp.oo, sp.zoo):
        return {}
    load = sp.Symbol("load")
    irl = ino * z / (load + z)
    vrl = load * ino * z / (load + z)
    if domain == "dc":
        prl = load * ino**2 * z**2 / (load + z)**2
    else:
        s_load = (ino * sp.conjugate(ino) * load * z * sp.conjugate(z)
                  / ((load + z) * (sp.conjugate(load) + sp.conjugate(z))))
        prl = sp.re(s_load) if use_rms else sp.re(s_load) / 2
    return {"irl": sp.simplify(irl), "vrl": sp.simplify(vrl),
            "prl": sp.simplify(prl)}


def _with_drops(result) -> dict:
    """The answers, plus each element's voltage drop where the solve did
    not stamp one.

    In dc and ac `v_<element>` is among the answers; in fd and tr it is
    not, the third level being computed for dc and ac alone. The app
    derives it for display from the two nodes the element spans -- so
    `vc` names an answer on the page while the package had no such key,
    and an equation about it went to the solver as a bare symbol. Ground
    is the literal zero: the solver carries no `v_0`."""
    values = dict(result.values)
    if not result.desc:
        return values
    try:
        from .elements import parse_circuit
        elements = parse_circuit(result.desc)
    except Exception:                                   # noqa: BLE001
        return values
    for el in elements:
        key = f"v_{el.name}"
        if el.kind not in "rlcejs" or key in values:
            continue
        ends = []
        for node in (getattr(el, "n1", None), getattr(el, "n2", None)):
            if node == "0":
                ends.append(sp.Integer(0))
            else:
                ends.append(values.get(f"v_{node}"))
        if all(e is not None for e in ends):
            try:
                values[key] = sp.simplify(ends[0] - ends[1])
            except Exception:                           # noqa: BLE001
                values[key] = ends[0] - ends[1]
    return values


def _answers(result, use_rms: bool = False):
    """(name -> value, domain) for whatever an analysis returned."""
    from .analysis import Result
    from .equiv import PortResult, TheveninResult

    if isinstance(result, Result):
        return _with_drops(result), result.domain
    if isinstance(result, TheveninResult):
        z = "req" if result.domain == "dc" else "zeq"
        values = {"vth": result.vth, "ino": result.ino, z: result.z,
                  "pmax": result.pmax}
        values.update(_load_answers(result.ino, result.z, result.domain,
                                    use_rms))
        return values, result.domain
    if isinstance(result, PortResult):
        return {f"{result.kind}{k}": v for k, v in result.items()}, ""
    if isinstance(result, Mapping):
        return {str(k): sp.sympify(v) for k, v in result.items()}, ""
    raise TypeError(
        "evaluate() and solve() take the result of an analysis, or a "
        "mapping of names to values -- for er(), which returns one "
        "expression, pass {'req': value} or {'zeq': value}.")


def _norm(name: str) -> str:
    return name.replace("_", "").lower()


def _alias_mapping(values: dict, exclude=(), expr=None) -> dict:
    """The symbols in `expr` mapped onto the answers, case and underscores
    ignored; a spelling two answers share is left alone, and so is a name
    in `exclude` -- an unknown being solved for must stay unknown."""
    skip = {_norm(str(e)) for e in exclude}
    by_norm, clashes = {}, set()
    for k, v in values.items():
        key = _norm(k)
        if key in by_norm:
            clashes.add(key)
        by_norm[key] = v
    mapping = {}
    for sym in (expr.free_symbols if expr is not None else ()):
        key = _norm(str(sym))
        if key in skip or key in clashes or key not in by_norm:
            continue
        mapping[sym] = by_norm[key]
    return mapping


def _text_aliases(values: dict) -> dict:
    """{sans-underscore spelling: stored name}, for rewriting the *text* of
    an equation before it is parsed: `re` and `im` are SymPy functions, so
    `re = 12000` has to become `r_e = 12000` first."""
    out, ambiguous = {}, set()
    for key in values:
        short = key.replace("_", "")
        if short == key:
            continue
        if short in out and out[short] != key:
            ambiguous.add(short)
        out[short] = key
    for short in ambiguous:
        out.pop(short, None)
    return out


def _apply_text_aliases(text: str, alias: dict) -> str:
    if not alias or not text:
        return text
    body = "|".join(re.escape(k) for k in sorted(alias, key=len, reverse=True))
    # not inside a name, not a call (`pr(6,3)` is the parallel function),
    # and an implied multiplication in front (`3ir1`) made explicit
    pat = re.compile(rf"(?<![\w.])(\d*\.?\d*)({body})(?![\w])(?!\s*\()")

    def sub(m):
        number, name = m.group(1), m.group(2)
        return f"{number}*{alias[name]}" if number else alias[name]

    return pat.sub(sub, text)


# ---------------------------------------------------------------------------
# Reading what was typed
# ---------------------------------------------------------------------------

_REARRANGERS = ("expand", "factor", "simplify", "cancel", "together",
                "apart", "collect", "powsimp", "radsimp", "trigsimp",
                "logcombine", "diff")


def _expand(text: str) -> str:
    try:
        from .si_prefix import expand_value
        return expand_value(text)
    except Exception:                                   # noqa: BLE001
        return text


def _read(text: str):
    from .si_prefix import safe_sympify
    return safe_sympify(_expand(text))


def _read_with_rearrangers(text: str):
    """safe_sympify with `expand(...)` and its kin left unapplied, so they
    act on the answer and not on the bare name standing for it."""
    from .si_prefix import (_allowed_namespace, _IDENT_RE, _shield_keywords,
                            _unshield_keywords, check_expression_syntax)
    shown = text
    text = _shield_keywords(text)
    check_expression_syntax(text, shown)
    ns = _allowed_namespace(True)
    used = set(_IDENT_RE.findall(text))
    for name in _REARRANGERS:
        if name in used:
            ns[name] = sp.Function(name)
    for name in used:
        ns.setdefault(name, sp.Symbol(name))
    return _unshield_keywords(sp.sympify(text, locals=ns))


def _apply_rearrangers(expr):
    """(expression, whether one of them changed its arrangement)."""
    if not getattr(expr, "replace", None):
        return expr, False
    changed = False

    def bind(name, fn):
        def run(*args):
            nonlocal changed
            try:
                out = fn(*args)
            except Exception as exc:
                raise ValueError(f"{name}() could not be applied to this "
                                 f"answer: {exc}") from exc
            if args and out != args[0]:
                changed = True
            return out
        return run

    for name in _REARRANGERS:
        fn = getattr(sp, name, None)
        if fn is not None:
            expr = expr.replace(sp.Function(name), bind(name, fn))
    return expr, changed


def _unbrace(text: str, domain: str) -> str:
    """`{...}` converts from time into s, so it means something in FD
    alone; anywhere else it is refused in words."""
    if "{" not in (text or ""):
        return text
    if domain != "fd":
        where = {"tr": "TR", "dc": "DC", "ac": "AC"}.get(domain,
                                                         "this analysis")
        raise CircuitError(
            "Brackets convert an expression from the time domain into s, "
            f"which only applies in FD. In {where} every input is already "
            "read in the domain the answers are in, so there is nothing "
            "to convert -- write the expression without the brackets.")
    from .si_prefix import expand_time_domain_braces
    return expand_time_domain_braces(text)


def _equality(lhs, rhs):
    """`lhs = rhs`; one against infinity kept unevaluated, since SymPy
    decides `Eq(t, oo)` is False and `t = oo` is a final value."""
    got = sp.Eq(lhs, rhs)
    ends = (sp.oo, -sp.oo, sp.zoo)
    if isinstance(got, sp.logic.boolalg.BooleanAtom) and \
            (lhs.has(*ends) or rhs.has(*ends)):
        return sp.Eq(lhs, rhs, evaluate=False)
    return got


def _parse_equation(text: str):
    if "=" in text:
        lhs, rhs = text.split("=", 1)
        return sp.Eq(_read(lhs), _read(rhs))
    return sp.Eq(_read(text), 0)


def _parse_condition(text: str):
    """One condition as a SymPy relational; `7 > x > 3` is the conjunction
    of its links, split by the engine's own function."""
    from .engine import split_chained_comparison
    fragments = split_chained_comparison(text)
    if len(fragments) > 1:
        return sp.And(*[_parse_condition(f) for f in fragments])
    ops = ((">=", lambda l, r: l >= r), ("<=", lambda l, r: l <= r),
           (">", lambda l, r: l > r), ("<", lambda l, r: l < r),
           ("=", _equality))
    for op, make in ops:
        if op in text:
            lhs, rhs = text.split(op, 1)
            return make(_read(lhs), _read(rhs))
    return _read(text)


# ---------------------------------------------------------------------------
# Evaluate's conditions: substitutions and assumptions
# ---------------------------------------------------------------------------

_ASSUMPTION_FOR = {">": "positive", ">=": "nonnegative",
                   "<": "negative", "<=": "nonpositive"}


def _evaluate_conditions(conditions, values):
    """(substitutions, assumptions). `t = 2` is the calculator's `|t=2`;
    a comparison is an assumption for refine(); an equality with anything
    but a single name on its left is an equation, which is solve()'s."""
    subs_map, assumptions = {}, []
    for line in [str(c).strip() for c in (conditions or []) if str(c).strip()]:
        parsed = _parse_condition(line)
        if isinstance(parsed, sp.Equality):
            if not isinstance(parsed.lhs, sp.Symbol):
                raise ValueError(
                    f"'{line}' is an equation to solve, not a value to "
                    "substitute -- a condition's left side is one name. "
                    "Use solve() for it.")
            right = parsed.rhs
            subs_map[parsed.lhs] = right.subs(_alias_mapping(values,
                                                             expr=right))
            continue
        name = _ASSUMPTION_FOR.get(getattr(parsed, "rel_op", None))
        if name is None:
            raise ValueError(f"'{line}' is neither a substitution "
                             "(name = value) nor a comparison.")
        side = parsed.lhs - parsed.rhs
        side = side.subs(_alias_mapping(values, expr=side))
        assumptions.append(getattr(sp.Q, name)(side))
    return subs_map, assumptions


def _limit_or(expr, name, point, fallback):
    try:
        got = sp.limit(sp.simplify(expr), name, point)
    except Exception:                                   # noqa: BLE001
        return fallback
    if isinstance(got, sp.Limit) or got.has(sp.nan):
        return fallback
    return got


def _substitute(expr, subs_map):
    """Substitution, with a limit wherever substitution cannot give the
    value: a condition at `oo`, or a finite one that comes out undefined."""
    ends = (sp.oo, -sp.oo)
    finite = {k: v for k, v in subs_map.items() if v not in ends}
    out = expr.subs(finite) if finite else expr
    if finite and out.has(sp.nan):
        out = expr
        for name, point in finite.items():
            stepped = out.subs(name, point)
            if stepped.has(sp.nan):
                stepped = _limit_or(out, name, point, stepped)
            out = stepped
    for name, point in subs_map.items():
        if point in ends:
            out = _limit_or(out, name, point, out.subs(name, point))
    return out


def _apply_conditions(expr, subs_map, assumptions):
    if subs_map:
        expr = _substitute(expr, subs_map)
    if assumptions:
        expr = sp.refine(expr, sp.And(*assumptions))
    return expr


# ---------------------------------------------------------------------------
# The three forms that must see the answers before they are applied
# ---------------------------------------------------------------------------

_PF_CALL = re.compile(r"^\s*pf\s*\((.*)\)\s*$", re.S)
_PF_NAME = re.compile(r"^[A-Za-z_]\w*$")
_TRANSFORM_CALL = re.compile(r"^\s*(s2t|t2s)\s*\((.*)\)\s*$", re.S)
_LIMIT_CALL = re.compile(r"^\s*limit\s*\((.*)\)\s*$", re.S)


def _split_top_level(inside: str) -> list:
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(inside):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(inside[start:i])
            start = i + 1
    parts.append(inside[start:])
    return [p.strip() for p in parts]


def _power_factor(inside: str, values, subs_map, assumptions):
    """`pf(x)`: the power factor, a number between 0 and 1. An element's
    name reads the power a source delivers or an impedance consumes; any
    other expression is read as a complex power, as given. (The card adds
    the word, leading or lagging; this returns the value alone.)"""
    if len(_split_top_level(inside)) != 1:
        raise ValueError("pf takes one value: a complex power, or the "
                         "name of an element.")
    text = inside.strip().strip("\"'")
    by_norm = {_norm(k): k for k in values}
    if _PF_NAME.match(text):
        v_key = by_norm.get("v" + _norm(text))
        i_key = by_norm.get("i" + _norm(text))
        if v_key and i_key:
            name = v_key.split("_", 1)[1] if "_" in v_key else v_key[1:]
            kind = name[:1].lower()
            if kind not in "ejrlc":
                raise ValueError(f"pf has no convention for element {name}.")
            sign = -1 if kind in "ej" else 1
            s = values[v_key] * sp.conjugate(sign * values[i_key])
            s = sp.N(sp.simplify(_apply_conditions(s, subs_map, assumptions)))
            if s.free_symbols:
                raise ValueError(f"pf({name}) needs numbers; still symbolic "
                                 "in " + ", ".join(sorted(map(str,
                                                              s.free_symbols))))
            if s == 0:
                raise ValueError(f"pf({name}): the power is zero.")
            return sp.Float(abs(sp.re(s)) / abs(s))
    z = _read(text)
    z = z.subs(_alias_mapping(values, expr=z))
    z = sp.simplify(_apply_conditions(z, subs_map, assumptions))
    if z == 0:
        raise ValueError(f"pf({text}): the power is zero.")
    if not z.free_symbols:
        return sp.N(sp.Abs(sp.re(z)) / sp.Abs(z))
    twins = {x: sp.Symbol(x.name, real=True) for x in z.free_symbols
             if x.is_real is not True}
    zr = z.xreplace(twins)
    ratio = sp.simplify(sp.Abs(sp.re(zr)) / sp.Abs(zr))
    return ratio.xreplace({twin: x for x, twin in twins.items()})


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------

def evaluate(result, expr: str, conditions: Optional[Iterable[str]] = None,
             use_rms: bool = False):
    """An expression in the circuit's answers, as the app's Evaluate card
    reads it. Returns SymPy.

        evaluate(res, "vo/vs")
        evaluate(res, "vc", conditions=["t = 2"])      # the calculator's |
        evaluate(res, "s*vo", conditions=["s = oo"])   # taken as a limit
        evaluate(res, "s2t(vo)")                        # an FD answer in time
        evaluate(res, "limit(s*vo, s, 0)")
        evaluate(res, "pf(e)")
        evaluate(th(...), "prl", conditions=["load = 1000"])

    `use_rms` matters only for a `th()` result in AC or FD, where it says
    which convention `prl` is the average power under."""
    values, domain = _answers(result, use_rms)
    text = _unbrace(str(expr), domain)
    subs_map, assumptions = _evaluate_conditions(conditions, values)

    m = _PF_CALL.match(text)
    if m:
        return _power_factor(m.group(1), values, subs_map, assumptions)

    m = _LIMIT_CALL.match(text)
    if m:
        args = _split_top_level(m.group(1))
        if len(args) == 3 and all(args):
            name = _read(args[1])
            if isinstance(name, sp.Symbol):
                inner = _read_with_rearrangers(_expand(args[0]))
                inner = inner.subs(_alias_mapping(values, expr=inner))
                inner, _ = _apply_rearrangers(inner)
                point = _read(args[2])
                point = point.subs(_alias_mapping(values, expr=point))
                others = {k: v for k, v in subs_map.items() if k != name}
                inner = _apply_conditions(inner, others, assumptions)
                return sp.simplify(sp.limit(sp.simplify(inner), name, point))

    m = _TRANSFORM_CALL.match(text)
    if m:
        from .laplace import s2t, t2s
        inner = _read(m.group(2))
        inner = inner.subs(_alias_mapping(values, expr=inner))
        inner = _apply_conditions(inner, subs_map, assumptions)
        return sp.simplify((s2t if m.group(1) == "s2t" else t2s)(
            sp.simplify(inner)))

    parsed = _read_with_rearrangers(_expand(text))
    out = parsed.subs(_alias_mapping(values, expr=parsed))
    out = _apply_conditions(out, subs_map, assumptions)
    out, rearranged = _apply_rearrangers(out)
    # asking to expand an answer is asking for that arrangement of it;
    # simplify would gather it straight back up
    return out if rearranged else sp.simplify(out)


# ---------------------------------------------------------------------------
# solve
# ---------------------------------------------------------------------------

def _equality_binding(cond, wanted):
    """(symbol, value) for `R_3 = 10` on a symbol that is not an unknown:
    the calculator's `|`, a substitution. Anything else is a filter."""
    if not isinstance(cond, sp.Equality):
        return None, None
    names = {str(w) for w in wanted}
    for sym, other in ((cond.lhs, cond.rhs), (cond.rhs, cond.lhs)):
        if isinstance(sym, sp.Symbol) and str(sym) not in names:
            return sym, other
    return None, None


def _holds(sol, filters) -> bool:
    """False only for a condition that reduces to a plain False; one that
    cannot be decided is no grounds to discard a solution."""
    for cond in filters:
        try:
            c = sp.simplify(cond.subs(sol))
        except Exception:                               # noqa: BLE001
            continue
        if c in (sp.false, False):
            return False
    return True


def solve(result, equations: Iterable[str],
          unknowns: Optional[Iterable[str]] = None,
          conditions: Optional[Iterable[str]] = None,
          real_only: bool = False, use_rms: bool = False) -> List[dict]:
    """Equations written in the circuit's answers, solved as the app's
    Solve card solves them. Returns a list of solutions, each a dict of
    name -> SymPy value; empty when there is none.

        solve(res, ["im(ze) = 0"], ["w"], conditions=["w > 0"],
              real_only=True)
        solve(res, ["isg = 0"], ["R_x"], conditions=["R_3 = 10"])
        solve(res, ["x = vc", "t = 2"], ["x", "t"])

    The answers are substituted in first, so an equation may name `v2` or
    `ir1` directly; whatever is left is solved for. `unknowns` may be
    omitted, and then every symbol left over is one. A condition that pins
    a symbol, `R_3 = 10`, is substituted before solving; a comparison,
    `w > 0`, filters the roots after. `real_only` is the calculator's
    solve() against its cSolve(): the unknowns are declared real and no
    complex root comes back."""
    values, domain = _answers(result, use_rms)
    if isinstance(equations, str):
        equations = [equations]
    if isinstance(unknowns, str):
        unknowns = [u for u in re.split(r"\s*,\s*", unknowns.strip()) if u]
    if isinstance(conditions, str):
        conditions = [conditions]

    from .laplace import t as time_symbol

    def canonical(sym):
        return time_symbol if str(sym) == "t" else sym

    wanted = [canonical(sp.Symbol(u)) for u in (unknowns or [])]
    alias = _text_aliases(values)
    texts = [_apply_text_aliases(_unbrace(str(e), domain), alias)
             for e in equations]
    cond_texts = [_apply_text_aliases(_unbrace(str(c), domain), alias)
                  for c in (conditions or [])]

    eqs = []
    for eq in (_parse_equation(e) for e in texts):
        eqs.append(eq.subs(_alias_mapping(
            values, exclude=[str(w) for w in wanted], expr=eq)))

    with_map, filters = {}, []
    for cond in (_parse_condition(c) for c in cond_texts):
        sym, val = _equality_binding(cond, wanted)
        if sym is not None:
            with_map[sym] = val.subs(with_map)
        else:
            filters.append(cond)
    if with_map:
        eqs = [eq.subs(with_map) for eq in eqs]

    if not wanted:
        free = set()
        for eq in eqs:
            free |= eq.free_symbols
        wanted = sorted(free, key=str)
    else:
        # an equation naming none of the unknowns brings its own
        named = set(wanted)
        for eq in eqs:
            if getattr(eq, "free_symbols", set()) & named:
                continue
            for sym in sorted(getattr(eq, "free_symbols", ()), key=str):
                if sym not in named and str(sym) not in ("s", "t"):
                    wanted.append(sym)
                    named.add(sym)
    if not wanted:
        raise ValueError("There is nothing to solve for: every name in the "
                         "equations is already an answer.")

    real_map = {}
    if real_only:
        real_map = {s: sp.Symbol(str(s), real=True)
                    for s in wanted if not s.is_real}
        eqs = [eq.xreplace(real_map) for eq in eqs]
        wanted = [real_map.get(s, s) for s in wanted]

    sols = sp.solve(eqs, wanted, dict=True)
    if real_only:
        def is_real(v):
            if getattr(v, "free_symbols", None):
                return True
            return sp.im(sp.nsimplify(v)) == 0 or v.is_real is not False
        sols = [s for s in sols if all(is_real(v) for v in s.values())]
    if filters:
        if real_map:
            filters = [c.xreplace(real_map) for c in filters]
        sols = [s for s in sols if _holds(s, filters)]

    out = []
    for sol in sols:
        row = {}
        for sym in wanted:
            if sym in sol:
                try:
                    row[str(sym)] = sp.simplify(sol[sym])
                except Exception:                       # noqa: BLE001
                    row[str(sym)] = sol[sym]
        if row:
            out.append(row)
    return out
