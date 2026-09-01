"""
Port of Symbulator's `symbv8si` / `symbv8sr` shorthand expansion.

On the calculator, values could be written with a unit-prefix shorthand
like `1'k` (1 kilo = 1e3) instead of typing `*10^3` by hand. The original
TI-Basic did a series of literal substring replacements (see `symbv8sr`,
called repeatedly from `symbv8si`). We reproduce the same substitution
table here, operating on plain Python strings, so circuit descriptions
written in the calculator's shorthand can be reused unchanged.

Note: the original also special-cased `{...}` (Laplace-of-time-function,
fd mode only) and `[...]` (parallel-impedance shortcut -> pr({...})).
The `[...]` shortcut is implemented; the fd-only `{...}` shortcut is not
(fd/tr analysis is out of scope for this phase).
"""

from __future__ import annotations

import ast
import decimal
import re

# (old substring, new substring) pairs, applied in this order -- mirrors
# the exact sequence of symbv8sr calls inside symbv8si.
# Exa is deliberately absent: "E" is reserved for scientific notation
# (8E3 = 8000), which is far more useful in a circuit than exa-ohms.
_SI_PREFIXES = [
    ("'k", "*10**3"),
    ("'K", "*10**3"),
    ("'M", "*10**6"),
    ("'G", "*10**9"),
    ("'T", "*10**12"),
    ("'P", "*10**15"),
    ("'m", "*10**-3"),
    ("'u", "*10**-6"),
    ("'\u00b5", "*10**-6"),   # MICRO SIGN
    ("'\u03bc", "*10**-6"),   # GREEK SMALL LETTER MU -- looks identical,
                               # and which one you get depends on the
                               # keyboard, so accept both.
    ("'n", "*10**-9"),
    ("'p", "*10**-12"),
    ("'f", "*10**-15"),
    ("'a", "*10**-18"),
]

# The prefix exponents, keyed by the letter alone, for folding a prefix
# into the number it follows rather than leaving a multiplication behind
# (`_scaled_literal`). Same letters as the pairs above; one table so the
# two cannot come to disagree about what `'n` means.
_PREFIX_EXP = {
    "k": 3, "K": 3, "M": 6, "G": 9, "T": 12, "P": 15,
    "m": -3, "u": -6, "µ": -6, "μ": -6,
    "n": -9, "p": -12, "f": -15, "a": -18,
}

# A number carrying a quoted prefix: `397.3'm`, `4.7'u`, `1'k`. The
# lookbehind keeps it to a number that stands on its own, so a prefix
# stuck to something else still falls through to the substring pass.
_QUOTED_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_.])((?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)'(["
    + "".join(sorted(set(_PREFIX_EXP))) + r"])")


def _scaled_literal(number: str, exponent: int) -> str:
    """`("397.3", -3)` -> `"0.3973"`: the prefix folded into the number,
    in base ten, before anything binary sees it.

    Left as `397.3*10**-3`, the multiplication happens in binary
    floating point and lands one unit in the last place away from the
    decimal the reader typed: 0.39730000000000004. Nothing downstream
    can undo that -- it is a different double, and `repr` is right to
    print all seventeen digits of it -- so a SPICE netlist carried the
    noise out to the page (Roberto, 1 Sep 2026). Scaling with `Decimal`
    is exact in base ten, so the literal that reaches SymPy is the one
    the reader wrote and the double is the nearest one to it.

    **An integer mantissa keeps the `n*10**e` form.** SymPy reads that
    as an exact Rational -- `100'p` is 1/10000000000, not a float -- and
    a circuit of whole-numbered values still solves exactly, which is
    the behaviour "Rounding: exact" exists for. Only a mantissa that
    already has a decimal point or an exponent (and is therefore a Float
    either way) is folded."""
    if "." not in number and "e" not in number and "E" not in number:
        return f"({number})*10**{exponent}"
    try:
        return str(decimal.Decimal(number).scaleb(exponent))
    except (decimal.InvalidOperation, decimal.Overflow, ValueError):
        return f"({number})*10**{exponent}"


class ShorthandError(ValueError):
    """Raised when circuit-description shorthand can't be expanded."""


