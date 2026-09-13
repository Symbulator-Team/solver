"""#438: the m line checked, and k= accepted (Roberto, 13 Sep 2026).

Both coupled elements must be of one kind -- inductors in henries or
coils written as impedances in ohms -- with values of that kind, and a
coupling no stronger than sqrt(L1*L2). `k=0.5` on the m line gives the
coupling as a factor, in either unit.
"""
import pytest
import sympy as sp

from symbulator import ac, tr, dc
from symbulator import messages as M
from symbulator.elements import CircuitError, parse_circuit


# NR12's Example 9.15: 9 H and 4 H at 400 rad/s with k = 0.5, so M = 3 H.
HENRIES = "e,1,0,300:r1,1,2,500:r3,2,p,200:l1,p,0,9:m,l1,l2,{m}:l2,q,0,4:r6,q,c,100:rl,c,0,800"
OHMS = "e,1,0,300:r1,1,2,500:r3,2,p,200:ra,p,0,3600j:m,ra,rb,{m}:rb,q,0,1600j:r6,q,c,100:rl,c,0,800"


def test_k_in_henries_equals_the_explicit_coupling():
    a = ac(HENRIES.format(m="3"), omega=400)
    b = ac(HENRIES.format(m="k=0.5"), omega=400)
    assert sp.simplify(a.values["i_rl"] - b.values["i_rl"]) == 0
    assert b.values["i_rl"] != 0


def test_k_in_ohms_equals_the_explicit_coupling():
    a = ac(OHMS.format(m="1200j"), omega=400)
    b = ac(OHMS.format(m="k=0.5"), omega=400)
    assert sp.simplify(a.values["i_rl"] - b.values["i_rl"]) == 0


def test_k_spelling_is_forgiving():
    els = parse_circuit("l1,1,0,9:l2,2,0,4:m,l1,l2,K = 0.5:r,2,0,5:e,1,0,1")
    m = [e for e in els if e.kind == "m"][0]
    assert sp.simplify(sp.sympify(m.fields[2]) - 3) == 0


def test_symbolic_k_gives_a_symbolic_coupling():
    els = parse_circuit("l1,1,0,L1:l2,2,0,L2:m,l1,l2,k=k:r,2,0,5:e,1,0,1")
    m = [e for e in els if e.kind == "m"][0]
    k, L1, L2 = sp.symbols("k L1 L2")
    assert sp.simplify(sp.sympify(m.fields[2]) - k * sp.sqrt(L1 * L2)) == 0


def test_echo_mode_keeps_k_as_typed():
    els = parse_circuit("l1,1,0,9:l2,2,0,4:m,l1,l2,k=0.5:r,2,0,5:e,1,0,1",
                        expand_si=False)
    m = [e for e in els if e.kind == "m"][0]
    assert m.fields[2] == "k=0.5"


@pytest.mark.parametrize("k", ["0", "1.5", "-0.2", "2j"])
def test_k_outside_its_range_is_refused(k):
    with pytest.raises(CircuitError) as err:
        parse_circuit("l1,1,0,9:l2,2,0,4:m,l1,l2,k=%s:r,2,0,5:e,1,0,1" % k)
    assert err.value.code == M.E_M_K_RANGE


def test_k_of_exactly_one_is_allowed():
    els = parse_circuit("l1,1,0,9:l2,2,0,4:m,l1,l2,k=1:r,2,0,5:e,1,0,1")
    assert els


def test_a_coupling_to_a_missing_element_is_refused():
    with pytest.raises(CircuitError) as err:
        parse_circuit("l1,1,0,9:m,l1,l9,3:r,1,0,5:e,1,0,1")
    assert err.value.code == M.E_M_NO_SUCH_ELEMENT
    assert err.value.args_map["other"] == "l9"


