"""
High-level API mirroring the TI programs `dc()` and `ac()`: parse a
circuit description, solve it, and also compute the third-level derived
quantities (branch voltage, power, impedance) that Symbulator computes
after solving -- ported from the tail of `symbv8s6`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import sympy as sp

from .elements import Element, parse_circuit
from .engine import solve_circuit, solve_circuit_all


@dataclass
class Result:
    """Everything `dc()`/`ac()`/`fd()`/`tr()` solved for, keyed by the same
    variable names the calculator used: `v_<node>` for a node voltage,
    `i_<name>` for an element/branch current, plus the derived `p_`/`s_`/
    `z_`/`r_` quantities computed after the solve (dc/ac only -- see
    `_derived`). `domain` records which analysis produced it ("dc", "ac",
    "fd", or "tr"), which matters because the same key means a different
    kind of number depending on domain (e.g. `v_2` is a real number in dc,
    a phasor in ac, an s-domain expression in fd, a function of t in tr)."""
    domain: str
    values: Dict[str, sp.Expr] = field(default_factory=dict)
    # Every solution the solver found, `values` being the first. Only an
    # expert-mode equation that is quadratic in an unknown (a power, say)
    # yields more than one; pin the root you mean with a condition such
    # as conditions=["e > 0"], or read the others from here.
    solutions: List[Dict[str, sp.Expr]] = field(default_factory=list)

    def __post_init__(self):
        if not self.solutions:
            self.solutions = [self.values]

    @property
    def multiple(self) -> bool:
        """True when the circuit had more than one solution."""
        return len(self.solutions) > 1

    def __getitem__(self, key: str) -> sp.Expr:
        """result["v_2"] -- direct lookup by variable name; raises KeyError
        if that variable wasn't solved for (e.g. asking for a node that
        doesn't exist, or a `tr()` variable that couldn't be inverse-
        transformed)."""
        return self.values[key]

    def get(self, key: str, default=None):
        """Same as __getitem__ but returns `default` instead of raising
        when the key is missing."""
        return self.values.get(key, default)

    def v(self, node) -> sp.Expr:
        """Voltage at `node` (shorthand for result[f"v_{node}"])."""
        return self.values[f"v_{node}"]

    def i(self, name) -> sp.Expr:
        """Current through the element/branch called `name` (shorthand
        for result[f"i_{name}"])."""
        return self.values[f"i_{name}"]

    def at(self, key: Optional[str] = None, **where):
        """Substitute by *name* -- `res.at(t=0.001)`, `res.at("v_2", t=0.001)`,
        `res.at(omega=1000)` -- so callers need not reproduce the exact
        SymPy symbol (with its assumptions) the solver used. With `key`,
        returns that one expression evaluated; without, a new Result with
        every expression evaluated.

        This is the safe route for the time symbol: `tr()` writes its
        answers in Symbol("t", nonnegative=True), and a bare Symbol("t") is
        a different symbol that `.subs()` would silently ignore."""
        def sub(expr):
            if not isinstance(expr, sp.Basic):
                return expr
            m = {sym: val for sym in expr.free_symbols
                 for name, val in where.items() if sym.name == name}
            return expr.subs(m) if m else expr

        if key is not None:
            return sub(self.values[key])
        return Result(domain=self.domain,
                      values={k: sub(v) for k, v in self.values.items()},
                      solutions=[{k: sub(v) for k, v in sol.items()}
                                 for sol in self.solutions])

    def __repr__(self) -> str:
        """One line per solved variable, sorted by name, so a Result
        prints legibly at the REPL or in a notebook instead of dumping an
        unordered dict."""
        lines = [f"Result(domain={self.domain!r})"]
        if self.multiple:
            lines[0] += (f"  -- {len(self.solutions)} solutions, showing #1; "
                         "the others are in .solutions")
        for k in sorted(self.values):
            lines.append(f"  {k} = {self.values[k]}")
        return "\n".join(lines)


def _node_v(solution: Dict[str, sp.Expr], node: str) -> sp.Expr:
    """Look up a node's voltage in a raw solve() dict, treating node "0"
    (ground) as the literal constant 0 even though the solver never
    creates a v_0 symbol for it. Falls back to a bare symbol (rather than
    raising) if the node was never stamped into the system -- this keeps
    `_derived` from blowing up on a malformed circuit; the missing-node
    error itself is already raised earlier, during parsing."""
    if node == "0":
        return sp.Integer(0)
    return solution.get(f"v_{node}", sp.Symbol(f"v_{node}"))


def _clean_noise(expr: sp.Expr, rel_tol: float = 1e-9) -> sp.Expr:
    """Zero out whichever part (real or imaginary) of a concrete complex
    answer is negligible next to the other. Multiplying and dividing
    already-computed floats -- as this module does to get power and
    impedance -- routinely leaves a residue of floating-point noise
    behind: an ideal inductor's complex power is purely reactive in
    reality, but a raw float computation comes back as something like
    -7.6e-19 + 0.0061j. Left alone, that residue survives every
    rounding/display mode (including "exact") and each one shows its
    own leftover digits instead of agreeing the offending part is zero.

    `rel_tol` is relative to the larger of the two parts, not an
    absolute cutoff -- 1e-9 is far above the ~1e-15 noise floor of
    double-precision arithmetic, and far below any part a real circuit
    would produce on purpose. Only a plain number with no free symbols
    is touched; a symbolic answer (e.g. an expression in r_a, r_b) is
    returned untouched, since there's no single scale to judge
    "negligible" against, and quantities the solver already reports as
    exact (rationals, integers) are left alone too since they can't
    carry float noise in the first place."""
    if not getattr(expr, "is_number", False) or expr.free_symbols:
        return expr
    if expr.is_rational:
        return expr
    try:
        val = complex(expr)
    except (TypeError, ValueError):
        return expr
    scale = max(abs(val.real), abs(val.imag))
    if scale == 0:
        return expr
    re = val.real if abs(val.real) > rel_tol * scale else 0.0
    im = val.imag if abs(val.imag) > rel_tol * scale else 0.0
    if re == val.real and im == val.imag:
        return expr  # nothing negligible, keep the original form as-is
    if im == 0:
        return sp.Float(re)
    if re == 0:
        return sp.Float(im) * sp.I
    return sp.Float(re) + sp.Float(im) * sp.I


def _seen_impedance(vdiff: sp.Expr, i: sp.Expr):
    """v / (-i) for the impedance/resistance a source sees, tolerating a
    zero current. A source pushing no current is looking into an open
    circuit, so the honest answer is infinite -- but a plain division
    would either produce SymPy's `zoo` (for exact zeros) or raise
    ZeroDivisionError outright (for float zeros, since mpmath refuses).
    Returns None for the genuinely undefined 0/0 case so the caller can
    omit the quantity instead of reporting nonsense."""
    denom = sp.simplify(-i)
    if denom.is_zero:
        num = sp.simplify(vdiff)
        if num.is_zero:
            return None          # 0 V across 0 A: undefined, not infinite
        return sp.oo
    return sp.simplify(vdiff / denom)


def _derived(elements, domain: str, solution: Dict[str, sp.Expr],
             use_rms: bool = False) -> Dict[str, sp.Expr]:
    """Compute the third-level quantities, derived *after* the KCL system
    is solved: branch voltage (v_<name>), power (p_<name> in dc, or
    complex power s_<name> plus its real part, the average power, in ac
    -- under p_<name> with RMS phasors and ap_<name> without), and
    -- for sources only -- the impedance/resistance the source sees
    looking into the rest of the circuit (z_<name> / r_<name>).

    "Third level" here is this port's classification, which the monograph
    follows: whatever is derived in this round. The 2000 thesis counts
    only the powers as third level and keeps the voltage drop as a
    second-level standing expression (§4.2.4); the drop is computed here
    instead, so it is third level by the rule this code uses. The seen
    resistance the thesis never classifies at all. These are
    plain algebra on the already-solved node voltages and branch currents,
    not part of the KCL system itself, so they're computed in one pass
    here rather than inside the solver in engine.py."""
    out: Dict[str, sp.Expr] = {}
    for e in elements:
        if e.kind in "ejrcl":
            i_key = f"i_{e.name}"
            if i_key not in solution:
                continue
            i = solution[i_key]
            vdiff = sp.simplify(_node_v(solution, e.n1) - _node_v(solution, e.n2))
            # Branch voltage (voltage drop across the element) -- the
            # original stored these as v<name> on the calculator.
            out[f"v_{e.name}"] = vdiff
            if domain == "ac":
                s = vdiff * sp.conjugate(i)
                if not use_rms:
                    s = s / 2
                s = _clean_noise(sp.simplify(s))
                out[f"s_{e.name}"] = s
                if e.kind in "ejr":
                    p = sp.simplify(sp.re(s))
                    out[f"p_{e.name}" if use_rms else f"ap_{e.name}"] = p
                    if e.kind in "ej":
                        z = _seen_impedance(vdiff, i)
                        if z is not None:
                            out[f"z_{e.name}"] = _clean_noise(z)
            else:  # dc
                out[f"p_{e.name}"] = sp.simplify(vdiff * i)
                if e.kind in "ej":
                    r = _seen_impedance(vdiff, i)
                    if r is not None:
                        out[f"r_{e.name}"] = r
        elif e.kind == "o":
            i_key = f"i_{e.name}"
            if i_key not in solution:
                continue
            i = solution[i_key]
            n_out = e.fields[2]
            vout = _node_v(solution, n_out)
            if domain == "ac":
                s = vout * sp.conjugate(-i)
                if not use_rms:
                    s = s / 2
                s = _clean_noise(sp.simplify(s))
                out[f"s_{e.name}"] = s
                out[f"p_{e.name}" if use_rms else f"ap_{e.name}"] = sp.simplify(sp.re(s))
            else:
                out[f"p_{e.name}"] = sp.simplify(vout * (-i))
    return out


def _run(desc: str, domain: str, omega=None, params=None, use_rms: bool = False,
         equations=None, unknowns=None, conditions=None,
         suffix: str = "ask") -> Result:
    """Shared body of `dc()`/`ac()`/`fd()`: parse the circuit, solve it,
    add the derived quantities where they apply, and wrap the result.
    Kept as one function so the three public entry points stay tiny and
    can't drift out of sync with each other."""
    elements = parse_circuit(desc)
    solutions = solve_circuit_all(elements, domain=domain, omega=omega, params=params,
                                  equations=equations, unknowns=unknowns,
                                  conditions=conditions, suffix=suffix)
    if domain in ("dc", "ac"):
        # Matches the original: the power/impedance third-level derived
        # quantities are only computed for dc/ac, not for fd (s-domain).
        for solution in solutions:
            solution.update(_derived(elements, domain, solution, use_rms=use_rms))
    return Result(domain=domain, values=solutions[0], solutions=solutions)