class AmbiguousValueError(ValueError):
    """Raised when a value like "1k" could mean either an SI unit
    (1'k = 1000) or a number times a variable (1*k), and the caller
    hasn't said which. `tokens` is a list of dicts, one per ambiguous
    value: {"element", "token", "number", "letter"}."""

    def __init__(self, tokens):
        """`tokens` is the list of ambiguous-value dicts found (see the
        class docstring); building the human-readable message here means
        callers that only want to display the error can just str() the
        exception, while callers that want to prompt the user field-by-
        field (like the web front end) can still read `.tokens` directly."""
        self.tokens = tokens
        listing = ", ".join(
            f"'{t['token']}' in {t['element']}" for t in tokens)
        super().__init__(
            f"Ambiguous value(s): {listing}. Write the SI-unit meaning "
            f"explicitly with an apostrophe (e.g. 1'k = 1000) or the "
            f"variable meaning with a star (e.g. 1*k), or pass "
            f"suffix='si' / suffix='var' to choose for all of them."
        )


# Convenience beyond the original calculator syntax: allow the common
# engineering-notation bare suffix ("1k", "4.7u", "10n") on a standalone
# numeric value field, in addition to the calculator's own `'k` syntax.
# Applied only when the *entire* field is just <number><suffix>, so it
# can't accidentally rewrite part of a symbolic expression.
_BARE_SUFFIX_EXP = {
    "k": 3, "K": 3, "M": 6, "G": 9, "T": 12, "P": 15,
    "m": -3, "u": -6, "\u00b5": -6, "\u03bc": -6,
    "n": -9, "p": -12, "f": -15, "a": -18,
}
_BARE_SUFFIX_RE = re.compile(r"^([+-]?\d+\.?\d*)([kKMGTPmu\u00b5\u03bcnpfa])$")


def bare_suffix_match(text: str):
    """Return the (number, letter) parts if `text` is a bare
    engineering-notation value like "1k" / "4.7u", else None."""
    m = _BARE_SUFFIX_RE.fullmatch(text.strip())
    return m.groups() if m else None


# The `{...}` shorthand, from symbv8si:
#
#     If betatool="fd" and inString(sitext,"{") Then
#       symbv8sr("{", "s\t2s(")
#       symbv8sr("}", ")")
#
# FD reads its source values in the s-domain. Wrapping one in braces says
# "this one is written in time -- convert it", which is exactly `t2s(...)`
# and five characters shorter. It is deliberately FD-only: TR converts its
# sources anyway, so there would be nothing for it to do there.
#
# A plain textual swap, as the original's is. The braces cannot nest and
# cannot contain a comma that matters -- a value containing one would
# already have been split into separate fields long before here.


def _brace_groups(text: str):
    """Every balanced `{...}` in `text`, as (start, end) slices."""
    out, depth, start = [], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                out.append((start, i + 1))
    return out


def expand_time_domain_braces(text: str) -> str:
    """`{expr}` -> the s-domain expression it stands for.

    FD reads every domain-sensitive input in s; the brackets are how a
    reader says "this one is in time". So the transform is done here,
    where it is still known that brackets were what was written -- a
    plain textual rewrite to `t2s(...)` would leave the error message
    blaming the function for something the reader never typed.

    Both ends are checked, and a failure stops the run rather than
    letting an unevaluated transform travel on into the equations.
    """
    if "{" not in text:
        return text

    from .laplace import t2s, _check_transform

    out, last = [], 0
    for start, end in _brace_groups(text):
        inner = text[start + 1:end - 1].strip()
        out.append(text[last:start])
        if not inner:
            out.append(text[start:end])       # `{}` is not ours to read
            last = end
            continue
        try:
            expr = safe_sympify(expand_value(inner), reserve_imaginary=False)
        except Exception:                                     # noqa: BLE001
            out.append(text[start:end])       # not an expression; leave it
            last = end
            continue
        # No fn: this is the `{...}` shorthand, not a call.
        result = _check_transform(expr, t2s(expr, validate=False), "s")
        out.append(f"({result})")
        last = end
    out.append(text[last:])
    return "".join(out)


