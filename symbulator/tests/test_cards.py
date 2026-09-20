"""evaluate() and solve(): the app's Evaluate and Solve cards (#466).

Each case is one the app's cards were once wrong about, or one of the
built-in entries; `notebooks/check_books.py` is the exhaustive check, over
every entry and against the app itself, and these are the ones that must
hold with no app beside the package.
"""

import pytest
import sympy as sp

from symbulator import ac, dc, er, evaluate, fd, port, solve, th, tr, t

DIVIDER = "e1,1,0,vs:r1,1,2,1'k:r2,2,0,1'k"


def test_a_ratio_of_two_answers_and_every_spelling():
    res = dc(DIVIDER)
    assert evaluate(res, "v2/vs") == sp.Rational(1, 2)
    for spelling in ("i_r1", "ir1", "IR1", "i_R1"):
        assert evaluate(res, spelling) == sp.Symbol("vs") / 2000


def test_a_condition_is_the_calculators_with_operator():
    assert evaluate(dc(DIVIDER), "v2", conditions=["vs = 10"]) == 5


def test_a_condition_that_is_an_equation_is_refused():
    with pytest.raises(ValueError, match="solve"):
        evaluate(dc(DIVIDER), "v2", conditions=["vs + 1 = 10"])


def test_a_rearranger_acts_on_the_answer_not_on_its_name():
    got = evaluate(dc(DIVIDER), "expand((v2 + 1)^2)")
    assert got == sp.expand((sp.Symbol("vs") / 2 + 1) ** 2)
    assert isinstance(got, sp.Add)          # left expanded, not re-gathered


def test_an_answer_named_like_a_sympy_function():
    # `re1` is fine; `re` alone is SymPy's real part, and the Solve card
    # died on "re = 12000" until the text was rewritten first
    res = dc("e,1,0,v:r1,1,2,1'k:r2,2,0,1'k")
    assert solve(res, ["re = x"], ["x"]) == [{"x": 2000}]


def test_s2t_limit_and_a_condition_at_infinity():
    res = fd("e,1,0,{u(t)}:r,1,2,1:c,2,0,1")
    assert evaluate(res, "s2t(v2)") == 1 - sp.exp(-t)
    assert evaluate(res, "limit(s*v2, s, 0)") == 1
    assert evaluate(res, "s*v2", conditions=["s = oo"]) == 0


def test_a_final_value_in_time():
    res = tr("e,1,0,10:r,1,2,1:c,2,0,1")
    assert evaluate(res, "v2", conditions=["t = oo"]) == 10
    assert solve(res, ["v2 = 5"], ["t"], real_only=True) == [{"t": sp.log(2)}]


def test_an_element_voltage_drop_in_fd_and_tr():
    # `v_c` is among the answers in dc and ac but not in fd or tr, where
    # the third level is not computed; the cards derive it from the nodes
    # the element spans, as the app's display does
    res = fd("e,1,0,{u(t)}:r,1,2,1:c,2,0,1")
    assert "v_c" not in res.values
    assert evaluate(res, "vc") == evaluate(res, "v2")
    assert solve(res, ["vc = 1/s - 1/(s+1)"], ["x"]) == []      # consistent
    step = tr("e,1,0,10:r,1,2,1:c,2,0,1")
    assert evaluate(step, "vc", conditions=["t = oo"]) == 10
    # rounded() and at() return a Result of their own, which must carry
    # the circuit with it or the drops quietly stop resolving
    assert evaluate(step.rounded(4), "vc", conditions=["t = oo"]) == 10
    assert evaluate(step.at(t=0), "vc") == 0


def test_braces_are_refused_outside_fd():
    with pytest.raises(Exception, match="FD"):
        evaluate(dc(DIVIDER), "{u(t)}")


def test_pf_of_an_element_and_of_a_value():
    res = ac("e,1,0,10:r,1,2,3:l,2,0,4", 1)
    assert abs(evaluate(res, "pf(e)") - 0.6) < 1e-12
    assert abs(evaluate(res, "pf(-se)") - 0.6) < 1e-12
    with pytest.raises(ValueError, match="one value"):
        evaluate(res, "pf(e, 1)")


def test_the_thevenin_load_answers():
    eq = th("e1,1,0,12:r1,1,2,4'k:r2,2,0,2'k", "2", "0")
    by_hand = evaluate(eq, "vth/(req + 2)")
    assert evaluate(eq, "irl", conditions=["load = 2"]) == by_hand
    assert evaluate(eq, "prl", conditions=["load = req"]) == eq.pmax


def test_a_port_result_and_a_plain_mapping():
    z = port("r1,1,2,20:r2,2,0,40:r3,2,3,30", "1", "3", "z")
    assert evaluate(z, "z11*z22 - z12*z21") == 60 * 70 - 40 * 40
    zeq = er("r,1,2,10:l,2,0,1:c,1,0,c", "1", "0", domain="ac", omega=10)
    got = solve({"zeq": zeq}, ["im(zeq) = 0"], ["c"], real_only=True)
    assert len(got) == 1 and got[0]["c"].is_real
    with pytest.raises(TypeError, match="mapping"):
        evaluate(zeq, "zeq")


def test_a_frequency_may_be_written_as_a_book_writes_it():
    # ac() always sympified its omega; th(), er() and port() did not, so a
    # string reached the stamping code and failed there
    z = er("c,1,0,c:r1,1,2,10:l,2,0,5'm", "1", "0", domain="ac",
           omega="2*pi*2e3")
    got = solve({"zeq": z}, ["im(zeq) = 0"], ["c"], real_only=True)
    assert len(got) == 1
    assert abs(float(got[0]["c"]) - 1.23522615159288e-6) < 1e-18
    assert th("e,1,0,10:r,1,2,3:l,2,0,4", "2", "0", domain="ac",
              omega="1").z == th("e,1,0,10:r,1,2,3:l,2,0,4", "2", "0",
                                 domain="ac", omega=1).z


def test_a_pinning_condition_substitutes_and_a_comparison_filters():
    # #433: NR12's Example 3.10 from the Solve card
    bridge = ("e,1,0,V_s:r1,1,2,100:r2,1,3,1000:r3,2,0,R_3:rx,3,0,R_x:"
              "sg,2,3")
    got = solve(dc(bridge), ["isg = 0"], ["R_x"], conditions=["R_3 = 10"],
                real_only=True)
    assert got == [{"R_x": 100}]
    roots = solve(dc(DIVIDER), ["v2^2 = 9"], ["vs"])
    assert sorted(r["vs"] for r in roots) == [-6, 6]
    assert solve(dc(DIVIDER), ["v2^2 = 9"], ["vs"],
                 conditions=["vs > 0"]) == [{"vs": 6}]


def test_real_only_is_solve_against_csolve():
    res = dc(DIVIDER)
    assert len(solve(res, ["x^2 = -v2"], ["x"], conditions=["vs = 2"])) == 2
    assert solve(res, ["x^2 = -v2"], ["x"], conditions=["vs = 2"],
                 real_only=True) == []


def test_nothing_to_solve_for_says_so():
    with pytest.raises(ValueError, match="nothing to solve"):
        solve(dc("e1,1,0,5:r1,1,0,1"), ["v1 = 5"])
