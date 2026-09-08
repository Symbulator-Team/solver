"""X14: the by-hand nodal and mesh systems, and the check that holds
them to the classic solve.

The classic Symbulator solve is the authority throughout. What is tested
here is that a system written the way a first course teaches -- KCL in
node voltages with supernodes, KVL in mesh currents with supermeshes --
arrives at the same answers, and that when it cannot be written the
refusal is a sentence rather than an exception.

`server/tools/check_byhand.py` runs the same check over every built-in
example; this module holds the shapes worth naming.
"""

import pytest
import sympy as sp

from symbulator import ac, byhand, dc, fd
from symbulator import messages as M
from symbulator.elements import parse_circuit


def _run(desc, method, domain="dc", **kw):
    """Build the by-hand system and compare it with the classic solve."""
    solver = {"dc": dc, "ac": ac, "fd": fd}[domain]
    classic = solver(desc, **kw) if domain != "dc" else dc(desc)
    elements = parse_circuit(desc)
    build = getattr(byhand, method)
    system = build(elements, domain,
                   omega=sp.sympify(kw["omega"]) if kw.get("omega") else None,
                   references=tuple(classic.references or ()))
    return system, byhand.compare(system, classic.values)


# --- circuits both methods must get right -------------------------------

LADDER = "e1,1,0,12\nr1,1,2,4\nr2,2,0,6\nr3,2,0,12"
SUPERNODE = "j1,0,1,2\nr1,1,0,4\ne1,1,2,6\nr2,2,0,8"
SUPERMESH = "e1,1,0,20\nr1,1,2,2\nj1,2,3,3\nr2,2,0,4\nr3,3,0,6"
BRIDGE = ("e1,1,0,10\nr1,1,2,100\nr2,1,3,200\nr3,2,3,300\n"
          "r4,2,0,400\nr5,3,0,500")
CCVS = "e1,1,0,6\nr1,1,2,2\ne2,2,0,3*i_r1"
VCCS = "j1,0,1,1\nr1,1,0,4\nj2,1,2,2*v_1\nr2,2,0,5"
SYMBOLIC = "e1,1,0,vs\nr1,1,2,ra\nr2,2,0,rb"


@pytest.mark.parametrize("desc", [LADDER, SUPERNODE, SUPERMESH, BRIDGE,
                                  CCVS, VCCS, SYMBOLIC])
@pytest.mark.parametrize("method", ["nodal", "mesh"])
def test_agrees_with_the_classic_solve(desc, method):
    system, verdict = _run(desc, method)
    assert system.supported, system.reason
    assert verdict.verdict == "agrees", verdict.message
    assert verdict.message["code"] == M.N_BH_AGREES
    assert verdict.checks, "nothing was actually compared"


@pytest.mark.parametrize("method", ["nodal", "mesh"])
def test_agrees_in_ac_and_fd(method):
    rlc = "e1,1,0,10\nr1,1,2,5\nl1,2,3,.1\nc1,3,0,1'u"
    for domain, extra in (("ac", {"omega": 377}), ("fd", {})):
        system, verdict = _run(rlc, method, domain=domain, **extra)
        assert system.supported, system.reason
        assert verdict.verdict == "agrees", (domain, verdict.message)


# --- the methods' own shapes --------------------------------------------

def test_a_source_between_two_nodes_makes_a_supernode():
    system, _ = _run(SUPERNODE, "nodal")
    kinds = [row.kind for row in system.rows]
    assert "supernode" in kinds
    assert "constraint" in kinds
    # One enclosure, one constraint: the equation the supernode gives up
    # is paid for by the source's own.
    assert kinds.count("supernode") == 1
    assert sum(1 for k in kinds if k in ("kcl", "supernode")) == 1


def test_a_shared_current_source_makes_a_supermesh():
    system, _ = _run(SUPERNODE, "mesh")
    kinds = [row.kind for row in system.rows]
    assert "supermesh" in kinds
    assert "mesh-constraint" in kinds


def test_a_boundary_current_source_drops_its_mesh_equation():
    """A current source on one mesh only fixes that mesh current
    outright; the loop's KVL is not written at all, and the source's
    drop never becomes an unknown."""
    system, verdict = _run(SUPERMESH, "mesh")
    assert verdict.verdict == "agrees", verdict.message
    assert not any(str(u).startswith("u_") for u in system.unknowns)
    assert any(row.kind == "mesh-constraint" for row in system.rows)


