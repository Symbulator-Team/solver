"""#314: a transformer or two-port block written with all four terminals,
and the current into every one of them.

The calculator's form names the top terminal of each port and grounds
the other two; since #314 a node term may be a bracketed pair
[top,bottom], and then all four are named. The two-node form is the
paired form with both bottoms on 0, and the engine treats it exactly so
-- which is the first thing checked here.
"""

import pytest
import sympy as sp

from symbulator import ac, dc
from symbulator.elements import CircuitError, parse_circuit
from symbulator import messages as M
from symbulator.schematic import to_svg
from symbulator.spice import to_spice


def _same(a, b):
    for k in a.values:
        assert k in b.values, k
        assert sp.simplify(a.values[k] - b.values[k]) == 0, (k, a.values[k], b.values[k])
    assert set(a.values) == set(b.values)


# --- the two-node form is the paired form with zeros ---------------------

@pytest.mark.parametrize("two, four", [
    ("e1,1,0,10:r1,1,2,4:z1,2,3,[100,10,20,50]:r2,3,0,6",
     "e1,1,0,10:r1,1,2,4:z1,[2,0],[3,0],[100,10,20,50]:r2,3,0,6"),
    ("e1,1,0,10:r1,1,2,4:h1,2,3,[100,0.1,-2,0.01]:r2,3,0,6",
     "e1,1,0,10:r1,1,2,4:h1,[2,0],[3,0],[100,0.1,-2,0.01]:r2,3,0,6"),
    ("e1,1,0,10:t1,1,2,1,2:r1,2,0,4",
     "e1,1,0,10:t1,[1,0],[2,0],[1,2]:r1,2,0,4"),
    # the bracketed turns on the two-node form
    ("e1,1,0,10:t1,1,2,1,2:r1,2,0,4",
     "e1,1,0,10:t1,1,2,[1,2]:r1,2,0,4"),
    # the tacit parameter term, symbols and all
    ("e1,1,0,10:r1,1,2,4:y1,2,3:r2,3,0,6",
     "e1,1,0,10:r1,1,2,4:y1,[2,0],[3,0]:r2,3,0,6"),
])
def test_paired_form_with_grounded_bottoms_equals_two_node(two, four):
    _same(dc(two), dc(four))


def test_paired_transformer_equals_two_node_in_ac():
    two = ac("e1,1,0,10:t1,1,2,1,3:r1,2,0,4", omega=100)
    four = ac("e1,1,0,10:t1,[1,0],[2,0],[1,3]:r1,2,0,4", omega=100)
    _same(two, four)


# --- a port between two live nodes --------------------------------------

def test_transformer_primary_between_live_nodes():
    # Source 10 V into r0 = 1, then the primary (2 turns) between nodes
    # 2 and 4, node 4 returning to ground through r4 = 3. Secondary
    # (1 turn) between 3 and 5, loaded by r1 = 100 into node 5, which
    # references ground through r5 = 7 -- which closes no loop, so it
    # carries nothing. Reflected secondary: 100 * (2/1)^2 = 400 in
    # series with 1 + 3, so i = 10 / 404.
    res = dc("e1,1,0,10:r0,1,2,1:t1,[2,4],[3,5],[2,1]:r4,4,0,3:r1,3,5,100:r5,5,0,7")
    v = res.values
    i = sp.Rational(10, 404)
    assert v["i_r0"] == i
    assert v["i_r5"] == 0
    assert v["i_r1"] == 2 * i                          # 2:1 steps current up
    # the current into every terminal (Roberto, #314)
    assert v["i_t12"] == i and v["i_t14"] == -i         # primary in, out
    assert v["i_t13"] == -2 * i and v["i_t15"] == 2 * i  # secondary
    # winding voltages in the turns ratio, measured across each pair
    v1 = v["v_2"] - v["v_4"]
    v2 = v["v_3"] - v["v_5"]
    assert sp.simplify(v1 / 2 - v2 / 1) == 0
    # an ideal transformer neither makes nor uses power
    assert sp.simplify(v1 * i + v2 * (-2 * i)) == 0