def dc(desc: str, params: Optional[dict] = None, equations=None,
       unknowns=None, conditions=None, suffix: str = "ask") -> Result:
    """DC steady-state analysis. `desc` is a Symbulator-style circuit
    description string, e.g. "e1,1,0,5:r1,1,2,1k:r2,2,0,1k".
    `equations`/`unknowns`/`conditions` add expert-mode extras to the
    system before solving (see `solve_circuit`)."""
    return _run(desc, "dc", params=params, equations=equations,
                unknowns=unknowns, conditions=conditions, suffix=suffix)


def ac(desc: str, omega, params: Optional[dict] = None, use_rms: bool = False,
       equations=None, unknowns=None, conditions=None,
       suffix: str = "ask") -> Result:
    """AC steady-state (phasor) analysis at angular frequency `omega`
    (a number or a SymPy expression/symbol for a symbolic frequency)."""
    return _run(desc, "ac", omega=sp.sympify(omega), params=params,
                use_rms=use_rms, equations=equations, unknowns=unknowns,
                conditions=conditions, suffix=suffix)


def fd(desc: str, params: Optional[dict] = None, equations=None,
       unknowns=None, conditions=None, suffix: str = "ask") -> Result:
    """s-domain (Laplace) analysis -- ports `fd()`.

    Source values are read **in the s-domain**: "5/s" is a 5 V step, "1"
    is an impulse. A value written in the time domain instead can be
    wrapped in braces -- "{5}", "{u(t)}", "{2*exp(-4*t)}" -- and it is
    transformed on the way in, which is the calculator's `{...}`
    shorthand for `t2s(...)`.

    `l`/`c` elements may carry an optional 5th field for a nonzero initial
    condition, e.g. "l1,1,2,0.1,2" for a 0.1 H inductor with 2 A of
    initial current."""
    from .si_prefix import expand_time_domain_braces

    def unbrace(items):
        """`{...}` in an added equation or condition, same as in a value.

        The brackets are how a reader says "this one is in time" while
        FD reads everything in s. That has to hold wherever the
        convention is enforced, not only in the circuit description --
        otherwise the rule is imposed in four places and escapable in
        one."""
        if not items:
            return items
        return [expand_time_domain_braces(str(x)) for x in items]

    return _run(expand_time_domain_braces(desc), "fd", params=params,
                equations=unbrace(equations), unknowns=unknowns,
                conditions=unbrace(conditions), suffix=suffix)
