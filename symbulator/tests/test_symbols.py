"""Reserved symbols, case folding and the SI-prefix set."""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import sympy as sp
import pytest

from symbulator import dc, ac, fd, th, er, port
from symbulator.elements import CircuitError, parse_circuit
from symbulator.si_prefix import safe_sympify, hijacked_names, bare_suffix_match

MICRO = "µ"   # MICRO SIGN
GREEK_MU = "μ"  # GREEK SMALL LETTER MU


# --- the imaginary unit -------------------------------------------------

@pytest.mark.parametrize("form", ["3j", "3*j", "3*i", "3*I", "3*J", "j*3"])
def test_all_imaginary_spellings_agree(form):
    assert safe_sympify(form) == 3 * sp.I


def test_bare_j_is_the_imaginary_unit():
    assert safe_sympify("j") == sp.I


def test_imaginary_works_as_an_ac_source_value():
    # A purely imaginary source, written the way an engineer would.
    res = ac("e1,1,0,j*10:r1,1,0,100", omega=1000)
    assert sp.simplify(res.v("1") - 10 * sp.I) == 0


# --- i/I/j/J are reserved in AC only -------------------------------------
#
# Outside AC (dc, fd, and by extension tr, which runs on fd) there is no
# such thing as a complex component value, so there is no reason to take
# four names away from someone describing a circuit. Only AC -- and the
# AC mode of th()/er()/port() -- reserves them.

@pytest.mark.parametrize("form", ["i", "I", "j", "J"])
def test_imaginary_letters_are_plain_symbols_outside_ac(form):
    expr = safe_sympify(form, reserve_imaginary=False)
    assert expr == sp.Symbol(form)


@pytest.mark.parametrize("form", ["i", "I", "j", "J"])
def test_imaginary_letters_still_reserved_by_default(form):
    # The default (reserve_imaginary=True) is unchanged, so every caller
    # that hasn't been made domain-aware keeps today's behaviour.
    assert safe_sympify(form) == sp.I


def test_i_is_an_ordinary_dc_variable():
    # 'i' used as a symbolic resistor value: like Q in
    # test_quality_factor_survives_a_solve, it stays a free symbolic
    # parameter that cancels out of this divider -- this used to be
    # impossible (i always meant sp.I, so this circuit would have solved
    # for a nonsensical complex resistance instead).
    res = dc("e1,1,0,10:r1,1,2,i:r2,2,0,i")
    assert sp.simplify(res.v("2") - 5) == 0


def test_i_is_an_ordinary_fd_variable():
    res = fd("e1,1,0,10:r1,1,2,i:r2,2,0,i")
    assert sp.simplify(res.v("2") - 5) == 0


def test_j_is_an_ordinary_dc_element_value_and_source():
    # A current source literally named with a value of 'j' (the letter,
    # not the imaginary unit) is a perfectly ordinary symbolic current
    # outside AC -- mirrors test_current_divider_dc's j1 pattern.
    res = dc("j1,0,1,j:r1,1,0,10")
    assert sp.simplify(res.v("1") - 10 * sp.Symbol("j")) == 0


def test_ac_still_normalises_and_reserves_imaginary_spellings():
    # The AC path (and its expert-mode equations/conditions) is untouched
    # by the AC-only change: every spelling still means sp.I there.
    res = ac("e1,1,0,10:r1,1,2,100:r2,2,0,100", omega=1000,
             equations=["v_x = i*v_2"], unknowns=["v_x"])
    assert sp.simplify(res["v_x"] - sp.I * res.v("2")) == 0


def test_th_reserves_imaginary_only_in_its_ac_mode():
    # th() in DC: 'i' is an ordinary symbolic resistor value (a divider,
    # so it cancels out of the open-circuit voltage regardless).
    dc_th = th("e1,1,0,10:r1,1,2,i:r2,2,0,i", "2", "0", domain="dc")
    assert sp.simplify(dc_th.vth - 5) == 0

    # th() in AC: 'j' still means the imaginary unit.
    ac_th = th("e1,1,0,j*10:r1,1,2,100:r2,2,0,100", "2", "0",
              domain="ac", omega=1000)
    assert sp.simplify(ac_th.vth - 5 * sp.I) == 0


# --- names SymPy would otherwise hijack ---------------------------------

@pytest.mark.parametrize("name", ["Q", "S", "N", "beta", "gamma", "E"])
def test_hijack_prone_names_become_plain_variables(name):
    expr = safe_sympify(name)
    assert expr.free_symbols == {sp.Symbol(name)}


def test_quality_factor_survives_a_solve():
    # Q is an assumptions object to bare sympify; here it must behave as
    # an ordinary symbolic resistance.
    res = dc("e1,1,0,10:r1,1,2,Q:r2,2,0,Q")
    assert sp.simplify(res.v("2") - 5) == 0


def test_hijacked_names_are_reported():
    found = hijacked_names("Q*2 + beta + r_load + pi + exp(1)")
    assert set(found) == {"Q", "beta"}
    assert "r_load" not in found and "pi" not in found