# Polar phasors, the way every circuits textbook writes them and the way
# versions 7 and 8 accept them: `(20<30 degrees)`, using the angle sign.
#
# This becomes a rectangular *number*, not `20*exp(I*pi/6)`. That is a
# deliberate choice and it matters more than it looks. SymPy cannot reduce
# `exp(I*pi*130/180)` to any closed form, so the expression is carried
# unevaluated through every mesh equation of the circuit -- AS7's Example
# 12.12 took over 25 seconds and was killed, while the same circuit with
# rectangular sources solves in about one. Where the angle happens to
# simplify (120 degrees, say) it is fast, which is what made the problem
# look circuit-specific rather than notation-specific.
#
# The cost is exactness: `100<0 degrees` becomes 100.0 rather than 100.
# Roberto's call, 25 Aug 2026 -- a phasor angle is a measurement, and the
# alternative is circuits that do not solve.
#
# Both degree characters are accepted. The 2023 documentation uses the
# real degree sign 111 times and the masculine ordinal -- which looks
# identical in most fonts -- another 20, and a reader copying from either
# should not have to know which they got.
_ANGLE_RE = re.compile(
    r"(\d+\.?\d*)\s*\u2220\s*([+-\u2212\u2013]?\s*\d+\.?\d*)\s*[\u00b0\u00ba]?")


def _rectangular(magnitude: str, degrees: str) -> str:
    """`20`, `30` -> `(17.320508+10.0j)`."""
    import math

    deg = degrees.replace("\u2212", "-").replace("\u2013", "-").replace(" ", "")
    radians = math.radians(float(deg))
    mag = float(magnitude)
    re_part = mag * math.cos(radians)
    im_part = mag * math.sin(radians)
    # Trim the floating-point dust: cos(90 degrees) is 6.1e-17, not 0, and
    # a stray 1e-17 in a source value is noise in every answer after it.
    if abs(re_part) < 1e-12 * max(1.0, abs(mag)):
        re_part = 0.0
    if abs(im_part) < 1e-12 * max(1.0, abs(mag)):
        im_part = 0.0
    sign = "+" if im_part >= 0 else "-"
    return f"({re_part!r}{sign}{abs(im_part)!r}j)"


def expand_angle_notation(text: str) -> str:
    """Every `A<B` phasor in `text`, as a rectangular number."""
    if "\u2220" not in text:
        return text
    return _ANGLE_RE.sub(
        lambda m: _rectangular(m.group(1), m.group(2)), text)


def expand_value(text: str, suffix: str = "si") -> str:
    """Expand a single value field. A bare engineering-notation suffix
    ("1k", "4.7u", ...) is inherently ambiguous -- 1k could mean the SI
    unit (1'k = 1000) or one times a variable named k (1*k) -- so the
    `suffix` policy decides: "si" reads it as the SI unit, "var" as
    number*variable, and "ask" refuses with AmbiguousValueError so the
    caller can ask the user. Fields that aren't bare-suffix values pass
    through the calculator's `'k`-style shorthand expansion unchanged."""
    stripped = text.strip()
    m = _BARE_SUFFIX_RE.fullmatch(stripped)
    if m:
        num, suf = m.groups()
        if suffix == "var":
            return f"({num})*{suf}"
        if suffix == "ask":
            raise AmbiguousValueError([{"element": "?", "token": stripped,
                                        "number": num, "letter": suf}])
        # Folded in base ten, same as the quoted form -- `397.3m` and
        # `397.3'm` are the same value and must produce the same double.
        return _scaled_literal(num, _BARE_SUFFIX_EXP[suf])
    return expand_shorthand(text)


# The calculator's names for the step and the impulse, so a description
# written for versions 7 and 8 reads unchanged in version 9.
#
# `u` is the hard one, because it is also the micro prefix, and Roberto
# fixed the rule on 24 Aug 2026: whether a `(` follows decides it.
#
#     7'u      micro      the quoted prefix, untouched here
#     7u       micro      a bare suffix, left for _BARE_SUFFIX_RE
#     7*u(t)   function   an explicit multiplication
#     7u(t)    function   the same, with the multiplication implied
#
# So only `u(` is rewritten, never a bare `u` -- which also means someone
# using `u` as a plain variable keeps it, unlike the names in
# _allowed_namespace, which are taken away everywhere.
#
# The implied multiplication in `7u(t)` has to be made explicit here too:
# nothing later in the chain infers one, and `7Heaviside(t)` is a syntax
# error rather than a product.
_STEP_RE = re.compile(r"(?<![A-Za-z0-9_.])(\d+\.?\d*)?\s*"
                      r"(u|δ|delta)\s*\(")