def test_a_branch_on_no_mesh_carries_no_current():
    """An element hanging off a node nothing else touches -- what an
    open port looks like -- lies on no loop, so no current flows in it.

    Saying so is part of the method. Not saying it left a dependent
    source that *reads* such a branch holding a symbol nothing in the
    system ever bound, and the by-hand answers then disagreed with the
    classic solve. Found by sweeping every circuit in the example book
    rather than only the entries a by-hand run is offered for -- both
    circuits that showed it are `er`/`port` entries, which the ordinary
    sweep never reaches."""
    desc = "e,3,0,1.5*i_s1\nr3,3,2,3\nr2,2,0,2\ns1,2,1"
    system, verdict = _run(desc, "mesh")
    assert system.supported, system.reason
    assert verdict.verdict == "agrees", verdict.message
    written = {str(r.eq.lhs): r.eq.rhs for r in system.bridge}
    assert written["i_s1"] == 0
    # ...and nothing in the system is left in terms of that current.
    for row in system.rows:
        assert sp.Symbol("i_s1") not in (row.eq.lhs - row.eq.rhs).free_symbols


def test_a_current_source_on_no_mesh_is_refused_not_guessed():
    """A current source feeding a branch that goes nowhere has no mesh
    current to set. A sentence beats a contradiction dressed as an
    equation.

    The circuit has to stay *connected* to reach this: a source into a
    genuinely floating piece is refused by the parser first, so the
    dangling end here hangs off the source rather than the source
    hanging off nothing."""
    desc = "e1,1,0,10\nr1,1,0,5\nj1,1,2,2\nr2,2,3,3"
    system = byhand.mesh(parse_circuit(desc), "dc")
    assert not system.supported
    assert system.reason["code"] == M.E_BH_SOURCE_OFF_MESH
    assert "no mesh passes through" in system.reason["text"]
    assert not system.rows


def test_mesh_unknowns_are_named_for_the_drawing():
    system, _ = _run(BRIDGE, "mesh")
    assert [str(u) for u in system.unknowns] == ["I1", "I2", "I3"]
    assert set(system.loops) == {"I1", "I2", "I3"}
    for walk in system.loops.values():
        assert walk and all(sign in (1, -1) for _, sign in walk)


def test_nodal_unknowns_are_node_voltages_only():
    system, _ = _run(BRIDGE, "nodal")
    assert all(str(u).startswith("v_") for u in system.unknowns)


def test_the_system_is_square():
    for desc in (LADDER, SUPERNODE, SUPERMESH, BRIDGE):
        for method in ("nodal", "mesh"):
            system, _ = _run(desc, method)
            assert len(system.rows) == len(system.unknowns), (desc, method)


def test_the_bridge_names_classic_answers():
    """The bridge is what makes the two systems comparable: every line
    is a classic answer name written in the by-hand unknowns."""
    system, _ = _run(LADDER, "mesh")
    written = {str(row.eq.lhs) for row in system.bridge}
    assert {"i_r1", "i_r2", "i_r3"} <= written
    for row in system.bridge:
        assert all(str(s).startswith("I")
                   for s in row.eq.rhs.free_symbols), row.plain


def test_a_source_whose_current_is_named_keeps_both_kcls():
    """A current-controlled source reading a voltage source's current
    stops that source getting a supernode -- the current cannot cancel
    if something else names it -- and it becomes an unknown instead."""
    desc = "j1,0,1,2\nr1,1,0,4\ne1,1,2,6\nr2,2,0,8\nj2,0,2,3*i_e1"
    system, verdict = _run(desc, "nodal")
    assert system.supported, system.reason
    assert verdict.verdict == "agrees", verdict.message
    assert any(str(u) == "i_e1" for u in system.unknowns)
    assert not any(row.kind == "supernode" for row in system.rows)


# --- what is refused, and how -------------------------------------------

@pytest.mark.parametrize("desc, method", [
    ("e1,1,0,10\nt1,1,2,[1,2]\nr1,2,0,50", "nodal"),
    ("e1,1,0,10\nt1,1,2,[1,2]\nr1,2,0,50", "mesh"),
    ("e1,1,0,10\nr1,1,2,5\nz1,2,3,[1,2,3,4]\nr2,3,0,5", "nodal"),
    ("e1,1,0,1\nl1,1,2,1\nl2,3,0,1\nm1,l1,l2,0.5\nr1,2,0,1\nr2,3,0,1",
     "mesh"),
])
def test_refusals_are_sentences_not_exceptions(desc, method):
    system = getattr(byhand, method)(parse_circuit(desc), "dc")
    assert not system.supported
    # A code, not a sentence: the package returns structured messages
    # and the app puts them into words (#199). The English travels
    # beside the code for a traceback and for a page that has never seen
    # it, and is still a whole sentence.
    assert system.reason["code"] == M.E_BH_NOT_TAUGHT_FOR
    assert system.reason["text"].endswith(".")
    assert not system.rows
    # A refusal reaches `compare` as a verdict, never as a raise.
    assert byhand.compare(system, {}).verdict == "unsupported"