def test_capital_i_not_reported_as_hijacked_outside_ac():
    # 'I' is the one imaginary spelling that also happens to collide with
    # a real SymPy attribute (sp.I) -- outside AC it's read as a plain
    # symbol (see safe_sympify(reserve_imaginary=False)), so it must not
    # be reported as though SymPy's built-in meaning got in the way.
    found = hijacked_names("I*2 + beta", reserve_imaginary=False)
    assert "I" not in found
    assert "beta" in found

    # With the default (AC / unspecified), I is exempted a different way
    # -- it's in the allowed/reserved namespace -- but the outcome (not
    # reported) is the same, since reporting it as "hijacked" would be
    # confusing either way: it's supposed to mean the imaginary unit.
    assert "I" not in hijacked_names("I*2 + beta")


def test_intended_constants_still_work():
    assert safe_sympify("2*pi*1000") == 2000 * sp.pi
    assert safe_sympify("sqrt(2)") == sp.sqrt(2)
    assert safe_sympify("exp(1)") == sp.E          # Euler via exp(), not E


# --- case folding -------------------------------------------------------

def test_element_names_and_nodes_fold_to_lowercase():
    els = parse_circuit("E1,A,0,10:R1,A,B,100:RLoad,B,0,200")
    assert [e.name for e in els] == ["e1", "r1", "rload"]
    assert els[1].n1 == "a" and els[1].n2 == "b"


def test_same_name_in_different_case_is_a_duplicate():
    with pytest.raises(CircuitError):
        parse_circuit("e1,1,0,10:r1,1,2,100:R1,2,0,100")


def test_mutual_inductance_references_fold_too():
    els = parse_circuit(
        "e1,1,0,10:L1,1,2,0.1:L2,3,0,0.1:M1,L1,L2,0.05:r1,2,0,50:r2,3,0,50")
    m = next(e for e in els if e.kind == "m")
    assert m.fields[0] == "l1" and m.fields[1] == "l2"


def test_uppercase_circuit_solves_identically():
    a = dc("E1,1,0,10:R1,1,2,100:R2,2,0,100")
    b = dc("e1,1,0,10:r1,1,2,100:r2,2,0,100")
    assert sp.simplify(a.v("2") - b.v("2")) == 0


def test_values_keep_their_case():
    # 'M is mega and 'm is milli -- folding these would be catastrophic.
    els = parse_circuit("e1,1,0,5:r1,1,0,4.7'M:r2,1,0,4.7'm")
    assert els[1].fields[2] != els[2].fields[2]


# --- SI prefixes --------------------------------------------------------

def test_exa_is_gone_so_E_belongs_to_scientific_notation():
    assert bare_suffix_match("8E") is None
    assert bare_suffix_match("8E3") is None
    res = dc("e1,1,0,10:r1,1,0,8E3")
    assert abs(complex(res.i("r1")) - complex(10 / 8000)) < 1e-12


@pytest.mark.parametrize("mu", [MICRO, GREEK_MU])
def test_both_micro_characters_are_accepted(mu):
    assert bare_suffix_match(f"4.7{mu}") == ("4.7", mu)
    res = dc(f"e1,1,0,5:r1,1,0,4.7'{mu}")
    assert sp.simplify(res.i("r1") - 5 / sp.Rational(47, 10000000)) == 0


# --- packaging ----------------------------------------------------------

def test_version_is_exposed_and_well_formed():
    import symbulator
    import re as _re

    # A bare x.y.z is a release; an optional PEP 440 local label
    # (0.5.26+x1) marks a checkout that is not on PyPI (X1).
    assert _re.fullmatch(r"\d+\.\d+\.\d+(\+[a-z0-9]+(\.[a-z0-9]+)*)?",
                         symbulator.__version__)


def test_version_matches_the_installed_distribution_when_there_is_one():
    # pyproject reads __version__ at build time; if this package is
    # installed, the two must agree. Skipped when running from a source
    # tree with nothing installed.
    import symbulator
    from importlib.metadata import version, PackageNotFoundError

    try:
        installed = version("symbulator")
    except PackageNotFoundError:
        pytest.skip("symbulator is not installed in this environment")
    assert installed == symbulator.__version__


def test_values_must_be_arithmetic():
    # sympify() evaluates the value text as Python; the namespace trick
    # only constrains names. Syntax is now checked first, so nothing that
    # isn't plain arithmetic reaches eval -- the web app relies on this.
    import pytest
    from symbulator import dc, ex, UnsafeExpressionError
    from symbulator.si_prefix import check_expression_syntax
    for good in ["5", "-1e-6", "2*x**2 + 1", "sqrt(2)*exp(-3)", "E*R2/(R1+R2)",
                 "pr(2, 3)", "3*I", "Heaviside(t)"]:
        check_expression_syntax(good)
    for bad in ["1 if 1 else 0", "x.real", "(lambda: 7)()", "__builtins__",
                "f(k=1)", "'5'", "True", "a[0]", "x := 1"]:
        with pytest.raises(UnsafeExpressionError):
            check_expression_syntax(bad)
    with pytest.raises(UnsafeExpressionError):
        dc("e1,1,0,1 if 1 else 0:r1,1,0,1")
    with pytest.raises(UnsafeExpressionError):
        ex("e1,1,0,5:r1,1,0,rx", "dc", equations=["i_r1 = 1 if 1 else 2"],
           unknowns=["rx"])
    assert dc("e1,1,0,sqrt(4):r1,1,0,1")["v_1"] == 2
