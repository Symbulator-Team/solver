"""By-hand circuit analysis (X14) -- Symbulator X only, experimental.

The classic Symbulator solve is untouched by everything in this module.
It has 27 years of history behind it and it stays the authority: what is
built here is a *second* system, written the way a student is taught to
write one, solved separately, and then checked against the classic
answers. Nothing in here can change a classic answer, and a failure in
here must never reach the solve above it.

Two methods, the two a first course teaches:

    nodal()   KCL at each node in the node voltages alone, with a
              supernode wherever a voltage source sits between two
              non-reference nodes.
    mesh()    KVL around each mesh in the mesh currents I1, I2, I3...,
              with a supermesh wherever a current source is shared.

Both produce the same node voltages and branch currents the classic
solve produces -- that is the whole point, and `compare()` is what
checks it.

Why this is not a second implementation of the circuit rules
------------------------------------------------------------
It would be easy, and wrong, to write out "a resistor's drop is R*i, a
capacitor's is i/(jwC), an inductor in FD is s*L*i - L*i0..." again in
here. That is the engine's knowledge, it is domain-dependent, and a
second copy of it would drift from the first the moment either changed.

So this module never states a component rule. It runs the real
`Circuit.stamp_all()`, then reads each branch's v-i relation back *out*
of the equations the engine produced, by differentiation:

    f(u, i) = lhs - rhs = 0,  linear in the branch drop u and current i
    Z = -(df/di) / (df/du)          E = -(f at u=0, i=0) / (df/du)

giving every branch in the one form both methods need,

    v(n1) - v(n2) = Z*i + E

for whatever the domain happens to be. A capacitor and a current source
have no equation of their own -- the engine records their current
directly in `Circuit.known` -- so those are read the same way from the
admittance side instead. Add a domain rule to the engine and it appears
here for free; change one and it changes here too.

What is refused, and why
------------------------
Transformers, two-port parameter blocks and mutual inductance are not
offered by either method: a first course does not teach nodal or mesh
analysis on them, and the coupled multi-terminal constraints they stamp
are not a branch v-i relation at all. Mesh additionally refuses op-amps,
whose output current is supplied by the op-amp rather than flowing round
a mesh. Every refusal is a plain sentence for the reader, never an
exception into the page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import sympy as sp

from .elements import Element, PORT_KINDS
from .engine import Circuit, _sym


# --------------------------------------------------------------------
# What one line of a by-hand system is
# --------------------------------------------------------------------

@dataclass
class Row:
    """One written line: the equation, and the sentence a student would
    write beside it ("KCL at node 2", "supermesh around I1 and I2")."""
    kind: str          # kcl | supernode | constraint | opamp | kvl |
                       # supermesh | mesh-constraint | bridge
    label: str
    eq: sp.Eq

    @property
    def plain(self) -> str:
        return "{0} = {1}".format(self.eq.lhs, self.eq.rhs)


@dataclass
class ByHand:
    """A by-hand system, or the reason there isn't one."""
    method: str                     # "nodal" | "mesh"
    domain: str
    supported: bool = True
    reason: str = ""                # why not, when supported is False
    rows: List[Row] = field(default_factory=list)
    unknowns: List[sp.Symbol] = field(default_factory=list)
    #: How the by-hand unknowns become the classic answer names --
    #: `i_r3 = I1 - I2` for mesh, `i_r3 = (v_1 - v_2)/r3` for nodal.
    #: Shown to the reader *and* used by `compare`.
    bridge: List[Row] = field(default_factory=list)
    #: Meshes as ordered branch walks, for the drawing (mesh only):
    #: {"I1": [(element_name, +1|-1), ...]}.
    loops: Dict[str, List[Tuple[str, int]]] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @property
    def equations(self) -> List[sp.Eq]:
        return [r.eq for r in self.rows]


class ByHandError(Exception):
    """A by-hand system could not be built. Always caught and turned
    into `ByHand(supported=False, reason=...)` at the entry points."""


# --------------------------------------------------------------------
# Reading the engine's own stamp back out
# --------------------------------------------------------------------

