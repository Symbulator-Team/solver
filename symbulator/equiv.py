"""
Equivalent-circuit and two-port-extraction tools: ports `th()`, `er()`,
and `port()`.

All three work by adding one or two *test* excitations to a copy of the
given circuit description and re-running the existing `dc`/`ac` engine
-- no new circuit physics, just orchestration on top of Phase 1.

`er()` and `port()` use symbolic test-source values and extract the
answer by substitution after a single solve, mirroring how the original
TI-Basic used the test sources' own names as their (initially undefined,
i.e. symbolic) values and read off ratios/derivatives after the fact.

All three accept the expert-mode `equations`/`unknowns`/`conditions` and
pass them to every round they run. The original barred expert mode from
these tools; nothing about the physics required that, only that the
orchestration below had not been given the arguments to pass on. See
`th()` for the one case where "every round" is the wrong answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import sympy as sp

from .analysis import Result, _run
from . import messages as M
from .elements import CircuitError


def _v(res: Result, node: str) -> sp.Expr:
    """Node voltage, treating "0" (ground) as the literal constant 0
    even though the engine doesn't create a v_0 symbol for it."""
    if node == "0":
        return sp.Integer(0)
    return res.v(node)


@dataclass
class TheveninResult:
    """Everything `th()` computes: the Thevenin/Norton equivalent of a
    circuit as seen from a chosen pair of nodes, reduced to a single
    voltage source (vth) in series with an equivalent resistance/
    impedance (z) -- or equivalently a single current source (ino) in
    parallel with that same z. `pmax` is the maximum power a load
    connected across those two nodes could ever draw (achieved when the
    load's own resistance/impedance equals z, i.e. "matched")."""
    domain: str
    vth: sp.Expr      # open-circuit voltage between n1 and n2
    ino: sp.Expr       # short-circuit current from n1 to n2
    z: sp.Expr          # Req (dc) or Zeq (ac) = vth / ino
    pmax: sp.Expr        # maximum power transferable to a matched load
    #: Set when the short-circuit round had to be reasoned about rather
    #: than solved -- see th(). Empty on an ordinary run.
    note: str = ""

    def __repr__(self) -> str:
        """Labels the equivalent-impedance field "req" or "zeq" to match
        the vocabulary of the domain it was computed in (a dc circuit
        has a resistance, an ac circuit has an impedance) rather than
        printing the generic field name `z` either way."""
        z_label = "req" if self.domain == "dc" else "zeq"
        return (f"TheveninResult(domain={self.domain!r}, vth={self.vth}, "
                f"ino={self.ino}, {z_label}={self.z}, pmax={self.pmax})")


_AWKWARD_NOTE = (
    "The short-circuit round could not be solved directly; the current was "
    "found as the limit of a vanishing resistance instead.")


def _short_by_limit(desc, n1, n2, run_kwargs):
    """(current, note) for a short that could not be solved directly.

    A short is a resistance of zero, so put one of value x across the
    terminals and let x go to zero. The limit is what the short would have
    carried. `sp.oo` back means unbounded, which is a real answer about the
    circuit and not a failure: the terminals hold their voltage whatever
    current is drawn, so the equivalent impedance is zero.

    The infinity test is `has(oo, zoo)` rather than `is_infinite`. With a
    symbolic source the limit comes back as `oo*sign(Abs(vs*(r1+r2)/r1))`,
    whose `is_infinite` is None -- and that symbolic case is precisely the
    one this exists for.
    """
    probe = _run(f"{desc}:rtest,{n1},{n2},x_test", **run_kwargs)
    current = probe.i("rtest")

    # Match the symbol by name rather than rebuilding it. A Symbol carries
    # its assumptions in its identity, so Symbol("x_test", positive=True)
    # is a different symbol from the plain one the parser made, and a limit
    # taken in the wrong one silently finds nothing to do.
    xs = [t for t in current.free_symbols if t.name == "x_test"]
    if not xs:
        # The test resistance cancelled out, so the current never depended
        # on it and the short carries exactly this.
        return sp.simplify(current), _AWKWARD_NOTE
    x = xs[0]

    if sp.limit(sp.Abs(current), x, 0, "+").has(sp.oo, sp.zoo):
        return sp.oo, (
            "The short-circuit current is unbounded, so the equivalent is a "
            "voltage source with no impedance in series with it.")
    # Abs settled whether it is finite; this recovers the signed value,
    # which in AC and FD carries the phase as well.
    return sp.simplify(sp.limit(current, x, 0, "+")), _AWKWARD_NOTE


