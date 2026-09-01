"""The calculator's spellings for the step and the impulse.

Versions 7 and 8 write a step as `u(t)` and an impulse with a Greek delta.
Version 9 parses through SymPy, which knows neither, so a description
copied from the older documentation failed with "'Symbol' object is not
callable". These accept both spellings unchanged.

`u` is also the micro prefix, and the rule that separates them is whether
a `(` follows: `7u` is micro, `7u(t)` is the function. That means a bare
`u` is left alone entirely, so it still works as an ordinary variable --
unlike the names in `_allowed_namespace`, which are taken from everyone.
"""
import sympy as sp

import symbulator as sb

from symbulator import tr
from symbulator.elements import parse_circuit
from symbulator.si_prefix import (expand_shorthand, expand_value,
                                  safe_sympify)

DELTA = "δ"


# --------------------------------------------------------------- the rule ---

def test_u_before_a_bracket_is_the_step():
    assert expand_shorthand("V*u(t)", si=True) == "V*Heaviside(t)"


def test_u_without_a_bracket_is_left_for_the_micro_suffix():
    # Neither of these is a function call, so expand_shorthand must not
    # touch them -- `7u` is 7 micro-somethings, handled further down.
    assert expand_shorthand("7u", si=False) == "7u"
    # And through the real entry point it becomes micro, not a function.
    # A whole-numbered mantissa keeps the power form, which SymPy reads
    # as an exact Rational; one with a decimal point is folded into a
    # decimal literal instead, because multiplying it out in binary
    # costs a unit in the last place (see si_prefix._scaled_literal).
    assert expand_value("7u") == "(7)*10**-6"
    assert expand_value("4.7u") == "0.0000047"
    assert float(safe_sympify(expand_value("4.7u"))) == float("4.7e-6")


def test_quoted_micro_prefix_is_untouched():
    assert expand_shorthand("7'u", si=False) == "7'u"


def test_a_leading_number_gets_its_implied_multiplication():
    # `7u(t)` is a product on the calculator. Nothing later in the chain
    # infers one, and `7Heaviside(t)` is a syntax error rather than a
    # product, so the `*` has to be put in here.
    assert expand_shorthand("7u(t)", si=True) == "7*Heaviside(t)"
    assert expand_shorthand("7*u(t)", si=True) == "7*Heaviside(t)"


def test_greek_delta_is_the_impulse():
    assert expand_shorthand(f"i*{DELTA}(t)", si=True) == "i*DiracDelta(t)"


def test_ascii_delta_works_for_keyboards_without_the_character():
    assert expand_shorthand("i*delta(t)", si=True) == "i*DiracDelta(t)"


def test_a_name_ending_in_u_is_not_a_step():
    # `vu(t)` is not the step function; only a `u` that starts a name is.
    assert expand_shorthand("vu(t)", si=True) == "vu(t)"


# ------------------------------------------------------- reaching sympify ---

def test_step_reaches_sympy_as_heaviside():
    # expand_value is the real entry point: it tries the bare suffix first
    # (so `7u` stays micro) and falls through to expand_shorthand.
    # `t` parses as a neutral symbol on purpose -- see
    # test_a_positive_t_would_erase_the_impulse below.
    assert safe_sympify(expand_value("V*u(t)")) == (
        sp.Symbol("V") * sp.Heaviside(sb.t))


def test_impulse_reaches_sympy_as_diracdelta():
    got = safe_sympify(expand_value(f"i*{DELTA}(t)"),
                       reserve_imaginary=False)
    assert got == sp.Symbol("i") * sp.DiracDelta(sb.t)


def test_u_is_still_an_ordinary_variable():
    # The regression the first attempt at this introduced: putting `u` in
    # the namespace made every `u` a function, so a source valued `u`
    # stopped parsing.
    assert safe_sympify("u") == sp.Symbol("u")
    assert parse_circuit("e1,1,0,u")[0].value == "u"


# ------------------------------------------------------------ end to end ----

def test_a_calculator_step_circuit_solves():
    both = [tr(f"e1,1,0,V*{spelling}:r,1,2,r:c,2,0,c,0").v(2)
            for spelling in ("u(t)", "Heaviside(t)")]
    assert sp.simplify(both[0] - both[1]) == 0


