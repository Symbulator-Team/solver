"""
Tests for expert-mode extras (`equations`/`unknowns`/`conditions`,
solve_circuit's port of the calculator's "Add equations / Add unknowns /
Add conditions" prompts): solving for a symbolic component value, that a
new symbol in an extra equation is auto-detected as an unknown, that
extra equations accept the calculator's unit shorthand, that conditions
substitute at solve time, that the same extras work through ex(), and
that a malformed condition raises."""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import sympy as sp
import pytest

from symbulator import dc, ac, ex
from symbulator.elements import CircuitError


def test_extra_equation_solves_for_symbolic_component():
    # 12V source, 4k on top, unknown r_b on the bottom; forcing v_2 = 6
    # (half the supply) must find r_b = 4k. The component symbol has to
    # be listed as an unknown, same as the TI's "Add unknowns" prompt.
    res = dc("e1,1,0,12:r1,1,2,4'k:r2,2,0,r_b",
             equations=["v_2 = 6"], unknowns=["r_b"])
    assert sp.simplify(res.values["r_b"] - 4000) == 0


def test_new_symbol_in_extra_equation_is_auto_unknown():
    # A brand-new symbol in the equation itself (a derived quantity)
    # becomes an unknown automatically -- no unknowns list needed.
    res = dc("e1,1,0,12:r1,1,2,4'k:r2,2,0,2'k", equations=["pout = v_2*i_r2"])
    # v_2 = 4, i_r2 = 2mA -> pout = 8 mW
    assert sp.simplify(res.values["pout"] - sp.Rational(1, 125)) == 0


def test_extra_equation_accepts_unit_shorthand():
    # The original ran its SI-prefix expander over added equations too.
    res = dc("e1,1,0,12:r1,1,2,4'k:r2,2,0,r_b",
             equations=["r_b = 4'k"], unknowns=["r_b"])
    assert sp.simplify(res.v("2") - 6) == 0


def test_conditions_substitute_at_solve_time():
    # Symbolic divider pinned down by conditions (the TI's | operator).
    res = dc("e1,1,0,vin:r1,1,2,r_a:r2,2,0,r_b",
             conditions=["vin = 12", "r_a = 4'k", "r_b = 2'k"])
    assert sp.simplify(res.v("2") - 4) == 0


def test_expert_mode_via_ex_dispatcher():
    res = ex("e1,1,0,12:r1,1,2,4'k:r2,2,0,r_b", "dc",
             equations=["v_2 = 6"], unknowns=["r_b"])
    assert sp.simplify(res.values["r_b"] - 4000) == 0


def test_extra_equation_in_ac():
    # Symbolic inductance chosen so the current magnitude has a known
    # value: |i| = 10/sqrt(100^2 + (w L)^2); force wL = 100 via equation.
    res = ac("e1,1,0,10:r1,1,2,100:l1,2,0,lx", omega=1000,
             equations=["1000*lx = 100"])
    assert sp.simplify(res.values["lx"] - sp.Rational(1, 10)) == 0


def test_bad_condition_raises():
    with pytest.raises(CircuitError):
        dc("e1,1,0,12:r1,1,0,1'k", conditions=["r_a"])


# -- equations written on derived quantities -------------------------------
# The quantities in analysis._derived (branch voltage v_<element>, power
# p_<element>, and the r_/z_ a source sees) are computed after the system is
# solved, so the solver knows nothing about them. Before these, an equation
# naming one introduced a free variable of the same name that satisfied
# itself, leaving the circuit one equation short -- the answer came back
# correct but parametrized, with nothing to say the constraint had been
# ignored. solve_circuit now stamps the defining equation instead.

def test_equation_on_r_of_a_source_constrains_the_circuit():
    # From the documentation's own expert-mode example: the source sees
    # 12k and R3 carries 6 mA, which pins e = 72 V and rx = 2 k.
    res = ex("e,1,0,e:r1,1,2,rx:r2,2,3,4'k:r3,3,0,6'k", "dc",
             equations=["r_e = 12000", "i_r3 = 0.006"],
             unknowns=["e", "rx"])
    assert sp.simplify(res.values["e"] - 72) == 0
    assert sp.simplify(res.values["rx"] - 2000) == 0


def test_derived_definition_agrees_with_what_derived_computes():
    # The definitions in engine._derived_definition duplicate the formulas
    # in analysis._derived; this is the guard against the two drifting.
    # Ask for r_e as a constraint, then check the value _derived reports
    # afterwards is the one that was asked for.
    res = ex("e,1,0,e:r1,1,2,rx:r2,2,3,4'k:r3,3,0,6'k", "dc",
             equations=["r_e = 12000", "i_r3 = 0.006"],
             unknowns=["e", "rx"])
    assert sp.simplify(res.values["r_e"] - 12000) == 0


def test_equation_on_branch_voltage():
    # v_r2 names the drop across r2, not a node -- half of 12 V puts the
    # unknown leg at 4k, matching the fixed one.
    res = dc("e1,1,0,12:r1,1,2,4'k:r2,2,0,rb",
             equations=["v_r2 = 6"], unknowns=["rb"])
    assert sp.simplify(res.values["rb"] - 4000) == 0


