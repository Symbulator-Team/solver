"""Every two-terminal branch as `v(n1) - v(n2) = Z*i + E`, read back out
of the engine's own stamp rather than restated.

The module under test states no component rule, so what is checked here
is that the rules `engine.py` *does* state come back out intact in every
domain -- and the three distinctions that are easy to get wrong by hand:
a component depends on its terminals as their difference, a reference
terminal carries no coefficient, and a dependent source reading a node
voltage belongs in the source term rather than in a refusal.
"""

import pytest
import sympy as sp

from symbulator.branches import BranchError, branches_of
from symbulator.elements import parse_circuit


def _by_name(desc, domain, **kw):
    return {b.name: b for b in branches_of(parse_circuit(desc), domain, **kw)}


# --- the engine's own rules, coming back out ----------------------------

def test_a_resistor_is_its_resistance():
    b = _by_name("e1,1,0,10\nr1,1,2,470\nr2,2,0,220", "dc")["r1"]
    assert sp.simplify(b.Z - 470) == 0
    assert sp.simplify(b.E) == 0
    assert (b.n1, b.n2) == ("1", "2")
    assert str(b.i) == "i_r1"


def test_a_voltage_source_is_zero_impedance_and_its_value():
    b = _by_name("e1,1,0,10\nr1,1,0,470", "dc")["e1"]
    assert b.is_ideal_vsource
    assert sp.simplify(b.Z) == 0
    assert sp.simplify(b.E - 10) == 0


def test_a_current_source_is_a_current_not_an_impedance():
    b = _by_name("j1,0,1,3\nr1,1,0,470", "dc")["j1"]
    assert b.is_current_source
    assert b.Z is None
    assert sp.simplify(b.source_current - 3) == 0


#: The engine's own omega carries `real=True`. A freshly made
#: `Symbol("omega")` is a *different symbol* and will not cancel against
#: it -- the same trap the package README documents for `t`. Write
#: `sp.sympify("I*omega*2")` here instead and `simplify` returns
#: `2*I*(omega - omega)` rather than zero, which reads like a wrong
#: impedance and is a wrong test.
OMEGA = sp.Symbol("omega", real=True)
S = sp.Symbol("s")


@pytest.mark.parametrize("domain, expected", [
    ("ac", lambda: sp.I * OMEGA * 2),     # jwL
    ("fd", lambda: 2 * S),                # sL
])
def test_an_inductor_follows_the_domain(domain, expected):
    b = _by_name("e1,1,0,10\nl1,1,2,2\nr1,2,0,5", domain)["l1"]
    assert sp.simplify(b.Z - expected()) == 0


def test_an_inductor_is_a_wire_in_dc():
    b = _by_name("e1,1,0,10\nl1,1,2,2\nr1,2,0,5", "dc")["l1"]
    assert sp.simplify(b.Z) == 0 and sp.simplify(b.E) == 0


@pytest.mark.parametrize("domain, expected", [
    ("ac", lambda: 1 / (sp.I * OMEGA * 4)),
    ("fd", lambda: 1 / (4 * S)),
])
def test_a_capacitor_is_read_from_the_admittance_side(domain, expected):
    b = _by_name("e1,1,0,10\nr1,1,2,5\nc1,2,0,4", domain)["c1"]
    assert sp.simplify(b.Z - expected()) == 0


def test_a_capacitor_is_open_in_dc_and_absent_from_the_branches():
    assert "c1" not in _by_name("e1,1,0,10\nr1,1,2,5\nc1,2,0,4", "dc")


def test_a_charged_capacitor_carries_its_initial_voltage_as_a_source():
    """In s a charged capacitor is 1/(sC) in series with v0/s."""
    b = _by_name("r1,1,0,5\nc1,1,0,4,7", "fd")["c1"]
    assert sp.simplify(b.Z - 1 / (4 * S)) == 0
    assert sp.simplify(b.E - 7 / S) == 0


# --- the three distinctions -------------------------------------------

def test_a_vccs_reading_one_terminal_is_a_source_not_an_impedance():
    """`j2,1,2,.2*v_1` has a current in v_1 and not in v_2, so it does
    not depend on its terminals as a difference. It looks exactly like a
    5-ohm resistor to anything that only checks one coefficient."""
    b = _by_name("j1,0,1,1\nr1,1,0,4\nj2,1,2,.2*v_1\nr2,2,0,5", "dc")["j2"]
    assert b.is_current_source, "read as an impedance, which it is not"
    assert sp.simplify(b.source_current - sp.Symbol("v_1") / 5) == 0


