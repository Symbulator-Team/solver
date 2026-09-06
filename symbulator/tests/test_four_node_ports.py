"""X2: a transformer or two-port block written with all four terminals.

The calculator's form names the top terminal of each port and grounds
the other two; version X also takes `name,n1,n2,n1b,n2b,...`. The
two-node form is the four-node form with both bottoms on 0, and the
engine treats it exactly so -- which is the first thing checked here.
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


# --- the two-node form is the four-node form with zeros ----------------

@pytest.mark.parametrize("two, four", [
    ("e1,1,0,10:r1,1,2,4:z1,2,3,[100,10,20,50]:r2,3,0,6",
     "e1,1,0,10:r1,1,2,4:z1,2,3,0,0,[100,10,20,50]:r2,3,0,6"),
    ("e1,1,0,10:r1,1,2,4:h1,2,3,[100,0.1,-2,0.01]:r2,3,0,6",
     "e1,1,0,10:r1,1,2,4:h1,2,3,0,0,[100,0.1,-2,0.01]:r2,3,0,6"),
    ("e1,1,0,10:t1,1,2,1,2:r1,2,0,4",
     "e1,1,0,10:t1,1,2,0,0,1,2:r1,2,0,4"),
    # the tacit parameter term, symbols and all
    ("e1,1,0,10:r1,1,2,4:y1,2,3:r2,3,0,6",
     "e1,1,0,10:r1,1,2,4:y1,2,3,0,0:r2,3,0,6"),
])
def test_four_node_with_grounded_bottoms_equals_two_node(two, four):
    _same(dc(two), dc(four))


def test_four_node_transformer_equals_two_node_in_ac():
    two = ac("e1,1,0,10:t1,1,2,1,3:r1,2,0,4", omega=100)
    four = ac("e1,1,0,10:t1,1,2,0,0,1,3:r1,2,0,4", omega=100)
    _same(two, four)


# --- a port between two live nodes --------------------------------------

def test_transformer_primary_between_live_nodes():
    # Source 10 V into r0 = 1, then the primary (2 turns) between nodes
    # 2 and 4, node 4 returning to ground through r4 = 3. Secondary
    # (1 turn) between 3 and 5, loaded by r1 = 100 into node 5, which
    # references ground through r5 = 7 -- which closes no loop, so it
    # carries nothing. Reflected secondary: 100 * (2/1)^2 = 400 in
    # series with 1 + 3, so i = 10 / 404.
    res = dc("e1,1,0,10:r0,1,2,1:t1,2,3,4,5,2,1:r4,4,0,3:r1,3,5,100:r5,5,0,7")
    i = sp.Rational(10, 404)
    assert res.values["i_r0"] == i
    assert res.values["i_r5"] == 0
    assert res.values["i_t12"] == i                    # into the top of port 1
    assert res.values["i_r1"] == 2 * i                 # 2:1 steps current up
    # winding voltages in the turns ratio, measured across each pair
    v1 = res.values["v_2"] - res.values["v_4"]
    v2 = res.values["v_3"] - res.values["v_5"]
    assert sp.simplify(v1 / 2 - v2 / 1) == 0
    # an ideal transformer neither makes nor uses power
    assert sp.simplify(v1 * i + v2 * (-2 * i)) == 0


def test_two_port_between_live_pairs_y_parameters():
    # y-parameters are the defining equations themselves, so the check
    # is direct: i1 = y11 v1 + y12 v2, i2 = y21 v1 + y22 v2, each v the
    # difference across its own pair.
    res = dc("e1,1,0,10:r0,1,2,3:y,2,3,4,5,[0.02,-0.01,-0.01,0.02]:"
             "r4,4,0,2:rl,3,5,200:r5,5,0,9")
    v = res.values
    v1 = v["v_2"] - v["v_4"]
    v2 = v["v_3"] - v["v_5"]
    i1, i2 = v["i_y2"], v["i_y3"]
    # decimal parameters solve as floats, so compare to a tolerance
    assert abs(float(i1 - (0.02 * v1 - 0.01 * v2))) < 1e-12
    assert abs(float(i2 - (-0.01 * v1 + 0.02 * v2))) < 1e-12
    # the bottom terminals carry the same currents out: KCL at node 4
    # says i1 leaves the port into r4
    assert abs(float(v["i_r4"] - i1)) < 1e-12


def test_four_node_two_port_symbolic_parameters_stay_free():
    res = dc("e1,1,0,10:z,1,2,3,0:r3,3,0,1:rl,2,0,5")
    syms = {str(s) for val in res.values.values() for s in val.free_symbols}
    assert {"z11", "z12", "z21", "z22"} <= syms


# --- the rules -------------------------------------------------------

def test_secondary_side_with_no_ground_is_floating():
    with pytest.raises(CircuitError) as err:
        dc("e1,1,0,10:t1,1,2,0,3,1,2:r1,2,3,4")
    assert err.value.code == M.E_FLOATING_NODES
    # ...and it is the whole side, not the element: hanging more on it
    # does not help until something reaches 0
    with pytest.raises(CircuitError) as err:
        dc("e1,1,0,10:t1,1,2,0,3,1,2:r1,2,3,4:r2,3,6,1:r3,6,2,1")
    assert err.value.code == M.E_FLOATING_NODES
    # grounding any node of that side settles it
    dc("e1,1,0,10:t1,1,2,0,3,1,2:r1,2,3,4:r2,3,0,1")


def test_two_port_side_with_no_ground_is_floating():
    with pytest.raises(CircuitError) as err:
        dc("e1,1,0,10:z,1,2,0,3,[100,10,20,50]:rl,2,3,5")
    assert err.value.code == M.E_FLOATING_NODES


def test_port_shorted_on_itself_is_refused():
    for desc in ("e1,1,0,10:t1,1,2,1,0,1,2:r1,2,0,4",
                 "e1,1,0,10:z,1,2,3,2,[1,2,3,4]:r1,2,0,4:r3,3,0,1"):
        with pytest.raises(CircuitError) as err:
            dc(desc)
        assert err.value.code == M.E_PORT_SAME_NODE


def test_four_node_form_allows_ground_on_a_top_terminal():
    # `z,1,2,0,0` is the two-node form; `z,0,1,3,2` is merely a port
    # wired upside down, and nothing forbids it.
    dc("e1,1,0,10:z,0,2,1,0,[100,10,20,50]:rl,2,0,5")


@pytest.mark.parametrize("bad, code", [
    ("z1,1,[1,2,3,4]", M.E_TERMS_TWO_PORT),
    ("z1,1,2,3,[1,2,3,4]", M.E_TERMS_TWO_PORT),
    ("z1,1,2,3,4,5,6", M.E_TERMS_TWO_PORT),
    ("z1,1,2,3", M.E_TWOPORT_LAST_TERM),
    ("z1,1,2,3,4,5", M.E_TWOPORT_LAST_TERM),
    ("t1,1,2,3,1,2", M.E_TERMS_EXACT),
    ("t1,1,2,3,4,5,1,2", M.E_TERMS_EXACT),
])
def test_wrong_counts_are_refused(bad, code):
    with pytest.raises(CircuitError) as err:
        parse_circuit("e1,1,0,1:" + bad + ":r1,1,0,1")
    assert err.value.code == code


def test_brackets_only_in_the_last_field():
    with pytest.raises(CircuitError) as err:
        parse_circuit("e1,1,0,1:z1,1,[1,2,3,4],3,4:r1,1,0,1")
    assert err.value.code == M.E_BRACKETS_MISUSED


def test_node_names_fold_case_in_all_four_fields():
    els = parse_circuit("e1,A,0,1:t1,A,B,C,0,1,2:r1,B,C,1:r2,C,0,1")
    t = [e for e in els if e.name == "t1"][0]
    assert t.fields[:4] == ["a", "b", "c", "0"]
    assert t.four_node and t.turns == ("1", "2")
    assert t.port_nodes == (("a", "c"), ("b", "0"))


def test_two_node_form_still_forbids_ground_on_a_top_node():
    with pytest.raises(CircuitError) as err:
        dc("e1,1,0,10:z,1,0,[100,10,20,50]:rl,1,0,5")
    assert err.value.code == M.E_TOP_NODE_GROUND


# --- the other consumers ---------------------------------------------

def test_spice_export_spans_the_named_pairs():
    net, warns = to_spice("e1,1,0,10:r0,1,2,1:t1,2,3,4,5,2,1:r4,4,0,3:"
                          "r1,3,5,100:r5,5,0,7")
    lines = [ln.split() for ln in net.splitlines() if ln and ln[0] in "EF"]
    e = [ln for ln in lines if ln[0].startswith("E")][0]
    f = [ln for ln in lines if ln[0].startswith("F")][0]
    assert e[2] == "5" and e[3:5] == ["2", "4"]      # E mid n2b n1 n1b
    assert f[1:3] == ["2", "4"]                       # F n1 n1b
    net, warns = to_spice("e1,1,0,10:y,1,2,3,4,[0.02,-0.01,-0.01,0.02]:"
                          "r3,3,0,1:r4,4,0,1:rl,2,0,5")
    g = [ln.split() for ln in net.splitlines() if ln.startswith("G")]
    assert g and all(len(ln) == 6 for ln in g)
    assert any(ln[1:5] == ["1", "3", "2", "4"] for ln in g)
    assert any("four-node" in w for w in warns)


def test_drawer_refuses_the_four_node_form_by_code():
    with pytest.raises(CircuitError) as err:
        to_svg("e1,1,0,10:t1,1,2,0,3,1,2:r1,2,3,4:r2,3,0,1")
    assert err.value.code == M.E_DRAW_FOUR_NODE
    # the two-node form draws as before
    assert "svg" in to_svg("e1,1,0,10:t1,1,2,1,2:r1,2,0,4")