def test_equation_on_dc_power():
    # 8 mW in the lower leg of a 12 V divider with 4k on top: rb = 2k
    # (8k is the other root; solve() returns one of the two).
    res = dc("e1,1,0,12:r1,1,2,4'k:r2,2,0,rb",
             equations=["p_r2 = 0.008"], unknowns=["rb"])
    assert sp.simplify(res.values["rb"] - 2000) == 0


def test_equation_on_impedance_seen_in_ac():
    res = ac("e1,1,0,10:r1,1,2,100:l1,2,0,lx", omega=1000,
             equations=["z_e1 = 100 + 100*I"], unknowns=["lx"])
    assert sp.simplify(res.values["lx"] - sp.Rational(1, 10)) == 0


def test_equation_on_op_amp_power():
    res = dc("e1,1,0,1:r1,1,2,1'k:r2,2,3,rf:o1,0,2,3",
             equations=["p_o1 = -0.002"], unknowns=["rf"])
    assert sp.simplify(res.values["rf"] - 2000) == 0


def test_ac_power_equation_is_refused_not_silently_ignored():
    # s_/p_/ap_ in AC are defined through conjugation and cannot go into
    # the system. Refusing beats the silent phantom this all guards against.
    for bad in ("s_e1 = 5", "ap_e1 = 5", "p_e1 = 5"):
        with pytest.raises(CircuitError):
            ac("e1,1,0,10:r1,1,2,100:l1,2,0,lx", omega=1000, equations=[bad])


def test_explicit_unknowns_still_win_over_the_inference():
    # r_b here is a component value, not "the r_ of an element named b" --
    # there is no element b, and it is listed under unknowns either way.
    res = dc("e1,1,0,12:r1,1,2,4'k:r2,2,0,r_b",
             equations=["v_2 = 6"], unknowns=["r_b"])
    assert sp.simplify(res.values["r_b"] - 4000) == 0


# -- equations on quantities recorded in `known` ---------------------------

def test_equation_on_a_known_source_current():
    # A j source's current is stamped into `known`, not solved for, so it
    # was phantomed the same way. Substituting the stamped value makes the
    # equation constrain the source's own symbolic value.
    res = dc("j1,0,1,jx:r1,1,0,1'k", equations=["i_j1 = 0.005"], unknowns=["jx"])
    assert sp.simplify(res.values["jx"] - sp.Rational(5, 1000)) == 0
    assert sp.simplify(res.v("1") - 5) == 0


def test_redundant_equation_on_a_known_current_is_harmless():
    res = dc("j1,0,1,5:r1,1,0,1'k", equations=["i_j1 = 5"])
    assert sp.simplify(res.v("1") - 5000) == 0


def test_contradictory_equation_on_a_known_current_is_reported():
    with pytest.raises(CircuitError):
        dc("j1,0,1,5:r1,1,0,1'k", equations=["i_j1 = 7"])


def test_quadratic_equation_keeps_both_roots_and_prefers_positive():
    # A power is quadratic in the source, so p_r1 = 25 mW admits e = +5
    # and e = -5. The solver used to keep whichever root SymPy listed
    # first (-5); now every root is kept and the non-negative one leads.
    res = ex("e,1,0,e:r1,1,0,1'k", "dc",
             equations=["p_r1 = 0.025"], unknowns=["e"])
    assert res.multiple
    assert [float(s["e"]) for s in res.solutions] == [5.0, -5.0]
    assert float(res["e"]) == 5.0
    assert "2 solutions" in repr(res)


def test_single_solution_is_not_flagged_multiple():
    from symbulator import dc
    res = dc("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k")
    assert not res.multiple
    assert res.solutions == [res.values]
    assert "solutions" not in repr(res)


# --- inequality conditions: the | operator's full breadth -------------

def test_inequality_condition_selects_among_solutions():
    """A quadratic power constraint yields sign-symmetric roots; an
    inequality condition -- solve(...)|x>0 on the calculator -- picks
    among them, in the same call."""
    res = dc("e1,1,0,vx:r1,1,0,4",
             equations=["p_r1 = 9"], unknowns=["vx"],
             conditions=["vx > 0"])
    assert len(res.solutions) == 1
    assert res["vx"] == 6


def test_inequality_condition_that_excludes_everything_is_reported():
    with pytest.raises(CircuitError):
        dc("e1,1,0,vx:r1,1,0,4",
           equations=["p_r1 = 9"], unknowns=["vx"],
           conditions=["vx > 100"])


def test_chained_comparison_condition_selects_among_solutions():
    """#392: `7 > vx > 3` is how anyone writes a range, and every parser
    here used to split on the first operator it met -- handing `vx > 3`
    to a value parser that rightly refuses a comparison inside a value.
    It is the conjunction of its links now."""
    res = dc("e1,1,0,vx:r1,1,0,4",
             equations=["p_r1 = 9"], unknowns=["vx"],
             conditions=["7 > vx > 3"])
    assert len(res.solutions) == 1
    assert res["vx"] == 6