def test_a_grounded_element_is_still_a_component():
    """One terminal on the reference carries no coefficient, so the
    difference test cannot be applied to it -- and must not be, or every
    grounded resistor becomes a "source"."""
    b = _by_name("e1,1,0,10\nr1,1,0,470", "dc")["r1"]
    assert not b.is_current_source
    assert sp.simplify(b.Z - 470) == 0


def test_a_dependent_source_on_its_own_terminal_is_not_a_refusal():
    """A VCVS whose controlling node is one of its own terminals leaves
    a dependence over after the drop is accounted for. That belongs in
    the source term, not in an exception."""
    got = _by_name("e1,1,0,10\nr1,1,2,5\ne2,2,3,3*v_2\nr2,3,0,7", "dc")
    assert "e2" in got
    assert sp.simplify(got["e2"].Z) == 0


def test_a_ccvs_keeps_the_current_it_names_in_its_source_term():
    b = _by_name("e1,1,0,6\nr1,1,2,2\ne2,2,0,3*i_r1", "dc")["e2"]
    assert sp.simplify(b.Z) == 0
    assert sp.Symbol("i_r1") in b.E.free_symbols


# --- shape and refusals ------------------------------------------------

def test_multi_terminal_elements_are_left_out():
    """A transformer, a two-port block and a mutual inductance are not
    two-terminal branches and have no v-i relation of this form."""
    for desc in ("e1,1,0,10\nt1,1,2,[1,2]\nr1,2,0,50",
                 "e1,1,0,10\nr1,1,2,5\nz1,2,3,[1,2,3,4]\nr2,3,0,5"):
        names = _by_name(desc, "dc")
        assert not ({"t1", "z1"} & set(names)), names


def test_every_branch_reproduces_its_own_stamped_equation():
    """The point of the module: Z and E are not a second opinion.

    For every branch that has an equation of its own, the engine's
    equation and the derived relation must be the *same* equation up to
    a constant factor -- so their ratio is a non-zero constant carrying
    none of the branch's own symbols. That covers an ideal voltage
    source (Z = 0) as strictly as a resistor, which a test written as
    "substitute i and see if it vanishes" quietly does not."""
    desc = "e1,1,0,24\nr1,1,2,4\nl1,2,3,2\nc1,3,0,5\nr2,2,0,8"
    checked = 0
    for domain in ("dc", "ac", "fd"):
        for b in branches_of(parse_circuit(desc), domain):
            if b.eq is None:
                continue
            u = ((sp.Symbol("v_" + b.n1) if b.n1 != "0" else sp.Integer(0))
                 - (sp.Symbol("v_" + b.n2) if b.n2 != "0" else sp.Integer(0)))
            derived = u - b.Z * b.i - b.E
            stamped_form = sp.expand(b.eq.lhs - b.eq.rhs)
            if sp.simplify(derived) == 0:      # a wire: both sides vanish
                assert sp.simplify(stamped_form) == 0, (domain, b.name)
                checked += 1
                continue
            ratio = sp.simplify(sp.cancel(stamped_form / derived))
            assert ratio != 0, (domain, b.name)
            # The factor may carry a domain symbol -- the engine writes
            # an inductor in FD as `(v1 - v2)/s = L*(i - i0/s)`, so the
            # ratio there is 1/s, and that is the same equation. What it
            # must not carry is either of the branch's own unknowns: its
            # current, or a node voltage. A factor in those would mean
            # the two are different equations that happen to share a
            # root, which is exactly the failure worth catching.
            unknowns = {b.i} | {sym for sym in derived.free_symbols
                                if str(sym).startswith("v_")}
            assert not (ratio.free_symbols & unknowns), \
                (domain, b.name, ratio)
            checked += 1
    assert checked >= 9, checked


def test_a_value_that_is_not_linear_in_its_own_current_is_refused():
    with pytest.raises(BranchError):
        branches_of(parse_circuit("e1,1,0,i_e1**2\nr1,1,0,5"), "dc")