def test_a_calculator_impulse_circuit_solves():
    both = [tr(f"j,0,1,i*{spelling}:c,1,0,c,0:r,1,0,r").v(1)
            for spelling in (f"{DELTA}(t)", "DiracDelta(t)")]
    assert sp.simplify(both[0] - both[1]) == 0


def test_micro_suffix_still_solves_beside_a_step():
    # Both meanings of `u` in one description, which is the whole point of
    # the rule: a micro capacitor driven by a step source.
    res = tr("e1,1,0,12*u(t):r,1,2,2:c,2,0,1'u,0")
    assert res.v(2) != 0


# ------------------------------------------- kept, not replaced -------------

def test_the_typed_notation_survives_being_echoed_back():
    """Parsed but kept, like `'k` -- not replaced, like `J` in AC.

    The web app puts the echoed circuit straight back into the Circuit
    Description box. Rewriting `u(t)` to `Heaviside(t)` there would take
    the calculator's notation away from someone who deliberately typed it,
    silently, on their first Run.
    """
    for typed in ("V*u(t)", f"i*{DELTA}(t)", "2ir3", "2e^(-4t)"):
        assert expand_shorthand(typed, si=False) == typed


def test_but_it_is_expanded_on_the_way_to_being_solved():
    assert expand_shorthand("V*u(t)", si=True) == "V*Heaviside(t)"
    assert expand_shorthand("2ir3", si=True) == "2*ir3"


def test_the_bracket_shortcut_is_still_expanded_either_way():
    # Unlike the rest, `[...]` has to go unconditionally: _split_fields
    # cannot tell the shortcut's inner commas from an element's own.
    assert expand_shorthand("[1,2]", si=False) == "pr(1,2)"


# ------------------------------------------- names with digits inside -------

def test_a_digit_inside_a_name_is_not_a_multiplication():
    """`t2s(t)` must not become `t2*s(t)`.

    The implicit-multiplication rule shipped in 0.5.1 looked only at the
    character before the letter, so it split every name with a digit in the
    middle -- including t2s and s2t themselves, the two functions most
    likely to be typed into a transient source.
    """
    for name in ("t2s(t)", "s2t(1/s)", "i2r", "v2", "r2d(x)"):
        assert expand_value(name) == name


def test_the_multiplication_is_still_inserted_where_it_belongs():
    assert expand_value("2ir3") == "2*ir3"
    assert expand_value(".2v1") == ".2*v1"
    assert expand_value("2t") == "2*t"


def test_t2s_reaches_the_solver_through_a_circuit_value():
    # The whole point of putting t2s in the namespace: a transient source
    # written in the time domain, converted where it is typed.
    from symbulator import tr
    ramp = tr("j,0,1,t2s(t):c,1,0,2,0").values["v_1"]
    assert sp.simplify(ramp - sb.t ** 2 / 4) == 0


def test_a_positive_t_would_erase_the_impulse():
    """Why the solver's `t` is non-negative and not strictly positive.

    0.5.3 bound the parsing namespace to Symbol("t", positive=True) so a
    hand-written `t` would match the answers. That changed what an
    expression *means*: SymPy evaluates DiracDelta of a strictly positive
    argument to zero, so an impulse source vanished before the transform
    ever saw it. The fix at the time was to parse a neutral t instead,
    which kept the impulse but left two symbols that never combined.

    Non-negative settles both. The impulse survives, because t >= 0 does
    not exclude the origin where it lives, and there is one symbol again.
    """
    assert sp.DiracDelta(sp.Symbol("t", positive=True)) == 0
    assert sp.DiracDelta(sp.Symbol("t")) != 0
    # The solver's own t is the one that has to survive, and does.
    assert sp.DiracDelta(sb.t) != 0
    assert sb.t.is_nonnegative and not sb.t.is_positive
    # And the parsed form is the one that survives.
    assert safe_sympify(expand_value("delta(t)")) != 0


# ---------------------------------------- TR reads its sources in time ------

def _in_time(expr):
    """Answers come back in Symbol("t", positive=True); expectations here
    are written with a neutral t. Same symbol name, different assumptions,
    and subtracting them does not cancel -- so line them up first."""
    e = sp.sympify(expr)
    T = sb.t
    return e.subs({x: T for x in e.free_symbols if x.name == "t"})