_STEP_FN = {"u": "Heaviside", "δ": "DiracDelta", "delta": "DiracDelta"}


def _expand_step_and_impulse(text: str) -> str:
    """`u(t)` -> `Heaviside(t)`, the delta -> `DiracDelta(t)`, inserting the
    multiplication a leading number implies."""
    def swap(m):
        number, name = m.group(1), m.group(2)
        head = f"{number}*" if number else ""
        return f"{head}{_STEP_FN[name]}("
    return _STEP_RE.sub(swap, text)


# The calculator's power notation and its implied multiplications.
#
# Both are habits every Symbulator 7/8 user has, and both used to fail in
# version 9 -- `2^3` was rejected outright (a caret is XOR in Python, which
# the AST guard refuses) and `2ir3` came back "invalid decimal literal".
# Between them they broke 37 of the 50 circuits in the version 9
# documentation.
#
# Scientific notation is the trap here. `1e-6`, `2.5e3` and `1E6` are
# ordinary numbers that SymPy already reads, and a naive "digit followed by
# a letter means multiply" rule turns `2.5e3` into `2.5*e3`, silently
# replacing a number with a symbol. So the exponent form is matched first
# and stepped over.
_SCI_RE = re.compile(r"\d\.?\d*[eE][+-]?\d+")

# The other thing that must survive untouched is a bare engineering suffix:
# `1k` is a thousand, not one times k, and `4.7u` is micro. Those are read
# further along (expand_value, find_ambiguous_values), which never gets the
# chance if a `*` has already been pushed into the middle of them. Letters
# outside this set -- the `t` in `2t`, the `i` in `2ir3` -- are not suffixes
# and do get the multiplication.
_BARE_UNIT_RE = re.compile(r"\d\.?\d*[kKMGTPmuµμnpfa](?![\w])")

# A number meeting a name or an opening bracket, or a bracket meeting
# either -- never a name meeting a bracket, which is a function call.
# The number must not be part of a name. Without the lookbehind, `t2s(t)`
# becomes `t2*s(t)` and the function disappears -- which broke t2s and s2t
# themselves, the two names most likely to be typed here.
_IMPLICIT_NUM = re.compile(r"(?<![A-Za-z_])(\.?\d+\.?\d*)(?=[A-Za-z_(])")
_IMPLICIT_PAREN = re.compile(r"(?<=\))(?=[\w(])")


def _insert_implicit_multiplication(text: str) -> str:
    """`2ir3` -> `2*ir3`, `2(a+b)` -> `2*(a+b)`, `(a)(b)` -> `(a)*(b)`."""
    # Protect scientific notation, then put it back untouched.
    kept = []

    def stash(m):
        kept.append(m.group(0))
        return f"\x00{len(kept) - 1}\x00"

    guarded = _SCI_RE.sub(stash, text)
    guarded = _BARE_UNIT_RE.sub(stash, guarded)
    guarded = _IMPLICIT_NUM.sub(r"\1*", guarded)
    guarded = _IMPLICIT_PAREN.sub("*", guarded)
    for n, original in enumerate(kept):
        guarded = guarded.replace(f"\x00{n}\x00", original)
    return guarded


