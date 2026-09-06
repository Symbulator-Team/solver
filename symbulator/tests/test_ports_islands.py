"""#320: `port()` takes a port as a node or a [top,bottom] pair.
#322: an island behind a port gets a reference of its own.

The three circuits are Alexander & Sadiku's Problems 19.2, 19.19 and
19.70, which a reader sent on 7 Sep 2026 to try the four-terminal
forms; none of them has a grounded port."""

import sympy as sp
import pytest

from symbulator import dc, port, th, er, tr, s, t
from symbulator import messages as M
from symbulator.elements import CircuitError, local_references, parse_circuit
from symbulator.equiv import _port_pair

LADDER = ("r1,a,b,1:r2,b,c,1:r3,c,d,1:r4,d,e,1:"
          "r5,f,g,1:r6,g,h,1:r7,h,i,1:r8,i,j,1:"
          "r9,b,g,1:r10,c,h,1:r11,d,i,1")                  # AS7 Problem 19.2
LC = "r1,a,x,1:r2,f,y,1:c,x,y,1:l,x,y,1"                    # AS7 Problem 19.19
BLOCKS = ("za,[p,0],[q,m],[25,20,5,10]:"
          "zb,[p,0],[m,n],[50,25,25,30]")                   # AS7 Problem 19.70


# --- #320: the extractor's ports -----------------------------------------

def test_port_pair_spellings():
    assert _port_pair("2") == ("2", "0")
    assert _port_pair("[a,f]") == ("a", "f")
    assert _port_pair(" [ a , f ] ") == ("a", "f")
    assert _port_pair(("x", "y")) == ("x", "y")
    assert _port_pair(["x"]) == ("x", "0")
    with pytest.raises(ValueError):
        _port_pair("[a,a]")
    with pytest.raises(ValueError):
        _port_pair("[a,b,c]")


def test_z_of_a_ladder_with_both_rails_resistive():
    z = port(LADDER, "[a,f]", "[e,j]", "z")
    assert z["11"] == z["22"] == sp.Rational(41, 15)
    assert z["12"] == z["21"] == sp.Rational(1, 15)


def test_grounding_both_bottoms_is_a_different_circuit():
    # what the two-node form measures on the same drawing: the lower
    # rail shorted out -- the thing #320 exists to stop
    z = port(LADDER.replace(",f,", ",0,").replace("r5,f,g", "r5,0,g")
             .replace("r8,i,j", "r8,i,0"), "a", "e", "z")
    assert z["11"] == sp.Rational(11, 5) and z["12"] == sp.Rational(3, 5)


def test_y_in_s_of_the_floating_lc_network():
    y = port(LC, "[a,f]", "[x,y]", "y", domain="fd")
    assert y["11"] == sp.Rational(1, 2)
    assert y["12"] == y["21"] == -sp.Rational(1, 2)
    assert sp.simplify(y["22"] - (s + sp.Rational(1, 2) + 1 / s)) == 0


def test_g_of_a_parallel_series_connection_of_two_blocks():
    g = port(BLOCKS, "p", "[q,n]", "g")
    assert (g["11"], g["12"], g["21"], g["22"]) == (
        sp.Rational(3, 50), -sp.Rational(13, 10), sp.Rational(7, 10), sp.Rational(47, 2))
    # the same by hand: each block's g from its z, then the sum
    def g_of(z11, z12, z21, z22):
        d = z11 * z22 - z12 * z21
        return sp.Matrix([[sp.Rational(1, z11), -sp.Rational(z12, z11)],
                          [sp.Rational(z21, z11), sp.Rational(d, z11)]])
    assert sp.Matrix([[g["11"], g["12"]], [g["21"], g["22"]]]) == \
        g_of(25, 20, 5, 10) + g_of(50, 25, 25, 30)


def test_two_node_form_is_unchanged():
    p = port("r1,1,3,100:r2,2,3,200:r3,3,0,50", "1", "2", "z")
    assert (p["11"], p["12"], p["21"], p["22"]) == (150, 50, 50, 250)