def _tr_gives(desc, key, expected):
    from symbulator import tr
    got = _in_time(tr(desc).values[key])
    assert sp.simplify(sp.expand(got - _in_time(expected))) == 0


def test_a_constant_source_is_a_step():
    # symbv8s5: a NUM value becomes value/s.
    _tr_gives("j,0,1,1:c,1,0,2,0", "v_1", sb.t / 2)


def test_a_waveform_source_is_transformed():
    # symbv8s5: a value that changes with t becomes t2s(value).
    _tr_gives("j,0,1,t:c,1,0,2,0", "v_1", sb.t ** 2 / 4)


def test_the_rc_step_response_is_the_step_response():
    t = sb.t
    _tr_gives("e1,1,0,12:r,1,2,2:c,2,0,1,0", "v_2", 12 - 12 * sp.exp(-t / 2))


def test_the_calculators_spelling_gives_the_calculators_answer():
    # AS7's Example 16.1, whose answer is in print in both the version 7
    # and version 8 documentation.
    t = sb.t
    _tr_gives("e1,1,0,u(t):r1,1,2,1:r2,2,o,5:c,2,0,1/3,0:l,o,0,1,0", "v_o",
              3 * sp.sqrt(2) * sp.exp(-4 * t) * sp.sin(sp.sqrt(2) * t) / 2)


def test_an_s_domain_source_is_left_alone():
    # Anything already written in s is the caller having done the
    # conversion; transforming it again would be wrong. This is also what
    # keeps every existing version 9 description working.
    t = sb.t
    _tr_gives("e1,1,0,5/s:r1,1,2,1000:c1,2,0,1e-6", "v_2",
              5 - 5 * sp.exp(-1000 * t))


def test_a_controlled_source_is_left_alone():
    """A controlled source's value is a relation, not a waveform.

    symbv8s5 decides this by substituting each node voltage and element
    current in turn and seeing whether the value changes; this port looks
    at which symbols the value contains. Either way `2*i_r3` must not be
    divided by s.
    """
    from symbulator.laplace import _sources_to_s
    desc = "e1,1,0,2:r3,1,2,3:r5,2,3,5:c,3,0,1:j,0,2,2*i_r3"
    assert "2*i_r3" in _sources_to_s(desc)
    assert "e1,1,0,2/s" in _sources_to_s(desc)


# ----------------------------------------- the {...} shorthand in FD --------

def test_braces_mark_a_time_domain_source_in_fd():
    """symbv8si: when the tool is fd, `{` becomes `t2s(` and `}` becomes `)`.

    FD reads its sources in the s-domain; the braces say "this one is
    written in time". `{5}` is therefore a 5 V step and a bare `5` is an
    impulse -- different circuits, which is the whole reason the shorthand
    exists.
    """
    from symbulator import fd
    step = fd("e1,1,0,{5}:r1,1,2,2:c,2,0,1,0").values["v_2"]
    same = fd("e1,1,0,t2s(5):r1,1,2,2:c,2,0,1,0").values["v_2"]
    impulse = fd("e1,1,0,5:r1,1,2,2:c,2,0,1,0").values["v_2"]
    assert sp.simplify(step - same) == 0
    assert sp.simplify(step - impulse) != 0


def test_the_brace_shorthand_agrees_with_writing_it_out():
    from symbulator import fd
    braces = fd("e1,1,0,{u(t)}:r1,1,2,1:r2,2,o,5:c,2,0,1/3,0:l,o,0,1,0")
    explicit = fd("e1,1,0,1/s:r1,1,2,1:r2,2,o,5:c,2,0,1/3,0:l,o,0,1,0")
    assert sp.simplify(braces.values["v_o"] - explicit.values["v_o"]) == 0


def test_the_parallel_shortcut_is_a_different_bracket():
    """`[...]` is pr(...) and applies in every analysis; `{...}` is t2s(...)
    and applies only in FD. Easy to conflate, so pin both.

    The square bracket is rewritten to a call and evaluated later. The
    curly one is evaluated where it stands, so that a failure can be
    reported against the brackets the reader typed rather than against a
    function they never wrote."""
    from symbulator.si_prefix import expand_shorthand, expand_time_domain_braces
    assert expand_shorthand("[2,3]", si=False) == "pr(2,3)"
    assert expand_time_domain_braces("{u(t)}") == "(1/s)"
    assert expand_time_domain_braces("2*v_1") == "2*v_1"


