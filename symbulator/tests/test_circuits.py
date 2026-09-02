"""
Validation suite: textbook circuits with known, independently
hand-calculated answers, checked against the symbulator port.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import sympy as sp
import pytest

from symbulator import dc, ac, pr
from symbulator.elements import CircuitError


def approx_eq(a, b, tol=1e-9):
    diff = sp.simplify(sp.N(a) - sp.N(b))
    return abs(complex(diff)) < tol


def test_voltage_divider_dc():
    res = dc("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k")
    assert approx_eq(res.v("2"), 2.5)
    assert approx_eq(res.i("r1"), 2.5e-3)
    assert approx_eq(res.i("r2"), 2.5e-3)


def test_voltage_divider_dc_unequal():
    # 10V across a 3k/1k divider -> node between them = 10 * 1k/(3k+1k) = 2.5V
    res = dc("e1,1,0,10:r1,1,2,3'k:r2,2,0,1'k")
    assert approx_eq(res.v("2"), 2.5)


def test_current_divider_dc():
    # 6A injected into a node with 1k and 3k to ground in parallel.
    res = dc("j1,0,1,6:r1,1,0,1'k:r2,1,0,3'k")
    expected_v = 6 * float(pr(1000, 3000))
    assert approx_eq(res.v("1"), expected_v)
    assert approx_eq(res.i("r1"), expected_v / 1000)
    assert approx_eq(res.i("r2"), expected_v / 3000)


def test_bracket_parallel_resistor_shortcut():
    # r1,1,2,[10,20,30] is the calculator's `[...]` shortcut for three
    # resistors in parallel, expanded internally to pr(10,20,30). Two
    # things had to work together for this to parse at all: splitting
    # an element's fields on "," must skip commas inside pr(...)'s own
    # parentheses, and "pr" must resolve to the real function rather
    # than becoming a plain symbol once the value field is sympified.
    res = dc("e1,1,0,10:r1,1,2,[10,20,30]:r2,2,0,5")
    expected_r1 = float(pr(10, 20, 30))
    expected_v2 = 10 * 5 / (expected_r1 + 5)
    assert approx_eq(res.v("2"), expected_v2)
    assert approx_eq(res.i("r1"), 10 / (expected_r1 + 5))

    # A single bracketed value degenerates to just that value.
    res2 = dc("e1,1,0,10:r1,1,2,[10]:r2,2,0,10")
    assert approx_eq(res2.v("2"), 5)


def test_series_rlc_ac_impedance():
    R, L, C, w = 100, 0.1, 1e-6, 1000
    res = ac("e1,1,0,10:r1,1,2,100:l1,2,3,0.1:c1,3,0,1e-6", omega=w)
    z_expected = R + 1j * w * L + 1 / (1j * w * C)
    i_expected = 10 / z_expected
    assert approx_eq(res.i("e1"), -i_expected)  # e1 current defined n1->n2 = into source = -loop current
    # cross-check via voltage drop / current (r1 current is defined n1->n2, same as the loop current)
    z_measured = sp.N((res.v("1") - res.v("2")) / res.i("r1"))
    assert approx_eq(z_measured, R)


def test_opamp_inverting_amplifier_dc():
    # Vout = -(Rf/Rin) * Vin, ideal op-amp, non-inverting input grounded.
    res = dc("e1,1,0,2:rin,1,2,1'k:rf,2,3,4'k:o1,0,2,3")
    assert approx_eq(res.v("2"), 0)  # virtual ground
    assert approx_eq(res.v("3"), -8)


def test_opamp_noninverting_amplifier_dc():
    # Non-inverting amp: Vout = Vin * (1 + Rf/Rg), input applied to +.
    res = dc("e1,1,0,3:rg,2,0,1'k:rf,2,3,2'k:o1,1,2,3")
    assert approx_eq(res.v("2"), 3)  # virtual short to + input
    assert approx_eq(res.v("3"), 3 * (1 + 2000 / 1000))


def test_voltage_controlled_voltage_source_dc():
    # e2 = 2 * v_2, where v_2 is set to 5V by an upstream divider.
    res = dc("e1,1,0,10:r1,1,2,1'k:r2,2,0,1'k:e2,3,0,2*v_2:r3,3,0,500")
    assert approx_eq(res.v("2"), 5)
    assert approx_eq(res.v("3"), 10)
    assert approx_eq(res.i("r3"), 10 / 500)


def test_pr_parallel_helper():
    assert approx_eq(pr(1000, 1000), 500)
    assert approx_eq(pr(1000, 3000), 750)
    assert pr(1000, 0) == 0


def test_ideal_transformer_ac():
    # Step-down 2:1 ideal transformer, driven directly on the primary.
    res = ac("e1,1,0,10:t1,1,2,2,1:rl,2,0,100", omega=1000)
    assert approx_eq(res.v("2"), 5)
    assert approx_eq(res.i("rl"), 5 / 100)


def test_mutual_inductance_zero_coupling_isolates_secondary():
    # Secondary loop (l2 + rload) is grounded on one end so its node
    # voltages are well-defined. With M=0 it has no source of its own,
    # so it should carry no current and show 0V.
    res = ac("e1,1,0,10:l1,1,0,0.05:m1,l1,l2,0:l2,0,4,0.02:rload,4,0,50",
             omega=500)
    assert approx_eq(res.v("4"), 0)
    assert approx_eq(res.i("l2"), 0)


def test_mutual_inductance_couples_secondary():
    # Same topology with nonzero M: the secondary should now carry
    # induced current, i.e. it must NOT stay at zero.
    res = ac("e1,1,0,10:l1,1,0,0.05:m1,l1,l2,0.01:l2,0,4,0.02:rload,4,0,50",
             omega=500)
    assert abs(complex(sp.N(res.i("l2")))) > 1e-6


def test_two_port_y_parameters_as_series_resistor():
    # y-parameters of an ideal 1k series resistor between two grounded
    # ports: y11=y22=1/Z, y12=y21=-1/Z. Chaining it after a 10V source
    # into a 1k load should behave exactly like a plain voltage divider.
    params = {"y1": {"11": "0.001", "12": "-0.001", "21": "-0.001", "22": "0.001"}}
    res = dc("e1,1,0,10:y1,1,2:rl,2,0,1'k", params=params)
    assert approx_eq(res.v("2"), 5)


def test_dc_power_derived_quantity():
    res = dc("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k")
    i = float(sp.N(res.i("r1")))
    assert approx_eq(res["p_r1"], i * i * 1000)


def test_ac_complex_power_derived_quantity():
    res = ac("e1,1,0,10:r1,1,0,50", omega=1000)
    # Pure resistive load: average power (peak convention) = |V|^2/(2R).
    # `ap_` holds the average power, not the apparent power. On a resistor
    # the two coincide, so this assertion cannot tell them apart -- which
    # is exactly why the README called `ap_` apparent for months without
    # a test ever going red over it (#223). The next test can tell.
    assert approx_eq(res["ap_r1"], 10**2 / (2 * 50))


def test_ap_is_average_power_not_apparent_power():
    """`ap_` is the real part of the complex power, not its magnitude.

    A resistive load cannot show the difference. A reactive one can: at
    the source of this series R-L, S = -0.6 - 0.8j VA, so the average
    power is -0.6 W while the apparent power |S| is 1.0 VA. If `ap_`
    were ever made the magnitude, this goes red; the resistor test above
    would not. #223.
    """
    # 10 V peak, omega = 1000, R = 30, omega*L = 40 -> |Z| = 50, pf = 0.6
    res = ac("e1,1,0,10:r1,1,2,30:l1,2,0,0.04", omega=1000)
    s = sp.N(res["s_e1"])
    assert approx_eq(res["ap_e1"], sp.re(s))
    assert approx_eq(res["ap_e1"], -0.6)
    assert approx_eq(abs(s), 1.0)
    # ...and the two really are different numbers here.
    assert not approx_eq(res["ap_e1"], abs(s))


def test_third_level_capacitor_current_is_fully_substituted():
    # Regression test: a capacitor's current is stamped as a formula in
    # terms of the node-voltage *symbols* (Circuit.v()) before the KCL
    # system is solved -- so without a final substitution pass, it used
    # to come back still containing e.g. v_3 even though v_3 itself was
    # solved to a plain number two lines earlier in solve_circuit(). On
    # the original calculator this kind of "third-level" quantity was
    # evaluated on the fly; here it needs one explicit substitution pass
    # at the end. Reproduces the series RLC example from the built-in
    # examples list, reported against symbulator 0.4.2.
    res = ac("e1,1,0,10:r1,1,2,100:l1,2,3,0.1:c1,3,0,1e-6", omega=1000)
    assert res.v("3").free_symbols == set()
    assert res.i("c1").free_symbols == set()
    # i = (v1 - v2) * j*omega*C, checked against the independently
    # solved v_3 rather than re-deriving it from scratch.
    expected_i = res.v("3") * (sp.I * 1000 * 1e-6)
    assert approx_eq(res.i("c1"), expected_i)


def test_third_level_complex_power_has_no_leftover_conjugate():
    # Same bug, one layer further downstream: complex power is
    # V * conjugate(I), computed in analysis.py from the (now-fixed)
    # branch current -- confirm no symbolic conjugate(v_3) leaks into
    # the answer, and that the number matches a manual V*conj(I)/2.
    res = ac("e1,1,0,10:r1,1,2,100:l1,2,3,0.1:c1,3,0,1e-6", omega=1000)
    s_c1 = res["s_c1"]
    assert s_c1.free_symbols == set()
    expected_s = sp.simplify(res.v("3") * sp.conjugate(res.i("c1")) / 2)
    assert approx_eq(s_c1, expected_s)
    # A pure capacitor stores/returns energy but dissipates none, so its
    # complex power should be purely reactive (zero real part).
    assert approx_eq(sp.re(s_c1), 0)


def test_ac_complex_power_noise_is_zeroed_not_just_small():
    # Regression test for a bug reported against 0.4.3: the *negligible*
    # part of a purely-real or purely-imaginary complex power wasn't an
    # exact zero, just floating-point noise close to zero (e.g.
    # `4.445e-18j` next to a resistor's real power, or `-7.589e-19`
    # next to an inductor's reactive power). approx_eq-style tolerance
    # checks (see test_third_level_complex_power_has_no_leftover_
    # conjugate above) don't catch this, because the noise genuinely is
    # tiny -- the bug is that it was never rounded to a clean, exact 0,
    # so every display mode (including "exact") showed its own leftover
    # digits instead of agreeing the offending part is zero.
    res = ac("e1,1,0,10:r1,1,2,100:l1,2,3,0.1:c1,3,0,1e-6", omega=1000)
    # A resistor's complex power is purely real: the imaginary part
    # must be an exact 0, not merely close to it.
    s_r1 = res["s_r1"]
    assert sp.im(s_r1) == 0
    assert approx_eq(sp.re(s_r1), res["ap_r1"])
    # An inductor's complex power is purely reactive: the real part
    # must be an exact 0.
    s_l1 = res["s_l1"]
    assert sp.re(s_l1) == 0
    assert sp.im(s_l1) != 0  # still genuinely reactive, not zeroed out entirely


def test_third_level_dependent_current_source_is_fully_substituted():
    # Same class of bug, different element: a dependent current source's
    # value can reference another element's *current* by name (not just
    # capacitors referencing node voltages), and was stamped into
    # `known` the same un-substituted way.
    res = dc("e1,1,0,10:r1,1,2,1000:j1,2,0,0.5*i_r1:r2,2,0,1000")
    assert res.i("j1").free_symbols == set()
    assert approx_eq(res.i("j1"), 0.5 * res.i("r1"))


def test_zero_valued_capacitor_is_open_not_short_dc():
    # Regression test for a bug found in the original TI-Basic source:
    # a 0F capacitor must behave as an open circuit, not a short.
    # e1 -- r1 -- (node2) -- c1(0F) -- gnd : node2 should float up to
    # the full source voltage (no current flows, no drop across r1).
    res = dc("e1,1,0,9:r1,1,2,1'k:c1,2,0,0")
    assert approx_eq(res.v("2"), 9)
    assert approx_eq(res.i("c1"), 0)
    assert approx_eq(res.i("r1"), 0)


def test_zero_valued_capacitor_is_open_not_short_ac():
    res = ac("e1,1,0,9:r1,1,2,1'k:c1,2,0,0", omega=1000)
    assert approx_eq(res.v("2"), 9)
    assert approx_eq(res.i("c1"), 0)


def test_nonzero_capacitor_still_open_in_dc():
    # Make sure fixing the C=0 case didn't disturb the existing
    # (already-correct) nonzero-capacitance DC behavior.
    res = dc("e1,1,0,9:r1,1,2,1'k:c1,2,0,10e-6")
    assert approx_eq(res.v("2"), 9)
    assert approx_eq(res.i("c1"), 0)


def test_parser_rejects_missing_ground():
    with pytest.raises(CircuitError):
        dc("e1,1,2,5:r1,1,2,1'k")


def test_parser_rejects_duplicate_names():
    with pytest.raises(CircuitError):
        dc("e1,1,0,5:e1,2,0,3:r1,1,2,1'k")


def test_parser_rejects_wrong_field_count():
    with pytest.raises(CircuitError):
        dc("e1,1,0,5,99:r1,1,0,1'k")


def test_parser_rejects_names_that_break_expressions():
    # A hyphen, dot or space in a name would make i_<name> unwritable
    # inside a value or equation (`2*i_r-x` reads as `2*i_r - x`), so
    # the parser refuses the name outright, quoting it as typed.
    for bad in ("r-x", "r.1", "r x"):
        with pytest.raises(CircuitError, match="cannot be part of a name"):
            dc(f"e1,1,0,5:{bad},1,2,1'k:r2,2,0,1'k")


def test_parser_accepts_plain_names():
    # The rule must not cost anything it is not aimed at: a bare `r`,
    # digits, underscores and long names all still solve.
    for good in ("r", "r0", "r_1", "ris", "rlongname99"):
        res = dc(f"e1,1,0,5:{good},1,2,1'k:r2,2,0,1'k")
        assert res.i(good) == sp.Rational(1, 400)


def test_thevenin_style_two_source_dc():
    # Two sources feeding a common resistor node -- solved independently
    # via superposition by hand:
    #   e1=12V through r1=2k into node A; e2=6V through r2=1k into node A;
    #   rL=3k from A to ground.
    # Node equation: (V-12)/2k + (V-6)/1k + V/3k = 0
    # Multiply by 6k: 3(V-12) + 6(V-6) + 2V = 0 -> 11V = 36+36=72 -> V=72/11
    res = dc("e1,1,0,12:r1,1,2,2'k:e2,3,0,6:r2,3,2,1'k:rl,2,0,3'k")
    assert approx_eq(res.v("2"), sp.Rational(72, 11))


def test_source_with_no_current_reports_infinite_resistance():
    # A source feeding only a capacitor pushes no DC current, so the
    # "resistance seen" is infinite. Float values used to make this
    # raise ZeroDivisionError out of mpmath instead of answering.
    res = dc("e1,1,0,5:r1,1,2,4.7'k:c1,2,0,10*u")
    assert res.i("e1").is_zero
    assert res["r_e1"] == sp.oo
    assert sp.simplify(res.v("2") - 5) == 0


def test_zero_volt_zero_current_source_omits_resistance():
    # 0 V across 0 A is undefined, not infinite -- the key is omitted
    # rather than reporting a bogus number.
    res = dc("e1,1,0,0:r1,1,2,1'k:c1,2,0,1e-6")
    assert "r_e1" not in res.values


def test_floating_subcircuit_is_refused():
    # A part with no path to node 0 has undefined voltages; before this
    # check the solver returned it silently parametrized (v_2 = v_3).
    import pytest
    from symbulator import dc
    from symbulator.elements import CircuitError
    with pytest.raises(CircuitError, match="floating"):
        dc("e1,1,0,5:r1,2,3,1")
    # An op-amp and a grounded two-port block both count as connections.
    assert dc("e1,1,0,5:r1,1,2,1:o1,2,3,4:r2,4,0,1")["v_1"] == 5
    assert dc("e1,1,0,5:r1,1,2,1:z1,2,3")["v_1"] == 5


def test_unsolvable_circuits_name_the_contradiction():
    # Before: a generic "Could not solve... try symbolic values", which
    # cannot help when the circuit itself is contradictory.
    import pytest
    from symbulator import dc, ac
    from symbulator.elements import CircuitError
    with pytest.raises(CircuitError, match="e1, e2 form a loop"):
        dc("e1,1,0,5:e2,1,0,3")              # sources in parallel
    with pytest.raises(CircuitError, match="e1, r1 form a loop"):
        dc("e1,1,0,5:r1,1,0,0")              # 0 ohm across a source
    with pytest.raises(CircuitError, match="e2, l1 form a loop"):
        dc("e1,1,0,5:r1,1,2,1:l1,2,0,1e-3:e2,2,0,2")   # inductor is a dc short
    with pytest.raises(CircuitError, match="Node 1 connects only to j1, j2"):
        dc("j1,0,1,2:j2,1,0,3")              # current sources in series
    # The same inductor loop is perfectly fine in ac.
    assert abs(ac("e1,1,0,5:r1,1,2,1:l1,2,0,1e-3:e2,2,0,2", omega=1000)["v_2"] - 2) < 1e-12


def test_brackets_only_mean_pr_in_a_resistor_value():
    # #165: [...] is the parallel-resistor shorthand (r values) or a
    # two-port's parameter term. Anywhere else it used to be silently
    # passed to pr() -- e1,1,0,[4,4] became a meaningless "2 V" source
    # -- and now stops with a message instead.
    assert dc("e1,1,0,5:r1,1,0,[2,2]").i("r1") == 5          # 2||2 = 1 ohm
    for bad in ("e1,1,0,[4,4]:r1,1,0,1",
                "j1,0,1,0.001:c1,1,0,[2,2]",
                "e1,1,0,5:l1,1,2,[2,2]:r1,2,0,1"):
        with pytest.raises(CircuitError, match="parallel-resistor"):
            dc(bad)
    # A pr(...) the user typed is a function call, legitimate anywhere.
    dc("e1,1,0,5:l1,1,2,pr(2,2):r1,2,0,1")