def test_two_port_between_live_pairs_y_parameters():
    # y-parameters are the defining equations themselves, so the check
    # is direct: i1 = y11 v1 + y12 v2, i2 = y21 v1 + y22 v2, each v the
    # difference across its own pair.
    res = dc("e1,1,0,10:r0,1,2,3:y,[2,4],[3,5],[0.02,-0.01,-0.01,0.02]:"
             "r4,4,0,2:rl,3,5,200:r5,5,0,9")
    v = res.values
    v1 = v["v_2"] - v["v_4"]
    v2 = v["v_3"] - v["v_5"]
    i1, i2 = v["i_y2"], v["i_y3"]
    # decimal parameters solve as floats, so compare to a tolerance
    assert abs(float(i1 - (0.02 * v1 - 0.01 * v2))) < 1e-12
    assert abs(float(i2 - (-0.01 * v1 + 0.02 * v2))) < 1e-12
    # every terminal reports, and each pair sums to zero
    for top, bottom in (("2", "4"), ("3", "5")):
        assert abs(float(v[f"i_y{top}"] + v[f"i_y{bottom}"])) < 1e-12
    # KCL at node 4: what leaves the port there goes down r4
    assert abs(float(v["i_r4"] - i1)) < 1e-12


def test_paired_two_port_symbolic_parameters_stay_free():
    res = dc("e1,1,0,10:z,[1,3],[2,0]:r3,3,0,1:rl,2,0,5")
    syms = {str(s) for val in res.values.values() for s in val.free_symbols}
    assert {"z11", "z12", "z21", "z22"} <= syms


# --- the currents -------------------------------------------------------

def test_two_node_transformer_reports_both_currents_as_version_8_did():
    v = dc("e1,1,0,10:t1,1,2,1,2:r1,2,0,4").values
    assert v["i_t11"] == 10 and v["i_t12"] == -5
    assert "i_t10" not in v                       # ground reports nothing


def test_common_terminal_reports_the_sum():
    # A three-terminal block: both bottoms on node 3, which then carries
    # the total of the two port returns under the one name.
    v = dc("e1,1,0,10:z,[1,3],[2,3],[100,10,20,50]:r3,3,0,1:rl,2,0,5").values
    assert set(k for k in v if k.startswith("i_z")) == {"i_z1", "i_z2", "i_z3"}
    assert sp.simplify(v["i_z1"] + v["i_z2"] + v["i_z3"]) == 0
    # KCL at node 3: what enters the block there and what leaves down
    # r3 sum to zero
    assert sp.simplify(v["i_z3"] + v["i_r3"]) == 0


def test_primary_node_repeated_at_another_terminal():
    # top-left is also port 2's bottom: the free unknown steps aside and
    # the node's answer is the sum of what enters there.
    v = dc("e1,1,0,10:r0,1,2,1:t1,[2,4],[3,2],[1,1]:r4,4,0,3:r1,3,0,10").values
    assert {"i_t12", "i_t13", "i_t14"} <= set(v)
    assert sp.simplify(v["i_t12"] + v["i_t13"] + v["i_t14"]) == 0
    # the free unknown that stepped aside is the system's, not an answer
    assert not any(k.endswith("_p1") for k in v)
    v = dc("e,1,0,120:t,[1,0],[2,1],[80,120]:rl,2,0,8").values
    assert not any(k.endswith("_p1") for k in v)
    assert v["i_t1"] == sp.Rational(-1, 1) * v["i_e"]


# --- the rules -------------------------------------------------------

def test_secondary_side_with_no_ground_gets_its_own_reference():
    # #322 (Roberto, 7 Sep 2026): a side with no path to 0 is not a
    # mistake -- it is the far side of a port -- so it is given a
    # reference of its own, the port's bottom, and the answers say so.
    # Until #322 this raised E_FLOATING_NODES.
    res = dc("e1,1,0,10:t1,[1,0],[2,3],[1,2]:r1,2,3,4")
    assert res.references == {"3": ["2"]}
    assert res["v_3"] == 0 and res["v_2"] == 20 and res["i_r1"] == 5
    assert res.notes and res.notes[0]["code"] == M.N_LOCAL_REFERENCE
    assert res.notes[0]["args"] == {"nodes": "3, 2", "ref": "3"}
    assert res.notes[0]["severity"] == "warning"
    # the whole side is one island, however much hangs on it
    res = dc("e1,1,0,10:t1,[1,0],[2,3],[1,2]:r1,2,3,4:r2,3,6,1:r3,6,2,1")
    assert set(res.references["3"]) == {"2", "6"}
    # grounding any node of that side settles it as before, with no note
    res = dc("e1,1,0,10:t1,[1,0],[2,3],[1,2]:r1,2,3,4:r2,3,0,1")
    assert res.references == {} and res.notes == []