def test_a_bracket_that_cannot_transform_is_refused():
    """Both ends are checked, and a failure stops rather than travelling.

    `{1/s}` is already in s -- the brackets convert *from* time, so there
    is nothing to do and the reader has misread the convention. `{1/t}`
    has no closed-form transform. Either way the message names the
    brackets, because that is what was typed."""
    from symbulator.si_prefix import expand_time_domain_braces
    from symbulator.elements import CircuitError

    for bad, expect in (("{1/s}", "already in the s-domain"),
                        ("{1/t}", "does not evaluate")):
        try:
            expand_time_domain_braces(bad)
        except CircuitError as exc:
            assert "between brackets" in str(exc), str(exc)
            assert expect in str(exc), str(exc)
        else:
            raise AssertionError(f"{bad} should have been refused")


# ---------------------------- an s-free circuit answer is an impulse --------
#
# Until 0.5.15, tr() passed every s-free s-domain value through
# untransformed, so a 10 V*s impulse printed identically to a 10 V step
# -- both said 10. The pass-through was protecting two real cases, and
# still does: a solved expert-mode unknown is a scalar (recognised by
# its key), and a dependent source's echo of its controlling answer is a
# relation whose symbols name functions (recognised by _is_controlled).
# Everything else that is constant in s IS an impulse -- a step arrives
# as k/s and a waveform brings its own s, so a bare constant has nowhere
# else to come from.

def test_an_impulse_into_a_resistor_says_delta():
    t = sb.t
    res = tr("e,1,0,10*delta(t):r1,1,0,1")
    assert sp.simplify(res.values["v_1"] - 10 * sp.DiracDelta(t)) == 0
    assert sp.simplify(res.values["i_r1"] - 10 * sp.DiracDelta(t)) == 0


def test_a_step_into_a_resistor_still_says_its_level():
    res = tr("e,1,0,10*u(t):r1,1,0,1")
    assert sp.simplify(_in_time(res.values["v_1"]) - 10) == 0


def test_a_symbolic_impulse_keeps_its_amplitude():
    # Bo2's Figure 5.38: the source's own current is i*delta(t), and it
    # used to come back as the bare i.
    t = sb.t
    res = tr("j,0,1,i*delta(t):c,1,0,c,0:r,1,0,r")
    assert sp.simplify(res.values["i_j"]
                       - sp.Symbol("i") * sp.DiracDelta(t)) == 0


def test_a_capacitor_across_a_step_draws_an_impulse():
    # i = C dv/dt, and the step's jump is the impulse.
    t = sb.t
    res = tr("e,1,0,u(t):c,1,0,1,0")
    assert sp.simplify(res.values["i_c"] - sp.DiracDelta(t)) == 0


def test_a_zero_answer_stays_zero():
    # Bo2's Example 5.7: no current into an ideal op-amp's source leg.
    res = tr("e,1,0,u(t):o,1,2,o:c,2,o,1/8,0:r2,2,o,2:r1,2,0,1")
    assert res.values["i_e"] == 0


def test_a_dependent_echo_is_a_relation_not_an_impulse():
    # Bo2's p230: the controlled source's current echoes its controlling
    # current. Since spelling equivalence, `ir` IS `i_r`, so the echo
    # resolves through the closure to twice the resistor's current --
    # and must not gain a delta on the way.
    res = tr("r,v,0,r:j,v,0,2*ir:l,v,0,l,5")
    assert sp.simplify(res.values["i_j"] - 2 * res.values["i_r"]) == 0
    assert not res.values["i_j"].has(sp.DiracDelta)


def test_an_expert_unknown_stays_a_scalar():
    # Solving for a source's amplitude: the answer is the number 5, not
    # an impulse of 5. This is the case the old shape-based pass-through
    # existed to protect, now recognised by its key instead.
    res = tr("e,1,0,k:r1,1,0,1", equations=["v_1 = 5"], unknowns=["k"])
    assert sp.simplify(res.values["k"] - 5) == 0