def test_th_and_er_on_a_network_with_no_ground():
    # both name their second node as the reference, so a groundless
    # network is measured across the port it was asked about
    assert er(LADDER, "a", "f") == sp.Rational(41, 15)
    # (the source is `es`, not `e`: a node and an element of one name
    # share `v_e`, the drop overwriting the node voltage)
    eq = th(LADDER + ":es,a,f,15", "e", "j")
    # vth = z21 * v1 / z11 = (1/15) * 15 / (41/15); req = z22 - z12*z21/z11
    assert eq.vth == sp.Rational(15, 41)
    assert eq.z == sp.Rational(41, 15) - sp.Rational(1, 15) ** 2 / sp.Rational(41, 15)


# --- #322: islands ------------------------------------------------------

def test_island_behind_a_block_gets_the_ports_bottom_as_reference():
    res = dc(BLOCKS + ":e1,p,0,1")
    assert res.references == {"m": ["q", "n"]}
    assert res["v_m"] == 0
    assert -res["i_e1"] == sp.Rational(3, 50)
    assert res["v_q"] - res["v_n"] == sp.Rational(7, 10)
    assert len(res.notes) == 1
    n = res.notes[0]
    assert n["code"] == M.N_LOCAL_REFERENCE and n["severity"] == "warning"
    assert n["args"] == {"nodes": "m, q, n", "ref": "m"}
    assert "measured against m" in n["text"]


def test_notes_and_references_survive_at_rounded_and_tr():
    res = dc(BLOCKS + ":e1,p,0,1")
    assert res.at(x=1).notes == res.notes and res.rounded(3).references == res.references
    assert "note:" in repr(res) and "measured against" in res._repr_latex_()
    t_ = tr("t,[1,0],[2,3],[1,2]:e,1,0,5:r,2,3,10")
    assert t_.references == {"3": ["2"]} and t_.notes[0]["code"] == M.N_LOCAL_REFERENCE


def test_local_references_prefers_the_callers_node_then_a_bottom():
    els = parse_circuit(BLOCKS + ":e1,p,0,1")
    assert local_references(els) == {"m": ["q", "n"]}
    assert local_references(els, preferred=["n"]) == {"n": ["q", "m"]}
    assert local_references(els, preferred=["zz", "q"]) == {"q": ["m", "n"]}


def test_island_behind_a_coupling_gets_the_coils_second_node_as_reference():
    # #323: the secondary of a coupled pair, Nilsson & Riedel's 60 V
    # problem after the switch -- typed as drawn, no ground on that side
    res = tr("r3,0,2,3:l1,2,0,2,5:l2,3,4,8,0:r2,3,5,2:r10,5,4,10:m,l1,l2,2")
    assert res.references == {"4": ["3", "5"]}
    assert sp.simplify(res["il1"] - sp.Rational(5, 2) * (sp.exp(-t) + sp.exp(-3 * t))) == 0
    assert sp.simplify(res["il2"] - sp.Rational(5, 4) * (sp.exp(-t) - sp.exp(-3 * t))) == 0
    assert "coupling" in res.notes[0]["text"]
    # a dangling coil that nothing couples is still floating
    with pytest.raises(CircuitError) as err:
        dc("e1,1,0,5:r1,1,0,1:l9,2,3,1")
    assert err.value.code == M.E_FLOATING_NODES


def test_a_groundless_ladder_needs_a_caller_to_name_a_reference():
    # by itself the ladder has no node 0 and no port: still a mistake
    with pytest.raises(CircuitError) as err:
        dc(LADDER)
    assert err.value.code == M.E_NEED_REFERENCE_NODE
    # a dangling piece of ordinary elements is still floating
    with pytest.raises(CircuitError) as err:
        dc("e1,1,0,5:r1,1,0,1:r2,2,3,1")
    assert err.value.code == M.E_FLOATING_NODES