def test_mesh_refuses_an_op_amp_and_says_to_use_nodal():
    desc = "e1,1,0,1\nr1,1,2,1'k\nr2,2,3,10'k\no1,0,2,3"
    system = byhand.mesh(parse_circuit(desc), "dc")
    assert not system.supported
    assert system.reason["code"] == M.E_BH_MESH_OPAMP
    assert "op-amp" in system.reason["text"]
    assert "nodal" in system.reason["text"].lower()


def test_nodal_handles_an_op_amp():
    """The output node carries whatever the op-amp supplies, so it gets
    no KCL; the input equality takes its place."""
    desc = "e1,1,0,1\nr1,1,2,1'k\nr2,2,3,10'k\no1,0,2,3"
    system, verdict = _run(desc, "nodal")
    assert system.supported, system.reason
    assert verdict.verdict == "agrees", verdict.message
    assert any(row.kind == "opamp" for row in system.rows)


# --- the comparison itself ----------------------------------------------

def test_a_wrong_system_is_reported_as_differing():
    system, _ = _run(LADDER, "nodal")
    broken = byhand.ByHand(
        method="nodal", domain="dc",
        rows=[byhand.Row(r.kind, r.label, r.eq) for r in system.rows],
        unknowns=list(system.unknowns), bridge=list(system.bridge))
    # Change one coefficient: the answers must move, and be caught.
    first = broken.rows[0]
    broken.rows[0] = byhand.Row(first.kind, first.label,
                                sp.Eq(first.eq.lhs + sp.Symbol("v_2"), 0))
    verdict = byhand.compare(broken, dc(LADDER).values)
    assert verdict.verdict == "differs"
    assert verdict.message["code"] == M.N_BH_DIFFERS
    assert verdict.differing


def test_an_unsolvable_system_says_so_rather_than_raising():
    empty = byhand.ByHand(
        method="nodal", domain="dc",
        rows=[byhand.Row("kcl", {"code": M.N_BH_KCL_NODE, "args": {},
                                 "text": "impossible"},
                         sp.Eq(sp.Integer(1), 0))],
        unknowns=[sp.Symbol("v_1")])
    verdict = byhand.compare(empty, {"v_1": sp.Integer(0)})
    assert verdict.verdict == "unsolved"
    assert verdict.message["code"] == M.N_BH_UNSOLVED
    assert "classic answers above stand" in verdict.message["text"]


def test_a_symbolic_circuit_still_compares():
    system, verdict = _run(SYMBOLIC, "nodal")
    assert verdict.verdict == "agrees", verdict.message
    assert any(c.how in ("exact", "numeric") for c in verdict.checks)


# --- the messages themselves (#329) -------------------------------------

def test_every_row_and_verdict_carries_a_code():
    """Nothing the package says reaches the app as bare prose. A code is
    what the thirteen languages are keyed on, so a label or a verdict
    that forgot one would be permanently English."""
    for desc, method in ((SUPERNODE, "nodal"), (SUPERMESH, "mesh"),
                         (BRIDGE, "nodal"), (BRIDGE, "mesh")):
        system, verdict = _run(desc, method)
        for row in list(system.rows) + list(system.bridge):
            assert isinstance(row.label, dict), row
            assert row.label["code"] in M.CATALOGUE
            assert row.label["text"]
        assert verdict.message["code"] in M.CATALOGUE


def test_the_shorter_route_is_named():
    """Roberto, 8 Sep 2026: how does a reader know which method to use?
    By being told which writes fewer equations -- built, never solved."""
    els = parse_circuit(BRIDGE)
    n, m = byhand.nodal(els, "dc"), byhand.mesh(els, "dc")
    said = byhand.shorter_route(n, m)
    assert said["code"] in (M.N_BH_MESH_SHORTER, M.N_BH_NODAL_SHORTER,
                            M.N_BH_METHODS_EVEN)
    # The bridge circuit is three meshes and three live nodes.
    assert said["args"].get("mesh") == "3" or said["args"].get("n") == "3"


def test_a_method_that_is_not_offered_is_named_as_such():
    els = parse_circuit("e1,1,0,1\nr1,1,2,1000\nr2,2,3,10000\no1,0,2,3")
    said = byhand.shorter_route(byhand.nodal(els, "dc"),
                                byhand.mesh(els, "dc"))
    assert said["code"] == M.N_BH_NO_MESH_HERE
