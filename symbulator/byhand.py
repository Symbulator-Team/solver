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

from . import messages as M
from .branches import (Branch, BranchError, close as _close,
                       read_branches as _branches, stamped as _traced)
from .elements import Element, PORT_KINDS
from .engine import _sym


def _m(code: int, **args) -> Dict[str, object]:
    """One message in the shape the app already renders (#199/#200):
    the code, its arguments, and the catalogue's English beside them.

    The English travels so that a page which has never seen this code --
    a browser mid-deploy, an older offline build -- still shows a
    sentence rather than nothing, and so that a harness, a traceback or
    a downloaded `.txt` has something to print."""
    return {"code": code,
            "args": {k: str(v) for k, v in args.items()},
            "text": M.render(code, args)}


# --------------------------------------------------------------------
# What one line of a by-hand system is
# --------------------------------------------------------------------

@dataclass
class Row:
    """One written line: the equation, and the sentence a student would
    write beside it ("KCL at node 2", "supermesh around I1 and I2").

    `label` is a coded message, not a string -- see `_m`."""
    kind: str          # kcl | supernode | constraint | opamp | kvl |
                       # supermesh | mesh-constraint | bridge
    label: Dict[str, object]
    eq: sp.Eq

    @property
    def plain(self) -> str:
        return "{0} = {1}".format(self.eq.lhs, self.eq.rhs)

    @property
    def label_text(self) -> str:
        """The label's English, for a harness or a traceback."""
        return str(self.label.get("text", "")) if self.label else ""


@dataclass
class ByHand:
    """A by-hand system, or the reason there isn't one."""
    method: str                     # "nodal" | "mesh"
    domain: str
    supported: bool = True
    #: Why not, when `supported` is False: a coded message, or None.
    reason: Optional[Dict[str, object]] = None
    rows: List[Row] = field(default_factory=list)
    unknowns: List[sp.Symbol] = field(default_factory=list)
    #: How the by-hand unknowns become the classic answer names --
    #: `i_r3 = I1 - I2` for mesh, `i_r3 = (v_1 - v_2)/r3` for nodal.
    #: Shown to the reader *and* used by `compare`.
    bridge: List[Row] = field(default_factory=list)
    #: Meshes as ordered branch walks, for the drawing (mesh only):
    #: {"I1": [(element_name, +1|-1), ...]}.
    loops: Dict[str, List[Tuple[str, int]]] = field(default_factory=dict)
    #: The nodes whose KCL is written (nodal only), for the drawing.
    marked_nodes: List[str] = field(default_factory=list)
    #: Node sets enclosed together (nodal only), one per supernode.
    supernodes: List[List[str]] = field(default_factory=list)
    #: Mesh names merged into one equation (mesh only), one per
    #: supermesh.
    supermeshes: List[List[str]] = field(default_factory=list)
    notes: List[Dict[str, object]] = field(default_factory=list)

    @property
    def equations(self) -> List[sp.Eq]:
        return [r.eq for r in self.rows]

    @property
    def marks(self) -> Dict[str, object]:
        """What the schematic should draw over the circuit for this
        run: `schematic.to_svg(desc, marks=system.marks)`. One shape for
        both methods, so the caller does not branch on which it has."""
        return {"nodes": list(self.marked_nodes),
                "supernodes": [list(g) for g in self.supernodes],
                "loops": dict(self.loops),
                "supermeshes": [list(g) for g in self.supermeshes]}


#: The branch reader lives in its own module now (it is useful without
#: any of this, and is proposed to version 9 on its own). The name stays
#: for continuity: everything here raised and caught `ByHandError`.
ByHandError = BranchError


#: What a multi-terminal element is called in a refusal. The words are
#: the app's to translate, so they travel as a vocabulary key rather
#: than as prose (`srv.` in `i18n/en.json`, the same route the answer
#: labels take).
_NOT_TAUGHT = {"m": "mutual inductance", "t": "a transformer"}
_NOT_TAUGHT_DEFAULT = "a two-port parameter block"