def _expand_caret(text: str) -> str:
    """`2^3` -> `2**3`, and `e^x` -> `exp(x)`.

    `e` has to become Euler's number rather than a symbol, but only where a
    caret follows: putting `e` in the namespace would take it away from
    everyone using it as an ordinary variable, the way `re` and `exp`
    already are. So the exponent's extent is found here instead -- a
    bracketed group, or a sign and one number or name -- and wrapped in a
    real exp() call.
    """
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch != "^":
            out.append(ch)
            i += 1
            continue

        # Is this the calculator's e^, rather than some other base?
        # A digit before the `e` is a coefficient -- `2e^3` is two times
        # Euler's number cubed. Only a letter or underscore means the `e`
        # is the tail of a name, as in `re^2`, where it is not Euler's.
        base_is_e = (out and out[-1] == "e"
                     and (len(out) < 2 or not (out[-2].isalpha()
                                               or out[-2] == "_")))
        j = i + 1
        if j < len(text) and text[j] == "(":
            depth, k = 1, j + 1
            while k < len(text) and depth:
                depth += (text[k] == "(") - (text[k] == ")")
                k += 1
            exponent, j = text[j + 1:k - 1], k
        else:
            k = j
            if k < len(text) and text[k] in "+-":
                k += 1
            while k < len(text) and (text[k].isalnum() or text[k] in "_."):
                k += 1
            exponent, j = text[j:k], k

        if base_is_e:
            out.pop()
            out.append(f"exp({exponent})")
        else:
            out.append(f"**({exponent})")
        i = j
    return "".join(out)


def expand_shorthand(text: str, si: bool = True) -> str:
    """Expand `'k`/`'M`/... unit-prefix shorthand and `[...]` parallel-
    impedance shortcuts in `text`, mirroring symbv8si.

    `[a,b,c]` becomes `pr(a,b,c)` (a call into utils.pr), matching how
    the original turned `[...]` into `s\\pr({...})`.

    `si=False` skips the `'`-prefix substitution (and its "unrecognized
    shorthand" check) while still doing the `[...]` rewrite, which is
    needed unconditionally so `_split_fields` can tell the difference
    between the shortcut's inner commas and an element's own field
    commas. This lets a caller that only wants the circuit *echoed back*
    (not solved) keep the SI-prefix notation the user actually typed --
    it is only expanded to a literal number just before solving."""
    result = text

    if "\u2220" in result:
        result = expand_angle_notation(result)

    if "[" in result or "]" in result:
        # Balance first, and complain in the reader's own notation. The
        # rewrite below turns [ into pr( , so an unmatched bracket became
        # an unmatched parenthesis and was reported hundreds of characters
        # later as "Could not read the value 'pr(1*10**3,2*10**3'" -- the
        # machine's rewrite of something the reader never wrote. And it
        # cannot be recovered afterwards: an unbalanced bracket makes the
        # typed text and the rewrite split into different numbers of
        # fields, so the two can no longer be lined up.
        depth = 0
        for ch in result:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth < 0:
                    break
        if depth:
            missing = "closing" if depth > 0 else "opening"
            raise ShorthandError(
                f"'{text.strip()}' is missing a {missing} bracket. A parallel "
                f"combination is written [a,b], as in [1'k,2'k]."
            )
        result = result.replace("[", "pr(").replace("]", ")")

    # Only when the value is on its way to being solved. `si=False` means
    # the caller wants the circuit echoed back the way it was typed -- the
    # web app puts that straight back into the Circuit Description box --
    # and rewriting `u(t)` to `Heaviside(t)` there would take the
    # calculator's notation away from someone who deliberately used it,
    # silently, on the first Run. The `[...]` rewrite above is different:
    # it has to happen unconditionally, because _split_fields cannot tell
    # the shortcut's inner commas from an element's own without it.
    if si:
        result = _expand_step_and_impulse(result)
        if "^" in result:
            result = _expand_caret(result)
        result = _insert_implicit_multiplication(result)

    if si and "'" in result:
        # Numbers first, so the prefix is folded into the number in base
        # ten (`_scaled_literal`); the substring pass below then mops up
        # any `'x` that was not sitting on a number of its own, which is
        # what it has always done.
        result = _QUOTED_NUM_RE.sub(
            lambda m: _scaled_literal(m.group(1), _PREFIX_EXP[m.group(2)]),
            result)
        for old, new in _SI_PREFIXES:
            result = result.replace(old, new)
        if "'" in result:
            raise ShorthandError(
                f"'{text.strip()}' uses unit shorthand that Symbulator does "
                f"not recognise. The prefixes are "
                f"{', '.join(p for p, _ in _SI_PREFIXES)}."
            )

    return result


