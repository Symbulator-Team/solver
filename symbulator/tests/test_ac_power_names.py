"""#441 (Roberto, 13 Sep 2026): in AC the powers are `p` = Re(S), `q` = Im(S)
and `s`, under both conventions -- P, Q and S as every book writes them --
with `ap` an alias of `p` for the calculator habit. So `pr1 + pe1`
evaluates whichever way the RMS tick is set, and a name on a card is
always a name that exists.
"""
import sympy as sp

from symbulator import ac, dc

# NR12's Example 10.8, as the sampler describes it.
DESC = "e1,a,0,150:r1,a,b,1+2j:r2,b,0,12-16j:r3,b,c,1+3j:e2,c,0,39*ir2"
NAMES = ("r1", "r2", "r3", "e1", "e2")


def _scheme(use_rms):
    res = ac(DESC, omega=1, use_rms=use_rms)
    for n in NAMES:
        assert f"p_{n}" in res.values and f"q_{n}" in res.values and f"s_{n}" in res.values
        assert sp.simplify(res.values[f"p_{n}"] - sp.re(res.values[f"s_{n}"])) == 0
        assert sp.simplify(res.values[f"q_{n}"] - sp.im(res.values[f"s_{n}"])) == 0
        assert res.values[f"ap_{n}"] == res.values[f"p_{n}"]      # the alias
    return res


def test_peak_convention_reports_p_q_and_s():
    res = _scheme(use_rms=False)
    assert sp.simplify(sum(res.values[f"p_{n}"] for n in NAMES)) == 0
    assert sp.simplify(sum(res.values[f"q_{n}"] for n in NAMES)) == 0


def test_rms_convention_reports_the_same_names_at_twice_the_value():
    rms, peak = _scheme(use_rms=True), _scheme(use_rms=False)
    for n in NAMES:
        assert sp.simplify(rms.values[f"p_{n}"] - 2 * peak.values[f"p_{n}"]) == 0
        assert sp.simplify(rms.values[f"q_{n}"] - 2 * peak.values[f"q_{n}"]) == 0


def test_the_book_values_of_10_8():
    res = ac(DESC, omega=1, use_rms=False)
    assert sp.simplify(res.values["s_r1"] - (1690 + 3380 * sp.I)) == 0
    assert res.values["p_r1"] == 1690 and res.values["q_r1"] == 3380


def test_op_amp_reports_the_same_triple():
    res = ac("e,1,0,1:r1,1,n,1'k:r2,n,2,10'k:o,0,n,2:rl,2,0,2'k", omega=1000)
    assert "p_o" in res.values and "q_o" in res.values and res.values["ap_o"] == res.values["p_o"]


def test_dc_is_untouched():
    res = dc("e1,1,0,12:r1,1,0,4")
    assert res.values["p_r1"] == 36 and "ap_r1" not in res.values and "q_r1" not in res.values


def test_every_spelling_reaches_the_answer():
    res = ac(DESC, omega=1)
    assert res["qr1"] == res["q_r1"] and res["apr1"] == res["pr1"] == res["p_r1"]