def test_chained_comparison_reads_in_either_direction():
    """Upward and downward say the same thing, and a chain over negative
    numbers is the case a sign-only reading would get backwards."""
    up = dc("e1,1,0,vx:r1,1,0,4", equations=["p_r1 = 9"],
            unknowns=["vx"], conditions=["3 < vx < 7"])
    assert up["vx"] == 6
    down = dc("e1,1,0,vx:r1,1,0,4", equations=["p_r1 = 9"],
              unknowns=["vx"], conditions=["-3 > vx > -7"])
    assert down["vx"] == -6


def test_chained_comparison_that_excludes_everything_is_reported():
    with pytest.raises(CircuitError):
        dc("e1,1,0,vx:r1,1,0,4", equations=["p_r1 = 9"],
           unknowns=["vx"], conditions=["7 > vx > 6.5"])


def test_split_chained_comparison():
    """The splitter itself, including the shapes that must come back
    whole rather than have a message invented for them."""
    from symbulator.engine import split_chained_comparison as split

    assert split("7 > x > 3") == ["7 > x ", " x > 3"]
    assert split("3 <= x <= 7") == ["3 <= x ", " x <= 7"]
    assert split("1 < a < b < 9") == ["1 < a ", " a < b ", " b < 9"]
    # One comparison, none at all, an equality, a negative bound: whole.
    for text in ("x > 3", "vs > 0", "x", "a = b", "x > -3"):
        assert split(text) == [text]
    # Malformed -- no operand between two operators -- goes back whole so
    # the caller's own parser reports it in its own words.
    assert split("7 > > 3") == ["7 > > 3"]


def test_equality_and_inequality_conditions_mix():
    res = dc("e1,1,0,vx:r1,1,0,rx",
             equations=["p_r1 = 9"], unknowns=["vx"],
             conditions=["rx = 4", "vx < 0"])
    assert res["vx"] == -6


# --- Python keywords as variable names --------------------------------

def test_is_works_as_a_symbol_name():
    """A source current named 'is' is the most natural name in circuit
    analysis; Python's grammar must not leak through the parser."""
    res = dc("j1,0,1,is:r1,1,0,10")
    assert res["v_1"] == 10 * sp.Symbol("is")


def test_is_can_be_solved_for_in_expert_mode():
    res = dc("j1,0,1,is:r1,1,0,10",
             equations=["v_1 = 25"], unknowns=["is"])
    assert res["is"] == sp.Rational(5, 2)


# --- full spelling equivalence of answer names ------------------------
# Underscored and non-underscored spellings are ONE name, everywhere:
# `ir1` is `i_r1` (and `IR1`, and `I_R1`), exactly as they were one
# calculator variable before version 9's underscore convention.

def test_dependent_source_accepts_calculator_spelling():
    """`0.5*ir1` in a value means half the current through r1."""
    a = dc("e1,1,0,12:r1,1,2,4:r2,2,0,2:j1,0,3,0.5*ir1:r3,3,0,10")
    b = dc("e1,1,0,12:r1,1,2,4:r2,2,0,2:j1,0,3,0.5*i_r1:r3,3,0,10")
    assert a["v_3"] == b["v_3"] != 0


def test_the_1999_netlist_runs_verbatim():
    """The two-stage amplifier from the 1999 competition paper and the
    2001 thesis (Problem 87), each element spelled exactly as printed
    a quarter century ago -- `5/100*v1` controls, `10^-10` powers,
    flat names -- with only the separator modernised."""
    from symbulator import fd
    desc = ("e1,5,0,vg:r1,5,1,150:r2,1,0,1000:"
            "cc1,1,0,10^-10:cc2,1,2,3*10^-12:jd1,2,0,5/100*v1:"
            "r3,2,0,2000:r4,2,3,100:r5,3,0,1000:"
            "cc3,3,0,10^-10:cc4,3,4,3*10^-12:jd2,4,0,5/100*v3:"
            "r6,4,0,2000")
    res = fd(desc)
    ratio = sp.simplify(res["v_4"] / res["v_5"])
    # The transfer function depends on s but not on the source symbol.
    assert sp.Symbol("s") in ratio.free_symbols
    assert sp.Symbol("vg") not in ratio.free_symbols


def test_alias_binds_is_to_the_short_named_s():
    """With an element named s in the circuit, `is` IS its current --
    the two spellings are one name by design."""
    res = dc("e1,1,0,12:s,1,2:r1,2,0,4:j1,0,3,2*is:r2,3,0,1")
    assert res["v_3"] == 6          # 2 * i_s = 2 * 3


def test_expert_equation_accepts_calculator_spelling():
    res = dc("e1,1,0,12:r1,1,2,4'k:r2,2,0,rx",
             equations=["vr2 = 6"], unknowns=["rx"])
    assert res["rx"] == 4000