def _refuse_for(elements: List[Element], method: str):
    """The coded reason a circuit is not one for this method, or None
    when it may go ahead.

    Since #332 a transformer, a two-port block or a coupled pair is no
    longer a refusal for *nodal*: it is carried the augmented way, its
    own relation standing as an extra equation beside the KCLs and its
    own current as an extra unknown, which is what a textbook does. Only
    two things are still refused, and both because the method genuinely
    does not apply rather than because this code cannot manage:

    * an op-amp in **mesh** -- its output current is supplied by the
      op-amp rather than circulating in a loop, so there is no mesh
      current to write. Every textbook uses nodal there;
    * mutually coupled coils in **nodal** -- a coupled coil's relation
      gives the *voltage* induced by another coil's current, which is a
      term in a loop equation. Nodal would have to invert a coupled pair
      to get either current in node voltages. Textbooks teach coupled
      coils in the mesh chapter for exactly this reason, so the refusal
      says so and points there.
    """
    kinds = {e.kind for e in elements}
    if method == "mesh" and "o" in kinds:
        return _m(M.E_BH_MESH_OPAMP)
    if method == "nodal" and "m" in kinds:
        return _m(M.E_BH_MUTUAL_USE_MESH)
    if method == "mesh":
        ports = sorted(kinds & set(PORT_KINDS))
        if ports:
            named = sorted({_NOT_TAUGHT.get(k, _NOT_TAUGHT_DEFAULT)
                            for k in ports})
            return _m(M.E_BH_PORT_USE_NODAL, what=", ".join(named))
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
                      reason=_m(exc.code, **exc.args_map))

    refs = set(circ.references)

    # #332: the elements the branch reader does not speak for -- a
    # transformer, a two-port parameter block, an op-amp -- and the
    # equations the engine wrote for each. They come in verbatim as
    # extra rows, and whatever unknowns they name come in with them.
    # This is the augmented method, and it is uniform: the code does not
    # know a transformer from a two-port, only that neither current can
    # be written in node voltages.
    by_element: Dict[str, List[sp.Eq]] = {}
    for idx, el in origin.items():
        by_element.setdefault(el.name, []).append(circ.equations[idx])
    special = [el for el in elements
               if el.kind in PORT_KINDS and el.kind != "m"]

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
            "opamp", _m(M.N_BH_OPAMP, name=el.name, node=n_out),
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
            label = _m(M.N_BH_KCL_NODE, node=live[0])
            kind = "kcl"
        else:
            label = _m(M.N_BH_KCL_SUPERNODE, nodes=", ".join(live))
            kind = "supernode"
            # The whole group is enclosed, including any node whose own
            # KCL was dropped for an op-amp: the enclosure is a picture
            # of which nodes the one equation covers, not of which of
            # them contributed a term.
            out.supernodes.append(sorted(nodes))
        out.marked_nodes.extend(live)
        out.rows.append(Row(kind, label, sp.Eq(total, 0, evaluate=False)))

    out.rows.extend(opamp_rows)

    for b in vsource:
        if b.n1 in refs or b.n2 in refs:
            fixed = b.n1 if b.n2 in refs else b.n2
            label = _m(M.N_BH_SOURCE_TO_REF, name=b.name, node=fixed)
        elif b.name in external:
            label = _m(M.N_BH_SOURCE_NAMED, name=b.name, a=b.n1, b=b.n2)
        else:
            label = _m(M.N_BH_SUPERNODE_TIE, name=b.name, a=b.n1, b=b.n2)
        out.rows.append(Row("constraint", label, constraint[b.name]))

    # #332: each special element's own relations, and the currents they
    # name. The KCLs above already carry those currents -- `stamp_all`
    # put them there -- so the system stays square: one extra equation
    # for one extra unknown, which is the whole trick of the augmented
    # method.
    special_unknowns: List[sp.Symbol] = []
    for el in special:
        for eq in by_element.get(el.name, []):
            shown = sp.Eq(sp.expand(eq.lhs.subs(subst)),
                          sp.expand(eq.rhs.subs(subst)), evaluate=False)
            out.rows.append(Row("element",
                                _m(M.N_BH_ELEMENT_EQ, name=el.name), shown))
            for sym in (shown.lhs - shown.rhs).free_symbols:
                if (str(sym).startswith("i_") and sym in circ.unknowns
                        and sym not in special_unknowns):
                    special_unknowns.append(sym)

    out.unknowns = [u for u in circ.unknowns if str(u).startswith("v_")]
    out.unknowns += [b.i for b in vsource if b.name in external]
    out.unknowns += [u for u in special_unknowns if u not in out.unknowns]

    # Backing out the branch currents is the last step of the method,
    # and it is also what lets the answers be compared.
    for b in branches:
        if b.i in subst:
            out.bridge.append(Row(
                "bridge", _m(M.N_BH_BRIDGE, name=b.name),
                sp.Eq(b.i, subst[b.i], evaluate=False)))
        elif b.is_current_source:
            # Known from the start, but worth writing down: it is one
            # more answer the classic solve reports, and one more thing
            # the comparison can hold the by-hand run to. A controlled
            # source's value may name another branch's current, which
            # has to come back to node voltages like everything else.
            out.bridge.append(Row(
                "bridge", _m(M.N_BH_BRIDGE, name=b.name),
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
            raise ByHandError(M.E_BH_LOOP_NOT_TRACED)
    if cur != start:
        raise ByHandError(M.E_BH_LOOP_NOT_CLOSED)
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
                          reason=_m(M.E_BH_NO_LOOP))
        walks = _orient([_walk(c, branches) for c in cycles])
    except ByHandError as exc:
        return ByHand(method="mesh", domain=domain, supported=False,
                      reason=_m(exc.code, **exc.args_map))

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

    # A branch that lies on no mesh at all -- the single element hanging
    # off a node nothing else touches, which is what an open port looks
    # like -- carries no current. Saying so is part of the method, and
    # not saying it left a dependent source that reads such a branch
    # (`e,3,0,1.5*is1` over a dangling `s1`) holding a free symbol that
    # nothing in the system ever bound. The classic solve answers 0 for
    # those, and so must this.
    stray = [k for k in range(len(branches)) if k not in in_branch]
    for k in stray:
        if branches[k].is_current_source:
            # A current source driving an open circuit. The mesh system
            # has no current to give it, and a contradiction dressed as
            # an equation is worse than a sentence.
            return ByHand(
                method="mesh", domain=domain, supported=False,
                reason=_m(M.E_BH_SOURCE_OFF_MESH, name=branches[k].name))
        i_map[branches[k].i] = sp.Integer(0)
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
            reason=_m(M.E_BH_NODE_CONTROLLED,
                      names=", ".join(n[2:] for n in stranded)))

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
            label = _m(M.N_BH_KVL_MESH, mesh=names[0])
            kind = "kvl"
        else:
            label = _m(M.N_BH_KVL_SUPERMESH, meshes=" and ".join(names))
            kind = "supermesh"
            out.supermeshes.append(list(names))
        for name in left:
            # A drop that would not eliminate: kept as an honest extra
            # unknown rather than a quietly wrong system. Added once
            # however many rows still carry it -- sympy refuses a
            # duplicated unknown outright.
            if _sym(name) in out.unknowns:
                continue
            out.notes.append(_m(M.N_BH_DROP_KEPT, name=name[2:]))
            out.unknowns.append(_sym(name))
        out.rows.append(Row(kind, label, sp.Eq(total, 0, evaluate=False)))

    # Each current source's own constraint.
    for b in sorted(drop_sym, key=lambda k: branches[k].name):
        br = branches[b]
        out.rows.append(Row(
            "mesh-constraint", _m(M.N_BH_MESH_CONSTRAINT, name=br.name),
            sp.Eq(in_branch[b],
                  sp.expand(_close(br.source_current, i_map)),
                  evaluate=False)))

    for k, br in enumerate(branches):
        if k in in_branch:
            out.bridge.append(Row(
                "bridge", _m(M.N_BH_BRIDGE, name=br.name),
                sp.Eq(br.i, sp.expand(in_branch[k]), evaluate=False)))
        else:
            out.bridge.append(Row(
                "bridge", _m(M.N_BH_BRIDGE_NO_MESH, name=br.name),
                sp.Eq(br.i, sp.Integer(0), evaluate=False)))

    # The same guard over the finished system: a constraint can strand
    # a node voltage where no KVL did.
    left_over = sorted({str(s) for row in out.rows
                        for s in (row.eq.lhs - row.eq.rhs).free_symbols
                        if str(s).startswith("v_")})
    if left_over:
        return ByHand(
            method="mesh", domain=domain, supported=False,
            reason=_m(M.E_BH_NODE_CONTROLLED,
                      names=", ".join(n[2:] for n in left_over)))

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
    #: A coded message (see `_m`), or None for a run with nothing to say.
    message: Optional[Dict[str, object]]
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
        raise ByHandError(int(bh.reason["code"]))
    sols = sp.solve(bh.equations, bh.unknowns, dict=True)
    if not sols:
        raise ByHandError(M.N_BH_UNSOLVED)
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
    except (ByHandError, Exception):
        # Any failure to solve is one sentence: the system was written,
        # it did not come out, and the classic answers stand. Which
        # exception it was is not the reader's business and is not
        # translatable prose.
        return Comparison("unsolved", _m(M.N_BH_UNSOLVED))

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
        return Comparison("unsure", _m(M.N_BH_NOTHING_CHECKED),
                          checks, solved)
    if any(c.verdict == "differs" for c in checks):
        names = ", ".join(c.name for c in checks if c.verdict == "differs")
        return Comparison("differs", _m(M.N_BH_DIFFERS, names=names),
                          checks, solved)
    if any(c.verdict == "unsure" for c in checks):
        return Comparison("unsure", _m(M.N_BH_UNSURE), checks, solved)
    return Comparison("agrees", _m(M.N_BH_AGREES, n=len(checks)),
                      checks, solved)


def shorter_route(nodal_system: ByHand, mesh_system: ByHand):
    """Which of the two methods writes fewer equations for this circuit,
    as a coded message -- or that one of them is not offered at all.

    Roberto asked, 8 Sep 2026: *how does the user know when to use nodal
    and when to use mesh?* Until now they found out by running one and
    reading the refusal. Counting is what a first course actually
    teaches -- take the method with fewer equations -- and building a
    system is cheap: it is the *solve* that costs, and this does not
    solve either of them."""
    if not nodal_system.supported and not mesh_system.supported:
        return None                         # both refusals speak for
    if not mesh_system.supported:           # themselves
        return _m(M.N_BH_NO_MESH_HERE)
    if not nodal_system.supported:
        return _m(M.N_BH_NO_NODAL_HERE)
    n, m = len(nodal_system.rows), len(mesh_system.rows)
    if n == m:
        return _m(M.N_BH_METHODS_EVEN, n=n)
    if m < n:
        return _m(M.N_BH_MESH_SHORTER, mesh=m, nodal=n)
    return _m(M.N_BH_NODAL_SHORTER, mesh=m, nodal=n)