def th(desc: str, n1: str, n2: str, domain: str = "dc", omega=None,
        params: Optional[dict] = None, use_rms: bool = False,
        equations=None, unknowns=None, conditions=None,
        suffix: str = "ask") -> TheveninResult:
    """Thevenin/Norton equivalent of the circuit `desc` as seen between
    nodes `n1` and `n2` -- ports `th()`. Works for active circuits (ones
    with independent sources); for source-free circuits use `er()`
    instead (this mirrors the original's own guidance message).

    `equations`/`unknowns`/`conditions` are the expert-mode extras, and
    they are added to *both* rounds -- the open-circuit solve and the
    short-circuit one. That is right for a condition on a parameter
    (`x = 3`) and for an equation that names a derived quantity
    (`vx = va-vb`), both of which mean the same thing in either round.
    It is not right for an equation that pins an unknown *element value*
    from a measurement (`ir2 = 4` for an unknown `rx`): that measurement
    holds in the circuit as given, not in the shorted copy, so the two
    rounds are asking for different things. In practice the short-circuit
    round then has no consistent solution and the solve raises rather
    than returning a mixed answer -- but do not rely on the refusal, and
    rely on it less than before: a raise now falls through to the limit
    probe below, which may well settle what the short could not and
    return a confident answer to a question that was malformed.
    Determine such a value with a plain solve first and put the number in
    the description."""
    n1, n2 = str(n1), str(n2)

    open_circuit = _run(desc, domain, omega=omega, params=params,
                        use_rms=use_rms, equations=equations,
                        unknowns=unknowns, conditions=conditions,
                        suffix=suffix)
    vth = sp.simplify(_v(open_circuit, n1) - _v(open_circuit, n2))
    if vth == 0:
        raise CircuitError(M.E_NOT_ACTIVE)

    run_kwargs = dict(domain=domain, omega=omega, params=params,
                      use_rms=use_rms, equations=equations,
                      unknowns=unknowns, conditions=conditions,
                      suffix=suffix)

    test_name = "stest"
    note = ""
    try:
        short_circuit = _run(f"{desc}:{test_name},{n1},{n2}", **run_kwargs)
        ino = sp.simplify(short_circuit.i(test_name))
    except Exception as short_failed:
        # The open-circuit round already answered half the question, and
        # throwing that away with the other half is what #107 was.
        try:
            ino, note = _short_by_limit(desc, n1, n2, run_kwargs)
        except Exception:
            raise CircuitError(M.E_NO_SHORT_CIRCUIT, vth=vth,
                               reason=short_failed) from short_failed

    if ino is sp.oo or ino == sp.oo:
        # Unbounded current through a short means no impedance in the way,
        # and a source that can deliver without limit.
        z, pmax = sp.Integer(0), sp.oo
    else:
        z = sp.simplify(vth / ino)
        if domain == "dc":
            pmax = sp.simplify(vth * ino / 4)
        else:
            denom = 4 if use_rms else 8
            real = sp.re(z)
            pmax = (sp.oo if real == 0
                    else sp.simplify(sp.Abs(vth) ** 2 / (denom * real)))

    return TheveninResult(domain=domain, vth=vth, ino=ino, z=z, pmax=pmax,
                          note=note)


def er(desc: str, n1: str, n2: str, domain: str = "dc", omega=None,
       params: Optional[dict] = None, equations=None, unknowns=None,
       conditions=None, suffix: str = "ask") -> sp.Expr:
    """Equivalent resistance (dc) / impedance (ac) of a source-free
    (passive) circuit `desc`, as seen between nodes `n1` and `n2` --
    ports `er()`. Injects a 1A test current source and reads the
    resulting voltage across the port directly; only valid when `desc`
    has no independent sources of its own (use `th()` for active
    circuits)."""
    n1, n2 = str(n1), str(n2)
    test_name = "jtest"
    res = _run(f"{desc}:{test_name},{n2},{n1},1", domain, omega=omega,
               params=params, equations=equations, unknowns=unknowns,
               conditions=conditions, suffix=suffix)
    return sp.simplify(_v(res, n1) - _v(res, n2))


_PORT_KINDS = ("z", "y", "h", "g", "a", "b")