def test_two_port_side_with_no_ground_gets_its_own_reference():
    res = dc("e1,1,0,10:z,[1,0],[2,3],[100,10,20,50]:rl,2,3,5")
    assert res.references == {"3": ["2"]}
    # the load's drop is still i*R, measured against the island's own 0
    assert sp.simplify(res["v_2"] - res["v_3"] - 5 * res["i_rl"]) == 0


def test_an_island_of_ordinary_elements_is_still_a_mistake():
    with pytest.raises(CircuitError) as err:
        dc("e1,1,0,5:r1,1,0,1:r2,2,3,1")
    assert err.value.code == M.E_FLOATING_NODES


def test_port_shorted_on_itself_is_refused():
    for desc in ("e1,1,0,10:t1,[1,1],[2,0],[1,2]:r1,2,0,4",
                 "e1,1,0,10:z,[1,3],[2,2],[1,2,3,4]:r1,2,0,4:r3,3,0,1"):
        with pytest.raises(CircuitError) as err:
            dc(desc)
        assert err.value.code == M.E_PORT_SAME_NODE


def test_paired_form_allows_ground_on_a_top_terminal():
    # `z,[1,0],[2,0]` is the two-node form; `z,[0,1],[2,0]` is merely a
    # port wired upside down, and nothing forbids it.
    dc("e1,1,0,10:z,[0,1],[2,0],[100,10,20,50]:rl,2,0,5")


def test_two_node_form_still_forbids_ground_on_a_top_node():
    with pytest.raises(CircuitError) as err:
        dc("e1,1,0,10:z,1,0,[100,10,20,50]:rl,1,0,5")
    assert err.value.code == M.E_TOP_NODE_GROUND


@pytest.mark.parametrize("bad, code", [
    # the transformer's three forms, and nothing else
    ("t1,1,2,3,1,2", M.E_TERMS_TRANSFORMER),
    ("t1,1,2,[1,2,3]", M.E_TERMS_TRANSFORMER),
    ("t1,1,2,[1]", M.E_TERMS_TRANSFORMER),
    ("t1,[1,0],[2,0],1,2", M.E_TERMS_TRANSFORMER),    # pairs want [N1,N2]
    ("t1,1,2,3", M.E_TERMS_TRANSFORMER),
    # node terms both bare or both pairs, two entries each
    ("t1,[1,0],2,[1,2]", M.E_PORT_PAIR),
    ("z1,1,[2,0],[1,2,3,4]", M.E_PORT_PAIR),
    ("z1,[1,0,5],[2,0],[1,2,3,4]", M.E_PORT_PAIR),
    ("z1,[1],[2,0]", M.E_PORT_PAIR),
    # the two-port's counts and terms, unchanged
    ("z1,1,[1,2,3,4]", M.E_PORT_PAIR),        # one bare node, one pair
    ("z1,1,2,3,4", M.E_TERMS_TWO_PORT),
    ("z1,1,2,3", M.E_TWOPORT_LAST_TERM),
])
def test_wrong_shapes_are_refused(bad, code):
    with pytest.raises(CircuitError) as err:
        parse_circuit("e1,1,0,1:" + bad + ":r1,1,0,1")
    assert err.value.code == code


def test_brackets_still_refused_where_they_mean_nothing():
    with pytest.raises(CircuitError) as err:
        parse_circuit("e1,[1,0],0,1:r1,1,0,1")
    assert err.value.code == M.E_BRACKETS_MISUSED
    with pytest.raises(CircuitError) as err:
        parse_circuit("e1,1,0,1:r1,[1,2],0,1")
    assert err.value.code == M.E_BRACKETS_MISUSED


def test_node_names_fold_case_inside_the_pairs():
    els = parse_circuit("e1,A,0,1:t1,[A,C],[B,0],[1,2]:r1,B,C,1:r2,C,0,1")
    t = [e for e in els if e.name == "t1"][0]
    assert t.four_node and t.turns == ("1", "2")
    assert t.port_nodes == (("a", "c"), ("b", "0"))
    assert t.nodes == ["a", "c", "b", "0"]


