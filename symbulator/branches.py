"""Every two-terminal branch of a circuit as `v(n1) - v(n2) = Z*i + E`.

One question, asked of a stamped circuit: for each branch, what is its
impedance in this domain, and what source term rides in series with it?
The answer is the form nearly any second reading of a circuit needs --
mesh or loop analysis, a hand-written nodal system, a better netlist
export, "show me what the engine thinks this element is".

**It is derived, never restated.** The obvious implementation writes out
"a resistor's drop is `R*i`, a capacitor's is `i/(jwC)`, an inductor in
FD is `s*L*i - L*i0`..." a second time. That is `engine.py`'s knowledge,
it is domain-dependent, and a second copy of it drifts from the first
the moment either moves. So this module states no component rule at all.
It runs the real `Circuit.stamp_all()` and reads each branch's relation
back *out* of the equations the engine produced, by differentiation:

    f(u, i) = lhs - rhs = 0     linear in the branch drop u and current i
    Z = -(df/di) / (df/du)      E = -(f at u=0, i=0) / (df/du)

A capacitor and an independent current source have no equation of their
own -- the engine records their current directly in `Circuit.known` --
so those are read the same way from the admittance side instead. Add a
domain rule to `engine.py` and it appears here for free; change one and
it changes here too.

Three distinctions this makes that are easy to get wrong by hand, each
of which cost a real bug before it was written down here:

* **A component depends on its terminals as their difference.** A
  voltage-controlled current source between the same two nodes may
  depend on only one of them -- `j2,1,2,.2*vrx` has a current in `v_1`
  and not `v_2` -- which looks exactly like an admittance until you ask
  for the difference. Such an element is a source, not an impedance.
* **A terminal on a reference node carries no coefficient**, its voltage
  being the literal 0, so there is nothing to check it against and the
  test above does not apply to it.
* **A dependent source reading a node voltage is not a malformed
  component.** Whatever dependence is left over after the drop is
  accounted for belongs in the source term `E`, not in a refusal.

Nothing in `engine.py` changes to support this: which element produced
which equation is recorded by wrapping the `_stamp_<kind>` methods as
*instance* attributes for one circuit, which shadows the class methods
without the engine knowing this module exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import sympy as sp

from . import messages as M
from .elements import Element, PORT_KINDS
from .engine import Circuit


class BranchError(Exception):
    """A circuit's branches could not be read in this form -- a value
    that is not linear in its own current, an element whose relation
    does not involve its own nodes.

    Carries a message code and its arguments rather than a sentence
    (#199): the package returns structured messages and the interface
    puts them into words, which is what lets a reason reach a reader in
    their own language. `str()` still renders the English, for a
    traceback or a bug report."""

    def __init__(self, code: int, **args):
        self.code = code
        self.args_map = args
        super().__init__(M.render(code, args))


def stamped(elements: List[Element], domain: str, omega=None,
            suffix: str = "si", references: Sequence[str] = ()):
    """Stamp the circuit for real, recording which element produced
    which equation.

    `Circuit.stamp_all` dispatches through `getattr(self, "_stamp_" +
    kind)`, so wrapping those as *instance* attributes shadows the class
    methods for this one circuit and records the range of equations each
    element appended -- without the engine knowing this module exists.
    The known-value substitution at the end of `stamp_all` rebuilds the
    list at the same length, so the indices stay valid."""
    circ = Circuit(elements, domain, omega=omega, suffix=suffix,
                   references=references)
    origin: Dict[int, Element] = {}

    def record(orig):
        def wrapped(e: Element) -> None:
            before = len(circ.equations)
            orig(e)
            for i in range(before, len(circ.equations)):
                origin[i] = e
        return wrapped

    for kind in {e.kind for e in elements}:
        method = getattr(circ, "_stamp_" + kind, None)
        if method is not None:
            setattr(circ, "_stamp_" + kind, record(method))

    circ.stamp_all()

    # A two-port written with its parameters in brackets --
    # `z,1,2,[1,2,3,4]` -- does not pass them to `Circuit`: they reach
    # the solve as *conditions*, `z111 = 1` and the rest, which is the
    # calculator's "the values are stored in the parameter variables".
    # Nothing here runs the conditions machinery, so without this the
    # stamped system carries the free symbols `z111 ...` while the
    # classic solve carries 1, 2, 3, 4 -- and the two then disagree for
    # a reason that has nothing to do with the method (#332).
    from .elements import two_port_param_conditions
    bindings = {}
    for cond in two_port_param_conditions(elements):
        name, _, text = cond.partition("=")
        try:
            bindings[sp.Symbol(name.strip())] = circ._value(text.strip())
        except Exception:                    # noqa: BLE001
            continue
    if bindings:
        circ.equations = [sp.Eq(eq.lhs.subs(bindings), eq.rhs.subs(bindings),
                                evaluate=False) for eq in circ.equations]
        circ.known = {k: (v.subs(bindings) if hasattr(v, "subs") else v)
                      for k, v in circ.known.items()}
    return circ, origin


@dataclass
class Branch:
    """One two-terminal branch, in the single form both methods need."""
    name: str
    n1: str
    n2: str
    i: sp.Symbol                 # the classic answer symbol, i_<name>
    Z: Optional[sp.Expr]         # None for a current-source branch
    E: sp.Expr                   # series source term, drop n1->n2
    source_current: Optional[sp.Expr] = None   # set iff Z is None
    #: The engine's own equation for this branch, where it had one --
    #: shown verbatim rather than re-printed from Z and E.
    eq: Optional[sp.Eq] = None

    @property
    def is_current_source(self) -> bool:
        return self.Z is None

    @property
    def is_ideal_vsource(self) -> bool:
        return self.Z is not None and sp.simplify(self.Z) == 0


def close(expr, mapping: Dict[sp.Symbol, sp.Expr], rounds: int = 8):
    """Substitute until nothing changes.

    A dependent source's value may name another branch's current --
    `jd,1,2,i_ro/4` -- and that current is not one of either method's
    unknowns: nodal wants it in node voltages, mesh in mesh currents.
    One pass is usually enough, but a source controlled by a branch that
    is itself a controlled source needs another, so this runs to a fixed
    point (bounded, since a source controlled by its own current would
    otherwise spin here)."""
    for _ in range(rounds):
        stepped = expr.subs(mapping)
        if stepped == expr:
            return expr
        expr = stepped
    return expr


def linear_part(expr, sym):
    """d(expr)/d(sym), with a check that expr really is linear in it."""
    d = sp.diff(expr, sym)
    if d.has(sym):
        raise BranchError(M.E_BH_NOT_LINEAR)
    return d


def read_branches(circ: Circuit, origin: Dict[int, Element]) -> List[Branch]:
    """Every two-terminal branch as `v(n1) - v(n2) = Z*i + E`, read out
    of what the engine stamped. See the module docstring."""
    by_element: Dict[str, List[sp.Eq]] = {}
    for idx, el in origin.items():
        by_element.setdefault(el.name, []).append(circ.equations[idx])

    out: List[Branch] = []
    for el in circ.elements:
        if el.kind in ("m", "o") or el.kind in PORT_KINDS:
            continue                      # handled, or refused, elsewhere
        n1, n2 = el.n1, el.n2
        if n1 == n2:
            continue                      # degenerate self-loop
        i_sym = circ.i_symbol(el.name)
        u1, u2 = circ.v(n1), circ.v(n2)
        # A reference node's voltage is the literal 0, so that terminal
        # carries no coefficient and there is nothing to check it
        # against. With neither terminal on a reference, a genuine
        # two-terminal component must depend on the two *only as their
        # difference* -- and the check below is what tells one apart
        # from a dependent source that happens to sit between the same
        # two nodes. See the `y1 + y2` test further down: a VCCS reading
        # the voltage at its own left-hand node has y2 = 0, which reads
        # exactly like a component until you ask for the difference.
        spans = n1 not in circ.references and n2 not in circ.references
        eqs = by_element.get(el.name, [])

        if str(i_sym) in {str(u) for u in circ.unknowns}:
            # A branch with a free current: its one equation is its v-i
            # relation. (A voltage source's equation does not mention
            # the current at all, which is exactly Z = 0.)
            if len(eqs) != 1:
                raise BranchError(M.E_BH_ONE_RELATION, name=el.name)
            eq = eqs[0]
            f = sp.expand(eq.lhs - eq.rhs)
            a1 = linear_part(f, u1) if u1.free_symbols else sp.Integer(0)
            a2 = linear_part(f, u2) if u2.free_symbols else sp.Integer(0)
            b = linear_part(f, i_sym)
            c = f.subs({u1: 0, u2: 0, i_sym: 0})
            # Rewrite f in the branch drop u = v(n1) - v(n2). With one
            # terminal on a reference node that voltage is the literal
            # 0 and the other side carries the whole coefficient.
            # Otherwise substitute v(n1) = u + v(n2) (or the mirror when
            # it is n1 whose coefficient vanished): whatever dependence
            # is left over is *not* part of the component -- it is a
            # dependent source reading a node voltage, which belongs in
            # the source term E, not in a refusal. Rejecting it outright
            # turned away three circuits whose only sin was a VCVS whose
            # controlling node is one of its own terminals.
            if not spans:
                a = a1 if n2 in circ.references else -a2
                excess = sp.Integer(0)
            elif a1 != 0:
                a = a1
                excess = sp.expand((a1 + a2) * u2)
            else:
                a = -a2
                excess = sp.expand((a1 + a2) * u1)
            if a == 0:
                raise BranchError(M.E_BH_NO_OWN_NODES, name=el.name)
            out.append(Branch(el.name, n1, n2, i_sym,
                              Z=sp.simplify(-b / a),
                              E=sp.simplify(-(c + excess) / a),
                              eq=eq))
            continue

        # No free current: the engine recorded the current directly --
        # a capacitor (an admittance) or a current source (a constant).
        known = circ.known.get("i_" + el.name)
        if known is None:
            continue
        known = sp.expand(known)
        y1 = linear_part(known, u1) if u1.free_symbols else sp.Integer(0)
        y2 = linear_part(known, u2) if u2.free_symbols else sp.Integer(0)
        Y = y1 if y1 != 0 else -y2
        i0 = known.subs({u1: 0, u2: 0})
        if Y == 0 or (spans and sp.simplify(y1 + y2) != 0):
            # Either no dependence on its own drop at all (an
            # independent current source), or a dependence that is not
            # on the *difference* -- a dependent current source reading
            # a voltage elsewhere. Both are current sources whose value
            # happens to be an expression, not impedances.
            if Y == 0 and sp.simplify(i0) == 0:
                continue                  # an open branch: not in the graph
            out.append(Branch(el.name, n1, n2, i_sym, Z=None,
                              E=sp.Integer(0),
                              source_current=sp.simplify(known)))
            continue
        out.append(Branch(el.name, n1, n2, i_sym,
                          Z=sp.simplify(1 / Y), E=sp.simplify(-i0 / Y)))
    return out


def branches_of(elements: List[Element], domain: str, omega=None,
                suffix: str = "si",
                references: Sequence[str] = ()) -> List[Branch]:
    """Stamp `elements` in `domain` and hand back every two-terminal
    branch in the `v(n1) - v(n2) = Z*i + E` form. The one call most
    users want; `stamped` and `read_branches` are there for a caller
    that needs the `Circuit` as well."""
    circ, origin = stamped(elements, domain, omega, suffix, references)
    return read_branches(circ, origin)
