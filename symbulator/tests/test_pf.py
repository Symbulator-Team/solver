"""`pf()` as version 8 had it (#430): one value, two forms.

The complex-value form answers with |Re| / |S| and no direction; the
element-name form answers with the calculator's sentence, reading a load
on the power it consumes and a source on the power it delivers. The
readings asserted here are the ones the tutorial prints (Lesson 8, AS7's
Example 11.10, Practice Problem 11.10 and Problem 11.75).
"""
import pytest
import sympy as sp

from symbulator import ac, pf
from symbulator.utils import pf_reading

OMEGA = sp.Symbol("omega")


def _ac(desc, omega=OMEGA):
    return ac(desc, omega=omega, use_rms=True)


# --- the complex-value form ------------------------------------------------

def test_number_gives_the_ratio_and_no_word():
    assert pf(3 + 4 * sp.I) == sp.Float(0.6)
    assert pf("3 + 4*I") == sp.Float(0.6)
    assert pf(complex(3, -4)) == sp.Float(0.6)      # the sign cannot show
    assert float(pf(5)) == 1                        # a Float, not the Integer
    assert float(pf(-5)) == 1
    assert float(pf(4 * sp.I)) == 0


def test_symbol_is_taken_real_and_handed_back_as_typed():
    x = sp.Symbol("x")
    got = pf(x + 2 * sp.I)
    assert got == sp.Abs(x) / sp.sqrt(x**2 + 4)
    assert got.free_symbols == {x}                 # the caller's own x
    assert got.subs(x, 2) == sp.sqrt(2) / 2         # so subs() still works


def test_complex_power_of_a_source_and_of_its_load_agree_in_value():
    res = _ac("e,1,0,10:r,1,0,3+4j")
    assert abs(pf(res["s_e"]) - 0.6) < 1e-12
    assert abs(pf(-res["s_e"]) - 0.6) < 1e-12       # consumed or delivered
    assert abs(pf(res["s_r"]) - 0.6) < 1e-12


def test_zero_is_refused():
    with pytest.raises(ValueError):
        pf(0)


# --- the element-name form -------------------------------------------------

def test_example_11_10_reads_leading_at_the_source():
    res = _ac("e,1,0,30:r1,1,2,6:r2,2,0,-2j:r3,2,0,4")
    assert pf("e", res) == "pf: 0.97342 leading"
    assert abs(pf(res["s_e"]) - 0.973417) < 1e-6
    assert pf("r1", res) == "pf: 1.0"               # purely real: no word
    assert pf("r2", res) == "pf: 0.0 leading"       # a capacitor, as an impedance


def test_practice_problem_11_10_and_problem_11_75():
    res = _ac("e,1,0,165:r1,1,2,10:r2,2,0,4j:r3,2,3,8:r4,3,0,-6j")
    assert pf("e", res) == "pf: 0.93595 lagging"
    res = _ac("e,1,0,240:r1,1,0,80-50j:r2,1,0,120+70j:r3,1,0,60")
    assert pf("e", res) == "pf: 0.99805 leading"


def test_a_source_reads_the_load_it_sees_and_not_its_consumed_power():
    """The distinction the tool exists for: `s_e` is the power the source
    consumes, and read on that the word comes out backwards."""
    res = _ac("e,1,0,10:r,1,0,3+4j")
    assert pf("e", res) == "pf: 0.6 lagging"
    assert pf("r", res) == "pf: 0.6 lagging"
    # The control: the same reading taken on the consumed power flips.
    assert pf_reading(complex(res["s_e"])) == (0.6, "leading")
    assert pf_reading(complex(-res["s_e"])) == (0.6, "lagging")


def test_a_current_source_is_read_the_same_way():
    res = _ac("j,1,0,2:r,1,0,3+4j")
    assert pf("j", res) == "pf: 0.6 lagging"
    assert pf("r", res) == "pf: 0.6 lagging"


def test_coil_and_capacitor_take_the_load_convention():
    res = _ac("e,1,0,10:l,1,0,0.04:c,1,0,1e-4", omega=1000)
    assert pf("l", res) == "pf: 0.0 lagging"
    assert pf("c", res) == "pf: 0.0 leading"


def test_quotes_and_spelling_are_forgiven():
    res = _ac("e,1,0,30:r1,1,2,6:r2,2,0,-2j:r3,2,0,4")
    assert pf('"e"', res) == pf(" e ", res) == "pf: 0.97342 leading"


def test_name_form_refuses_what_it_cannot_read():
    res = _ac("e,1,0,240:r1,1,0,80-50j:r2,1,0,120+70j:r3,1,0,60:c,1,0,x",
              omega=2 * sp.pi * 50)
    with pytest.raises(ValueError, match="did not evaluate"):
        pf("e", res)                                  # x is still in it
    assert pf(res["s_e"]).free_symbols == {sp.Symbol("x")}   # this form does
    with pytest.raises(ValueError, match="not an element"):
        pf("r9", res)
    with pytest.raises(ValueError, match="no sign convention"):
        pf("g1", {"v_g1": sp.Integer(1), "i_g1": sp.Integer(1)})


def test_float_noise_in_the_reactive_part_is_not_a_word():
    assert pf_reading(complex(12.0, 1e-13)) == (1.0, "")
    assert pf_reading(complex(12.0, 1e-3)) == (1.0, "lagging")