def port(desc: str, n1: str, n2: str, kind: str, domain: str = "dc", omega=None,
         params: Optional[dict] = None, equations=None, unknowns=None,
         conditions=None, suffix: str = "ask") -> dict:
    """Extract the z/y/h/g/a/b two-port parameters of the circuit `desc`
    between ports (`n1`, ground) and (`n2`, ground) -- ports `port()`.
    Returns a dict with keys "11", "12", "21", "22".

    Uses one solve with symbolic test-source values (a current source
    at each port for z/a/b, a voltage source at each port for y, one of
    each for h/g -- matching the original's own choice of excitation
    per parameter type) and extracts each parameter by substituting the
    other port's test value to 0, exactly as the original does."""
    kind = kind.lower()
    if kind not in _PORT_KINDS:
        raise ValueError(f"Unknown two-port kind '{kind}'; must be one of {_PORT_KINDS}.")
    n1, n2 = str(n1), str(n2)

    # Two symbolic test values, one per port. Leaving them as free
    # symbols (rather than picking e.g. 1 A) is what lets a single solve
    # yield every parameter: each parameter is some ratio of the
    # solution's dependence on x1 vs x2, extracted below by substituting
    # the *other* test value to 0 (see `ratio`).
    x1, x2 = sp.symbols("x_test1 x_test2")

    # Which kind of test source to attach at each port follows the
    # classic definition of that parameter family: z (and a/b, which are
    # derived from an intermediate z below) are "open-circuit" parameters
    # -- driven by current sources, since forcing one port's *other*
    # test value to 0 open-circuits it exactly the way an open-circuit
    # measurement would. y is the dual: "short-circuit" parameters,
    # driven by voltage sources, where zeroing a test value short-circuits
    # that port. h and g are hybrids of the two -- one port driven by
    # current, the other by voltage -- matching which of v/i each of
    # their four defining equations mixes (see the comments in
    # `engine._stamp_two_port` for the h/g defining equations).
    if kind in ("z", "a", "b"):
        test = f"jtest1,0,{n1},{x1}:jtest2,0,{n2},{x2}"
        i1_of = lambda res: res.i("jtest1")
        i2_of = lambda res: res.i("jtest2")
    elif kind == "y":
        test = f"etest1,{n1},0,{x1}:etest2,{n2},0,{x2}"
        i1_of = lambda res: -res.i("etest1")
        i2_of = lambda res: -res.i("etest2")
    elif kind == "h":
        test = f"jtest1,0,{n1},{x1}:etest2,{n2},0,{x2}"
        i1_of = lambda res: res.i("jtest1")
        i2_of = lambda res: -res.i("etest2")
    else:  # g
        test = f"etest1,{n1},0,{x1}:jtest2,0,{n2},{x2}"
        i1_of = lambda res: -res.i("etest1")
        i2_of = lambda res: res.i("jtest2")

    res = _run(f"{desc}:{test}", domain, omega=omega, params=params,
               equations=equations, unknowns=unknowns, conditions=conditions,
               suffix=suffix)
    V1, V2 = _v(res, n1), _v(res, n2)
    I1, I2 = i1_of(res), i2_of(res)

    def ratio(expr, zero_sym, divisor):
        """`expr` with `zero_sym` (the *other* port's test value) set to
        0, divided by `divisor` (this port's own test value) -- this is
        exactly "response at this port, per unit excitation, with the
        other port open/shorted" for whichever parameter is being read
        off, i.e. one entry of the two-port matrix."""
        return sp.simplify(expr.subs(zero_sym, 0) / divisor)

    if kind == "z":
        # z11 = v1/i1 (port 2 open, i.e. x2=0); z12 = v1/i2 (port 1
        # open); z21, z22 are the same idea for v2. See the z branch of
        # `engine._stamp_two_port` for the matrix these four solve.
        p11 = ratio(V1, x2, x1)
        p12 = ratio(V1, x1, x2)
        p21 = ratio(V2, x2, x1)
        p22 = ratio(V2, x1, x2)
    elif kind == "y":
        # Dual of z: y11 = i1/v1 (port 2 shorted), etc.
        p11 = ratio(I1, x2, x1)
        p12 = ratio(I1, x1, x2)
        p21 = ratio(I2, x2, x1)
        p22 = ratio(I2, x1, x2)
    elif kind == "h":
        # h11 = v1/i1 (port 2 shorted, x2=0); h12 = v1/v2 (port 1 open,
        # x1=0, so no test current flows and V1 is driven purely by V2's
        # effect through the network); h21, h22 mirror that for i2.
        p11 = ratio(V1, x2, x1)
        p12 = sp.simplify(V1.subs(x1, 0) / V2)
        p21 = ratio(I2, x2, x1)
        p22 = ratio(I2, x1, x2)
    elif kind == "g":
        # Mirror image of h: g11 = i1/v1 (port 2 open), g12 = i1/i2
        # (port 1 shorted), g21 = v2/v1 (port 2 open, no test current at
        # port 2 to disturb it), g22 = v2/i2.
        p11 = ratio(I1, x2, x1)
        p12 = ratio(I1, x1, x2)
        p21 = sp.simplify(V2.subs(x2, 0) / V1)
        p22 = ratio(V2, x1, x2)
    else:  # a or b: derived from the z-equivalent intermediate values
        # rather than a separate excitation, since both a/b matrices have
        # simple closed-form conversions from z -- cheaper and less code
        # than deriving a bespoke excitation scheme for each.
        z11 = ratio(V1, x2, x1)
        z12 = ratio(V1, x1, x2)
        z21 = ratio(V2, x2, x1)
        z22 = ratio(V2, x1, x2)
        det = sp.simplify(z11 * z22 - z12 * z21)
        if kind == "a":
            # Standard z -> ABCD (chain-matrix) conversion: with the
            # ABCD convention v1 = A*v2 - B*i2, i1 = C*v2 - D*i2 (i2
            # flowing out of port 2, the convention that makes chaining
            # two-ports end-to-end a matrix product).
            p11 = sp.simplify(z11 / z21)
            p12 = sp.simplify(det / z21)
            p21 = sp.simplify(1 / z21)
            p22 = sp.simplify(z22 / z21)
        else:  # b
            # Same conversion with ports 1 and 2 swapped, giving the
            # inverse chain matrix (b-parameters relate port 2 back to
            # port 1 instead of the other way around).
            p11 = sp.simplify(z22 / z12)
            p12 = sp.simplify(det / z12)
            p21 = sp.simplify(1 / z12)
            p22 = sp.simplify(z11 / z12)

    return {"11": p11, "12": p12, "21": p21, "22": p22}