def test_mixed_kinds_are_refused():
    """The Course's own warning made real: henries on one side, ohms on
    the other used to be accepted and answered wrongly."""
    with pytest.raises(CircuitError) as err:
        parse_circuit("l1,1,0,9:r2,2,0,1600j:m,l1,r2,3:r,2,0,5:e,1,0,1")
    assert err.value.code == M.E_M_MIXED_KINDS
    with pytest.raises(CircuitError) as err:
        parse_circuit("c1,1,0,9:c2,2,0,4:m,c1,c2,3:r,2,0,5:e,1,0,1")
    assert err.value.code == M.E_M_MIXED_KINDS


@pytest.mark.parametrize("desc", [
    "l1,1,0,-9:l2,2,0,4:m,l1,l2,3:r,2,0,5:e,1,0,1",      # a negative inductance
    "l1,1,0,9:l2,2,0,4:m,l1,l2,-3:r,2,0,5:e,1,0,1",      # a negative coupling
    "l1,1,0,9:l2,2,0,4:m,l1,l2,3j:r,2,0,5:e,1,0,1",      # ohms among henries
    "l1,1,0,0:l2,2,0,4:m,l1,l2,3:r,2,0,5:e,1,0,1",       # a zero inductance
])
def test_henries_must_be_real_and_positive(desc):
    with pytest.raises(CircuitError) as err:
        parse_circuit(desc)
    assert err.value.code == M.E_M_NOT_REAL


@pytest.mark.parametrize("desc", [
    "ra,1,0,3600j:rb,2,0,1600j:m,ra,rb,1200:r,2,0,5:e,1,0,1",     # a real coupling
    "ra,1,0,10+3600j:rb,2,0,1600j:m,ra,rb,1200j:r,2,0,5:e,1,0,1", # a resistive part
    "ra,1,0,-3600j:rb,2,0,1600j:m,ra,rb,1200j:r,2,0,5:e,1,0,1",   # a capacitive coil
])
def test_ohms_must_be_positive_imaginary(desc):
    with pytest.raises(CircuitError) as err:
        parse_circuit(desc)
    assert err.value.code == M.E_M_NOT_IMAGINARY


def test_a_coupling_above_sqrt_l1_l2_is_refused():
    with pytest.raises(CircuitError) as err:
        parse_circuit("l1,1,0,9:l2,2,0,4:m,l1,l2,6.5:r,2,0,5:e,1,0,1")
    assert err.value.code == M.E_M_TOO_STRONG
    with pytest.raises(CircuitError) as err:
        parse_circuit("ra,1,0,3600j:rb,2,0,1600j:m,ra,rb,2500j:r,2,0,5:e,1,0,1")
    assert err.value.code == M.E_M_TOO_STRONG


def test_a_coupling_at_the_limit_is_allowed():
    els = parse_circuit("l1,1,0,9:l2,2,0,4:m,l1,l2,6:r,2,0,5:e,1,0,1")
    assert els


def test_symbolic_values_pass_every_check():
    els = parse_circuit("l1,1,0,L1:l2,2,0,4:m,l1,l2,M:r,2,0,5:e,1,0,1")
    assert els
    els = parse_circuit("ra,1,0,Z1:rb,2,0,1600j:m,ra,rb,Zm:r,2,0,5:e,1,0,1")
    assert els


def test_a_pair_in_ohms_couples_in_ac_only():
    desc = "e,1,0,12:ra,1,0,5j:m,ra,rb,3j:rb,2,0,6j:r4,2,0,12"
    assert ac(desc, omega=1).values["i_r4"] != 0
    with pytest.raises(CircuitError) as err:
        tr(desc)
    assert err.value.code == M.E_M_IMPEDANCE_DOMAIN
    with pytest.raises(CircuitError) as err:
        dc(desc)
    assert err.value.code == M.E_M_IMPEDANCE_DOMAIN


def test_henries_still_couple_in_every_domain():
    desc = "e,1,0,60:r9,1,a,9:r3,a,p,3:l1,p,0,2:m,l1,l2,2:l2,q,0,8:r2b,q,c,2:r10,c,0,10"
    assert dc(desc).values["i_l1"] == 5
    assert ac(desc, omega=1).values["i_l2"] != 0
    assert tr(desc).values["i_l2"] is not None