# ---------------------------------------------------------------------------
# Safe parsing of user-written values
# ---------------------------------------------------------------------------
#
# sympify() evaluates its input against SymPy's whole namespace, which
# means a value written `Q` becomes an internal assumptions object, `N`
# becomes a function, and `beta` becomes a function class -- none of
# which a person typing a circuit could possibly intend. Worse, they
# fail quietly rather than loudly.
#
# So values are parsed against a deliberately small namespace: the
# imaginary unit, pi, and the handful of mathematical functions a
# circuit genuinely needs. Every other name becomes an ordinary symbol,
# which is what someone writing `Q` for quality factor meant all along.

_IMAGINARY_NAMES = ("i", "I", "j", "J")

#: Names a value is allowed to mean something special by.
def _allowed_namespace(reserve_imaginary: bool = True):
    """Build the small dict of names `safe_sympify`/`hijacked_names` treat
    as meaning something other than a plain variable: a handful of
    constants (pi, oo) and functions (trig, exp/log, Heaviside/DiracDelta
    for transient sources, Min/Max) that a circuit description could
    genuinely need, plus -- when `reserve_imaginary` is true -- the four
    imaginary-unit spellings. Built fresh on each call rather than as a
    module-level constant purely so the `import sympy as sp` stays local
    to the handful of functions that need it, matching this module's
    style of keeping SymPy import cost out of code paths that don't
    touch it.

    `reserve_imaginary` is false outside AC analysis (and outside the AC
    mode of the equivalence tools): i, I, j and J only ever mean
    something to a circuit when a source or component value can be
    complex, which only happens in AC, so there's no reason to take
    those four names away from someone writing a DC or s-domain
    circuit."""
    import sympy as sp
    # Imported here rather than at module level: laplace imports
    # analysis, which imports this module, so a top-level import
    # would be a cycle. The same reason `pr` has always been local.
    from .laplace import S, T, s2t, t2s
    from .utils import pr

    ns = {
        "pi": sp.pi,
        "oo": sp.oo,
        "exp": sp.exp, "log": sp.log, "ln": sp.log, "sqrt": sp.sqrt,
        "sin": sp.sin, "cos": sp.cos, "tan": sp.tan,
        "asin": sp.asin, "acos": sp.acos, "atan": sp.atan,
        "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh,
        "Abs": sp.Abs, "abs": sp.Abs, "re": sp.re, "im": sp.im,
        "arg": sp.arg, "conjugate": sp.conjugate, "sign": sp.sign,
        "Heaviside": sp.Heaviside, "DiracDelta": sp.DiracDelta,
        "Min": sp.Min, "Max": sp.Max,
        # The `[...]` parallel-impedance shortcut expands to a literal
        # `pr(...)` call (see expand_shorthand below), so `pr` has to
        # resolve to the real function here or that call fails with
        # "'Symbol' object is not callable" once it reaches sympify.
        "pr": pr,
        # The version 7 aids for moving an expression between the
        # time and complex frequency domains, which had no way in
        # before. `pf` is deliberately NOT here: it returns a
        # sentence ("pf: 0.6 lagging"), not an expression, so
        # sympify hands back a Python str and every formatter
        # downstream is expecting a SymPy object.
        "t2s": t2s, "s2t": s2t,
        # Both are the solver's own symbols. `t` used to be parsed as a
        # separate neutral symbol, because binding the solver's t --
        # strictly positive at the time -- made SymPy evaluate
        # DiracDelta of it to 0, and `delta(t)` silently became nothing.
        # The solver's t is non-negative now, which keeps the impulse, so
        # the workaround is gone and there is one t again.
        #
        # That matters beyond impulses: two identical-looking t symbols
        # never combine, so `v_2 + t` in Evaluate carried both and no
        # amount of simplifying would bring them together.
        "t": T, "s": S,
    }
    if reserve_imaginary:
        # i, I, j and J all mean the imaginary unit. Reserving all four
        # is what lets `3*j` be unambiguous: no variable may use those
        # names, so there is nothing else they could mean.
        for name in _IMAGINARY_NAMES:
            ns[name] = sp.I
    return ns


