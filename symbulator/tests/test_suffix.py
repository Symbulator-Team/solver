"""
Tests for the bare engineering-notation suffix feature (si_prefix.py):
that a value like "1k" is ambiguous by default and raises, that the
si/var suffix policies each read it the way they claim to, that the two
explicit spellings (1'k / 1*k) are never ambiguous regardless of policy,
that find_ambiguous_values() reports every occurrence, and that neither
node names nor scientific notation (8E3) get mistaken for the suffix.
Also covers parse_circuit's/expand_shorthand's `expand_si` flag, which
lets a caller echo a circuit back with its SI-prefix notation intact
instead of expanded to a literal number."""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import sympy as sp
import pytest

from symbulator import dc, find_ambiguous_values, AmbiguousValueError
from symbulator.elements import parse_circuit
from symbulator.elements import parse_circuit


DIVIDER = "e1,1,0,5:r1,1,2,1k:r2,2,0,1k"


def test_bare_suffix_asks_by_default():
    with pytest.raises(AmbiguousValueError) as exc:
        dc(DIVIDER)
    tokens = exc.value.tokens
    assert [(t["element"], t["token"]) for t in tokens] == [("r1", "1k"), ("r2", "1k")]


def test_suffix_si_reads_as_unit():
    res = dc(DIVIDER, suffix="si")
    assert res.v("2") == sp.Rational(5, 2)
    assert res.i("r1") == sp.Rational(1, 400)


def test_suffix_var_reads_as_variable():
    res = dc(DIVIDER, suffix="var")
    # Both resistors are 1*k -> divider still splits 5V in half, and the
    # current depends on the symbol k.
    assert sp.simplify(res.v("2") - sp.Rational(5, 2)) == 0
    k = sp.Symbol("k")
    assert sp.simplify(res.i("r1") - 5 / (2 * k)) == 0


def test_explicit_spellings_never_ambiguous():
    # 1'k (SI) and 1*k (variable) both pass under the default policy.
    res = dc("e1,1,0,5:r1,1,2,1'k:r2,2,0,1*k")
    assert find_ambiguous_values("e1,1,0,5:r1,1,2,1'k:r2,2,0,1*k") == []
    assert "v_2" in res.values


def test_find_ambiguous_values_reports_each_occurrence():
    found = find_ambiguous_values("e1,1,0,5:r1,1,2,4.7u:r2,2,0,1000:l1,2,0,2m")
    assert [(f["element"], f["token"], f["letter"]) for f in found] == [
        ("r1", "4.7u", "u"), ("l1", "2m", "m")]


def test_node_names_are_not_flagged():
    # A node literally named "2k" is a node name, not a value.
    assert find_ambiguous_values("e1,1,0,5:r1,1,2k,100:r2,2k,0,100") == []


def test_scientific_notation_is_not_flagged():
    # 8E3 is scientific notation (8000), not an ambiguous suffix.
    assert find_ambiguous_values("e1,1,0,5:r1,1,0,8E3") == []
    res = dc("e1,1,0,5:r1,1,0,8E3")
    assert abs(complex(res.i("r1")) - complex(5 / 8000)) < 1e-12


def test_expand_si_false_preserves_the_typed_notation():
    # A caller that only wants to echo the circuit back (not solve it)
    # can ask parse_circuit to leave "4.7'M" exactly as typed.
    elements = parse_circuit("r1,1,0,4.7'M", expand_si=False)
    assert elements[0].value == "4.7'M"


def test_expand_si_true_is_still_the_default_and_solves_normally():
    # The default behaviour (used at actual solve time) is unchanged:
    # the shorthand is expanded to a literal number, not left as typed.
    elements = parse_circuit("r1,1,0,4.7'M")
    assert "'" not in elements[0].value
    res = dc("e1,1,0,5:r1,1,0,4.7'M")
    assert abs(complex(res.i("r1")) - complex(5 / 4.7e6)) < 1e-12


def test_expand_si_false_still_expands_bracket_shorthand():
    # The `[...]` -> `pr(...)` rewrite always happens regardless of
    # `expand_si`, since `_split_fields` needs it to tell the shortcut's
    # inner commas apart from an element's own field commas.
    elements = parse_circuit("r1,1,0,[1k,2k]", expand_si=False)
    assert elements[0].value == "pr(1k,2k)"


# ---- #59: an error quotes what was typed, not the machine's rewrite ------
#
# Values are rewritten before they are parsed -- [a,b] becomes pr(a,b) and
# 1'k becomes 1*10**3 -- so a complaint used to be about a string the
# reader never wrote.

def test_unbalanced_bracket_is_named_in_the_readers_own_notation():
    from symbulator.si_prefix import ShorthandError
    with pytest.raises(ShorthandError) as caught:
        parse_circuit("e,1,0,10:r1,1,0,[1'k,2'k")
    msg = str(caught.value)
    assert "[1'k,2'k" in msg          # what was typed
    assert "pr(" not in msg           # not what the rewrite made of it
    assert "closing bracket" in msg


def test_a_name_used_as_a_function_says_so_without_inventing_one():
    # rx[1'k] rewrites into rxpr(...), so the name SymPy chokes on is one
    # the reader never typed. It must not appear in the message.
    from symbulator.elements import CircuitError
    from symbulator.si_prefix import UnsafeExpressionError
    with pytest.raises((UnsafeExpressionError, CircuitError)) as caught:
        dc("e,1,0,10:rx,1,0,rx[1'k]")
    msg = str(caught.value)
    assert "rx[1'k]" in msg
    assert "rxpr" not in msg
    assert "not callable" not in msg


def test_an_unrecognised_prefix_names_the_value():
    from symbulator.si_prefix import ShorthandError
    with pytest.raises(ShorthandError) as caught:
        parse_circuit("e,1,0,10:r1,1,0,1'Q")
    assert "1'Q" in str(caught.value)


def test_the_shorthand_that_is_correct_still_solves():
    # The guard must not cost the feature it guards.
    r = dc("e,1,0,10:r1,1,0,[1'k,2'k]")
    assert r.v("1") == 10


# --- an SI prefix is a decimal shift, so it is done in decimal --------

def test_a_prefix_is_folded_in_base_ten():
    """`397.3'm` is 0.3973, not 0.39730000000000004.

    Left as `397.3*10**-3` the multiplication happens in binary and
    lands one unit in the last place from the decimal the reader typed.
    Nothing downstream can undo that -- it is a different double -- so
    the fold happens in base ten, before anything binary sees it
    (Roberto, 1 Sep 2026, having found it in a SPICE netlist)."""
    from symbulator.si_prefix import expand_value, safe_sympify
    for typed, meant in [("397.3'm", "0.3973"), ("2.2'n", "2.2e-9"),
                         ("4.7'u", "4.7e-6"), ("1.1'k", "1100"),
                         ("6.6'M", "6.6e6"), ("123.456'u", "123.456e-6"),
                         ("397.3m", "0.3973"), ("2.2n", "2.2e-9")]:
        got = float(safe_sympify(expand_value(typed)))
        assert got == float(meant), (typed, repr(got), meant)


def test_a_whole_numbered_prefix_value_stays_exact():
    """An integer mantissa keeps the `n*10**e` form, which SymPy reads
    as an exact Rational -- `100'p` is 1/10000000000, not a float, and
    a circuit of whole-numbered values still solves exactly."""
    from symbulator.si_prefix import expand_value, safe_sympify
    for typed in ("1'k", "100'p", "47'n", "2'k"):
        assert safe_sympify(expand_value(typed)).is_Rational, typed