def test_pair_entries_may_carry_their_own_parentheses():
    # a turns pair holding a parallel shorthand, split on top-level commas
    v = dc("e1,1,0,10:t1,1,2,[1,pr(4,4)]:r1,2,0,4").values
    assert v["i_t11"] == 10 and v["i_t12"] == -5


# --- the other consumers ---------------------------------------------

def test_spice_export_spans_the_named_pairs():
    net, warns = to_spice("e1,1,0,10:r0,1,2,1:t1,[2,4],[3,5],[2,1]:r4,4,0,3:"
                          "r1,3,5,100:r5,5,0,7")
    lines = [ln.split() for ln in net.splitlines() if ln and ln[0] in "EF"]
    e = [ln for ln in lines if ln[0].startswith("E")][0]
    f = [ln for ln in lines if ln[0].startswith("F")][0]
    assert e[2] == "5" and e[3:5] == ["2", "4"]      # E mid n2b n1 n1b
    assert f[1:3] == ["2", "4"]                       # F n1 n1b
    net, warns = to_spice("e1,1,0,10:y,[1,3],[2,4],[0.02,-0.01,-0.01,0.02]:"
                          "r3,3,0,1:r4,4,0,1:rl,2,0,5")
    g = [ln.split() for ln in net.splitlines() if ln.startswith("G")]
    assert g and all(len(ln) == 6 for ln in g)
    assert any(ln[1:5] == ["1", "3", "2", "4"] for ln in g)
    assert any("four-node" in w for w in warns)
    # an old description's netlist is unchanged in shape
    net, _ = to_spice("e1,1,0,10:r0,1,2,1:t1,2,3,2,1:r1,3,0,100")
    assert "Ft1 2 0 Vi_t1" in net


@pytest.mark.parametrize("desc", [
    # every four-terminal shape the drawer has a placement for
    "e1,1,0,10:r0,1,2,1:t1,[2,4],[3,5],[2,1]:r4,4,0,3:r1,3,5,100:r5,5,0,7",
    "e1,1,0,10:t1,[1,0],[2,3],[1,2]:r1,2,3,4:r2,3,0,1",
    "e1,1,0,10:r0,1,2,1:t1,[2,3],[4,3],[80,120]:r3,3,0,5:rl,4,0,8",
    "e1,1,0,10:r0,1,2,3:z,[2,4],[3,5],[100,10,20,50]:r4,4,0,2:rl,3,5,200:r5,5,0,9",
    "e1,1,0,10:z,[1,0],[2,3],[100,10,20,50]:rl,2,3,5:r3,3,0,1",
    "e1,1,0,10:z,[1,3],[2,3],[100,10,20,50]:r3,3,0,1:rl,2,0,5",
    "t1,[1,3],[2,4],[1,2]:e1,1,3,10:r3,3,0,1:rl,2,4,5:r4,4,0,2",
    # a bottom that is the other port's top: the autotransformer as one
    # tapped winding, whose lead goes round the far side of the block
    "e,1,0,120:t,[1,0],[2,1],[80,120]:rl,2,0,8",
    "e,1,0,10:z,[1,0],[2,3],[100,10,20,50]:rl,2,0,200:r3,3,0,20",
    "e,1,0,0.01:rs,1,2,1000:h,[2,3],[4,3],[1000,2.5e-4,100,25e-6]:re,3,0,100:rc,4,0,2000",
])
def test_drawer_draws_the_paired_forms(desc):
    svg = to_svg(desc)
    assert "<svg" in svg and "</svg>" in svg


def test_paired_form_with_grounded_bottoms_draws_as_two_node():
    # `z,[1,0],[2,0]` is `z,1,2` written out and must draw the same:
    # the rail cut at the block, two ground symbols and all.
    two = to_svg("e1,1,0,10:z,1,2,[100,10,20,50]:rl,2,0,200")
    four = to_svg("e1,1,0,10:z,[1,0],[2,0],[100,10,20,50]:rl,2,0,200")
    assert two == four
    two = to_svg("e1,1,0,10:t1,1,2,1,2:r1,2,0,4")
    four = to_svg("e1,1,0,10:t1,[1,0],[2,0],[1,2]:r1,2,0,4")
    assert two == four
    # and the bracketed turns on the two-node form draw as the bare ones
    assert two == to_svg("e1,1,0,10:t1,1,2,[1,2]:r1,2,0,4")