_IDENT_RE = re.compile(r"(?<![\w.])([A-Za-z_]\w*)")


def hijacked_names(text: str, reserve_imaginary: bool = True):
    """Names in `text` that SymPy would quietly reinterpret as something
    other than a variable -- `Q`, `N`, `beta`, `E` and friends. Returned
    so the caller can tell the user they were read as plain variables.

    `reserve_imaginary` must match whatever was passed to `safe_sympify`
    for the same text, so this never reports i/I/j/J as "hijacked" when
    they were in fact read as ordinary variables (outside AC)."""
    import sympy as sp

    allowed = _allowed_namespace(reserve_imaginary)
    found = []
    for name in dict.fromkeys(_IDENT_RE.findall(text)):
        if name in allowed:
            continue
        if not reserve_imaginary and name in _IMAGINARY_NAMES:
            # Deliberately plain symbols here, not a SymPy built-in that
            # got in the way -- so this isn't a "hijack" to report.
            continue
        looked_up = getattr(sp, name, None)
        if looked_up is not None and not isinstance(looked_up, sp.Symbol):
            found.append(name)
    return found


class UnsafeExpressionError(ValueError):
    """A value or equation contains Python syntax that is not arithmetic."""


_SAFE_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
_SAFE_UNARY = (ast.UAdd, ast.USub)

# The syntax tree's own class names -- IfExp, Subscript, ListComp -- are
# Python's vocabulary, not a circuit-solver user's, and the message a person
# reads should describe what they wrote. Anything not listed falls back to a
# phrase that names nothing rather than naming the wrong thing.
_PLAIN_NAMES = {
    "IfExp": "a conditional expression",
    "Compare": "a comparison",
    "BoolOp": "a boolean operator",
    "Lambda": "a lambda",
    "Attribute": "attribute access with a dot",
    "Subscript": "square-bracket indexing",
    "List": "a list",
    "Dict": "a dictionary",
    "Set": "a set",
    "ListComp": "a comprehension",
    "SetComp": "a comprehension",
    "DictComp": "a comprehension",
    "GeneratorExp": "a comprehension",
    "Starred": "a starred argument",
    "Slice": "a slice",
    "JoinedStr": "a formatted string",
    "NamedExpr": "an assignment",
    "Await": "an await",
    "Yield": "a yield",
}


def check_expression_syntax(text: str, original: str = None) -> None:
    """Refuse `text` unless it is plain arithmetic: numbers, names, the
    operators + - * / ** %, parentheses, and calls of named functions.

    `original` is what the user actually typed, when `text` is a rewrite of
    it. Values are rewritten before they are parsed -- `[a,b]` becomes
    `pr(a,b)` and `1'k` becomes `1*10**3` -- so without it a complaint about
    `[1'k,2'k` came back quoting `pr(1*10**3,2*10**3`, which is the
    machine's business and not the reader's.

    sympify() hands the string to Python's eval, and the restricted
    namespace in `_allowed_namespace` only governs *names* -- Python
    syntax such as conditionals, comprehensions, lambdas, attribute
    access, subscripts and strings would still execute. Checking the
    syntax tree first makes the namespace trick unnecessary as a
    security boundary; it is what lets the web app accept circuit
    strings from strangers. Raises UnsafeExpressionError."""
    shown = (original if original is not None else text).strip()
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(
            f"Could not read the value '{shown}': {exc.msg}.") from None

    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load)):
            continue
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, complex)) \
                    and not isinstance(node.value, bool):
                continue
            bad = repr(node.value)
        elif isinstance(node, ast.Name):
            if node.id.startswith("__"):
                bad = node.id
            else:
                continue
        elif isinstance(node, ast.BinOp) and isinstance(node.op, _SAFE_BINOPS):
            continue
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, _SAFE_UNARY):
            continue
        elif isinstance(node, (ast.Tuple, *_SAFE_BINOPS, *_SAFE_UNARY)):
            continue
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and not node.keywords:
                continue
            bad = "a call that is not a plain function name"
        else:
            bad = _PLAIN_NAMES.get(type(node).__name__, "something")
        raise UnsafeExpressionError(
            f"The value '{shown}' contains {bad}, which is not arithmetic. "
            "Values may use numbers, symbols, + - * / ** and function calls "
            "such as sqrt(2) or exp(-3)."
        )


