"""#315: the package in a notebook -- the tutorial's spellings accepted
on a Result, rich display for the three result objects, `polar()`,
`Result.rounded()` and the cell magics."""

import sympy as sp
import pytest

from symbulator import (dc, ac, th, port, tr, polar, Phasor, PortResult,
                        Result)
from symbulator._display import value_latex, name_latex, aligned
from symbulator.notebook import _parse_options, run

DIVIDER = "e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k"


# --- lookup by either spelling ------------------------------------------

def test_tutorial_spelling_finds_the_same_answer():
    res = dc(DIVIDER)
    assert res["ir1"] == res["i_r1"] == sp.Rational(1, 400)
    assert res["v2"] == res["v_2"] == sp.Rational(5, 2)
    assert res["pr1"] == res["p_r1"]
    assert res["re1"] == res["r_e1"] == 2000


def test_two_letter_quantity_wins_over_one():
    res = ac("e1,1,0,10:r1,1,2,100:c1,2,0,1e-6", omega=1000)
    assert res["apr1"] == res["ap_r1"]
    assert res["sr1"] == res["s_r1"]
    assert res["ze1"] == res["z_e1"]


def test_get_contains_iter_len():
    res = dc(DIVIDER)
    assert "v2" in res and "v_2" in res and "v9" not in res
    assert 42 not in res
    assert res.get("v9") is None and res.get("v9", 0) == 0
    assert list(res) == sorted(res.values)
    assert len(res) == len(res.values)


def test_missing_key_names_the_answers():
    with pytest.raises(KeyError) as err:
        dc(DIVIDER)["v9"]
    assert "v_2" in str(err.value) and "i_r1" in str(err.value)


def test_at_accepts_either_spelling():
    res = tr("e1,1,0,5:r1,1,2,1000:c1,2,0,1e-6", variables=["v_2"])
    assert res.at("v2", t=0.001) == res.at("v_2", t=0.001)


def test_own_unknown_is_found_directly():
    res = dc("e1,1,0:r1,1,2,4'k:r2,2,0,2'k".replace("e1,1,0", "e1,1,0,12"),
             equations=["pout = v_2*i_r2"])
    assert "pout" in res and res["pout"] == res.values["pout"]


# --- rich display --------------------------------------------------------

def test_result_latex_is_one_aligned_block():
    tex = dc(DIVIDER)._repr_latex_()
    assert tex.startswith("$\\displaystyle") and tex.endswith("$")
    assert "\\begin{aligned}" in tex and "\\end{aligned}" in tex
    assert "\\text{dc analysis}" in tex
    assert "i_{r1} &= \\frac{1}{400}" in tex
    assert tex.count(" \\\\ ") == len(dc(DIVIDER)) - 1 + 1   # rows, plus the caption


def test_imaginary_unit_is_j_and_infinity_is_plain():
    import re
    tex = ac("e1,1,0,10:r1,1,2,100:c1,2,0,1e-6", omega=1000)._repr_latex_()
    assert re.search(r"\d j\b", tex) and not re.search(r"\d i\b", tex)
    assert value_latex(sp.zoo) == "\\infty"
    assert value_latex(-sp.oo) == "-\\infty"
    assert value_latex("plain") == "\\text{plain}"
    assert value_latex(2.5) == "2.5" and value_latex(3) == "3"


def test_name_latex_follows_sympy():
    assert name_latex("i_r1") == "i_{r1}"
    assert name_latex("pout") == "pout"


def test_aligned_without_caption_has_no_gathered():
    assert "gathered" not in aligned([("a", "1")])
    assert "gathered" in aligned([("a", "1")], "cap")


def test_thevenin_latex_labels_req_or_zeq():
    eq = th("e1,1,0,12:r1,1,2,4'k:r2,2,0,2'k", "2", "0", domain="dc")
    tex = eq._repr_latex_()
    assert "vth &= 4" in tex and "req &=" in tex and "zeq" not in tex
    assert "Thevenin" in tex