def _traced(elements: List[Element], domain: str, omega=None,
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


def _close(expr, mapping: Dict[sp.Symbol, sp.Expr], rounds: int = 8):
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


def _linear_part(expr, sym):
    """d(expr)/d(sym), with a check that expr really is linear in it."""
    d = sp.diff(expr, sym)
    if d.has(sym):
        raise ByHandError("a value here is not linear in the branch current")
    return d


def _branches(circ: Circuit, origin: Dict[int, Element]) -> List[Branch]:
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
                raise ByHandError(
                    el.name + " does not have a single branch relation")
            eq = eqs[0]
            f = sp.expand(eq.lhs - eq.rhs)
            a1 = _linear_part(f, u1) if u1.free_symbols else sp.Integer(0)
            a2 = _linear_part(f, u2) if u2.free_symbols else sp.Integer(0)
            b = _linear_part(f, i_sym)
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
                raise ByHandError(
                    el.name + "'s relation does not involve its own nodes")
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
        y1 = _linear_part(known, u1) if u1.free_symbols else sp.Integer(0)
        y2 = _linear_part(known, u2) if u2.free_symbols else sp.Integer(0)
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


def _refuse_for(elements: List[Element], method: str) -> Optional[str]:
    """The plain sentence for a circuit neither method should attempt,
    or None when it may go ahead."""
    kinds = {e.kind for e in elements}
    bad = sorted(kinds & (set(PORT_KINDS) | {"m"}))
    if bad:
        what = {"m": "mutual inductance", "t": "a transformer"}
        named = sorted({what.get(k, "a two-port parameter block")
                        for k in bad})
        return ("This circuit contains " + ", ".join(named) + ", which "
                + method + " analysis by hand is not taught for. The "
                "classic Symbulator answers above are unaffected.")
    if method == "mesh" and "o" in kinds:
        return ("This circuit contains an op-amp. Its output current is "
                "supplied by the op-amp rather than flowing round a mesh, "
                "so mesh analysis by hand does not apply. Nodal analysis "
                "does — try that instead.")
    return None


# --------------------------------------------------------------------
# Nodal analysis, with supernodes
# --------------------------------------------------------------------

def nodal(elements: List[Element], domain: str, omega=None,
          suffix: str = "si", references: Sequence[str] = ()) -> ByHand:
    """The node-voltage system a student would write.

    One KCL per node in the node voltages alone -- every branch current
    replaced by its own v-i relation solved for the current. A voltage
    source between two non-reference nodes has no such relation (its
    current is whatever the rest of the circuit demands), so the two
    nodes are enclosed in a supernode: one KCL for the enclosure, plus
    the source's own equation as the constraint that replaces the one
    that was lost. A source to a reference node needs no KCL at all --
    that node's voltage is known outright."""
    refusal = _refuse_for(elements, "nodal")
    if refusal:
        return ByHand(method="nodal", domain=domain, supported=False,
                      reason=refusal)
    out = ByHand(method="nodal", domain=domain)
    try:
        circ, origin = _traced(elements, domain, omega, suffix, references)
        branches = _branches(circ, origin)
    except ByHandError as exc:
        return ByHand(method="nodal", domain=domain, supported=False,
                      reason="A by-hand nodal system could not be built: "
                             + str(exc) + ".")

    refs = set(circ.references)

    # Branch currents that can be written in node voltages, and the
    # voltage-source branches that cannot.
    subst: Dict[sp.Symbol, sp.Expr] = {}
    vsource: List[Branch] = []
    for b in branches:
        if b.is_current_source:
            continue                       # already a number in the KCL sums
        if b.is_ideal_vsource:
            vsource.append(b)
            continue
        u = circ.v(b.n1) - circ.v(b.n2)
        subst[b.i] = sp.simplify((u - b.E) / b.Z)

    # Each node's KCL, taken from the equations rather than from
    # `Circuit.node_sum`. They are the same sum, but only the equations
    # get `stamp_all`'s closing pass over the quantities a dependent
    # source names -- a capacitor's current, another element's voltage
    # drop -- and a KCL still carrying `i_co` or `v_rx` is not in the
    # node voltages at all. `stamp_all` appends them last, one per node,
    # in `node_sum` order.
    first_kcl = len(circ.equations) - len(circ.node_sum)
    kcl: Dict[str, sp.Expr] = {}
    for offset, node in enumerate(circ.node_sum):
        eq = circ.equations[first_kcl + offset]
        kcl[node] = sp.expand(eq.lhs - eq.rhs)

    # An op-amp supplies its output node whatever current it needs, so
    # that node gets no KCL; the input equality takes its place.
    opamp_rows: List[Row] = []
    dropped: set = set()
    for el in elements:
        if el.kind != "o":
            continue
        n_plus, n_minus, n_out = el.fields[0], el.fields[1], el.fields[2]
        if n_out not in refs:
            dropped.add(n_out)
        opamp_rows.append(Row(
            "opamp",
            el.name + ": the inputs are held equal, and the output node "
            + n_out + " carries whatever current " + el.name
            + " supplies, so it gets no KCL",
            sp.Eq(circ.v(n_plus), circ.v(n_minus), evaluate=False)))

    # Supernodes: nodes tied together by a voltage source. A group that
    # reaches a reference node is known outright and writes no KCL.
    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Each voltage source's constraint, in the node voltages: the
    # engine's own equation with the branch currents it may name (a
    # current-controlled source) replaced by theirs.
    constraint: Dict[str, sp.Eq] = {}
    for b in vsource:
        eq = b.eq if b.eq is not None else sp.Eq(
            circ.v(b.n1) - circ.v(b.n2), b.E, evaluate=False)
        constraint[b.name] = sp.Eq(sp.expand(eq.lhs.subs(subst)),
                                   sp.expand(eq.rhs.subs(subst)),
                                   evaluate=False)

    # A voltage source's own current cancels when its two nodes are
    # enclosed together -- that is what makes a supernode work. It does
    # *not* cancel when something else names it: a current-controlled
    # source reading `i_e1` adds another `i_e1` to one of those very
    # nodes, and the two no longer come to zero. A student meets this by
    # not drawing the supernode at all -- writing both nodes' KCLs
    # separately and carrying the source's current as one more unknown --
    # and so does this.
    #
    # Which sources those are is decided by *forming the sum and looking*
    # rather than by guessing where the name might appear. Asking whether
    # `i_e1` turned up in some other node's KCL sounds equivalent and is
    # not: a source reading it can sit on one of the source's own two
    # nodes, where the test says no and the arithmetic says otherwise.
    # No built-in example has that shape, so only a written test caught
    # it. Splitting one source can strand another, hence the loop.
    external: set = set()
    groups: Dict[str, List[str]] = {}
    ref_roots: set = set()

    def regroup() -> None:
        parent.clear()
        for n in kcl:
            find(n)
        for r in refs:
            find(r)
        for b in vsource:
            if b.name not in external:
                union(b.n1, b.n2)
        groups.clear()
        for n in kcl:
            groups.setdefault(find(n), []).append(n)
        ref_roots.clear()
        ref_roots.update(find(r) for r in refs)

    for _ in range(len(vsource) + 1):
        regroup()
        stranded = set()
        for root, nodes in groups.items():
            if root in ref_roots:
                continue                    # not written, so nothing to strand
            total = sp.Integer(0)
            for n in nodes:
                if n not in dropped:
                    total += kcl[n]
            total = sp.expand(sp.expand(total).subs(subst))
            for b in vsource:
                if b.name not in external and total.has(b.i):
                    stranded.add(b.name)
        for name, eq in constraint.items():
            for b in vsource:
                if (b.name not in external and b.name != name
                        and (eq.lhs - eq.rhs).has(b.i)):
                    stranded.add(b.name)
        if not stranded:
            break
        external |= stranded
    else:                                   # pragma: no cover - defensive
        regroup()

    for root in sorted(groups, key=lambda r: sorted(groups[r])):
        nodes = sorted(groups[root])
        if root in ref_roots:
            continue                        # tied to a reference: known
        live = [n for n in nodes if n not in dropped]
        if not live:
            continue                        # every node here is an op-amp out
        total = sp.Integer(0)
        for n in live:
            total += kcl[n]
        total = sp.expand(sp.expand(total).subs(subst))
        if len(live) == 1:
            label = "KCL at node " + live[0]
            kind = "kcl"
        else:
            label = ("KCL around the supernode enclosing nodes "
                     + ", ".join(live))
            kind = "supernode"
        out.rows.append(Row(kind, label, sp.Eq(total, 0, evaluate=False)))

    out.rows.extend(opamp_rows)

    for b in vsource:
        if b.n1 in refs or b.n2 in refs:
            fixed = b.n1 if b.n2 in refs else b.n2
            label = b.name + " fixes node " + fixed + " against the reference"
        elif b.name in external:
            label = (b.name + "'s own equation. Its current is named "
                     "elsewhere in the circuit, so it is carried as an "
                     "unknown of its own and nodes " + b.n1 + " and "
                     + b.n2 + " keep their separate KCLs")
        else:
            label = (b.name + "'s own equation, the constraint that comes "
                     "with the supernode over " + b.n1 + " and " + b.n2)
        out.rows.append(Row("constraint", label, constraint[b.name]))

    out.unknowns = [u for u in circ.unknowns if str(u).startswith("v_")]
    out.unknowns += [b.i for b in vsource if b.name in external]

    # Backing out the branch currents is the last step of the method,
    # and it is also what lets the answers be compared.
    for b in branches:
        if b.i in subst:
            out.bridge.append(Row(
                "bridge", "the current through " + b.name,
                sp.Eq(b.i, subst[b.i], evaluate=False)))
        elif b.is_current_source:
            # Known from the start, but worth writing down: it is one
            # more answer the classic solve reports, and one more thing
            # the comparison can hold the by-hand run to. A controlled
            # source's value may name another branch's current, which
            # has to come back to node voltages like everything else.
            out.bridge.append(Row(
                "bridge", "the current through " + b.name,
                sp.Eq(b.i, sp.expand(_close(b.source_current, subst)),
                      evaluate=False)))
    return out


# --------------------------------------------------------------------
# Mesh analysis, with supermeshes
# --------------------------------------------------------------------

def _components(adj, nodes) -> int:
    seen, count = set(), 0
    for n in nodes:
        if n in seen:
            continue
        count += 1
        stack = [n]
        seen.add(n)
        while stack:
            cur = stack.pop()
            for nb, _ in adj[cur]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
    return count


def _is_cycle(picked: List[int], branches: List["Branch"]) -> bool:
    """Every vertex touched exactly twice, and the whole set connected --
    i.e. the branch set is one simple cycle, not two disjoint ones."""
    degree: Dict[str, int] = {}
    for k in picked:
        degree[branches[k].n1] = degree.get(branches[k].n1, 0) + 1
        degree[branches[k].n2] = degree.get(branches[k].n2, 0) + 1
    if any(d != 2 for d in degree.values()):
        return False
    seen = {branches[picked[0]].n1}
    changed = True
    while changed:
        changed = False
        for k in picked:
            a, b = branches[k].n1, branches[k].n2
            if a in seen and b not in seen:
                seen.add(b)
                changed = True
            elif b in seen and a not in seen:
                seen.add(a)
                changed = True
    return len(seen) == len(degree)


def _cycle_basis(branches: List["Branch"]) -> List[List[int]]:
    """A minimum-weight cycle basis of the branch multigraph, as lists
    of branch indices.

    For a planar graph a minimum cycle basis *is* the set of bounded
    faces -- the meshes -- which is what makes this the right basis to
    hand a student, and why it is worth the extra work over the
    fundamental cycles of a spanning tree (which are correct but need
    not look like anything anyone would draw). Horton's construction:
    for every vertex v and every branch (x, y), the cycle formed by the
    shortest path v->x, the branch, and the shortest path y->v; then
    take them shortest-first, keeping each one independent of those
    already kept (over GF(2), a branch set being a bit vector)."""
    nodes = sorted({b.n1 for b in branches} | {b.n2 for b in branches})
    if not nodes:
        return []
    adj: Dict[str, List[Tuple[str, int]]] = {n: [] for n in nodes}
    for k, b in enumerate(branches):
        adj[b.n1].append((b.n2, k))
        adj[b.n2].append((b.n1, k))

    dimension = len(branches) - len(nodes) + _components(adj, nodes)
    if dimension <= 0:
        return []

    # Shortest-path trees, each as {vertex: bitmask of branches used}.
    trees: Dict[str, Dict[str, int]] = {}
    for src in nodes:
        seen = {src: 0}
        queue = [src]
        while queue:
            nxt = []
            for cur in queue:
                for nb, k in adj[cur]:
                    if nb not in seen:
                        seen[nb] = seen[cur] ^ (1 << k)
                        nxt.append(nb)
            queue = nxt
        trees[src] = seen

    candidates = []
    for v in nodes:
        tree = trees[v]
        for k, b in enumerate(branches):
            if b.n1 not in tree or b.n2 not in tree:
                continue
            mask = tree[b.n1] ^ (1 << k) ^ tree[b.n2]
            if not mask:
                continue
            picked = [j for j in range(len(branches)) if mask >> j & 1]
            if _is_cycle(picked, branches):
                candidates.append((len(picked), mask))
    candidates.sort()

    basis: List[int] = []          # row-reduced, highest bit first
    chosen: List[int] = []
    seen_masks = set()
    for _, mask in candidates:
        if mask in seen_masks:
            continue
        seen_masks.add(mask)
        reduced = mask
        for row in basis:
            reduced = min(reduced, reduced ^ row)
        if reduced:
            basis.append(reduced)
            basis.sort(reverse=True)
            chosen.append(mask)
            if len(chosen) == dimension:
                break
    return [[k for k in range(len(branches)) if mask >> k & 1]
            for mask in chosen]


def _walk(cycle: List[int], branches: List["Branch"]) -> List[Tuple[int, int]]:
    """Order a cycle's branches into a closed walk, each with +1 when it
    is traversed from its own n1 to n2 and -1 when against."""
    remaining = list(cycle)
    first = remaining.pop(0)
    walk = [(first, 1)]
    start, cur = branches[first].n1, branches[first].n2
    while remaining:
        for pos, k in enumerate(remaining):
            b = branches[k]
            if b.n1 == cur:
                walk.append((k, 1))
                cur = b.n2
                remaining.pop(pos)
                break
            if b.n2 == cur:
                walk.append((k, -1))
                cur = b.n1
                remaining.pop(pos)
                break
        else:
            raise ByHandError("a mesh could not be traced as a single loop")
    if cur != start:
        raise ByHandError("a mesh did not close")
    return walk


def _orient(walks: List[List[Tuple[int, int]]]) -> List[List[Tuple[int, int]]]:
    """Flip whole meshes so that meshes sharing a branch traverse it in
    opposite directions -- the property adjacent faces of a planar
    drawing have, and what makes a supermesh read as the *sum* of its
    two loops.

    Decided by walking out from the first mesh and settling each one
    against the neighbours already fixed, rather than by one vote over
    every earlier mesh: a mesh sharing branches with several others can
    otherwise be outvoted by a mesh it does not touch. It is a
    2-colouring, so a mesh set that has no consistent orientation leaves
    some pair agreeing -- the elimination in `mesh` does not depend on
    getting this right, only the reading does."""
    out = [list(w) for w in walks]
    shared = {}
    for k, walk in enumerate(out):
        for b, _ in walk:
            shared.setdefault(b, []).append(k)

    settled = {0}
    queue = [0]
    while len(settled) < len(out):
        while queue:
            k = queue.pop(0)
            neighbours = {j for b, _ in out[k] for j in shared[b] if j != k}
            for j in sorted(neighbours - settled):
                agree = 0
                fixed = dict(out[k])
                for b, sign in out[j]:
                    if b in fixed:
                        agree += 1 if fixed[b] == sign else -1
                if agree > 0:               # same direction: flip it
                    out[j] = [(b, -s) for b, s in out[j]]
                settled.add(j)
                queue.append(j)
        # A mesh sharing no branch with anything settled so far: start
        # a fresh component from the lowest one left.
        rest = [k for k in range(len(out)) if k not in settled]
        if rest:
            settled.add(rest[0])
            queue.append(rest[0])
    return out


def _node_voltages(branches: List["Branch"], in_branch: Dict[int, sp.Expr],
                   references, i_map: Dict[sp.Symbol, sp.Expr]
                   ) -> Dict[sp.Symbol, sp.Expr]:
    """Every node voltage a mesh run can reach, written in mesh
    currents.

    A reference node is 0; from there, crossing a branch from a to b
    subtracts that branch's drop, `Z*i + E`, which is already in mesh
    currents. A current source's drop is not known, so those branches
    are not crossed -- a node reachable only through one keeps its
    symbol and the caller refuses the circuit.

    The drops are collected as equations and solved together rather than
    accumulated node by node, because a branch's own E may be a source
    controlled by a node voltage further along, which is not yet known
    when its turn comes."""
    adjacency: Dict[str, List[Tuple[str, int, int]]] = {}
    for k, branch in enumerate(branches):
        if branch.is_current_source or k not in in_branch:
            continue
        adjacency.setdefault(branch.n1, []).append((branch.n2, k, 1))
        adjacency.setdefault(branch.n2, []).append((branch.n1, k, -1))

    volts: Dict[str, sp.Symbol] = {}
    equations: List[sp.Eq] = []
    seen = set(references)
    frontier = sorted(references)
    while frontier:
        nxt = []
        for cur in frontier:
            for nb, k, direction in adjacency.get(cur, []):
                if nb in seen:
                    continue
                branch = branches[k]
                drop = branch.Z * in_branch[k] + branch.E
                here = volts.get(cur, sp.Integer(0))
                there = _sym("v_" + nb)
                volts[nb] = there
                # direction +1 means cur is this branch's n1, so the
                # drop is measured cur -> nb and takes v down by it.
                equations.append(sp.Eq(there, here - direction * drop))
                seen.add(nb)
                nxt.append(nb)
        frontier = sorted(nxt)

    if not equations:
        return {}
    try:
        solved = sp.solve(equations, list(volts.values()), dict=True)
    except Exception:
        return {}
    if not solved:
        return {}
    out: Dict[sp.Symbol, sp.Expr] = {}
    for sym, expr in solved[0].items():
        closed = _close(sp.expand(expr), i_map)
        if not any(str(s).startswith("v_") for s in closed.free_symbols):
            out[sym] = closed
    return out


def mesh(elements: List[Element], domain: str, omega=None,
         suffix: str = "si", references: Sequence[str] = ()) -> ByHand:
    """The mesh-current system a student would write.

    One KVL per mesh, in the mesh currents I1, I2, I3..., with every
    branch current written as the signed sum of the mesh currents that
    run through it. A current source shared by two meshes has no known
    drop, so the two are added into one supermesh -- the source's drop
    cancels -- and the source itself supplies the constraint that
    replaces the equation given up."""
    refusal = _refuse_for(elements, "mesh")
    if refusal:
        return ByHand(method="mesh", domain=domain, supported=False,
                      reason=refusal)
    out = ByHand(method="mesh", domain=domain)
    try:
        circ, origin = _traced(elements, domain, omega, suffix, references)
        branches = _branches(circ, origin)
        cycles = _cycle_basis(branches)
        if not cycles:
            return ByHand(method="mesh", domain=domain, supported=False,
                          reason="This circuit has no closed loop to write "
                                 "a mesh equation around.")
        walks = _orient([_walk(c, branches) for c in cycles])
    except ByHandError as exc:
        return ByHand(method="mesh", domain=domain, supported=False,
                      reason="A by-hand mesh system could not be built: "
                             + str(exc) + ".")

    mesh_syms = [_sym("I{0}".format(k + 1)) for k in range(len(walks))]
    out.unknowns = list(mesh_syms)

    # Each branch current as the signed sum of the meshes through it.
    in_branch: Dict[int, sp.Expr] = {}
    for k, walk in enumerate(walks):
        for b, sign in walk:
            in_branch[b] = in_branch.get(b, sp.Integer(0)) + sign * mesh_syms[k]

    # Every branch current, as the mesh currents through it: what a
    # controlled source's value has to be rewritten in before any of it
    # can be written down.
    i_map = {branches[k].i: in_branch[k] for k in in_branch}
    # ...and every node voltage likewise. Mesh analysis has no node
    # voltages of its own, but it can still reach one the way a student
    # does: walk from the reference to that node and add up the drops
    # along the way, each of which is already in mesh currents. Written
    # as one equation per node and solved together, since a drop may
    # itself be a source controlled by another node's voltage.
    i_map.update(_node_voltages(branches, in_branch, circ.references, i_map))

    # KVL round each mesh, with a current source's unknown drop carried
    # as a symbol so that adding two loops can visibly cancel it.
    drop_sym = {k: _sym("u_" + branches[k].name)
                for k in range(len(branches)) if branches[k].is_current_source}
    kvl: List[sp.Expr] = []
    for walk in walks:
        total = sp.Integer(0)
        for b, sign in walk:
            br = branches[b]
            if br.is_current_source:
                total += sign * drop_sym[b]
            else:
                total += sign * (br.Z * in_branch[b] + _close(br.E, i_map))
        kvl.append(sp.expand(total))

    stranded = sorted({str(s) for expr in kvl for s in expr.free_symbols
                       if str(s).startswith("v_")})
    if stranded:
        return ByHand(
            method="mesh", domain=domain, supported=False,
            reason=("A source in this circuit is controlled by "
                    + ", ".join(n[2:] for n in stranded)
                    + ", a node voltage. Mesh analysis works in mesh "
                      "currents and has no node voltage to give it, so "
                      "this circuit is one for nodal analysis instead."))

    # A current source's drop is unknown, and the method's job is to get
    # rid of it. Each `u_` appears in the KVL of every mesh whose loop
    # runs through that source -- two of them for a source between two
    # meshes, one for a source on the outer boundary. So:
    #
    #   in two loops -> add the two together. The drop appears once with
    #                   each sign and cancels: that sum *is* the
    #                   supermesh, and one equation is lost.
    #   in one loop  -> that loop's KVL is the only equation the drop
    #                   appears in, so it determines nothing else and is
    #                   dropped whole. The mesh current is fixed by the
    #                   source's own constraint instead.
    #
    # Either way the equation given up is paid for by the constraint the
    # source contributes below, and doing it one source at a time
    # handles a mesh that has both kinds on it.
    # One unknown drop costs exactly one equation, however many loops
    # run through the source. Take one of the loops carrying it as the
    # pivot, subtract that pivot from each of the others -- which is
    # where the supermesh comes from -- and then drop the pivot itself.
    #
    #   in two loops -> one row absorbs the other and the pivot goes:
    #                   the textbook supermesh, one equation lost.
    #   in one loop  -> that row is the pivot and simply goes. The mesh
    #                   current is fixed by the source's constraint.
    #
    # Eliminating pairwise instead spent one equation per *pair*, which
    # for a source shared by three loops threw away two and left a mesh
    # current no equation constrained -- an under-determined system that
    # still solved, returning answers in terms of I3.
    open_rows = [{"members": [k], "expr": kvl[k]} for k in range(len(walks))]
    for b in sorted(drop_sym, key=lambda k: branches[k].name):
        sym = drop_sym[b]
        holders = [r for r in open_rows if r["expr"].has(sym)]
        if not holders:
            continue
        pivot = holders[0]
        c_pivot = pivot["expr"].coeff(sym)
        if c_pivot == 0:
            continue                       # not linear in it; left to the note
        for row in holders[1:]:
            c_row = row["expr"].coeff(sym)
            if c_row == 0:
                continue
            # expr/c - pivot/c_pivot removes the drop whichever way
            # round the two loops happen to run it. Where they run it
            # opposite ways -- the orientation `_orient` aims for, and
            # what a planar drawing gives -- the two coefficients are
            # +1 and -1 and this is exactly the sum of the two loops a
            # textbook writes.
            combined = sp.expand(row["expr"] / c_row
                                 - pivot["expr"] / c_pivot)
            if combined.has(sym):
                continue                   # left to the note below
            row["members"] = row["members"] + pivot["members"]
            row["expr"] = combined
        open_rows.remove(pivot)

    for row in open_rows:
        total = sp.expand(row["expr"])
        names = [str(mesh_syms[k]) for k in row["members"]]
        left = sorted({str(s) for s in total.free_symbols
                       if str(s).startswith("u_")})
        if len(names) == 1:
            label = "KVL around mesh " + names[0]
            kind = "kvl"
        else:
            label = ("KVL around the supermesh formed by "
                     + " and ".join(names)
                     + " -- the shared current source's drop cancels")
            kind = "supermesh"
        for name in left:
            # A drop that would not eliminate: kept as an honest extra
            # unknown rather than a quietly wrong system. Added once
            # however many rows still carry it -- sympy refuses a
            # duplicated unknown outright.
            if _sym(name) in out.unknowns:
                continue
            out.notes.append(
                "The drop across " + name[2:] + " did not eliminate "
                "between the loops sharing it, so it is carried as an "
                "unknown of its own.")
            out.unknowns.append(_sym(name))
        out.rows.append(Row(kind, label, sp.Eq(total, 0, evaluate=False)))

    # Each current source's own constraint.
    for b in sorted(drop_sym, key=lambda k: branches[k].name):
        br = branches[b]
        out.rows.append(Row(
            "mesh-constraint",
            br.name + " sets the current in the branch it occupies",
            sp.Eq(in_branch[b],
                  sp.expand(_close(br.source_current, i_map)),
                  evaluate=False)))

    for k, br in enumerate(branches):
        if k in in_branch:
            out.bridge.append(Row(
                "bridge", "the current through " + br.name,
                sp.Eq(br.i, sp.expand(in_branch[k]), evaluate=False)))

    # The same guard over the finished system: a constraint can strand
    # a node voltage where no KVL did.
    left_over = sorted({str(s) for row in out.rows
                        for s in (row.eq.lhs - row.eq.rhs).free_symbols
                        if str(s).startswith("v_")})
    if left_over:
        return ByHand(
            method="mesh", domain=domain, supported=False,
            reason=("A source in this circuit is controlled by "
                    + ", ".join(n[2:] for n in left_over)
                    + ", a node voltage. Mesh analysis works in mesh "
                      "currents and has no node voltage to give it, so "
                      "this circuit is one for nodal analysis instead."))

    out.loops = {str(mesh_syms[k]): [(branches[b].name, s) for b, s in walk]
                 for k, walk in enumerate(walks)}
    return out


# --------------------------------------------------------------------
# Solving the by-hand system, and checking it against the classic one
# --------------------------------------------------------------------

@dataclass
class Check:
    """One classic answer, beside what the by-hand system made of it."""
    name: str
    classic: sp.Expr
    byhand: sp.Expr
    verdict: str        # "agrees" | "differs" | "unsure"
    how: str = ""       # "exact" | "numeric" | why it was inconclusive


@dataclass
class Comparison:
    """The verdict on a whole by-hand run.

    Three states, never two. A symbolic circuit can leave `simplify`
    unable to show a difference is zero, and reporting that as a
    disagreement would accuse a correct by-hand system of being wrong --
    which would make the whole card untrustworthy. So an inconclusive
    comparison says so."""
    verdict: str                    # agrees | differs | unsure | unsolved
    message: str
    checks: List[Check] = field(default_factory=list)
    solution: Dict[str, sp.Expr] = field(default_factory=dict)

    @property
    def differing(self) -> List[Check]:
        return [c for c in self.checks if c.verdict == "differs"]


def solve(bh: ByHand) -> Dict[sp.Symbol, sp.Expr]:
    """Solve a by-hand system for its own unknowns. Raises ByHandError
    when it has no single solution -- the caller turns that into a
    message rather than letting it reach the page."""
    if not bh.supported:
        raise ByHandError(bh.reason)
    sols = sp.solve(bh.equations, bh.unknowns, dict=True)
    if not sols:
        raise ByHandError("the system has no solution")
    return sols[0]


def _same(a: sp.Expr, b: sp.Expr, trials: int = 4) -> Tuple[bool, str]:
    """Are two answers the same number or the same expression?

    Exact first. Only when `simplify` cannot close it does this fall
    back to evaluating both at random values for whatever symbols are
    left -- and a disagreement is reported only when the numbers really
    do differ, never merely because the algebra was too hard."""
    try:
        diff = sp.simplify(sp.expand(a - b))
    except Exception:
        return False, "the difference could not be simplified"
    if diff == 0:
        return True, "exact"
    try:
        if diff.is_number and complex(sp.N(diff)) == 0:
            return True, "exact"
    except (TypeError, ValueError):
        pass

    free = sorted(diff.free_symbols, key=str)
    if not free:
        try:
            return abs(complex(sp.N(diff))) < 1e-9, "numeric"
        except (TypeError, ValueError):
            return False, "the difference did not evaluate to a number"

    import random
    rng = random.Random(20260908)
    agreed = 0
    for _ in range(trials):
        point = {s: sp.Rational(rng.randint(2, 97), rng.randint(1, 7))
                 for s in free}
        try:
            value = complex(sp.N(diff.subs(point)))
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if value != value:                 # NaN: this point told us nothing
            continue
        if abs(value) > 1e-7:
            return False, "numeric"
        agreed += 1
    if agreed:
        return True, "numeric"
    return False, "no test point could be evaluated"


def compare(bh: ByHand, classic: Dict[str, sp.Expr]) -> Comparison:
    """Solve the by-hand system and check every answer it produces
    against the classic solve's answer of the same name.

    The classic values are the authority throughout: this reports on the
    by-hand system, never the other way round."""
    if not bh.supported:
        return Comparison("unsupported", bh.reason)
    try:
        solution = solve(bh)
    except ByHandError as exc:
        return Comparison(
            "unsolved",
            "The by-hand system was written, but solving it did not "
            "succeed: " + str(exc) + ". The classic answers above stand.")
    except Exception as exc:                # pragma: no cover - defensive
        return Comparison(
            "unsolved",
            "The by-hand system was written, but solving it did not "
            "succeed (" + type(exc).__name__ + "). The classic answers "
            "above stand.")

    values: Dict[sp.Symbol, sp.Expr] = dict(solution)
    for row in bh.bridge:
        try:
            values[row.eq.lhs] = sp.expand(row.eq.rhs.subs(solution))
        except Exception:
            continue

    checks: List[Check] = []
    for name in sorted(classic):
        sym = sp.Symbol(name)
        if sym not in values:
            continue                        # the method does not produce it
        got, want = values[sym], classic[name]
        ok, how = _same(got, want)
        checks.append(Check(name, want, got,
                            "agrees" if ok else
                            ("differs" if how == "numeric" else "unsure"),
                            how))

    solved = {str(k): v for k, v in solution.items()}
    if not checks:
        return Comparison("unsure",
                          "The by-hand system solved, but it produced none "
                          "of the quantities the classic solve reports, so "
                          "there was nothing to check it against.",
                          checks, solved)
    if any(c.verdict == "differs" for c in checks):
        names = ", ".join(c.name for c in checks if c.verdict == "differs")
        return Comparison(
            "differs",
            "The by-hand answers do not match the classic solve for "
            + names + ". The classic answers above are the ones to "
            "trust; the by-hand system is the one at fault.",
            checks, solved)
    if any(c.verdict == "unsure" for c in checks):
        return Comparison(
            "unsure",
            "The by-hand answers could not be shown equal to the classic "
            "ones by algebra, and no numerical test point settled it "
            "either. This is not a disagreement -- it is an unproven "
            "match.",
            checks, solved)
    return Comparison(
        "agrees",
        "Every one of the " + str(len(checks)) + " quantities the by-hand "
        "system produces matches the classic Symbulator solve.",
        checks, solved)