def _names_called(text: str, ns: dict) -> list:
    """Names used as functions in `text` that are only symbols.

    `rx[1'k]` rewrites into something shaped like a call, which the syntax
    gate allows -- calls of named functions are legitimate -- so it reaches
    SymPy and dies there as "'Symbol' object is not callable", a message
    that names neither the circuit nor the value nor the culprit.
    """
    import sympy as sp
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if isinstance(ns.get(name), sp.Symbol) and name not in out:
                out.append(name)
    return out


# Python keywords, usable as plain variable names in a circuit. A
# circuits reader who names a current source `is` -- the most natural
# name there is for it -- should not be told about Python's grammar:
# `is`, `in`, `if` and the rest are meaningless in the value language
# anyway (the syntax gate refuses boolean and comparison operators),
# so a bare keyword token can only ever be a variable. It is shielded
# behind a sentinel name before parsing and the real symbol restored
# after. `True`/`False`/`None` stay excluded: those are literals, and
# refusing them (as before) beats quietly turning them into symbols.
import keyword as _keyword

_SHIELDABLE = [k for k in _keyword.kwlist
               if k not in ("True", "False", "None")]
# A keyword counts only as a standalone name: not part of a longer
# identifier, not attribute-ish, and not called like a function.
_KW_RE = re.compile(
    r"(?<![\w.])(" + "|".join(_SHIELDABLE) + r")(?![\w(])")
_KW_SENTINEL = "_kw_{}_zz"
_KW_BACK = re.compile(r"^_kw_(\w+)_zz$")


def _shield_keywords(text: str) -> str:
    return _KW_RE.sub(lambda m: _KW_SENTINEL.format(m.group(1)), text)


def _unshield_keywords(expr):
    """Sentinel symbols back to their keyword names, in the parsed
    expression. sp.Symbol('is') is perfectly legal inside SymPy -- only
    Python's own parser ever objected."""
    import sympy as sp

    mapping = {}
    for s in getattr(expr, "free_symbols", ()):  # noqa: B007
        m = _KW_BACK.match(s.name)
        if m:
            mapping[s] = sp.Symbol(m.group(1))
    return expr.subs(mapping) if mapping else expr


def safe_sympify(text: str, reserve_imaginary: bool = True,
                 original: str = None):
    """sympify() restricted to the namespace above: every identifier that
    isn't an intended constant or function becomes a plain Symbol.

    `reserve_imaginary` (default true, for backward compatibility with
    every caller that isn't domain-aware) controls whether i/I/j/J parse
    as the imaginary unit or as ordinary symbols -- pass false for any
    analysis where complex values don't apply (dc, fd, tr).

    `original` is what the user typed, when `text` is a rewrite of it --
    see check_expression_syntax.

    A Python keyword used as a plain name (`is`, for a source current)
    is accepted and becomes an ordinary symbol of that name -- see
    _shield_keywords above."""
    import sympy as sp

    shown_source = original if original is not None else text
    text = _shield_keywords(text)

    check_expression_syntax(text, shown_source)
    ns = _allowed_namespace(reserve_imaginary)
    for name in set(_IDENT_RE.findall(text)):
        ns.setdefault(name, sp.Symbol(name))
    try:
        return _unshield_keywords(sp.sympify(text, locals=ns))
    except TypeError as exc:
        if "not callable" not in str(exc):
            raise
        shown = shown_source.strip()
        called = _names_called(text, ns)
        rewritten = original is not None and original.strip() != text.strip()
        # Name the culprit only when the text was not rewritten -- after a
        # rewrite the name SymPy chokes on is one the reader never typed.
        who = (f"'{called[0]}' is a name, not a function, and it is being "
               f"used as one"
               if called and not rewritten
               else "a name is being used as a function")
        raise UnsafeExpressionError(
            f"Could not read the value '{shown}': {who}. Square brackets "
            f"straight after a name read that way -- a parallel combination "
            f"stands on its own, as in [1'k,2'k]."
        ) from None
