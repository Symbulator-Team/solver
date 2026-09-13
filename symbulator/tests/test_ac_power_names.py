"""#439 (Roberto, 13 Sep 2026): in AC the real power answers to both of
its names, `p_` and `ap_`, whatever the convention -- so an Evaluate of
`pr1 + pr2 + pr3 + pe1 + pe2` is a number with RMS off, as
`sr1 + ... + se2` always was. The calculator named the average power `ap`
with peak phasors and `p` with RMS ones, and version 9 kept that, which
left `pr1` an unknown symbol under the default convention.
"""
import sympy as sp

from symbulator import ac

# NR12's Example 10.8, as the sampler describes it.
DESC = "e1,a,0,150:r1,a,b,1+2j:r2,b,0,12-16j:r3,b,c,1+3j:e2,c,0,39*ir2"


def _both_names(use_rms):
    res = ac(DESC, omega=1, use_rms=use_rms)
    for name in ("r1", "r2", "r3", "e1", "e2"):
        assert f"p_{name}" in res.values and f"ap_{name}" in res.values
        assert sp.simplify(res.values[f"p_{name}"] - res.values[f"ap_{name}"]) == 0
        assert sp.simplify(res.values[f"p_{name}"] - sp.re(res.values[f"s_{name}"])) == 0
    return res


def test_peak_convention_has_both_names():
    res = _both_names(use_rms=False)
    # the balance Roberto could not evaluate: every real power sums to 0
    total = sum(res.values[f"p_{n}"] for n in ("r1", "r2", "r3", "e1", "e2"))
    assert sp.simplify(total) == 0


def test_rms_convention_has_both_names():
    res = _both_names(use_rms=True)
    peak = ac(DESC, omega=1, use_rms=False)
    # RMS reports twice the power peak does, under either name
    assert sp.simplify(res.values["p_r1"] - 2 * peak.values["ap_r1"]) == 0


def test_op_amp_power_has_both_names_too():
    res = ac("e,1,0,1:r1,1,n,1'k:r2,n,2,10'k:o,0,n,2:rl,2,0,2'k", omega=1000)
    assert "p_o" in res.values and "ap_o" in res.values
    assert sp.simplify(res.values["p_o"] - res.values["ap_o"]) == 0