def test_port_result_is_a_dict_and_a_matrix():
    p = port("r1,1,3,100:r2,2,3,200:r3,3,0,50", "1", "2", "z")
    assert isinstance(p, dict) and isinstance(p, PortResult)
    assert p["11"] == 150 and p["22"] == 250 and p.kind == "z"
    assert set(p) == {"11", "12", "21", "22"}
    tex = p._repr_latex_()
    assert "\\begin{bmatrix}150 & 50 \\\\ 50 & 250\\end{bmatrix}" in tex
    assert "\\mathbf{z}" in tex
    assert repr(p).startswith("PortResult('z', 11=150")


def test_multiple_solutions_are_named_in_the_caption():
    res = Result(domain="dc", values={"x": sp.Integer(1)},
                 solutions=[{"x": sp.Integer(1)}, {"x": sp.Integer(2)}])
    assert "2 solutions" in res._repr_latex_()


# --- rounded() and polar() ----------------------------------------------

def test_rounded_shortens_floats_and_keeps_integers():
    res = ac("e1,1,0,10:r1,1,2,100:l1,2,3,0.1:c1,3,0,1e-6", omega=1000)
    r4 = res.rounded(4)
    assert str(r4["v2"]) == "9.878 - 1.098*I"
    assert res["v2"] != r4["v2"]                    # the original is untouched
    assert dc("e1,1,0,36:r1,1,0,4").rounded(4)["v1"] == 36
    assert str(dc(DIVIDER).rounded(3)["v2"]) == "2.50"
    assert r4.domain == "ac" and len(r4.solutions) == 1


def test_polar_matches_the_aa_tool():
    p = polar(3 + 4j)
    assert isinstance(p, Phasor)
    assert (p.magnitude, p.angle) == (sp.Float("5.000", 4), sp.Float("53.13", 4))
    assert repr(p) == "5.000∠53.13°"
    assert "\\angle" in p._repr_latex_() and "^\\circ" in p._repr_latex_()
    assert list(p) == [p.magnitude, p.angle]
    assert abs(complex(p) - (3 + 4j)) < 1e-3


def test_polar_edge_cases():
    assert repr(polar(0)) == "0∠0°"
    assert repr(polar(-2)) == "2.000∠180.0°"
    assert repr(polar("3+4j", None)).startswith("5.0000")
    with pytest.raises(ValueError):
        polar(sp.Symbol("x") + 1)


def test_polar_takes_an_answer_straight():
    res = ac("e1,1,0,10:r1,1,2,100:c1,2,0,1e-6", omega=1000)
    mag, ang = polar(res["v2"])
    assert float(mag) == pytest.approx(abs(complex(res["v2"])), rel=1e-3)


# --- the cell magics ----------------------------------------------------

def test_option_line_parsing():
    assert _parse_options("omega=1000 rms variables=v_2,i_r1 into=res nodraw") == {
        "omega": 1000, "rms": True, "variables": ["v_2", "i_r1"],
        "into": "res", "nodraw": True}
    assert _parse_options("omega=w") == {"omega": "w"}
    assert _parse_options("") == {}


def test_run_without_ipython_binds_the_result():
    ns = {}
    res = run("ac", "omega=1000 rms nodraw into=res",
              "e1,1,0,10\nr1,1,2,100\nl1,2,3,0.1\nc1,3,0,1e-6", ns)
    assert res.domain == "ac" and ns["res"] is res
    assert "p_r1" in res            # use_rms=True names the power p_, not ap_


def test_run_single_variable_becomes_a_list():
    res = run("tr", "nodraw variables=v_2", "e1,1,0,5\nr1,1,2,1000\nc1,2,0,1e-6")
    assert list(res) == ["v_2"]


def test_extension_registers_four_magics():
    ipython = pytest.importorskip("IPython")
    from IPython.core.interactiveshell import InteractiveShell
    ip = InteractiveShell.instance()
    ip.run_line_magic("load_ext", "symbulator")
    for name in ("dc", "ac", "fd", "tr"):
        assert name in ip.magics_manager.magics["cell"]
    res = ip.run_cell_magic("dc", "nodraw into=res", DIVIDER.replace(":", "\n"))
    assert ip.user_ns["res"] is res and res["v2"] == sp.Rational(5, 2)
