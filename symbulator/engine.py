"""
Symbolic stamping engine: builds the KCL system for a parsed circuit and
solves it with SymPy. Ports `symbv8s6` / `symbv8s7` / `symbv8s8`.

Design note (deliberate simplification vs. the original):
The original TI-Basic code split unknowns into a "1st level" (solved
simultaneously via `solve`/`cSolve`) and a "2nd level" (computed by
direct substitution afterwards) purely as a performance optimization on
calculator hardware. This port always solves everything simultaneously
via `sympy.solve`. So a resistor's current, second level on the
calculator, is a first-level unknown here -- one of the two places this
port's classification departs from the 2000 thesis's census (the other
is the voltage drop; see `analysis._derived`). The monograph's §4.3
records both departures. The physics and the results are identical either way;
we trade a bit of solver efficiency for a much smaller, easier-to-verify
implementation. Two exceptions are kept as direct substitutions because
they are always locally computable and keeping them explicit avoids
inflating the unknown count for no benefit: capacitor current in AC mode,
and independent-source current for `j` elements.

Bug fix vs. the original: a resistor/inductor/voltage-source whose value
is literally "0" is treated as a plain wire (short circuit), in both DC
and AC -- matches physical intuition (0 ohm, 0 henry, 0 volt all reduce
to a wire) and is kept as-is. The original's `symbv8s8` also routed a
0-valued *capacitor* through that same short-circuit rule; a 0 F
capacitor is physically an open circuit, not a short (infinite
impedance, not zero), so this port always treats a capacitor as open in
DC and applies the normal AC admittance formula i = (v1-v2)*j*omega*C
unconditionally -- which naturally evaluates to an open circuit (i=0)
when C is 0, with no special-casing needed. See `_stamp_c` below.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import sympy as sp

from .elements import CircuitError, Element, TWO_PORT_KINDS
from .si_prefix import (expand_shorthand, expand_value, safe_sympify,
                        hijacked_names)


def _sym(name: str) -> sp.Symbol:
    """Tiny wrapper around sp.Symbol so call sites read `_sym("v_2")`
    instead of `sp.Symbol("v_2")` -- purely a naming/readability shim."""
    return sp.Symbol(name)


# Answer-name prefixes an alias may resolve to, per element. `v_` for
# nodes is added separately (and wins a collision, matching the
# node-owned precedence in stamp_all's reference closure).
_ALIAS_PREFIXES = ("i", "v", "p", "r", "z", "s", "ap")


def _norm_name(name: str) -> str:
    return name.lower().replace("_", "")


def _alias_map(elements: List[Element]) -> Dict[str, str]:
    """Underscore-and-case-insensitive spellings of every answer name
    this circuit can produce, mapped to the canonical spelling.

    Symbulator's design gives the underscored and non-underscored
    spellings FULL equivalence, everywhere a name can appear: `ir1`,
    `i_r1`, `IR1` and `I_R1` are one name, exactly as they were one
    calculator variable before version 9 introduced the underscore
    convention. The map is built from the circuit itself -- node
    voltages first (a node named `r1` owns `v_r1` over element r1's
    voltage drop, matching stamp_all), then every answer prefix of
    every element name. A spelling that matches nothing here is an
    ordinary free symbol, untouched."""
    amap: Dict[str, str] = {}
    for e in elements:
        if e.kind == "m":
            continue
        nodes = e.fields[:3] if e.kind == "o" else [e.n1, e.n2]
        for n in nodes:
            if n != "0":
                canon = f"v_{n}"
                amap.setdefault(_norm_name(canon), canon)
    for e in elements:
        if e.kind == "m":
            continue
        for p in _ALIAS_PREFIXES:
            canon = f"{p}_{e.name}"
            amap.setdefault(_norm_name(canon), canon)
    return amap


def _canonicalize(expr, amap: Dict[str, str]):
    """Rename free symbols in `expr` whose spelling aliases a canonical
    answer name. Exact canonical spellings pass through unchanged."""
    if not getattr(expr, "free_symbols", None):
        return expr
    subs = {}
    for s in expr.free_symbols:
        canon = amap.get(_norm_name(s.name))
        if canon is not None and s.name != canon:
            subs[s] = _sym(canon)
    return expr.subs(subs) if subs else expr


class Circuit:
    """Mutable working state while stamping a parsed element list.

    "Stamping" is circuit-analysis jargon for translating each element
    into its contribution to the system of equations -- one equation per
    element describing how its current relates to the voltage across it
    (Ohm's law for a resistor, a defining voltage for a source, etc.),
    plus a running per-node sum of currents that becomes that node's KCL
    (Kirchhoff's Current Law: currents into a node sum to zero) equation
    once every element has been stamped. See `stamp_all` and the
    `_stamp_<kind>` methods below, one per element type."""

    def __init__(self, elements: List[Element], domain: str, omega=None,
                 params: Optional[Dict[str, Dict[str, object]]] = None,
                 suffix: str = "si"):
        """Set up empty bookkeeping for a solve: `domain` picks dc/ac/fd
        stamping rules (see the _stamp_* methods), `omega` is the AC
        angular frequency symbol/value, `params` supplies numeric two-port
        parameters where the caller has them (see `_two_port_params`),
        and `suffix` controls how bare engineering-notation values like
        "1k" are read (see si_prefix.expand_value). Also pre-scans for
        `m` (mutual inductance) elements and records which inductor pairs
        they couple, since that coupling needs to be visible from inside
        `_stamp_l` for both inductors, not just processed once on its own."""
        if domain not in ("dc", "ac", "fd"):
            raise ValueError("domain must be 'dc', 'ac', or 'fd'")
        if suffix not in ("ask", "si", "var"):
            raise ValueError("suffix must be 'ask', 'si', or 'var'")
        self.suffix = suffix
        # Rewritten field -> what the reader typed, for the values where the
        # two differ. `_value` receives a field's text and nothing else, so
        # this is how it recovers the original to quote in an error. Only
        # differing pairs are stored, and a collision is harmless: the two
        # rewrote to the same thing, so either original describes it.
        self._typed_form = {}
        for _el in elements:
            for _new, _old in zip(_el.fields, getattr(_el, "raw_fields", [])):
                if _new != _old:
                    self._typed_form.setdefault(_new, _old)
        self.elements = elements
        self.domain = domain
        # i, I, j and J only mean the imaginary unit in AC, where a
        # source or component value can genuinely be complex. In dc/fd
        # they're free to use as ordinary variable names.
        self.reserve_imaginary = (domain == "ac")
        self.omega = omega if omega is not None else sp.Symbol("omega", real=True)
        self.s = sp.Symbol("s")
        self.params = params or {}

        self.node_sum: Dict[str, sp.Expr] = {}
        self.equations: List[sp.Eq] = []
        self.unknowns: List[sp.Symbol] = []
        self.known: Dict[str, sp.Expr] = {}
        self._by_name: Dict[str, Element] = {e.name: e for e in elements}
        # Built before the first _value call (the mutuals loop below
        # parses values): every spelling of every answer name this
        # circuit can produce, resolved to the canonical one.
        self.alias_map: Dict[str, str] = _alias_map(elements)

        self.mutual_of: Dict[str, List[Tuple[str, sp.Expr]]] = {}
        for e in elements:
            if e.kind == "m":
                l1, l2, m_val = e.fields[0], e.fields[1], self._value(e.fields[2])
                if m_val != 0:
                    self.mutual_of.setdefault(l1, []).append((l2, m_val))
                    self.mutual_of.setdefault(l2, []).append((l1, m_val))

    # -- helpers ----------------------------------------------------
    def _value(self, raw: str) -> sp.Expr:
        """Turn a raw field string (already through the parser, so still
        plain text like "4.7'u" or "2*v_2") into a SymPy expression: first
        expand any `'k`-style unit shorthand, then parse it through the
        restricted namespace in si_prefix.safe_sympify (so stray letters
        like "Q" become plain symbols, not SymPy internals), then resolve
        answer-name aliases: `0.5*ir1` means `0.5*i_r1`, calculator
        style -- see _alias_map."""
        expr = safe_sympify(expand_value(raw, self.suffix),
                            reserve_imaginary=self.reserve_imaginary,
                            original=self._typed_form.get(str(raw), str(raw)))
        return _canonicalize(expr, self.alias_map)

    def v(self, node: str) -> sp.Expr:
        """Return the (symbolic) voltage at `node`, registering it as an
        unknown the first time it's asked for. Ground ("0") is always
        the literal constant 0 rather than a symbol -- it's the reference
        every other node voltage is measured against, so it's never
        solved for. Calling this is also how a node first becomes known
        to the system: `node_sum` (its running KCL total) is created here
        too, even before any current has been added to it, so a node that
        only ever appears on the *voltage* side of an equation (e.g. an
        op-amp's untouched output before add_current is called) still
        ends up with a KCL equation once stamping is done."""
        if node == "0":
            return sp.Integer(0)
        sym = _sym(f"v_{node}")
        if f"v_{node}" not in [str(u) for u in self.unknowns]:
            self.unknowns.append(sym)
        self.node_sum.setdefault(node, sp.Integer(0))
        return sym

    def add_current(self, node: str, expr: sp.Expr) -> None:
        """Add `expr` to the running sum of currents leaving `node`
        (Kirchhoff's Current Law bookkeeping). Every `_stamp_*` method
        calls this once per terminal of the element it's stamping, with
        opposite signs at the two ends, so that once every element has
        been stamped, each node's total is "current in" minus "current
        out" and setting that total to 0 is exactly KCL for that node.
        Ground is exempt: current can freely flow to/from the reference
        node without needing its own balance equation."""
        if node == "0":
            return
        self.v(node)  # ensure node is registered
        self.node_sum[node] = self.node_sum[node] + expr

    def new_unknown(self, name: str) -> sp.Symbol:
        """Create a fresh unknown symbol not tied to any node voltage or
        element current -- used for things like a two-port block's
        internal port currents, which need their own symbol but aren't a
        node voltage or a simple branch current."""
        sym = _sym(name)
        self.unknowns.append(sym)
        return sym

    def i_symbol(self, element_name: str) -> sp.Symbol:
        """The symbol standing for the current through element
        `element_name` (by convention `i_<name>`), matching the
        `i<name>` calculator variable the original stored a solved
        current in."""
        return _sym(f"i_{element_name}")

    # -- element stamping --------------------------------------------
    def stamp_all(self) -> None:
        """Stamp every element in turn (dispatching to `_stamp_<kind>` by
        the element's first letter), then turn each node's finished
        current total into its KCL equation. After this call,
        `self.equations` is the complete system to hand to sympy.solve,
        and `self.unknowns` is every symbol it should solve for."""
        for e in self.elements:
            if e.kind == "m":
                continue  # folded into the 'l' elements it couples
            method = getattr(self, f"_stamp_{e.kind}", None)
            if method is None:
                raise CircuitError(f"No stamping rule implemented for element kind '{e.kind}'.")
            method(e)

        for node, total in self.node_sum.items():
            self.equations.append(sp.Eq(total, 0))

        # A dependent source may name a quantity that is *known* rather
        # than solved for: a capacitor's current in AC or FD, or another
        # source's current. Those never become unknowns, so the symbol
        # the user wrote -- `2*i_cx` on a `j` element, say -- has nothing
        # tying it to the capacitor it names. The system then gains a
        # free variable that no equation constrains, and sympy answers
        # every quantity *in terms of it*: AS7's Example 10.1 came back
        # with `i_cx = i_cx*(0.9655 + 0.4138j) + 2.897 + 1.241j`, an
        # equation the solver had been handed but never asked to close.
        # It looks like a solve rather than a failure, which is what let
        # it stand.
        #
        # Substituting the stamped expressions turns each such reference
        # into a real constraint on the node voltages it is made of. The
        # loop is because one known can name another (a `j` source
        # controlled by a capacitor's current is exactly that); it is
        # bounded because a source controlled by its own current is
        # circular and must not spin here.
        # The same hole exists for a *voltage*-controlled source. An
        # element's voltage is never an unknown either: it is derived at
        # reporting time as v(n1) - v(n2). So `3*v_rx` on an `e` element
        # -- AS7's Practice Problem 10.1 -- left `v_rx` free in exactly
        # the same way. A node may legitimately own the name (a node
        # called `rx` would make `v_rx` its own voltage), so the node
        # unknowns win where the two collide.
        node_owned = {str(u) for u in self.unknowns}
        refs = {}
        for e in self.elements:
            n1, n2 = getattr(e, "n1", None), getattr(e, "n2", None)
            key = f"v_{e.name}"
            if e.kind in ("m", "o", "t") or n1 is None or n2 is None:
                continue
            if key in node_owned:
                continue
            refs[_sym(key)] = self.v(n1) - self.v(n2)

        if self.known or refs:
            known_map = {_sym(k): v for k, v in self.known.items()}
            known_map.update(refs)
            for _ in range(len(known_map) + 1):
                stepped = {k: (v.subs(known_map) if hasattr(v, "subs") else v)
                           for k, v in known_map.items()}
                if stepped == known_map:
                    break
                known_map = stepped
            self.equations = [eq.subs(known_map) for eq in self.equations]
            self.known = {k: (v.subs(known_map) if hasattr(v, "subs") else v)
                          for k, v in self.known.items()}

    def _short(self, e: Element) -> None:
        """Zero-value r/l/c/e, and all 's' elements: v(n1) = v(n2),
        with the branch current as a fresh unknown."""
        n1, n2 = e.n1, e.n2
        i = self.i_symbol(e.name)
        self.unknowns.append(i)
        self.equations.append(sp.Eq(self.v(n1) - self.v(n2), 0))
        self.add_current(n1, i)
        self.add_current(n2, -i)

    def _stamp_r(self, e: Element) -> None:
        """Resistor: Ohm's law, v(n1) - v(n2) = R * i, with i flowing
        from n1 to n2 through the resistor. A 0-ohm resistor is stamped
        as a plain wire instead (see `_short`) -- Ohm's law would still
        be correct at R=0, but keeping it as a real equation costs the
        solver nothing while a wire is simpler and matches how every
        other zero-valued element in this engine is handled."""
        R = self._value(e.value)
        if R == 0:
            self._short(e)
            return
        i = self.i_symbol(e.name)
        self.unknowns.append(i)

        # A textbook gives a coupled pair one of two ways: two inductors in
        # henries, or two impedances already in jOhms. The second is written
        # here as `r` elements with imaginary values, coupled by an `m` whose
        # value is imaginary too -- and it has to be stamped, or the coupling
        # is silently ignored and the secondary carries no current at all.
        #
        # symbv8s8 does this only when the tool is ac, and adds the mutual
        # term without a jw factor, because a value in jOhms is already an
        # impedance:  v(n1) - v(n2) = Z*i_self + sum(M * i_other)
        coupling = sp.Integer(0)
        if self.domain == "ac":
            for other_name, m_val in self.mutual_of.get(e.name, []):
                coupling += m_val * self.i_symbol(other_name)
                other_sym = self.i_symbol(other_name)
                if other_sym not in self.unknowns:
                    self.unknowns.append(other_sym)

        self.equations.append(
            sp.Eq(self.v(e.n1) - self.v(e.n2), R * i + coupling))
        self.add_current(e.n1, i)
        self.add_current(e.n2, -i)

    def _stamp_l(self, e: Element) -> None:
        """Inductor: v-i relationship depends on the analysis domain --
        a short in DC steady state (no voltage drop once current has
        settled), v = jωL·i in AC (phasor impedance), and the s-domain
        form v/s = L(i - i₀/s) in FD, which is Laplace's version of
        v = L·di/dt with a nonzero initial current i₀ folded in. Also
        adds each mutually-coupled inductor's contribution (M·i_other,
        or its s-domain equivalent) to the voltage equation -- that's
        what a transformer-style magnetic coupling means physically: one
        coil's current induces a voltage in the other. A 0 H inductor
        is stamped as a plain wire (see `_short`), matching a resistor
        at R=0: a real inductance of zero has no way to sustain a
        voltage across it in any domain."""
        L = self._value(e.value)
        if L == 0:
            self._short(e)
            return
        i = self.i_symbol(e.name)
        self.unknowns.append(i)
        if self.domain == "dc":
            # Inductor is a short circuit in DC steady state.
            self.equations.append(sp.Eq(self.v(e.n1) - self.v(e.n2), 0))
        elif self.domain == "ac":
            coupling = sp.Integer(0)
            for other_name, m_val in self.mutual_of.get(e.name, []):
                coupling += m_val * self.i_symbol(other_name)
                other_sym = self.i_symbol(other_name)
                if other_sym not in self.unknowns:
                    self.unknowns.append(other_sym)
            self.equations.append(
                sp.Eq(self.v(e.n1) - self.v(e.n2), sp.I * self.omega * (L * i + coupling))
            )
        else:  # fd: s-domain, with initial condition i(0) = ic
            ic = self._value(e.ic)
            coupling = sp.Integer(0)
            for other_name, m_val in self.mutual_of.get(e.name, []):
                other_el = self._by_name[other_name]
                other_ic = self._value(other_el.ic)
                coupling += m_val * (self.i_symbol(other_name) - other_ic / self.s)
                other_sym = self.i_symbol(other_name)
                if other_sym not in self.unknowns:
                    self.unknowns.append(other_sym)
            self.equations.append(
                sp.Eq((self.v(e.n1) - self.v(e.n2)) / self.s,
                      L * (i - ic / self.s) + coupling)
            )
        self.add_current(e.n1, i)
        self.add_current(e.n2, -i)

    def _stamp_c(self, e: Element) -> None:
        """Capacitor: unlike every other element, a capacitor's branch
        current is *known* directly from the node voltages (i = C·dv/dt
        and its AC/FD equivalents below) rather than needing its own
        unknown -- so this stamps a current expression straight into
        `known` and both nodes' KCL sums, with no new equation or
        unknown added. See the module docstring for why 0 F is always
        treated as an open circuit here (never short-circuited)."""
        C = self._value(e.value)
        if self.domain == "dc":
            # Capacitor is an open circuit in DC steady state -- always,
            # regardless of capacitance value (including 0): no current,
            # nothing added to either node's KCL sum.
            self.known[f"i_{e.name}"] = sp.Integer(0)
            return
        if self.domain == "ac":
            # i = (v1 - v2) * j*omega*C. Applied unconditionally -- if
            # C is 0 this naturally evaluates to i=0 (open circuit), which
            # is the physically correct result, so no special-casing of
            # C==0 is needed (or wanted: shorting a 0F cap would be wrong).
            i_expr = (self.v(e.n1) - self.v(e.n2)) * (sp.I * self.omega * C)
        else:  # fd: s-domain, with initial condition v(0) = ic
            ic = self._value(e.ic)
            i_expr = (self.v(e.n1) - self.v(e.n2)) * (self.s * C) - C * ic
        self.known[f"i_{e.name}"] = i_expr
        self.add_current(e.n1, i_expr)
        self.add_current(e.n2, -i_expr)

    def _stamp_e(self, e: Element) -> None:
        """Voltage source (independent, or dependent on a value like
        "2*v_3"): defines v(n1) - v(n2) = value outright, with the
        current through it left as a free unknown for the solver to
        find (a voltage source supplies whatever current the rest of
        the circuit demands). A 0 V source is a wire either way, so it's
        stamped as a plain short (see `_short`)."""
        val = self._value(e.value)
        if val == 0:
            self._short(e)
            return
        i = self.i_symbol(e.name)
        self.unknowns.append(i)
        self.equations.append(sp.Eq(self.v(e.n1) - self.v(e.n2), val))
        self.add_current(e.n1, i)
        self.add_current(e.n2, -i)

    def _stamp_j(self, e: Element) -> None:
        """Current source (independent, or dependent on a value like
        "0.5*i_r1"): the current itself is already known outright, so
        (unlike every other source/component) no new unknown or equation
        is needed at all -- just record it and add it straight into both
        terminals' KCL sums. A 0 A source contributes nothing, so it's
        simply skipped rather than routed through `_short` (an open
        current source is correctly an open circuit, not a wire)."""
        val = self._value(e.value)
        self.known[f"i_{e.name}"] = val
        if val == 0:
            return
        self.add_current(e.n1, val)
        self.add_current(e.n2, -val)

    def _stamp_o(self, e: Element) -> None:
        """Ideal op-amp / nullor. Fields: n_plus, n_minus, n_out."""
        n_plus, n_minus, n_out = e.fields[0], e.fields[1], e.fields[2]
        self.equations.append(sp.Eq(self.v(n_plus), self.v(n_minus)))
        i_out = self.i_symbol(e.name)
        self.unknowns.append(i_out)
        # ensure input nodes are registered even though no current flows in
        self.v(n_plus)
        self.v(n_minus)
        self.add_current(n_out, -i_out)

    def _stamp_s(self, e: Element) -> None:
        """Explicit short circuit (an 's' element): always a wire,
        regardless of value -- there's no "value" field to check."""
        self._short(e)

    def _stamp_t(self, e: Element) -> None:
        """Ideal transformer. Fields: n1, n2, turns1, turns2. Relates the
        two windings' voltages by their turns ratio (v1/turns1 =
        v2/turns2) and their currents inversely (i2 = -i1 * turns1 /
        turns2, so an ideal transformer neither creates nor consumes
        power: v1*i1 + v2*i2 = 0). Only one current is a free unknown
        (i1); i2 is computed directly from it rather than needing its
        own equation."""
        n1, n2, n1t, n2t = e.n1, e.n2, self._value(e.fields[2]), self._value(e.fields[3])
        i1 = self.i_symbol(f"{e.name}{n1}")
        self.unknowns.append(i1)
        i2_expr = -i1 * n1t / n2t
        self.equations.append(sp.Eq(self.v(n1) / n1t, self.v(n2) / n2t))
        self.add_current(n1, i1)
        self.add_current(n2, i2_expr)

    def _two_port_params(self, e: Element) -> Tuple[sp.Expr, sp.Expr, sp.Expr, sp.Expr]:
        """The four z/y/h/g/a/b parameters for two-port element `e`, as
        SymPy expressions. If the caller supplied numeric/symbolic values
        for this element via `params` (e.g. {"11": "100", "12": "0"}),
        those are used directly; otherwise each parameter becomes its own
        free symbol (e.g. `z11`), which lets a circuit reference a
        two-port block's behaviour abstractly and solve for it, or have
        its value supplied later via `conditions`."""
        p = self.params.get(e.name)
        if p:
            return tuple(safe_sympify(expand_value(str(p[ij]), self.suffix),
                                      reserve_imaginary=self.reserve_imaginary,
                                      original=str(p[ij]))
                         for ij in ("11", "12", "21", "22"))
        return (_sym(f"{e.name}11"), _sym(f"{e.name}12"),
                _sym(f"{e.name}21"), _sym(f"{e.name}22"))

    def _stamp_two_port(self, e: Element) -> None:
        """A grounded two-port block (z/y/h/g/a/b) is a black box defined
        purely by its four parameters, standing in for some sub-circuit
        (an amplifier, a filter, another whole circuit characterized by
        `equiv.port()`) whose internals aren't modelled -- just its
        port1/port2 voltage-current relationship. Each parameter type
        (p11, p12, p21, p22, from `_two_port_params`) defines that
        relationship differently; below, each branch algebraically solves
        that type's two defining equations for the port currents i1, i2
        in terms of the port voltages v1, v2, since v1/v2 are what the
        rest of the KCL system already has (as node voltages) while i1/i2
        are what KCL needs (currents to add into each port node's sum).
        `n1`/`n2` are the two live nodes; the second terminal of each
        port is implicitly ground, hence "grounded two-port"."""
        n1, n2 = e.n1, e.n2
        v1, v2 = self.v(n1), self.v(n2)
        p11, p12, p21, p22 = self._two_port_params(e)
        k = e.kind

        if k == "z":
            # z (impedance) parameters define v1, v2 in terms of i1, i2:
            #   v1 = z11*i1 + z12*i2,  v2 = z21*i1 + z22*i2
            # That's the *opposite* direction from what's needed here, so
            # solve the 2x2 linear system for i1, i2 (equivalently: invert
            # the z-matrix). det is that matrix's determinant.
            det = p11 * p22 - p12 * p21
            i1 = (p22 * v1 - p12 * v2) / det
            i2 = (p11 * v2 - p21 * v1) / det
        elif k == "y":
            # y (admittance) parameters already give currents directly:
            #   i1 = y11*v1 + y12*v2,  i2 = y21*v1 + y22*v2
            # -- no algebra needed, just the defining equations.
            i1 = p11 * v1 + p12 * v2
            i2 = p21 * v1 + p22 * v2
        elif k == "h":
            # h (hybrid) parameters mix the two: v1 = h11*i1 + h12*v2 and
            # i2 = h21*i1 + h22*v2. Solve the first for i1, then
            # substitute into the second to get i2 purely in terms of
            # v1, v2.
            i1 = (v1 - p12 * v2) / p11
            i2 = p22 * v2 - p21 * ((p12 * v2 - v1) / p11)
        elif k == "g":
            # g (inverse hybrid) parameters: i1 = g11*v1 + g12*i2 and
            # v2 = g21*v1 + g22*i2 -- the mirror image of h. Solve the
            # second for i2, substitute into the first for i1. det here
            # is the same determinant-style combination as in the z case.
            det = p11 * p22 - p12 * p21
            i1 = ((det) / p22) * v1 + (p12 / p22) * v2
            i2 = (-p21 / p22) * v1 + (1 / p22) * v2
        elif k == "a":
            # a (ABCD / transmission / chain) parameters relate port 1 to
            # port 2 with i2 flowing *out* of the block:
            #   v1 = A*v2 - B*i2,  i1 = C*v2 - D*i2
            # (the convention that makes chaining two-ports end-to-end
            # just a matrix product). Solve the first for i2, substitute
            # into the second for i1.
            i1 = (-p11 * p22 * v2 + p12 * p21 * v2 + p22 * v1) / p12
            i2 = (p11 * v2 - v1) / p12
        elif k == "b":
            # b (inverse transmission) parameters are ABCD run the other
            # way, from port 2's perspective: v2 = B11*v1 - B12*i1,
            # i2 = B21*v1 - B22*i1. Solve the first for i1, substitute
            # into the second for i2.
            i1 = (p11 / p12) * v1 + (-1 / p12) * v2
            i2 = (-(p11 * p22 - p12 * p21) / p12) * v1 + (p22 / p12) * v2
        else:
            raise CircuitError(f"Unknown two-port kind '{k}'.")

        i1_sym = self.i_symbol(f"{e.name}{n1}")
        i2_sym = self.i_symbol(f"{e.name}{n2}")
        self.unknowns.append(i1_sym)
        self.unknowns.append(i2_sym)
        self.equations.append(sp.Eq(i1_sym, i1))
        self.equations.append(sp.Eq(i2_sym, i2))
        self.add_current(n1, i1_sym)
        self.add_current(n2, i2_sym)

    _stamp_z = _stamp_two_port
    _stamp_y = _stamp_two_port
    _stamp_h = _stamp_two_port
    _stamp_g = _stamp_two_port
    _stamp_a = _stamp_two_port
    _stamp_b = _stamp_two_port


def _parse_extra_equation(raw, reserve_imaginary: bool = True) -> sp.Eq:
    """Turn a user-supplied extra equation (expert mode) into a sympy Eq.
    Accepts "lhs = rhs" strings (with the calculator's 'k-style unit
    shorthand -- the original ran its prefix expander over added
    equations too), bare expressions (treated as expr = 0), or sympy
    Eq/Expr objects directly. `reserve_imaginary` should match the
    domain the equation is being added to (see `solve_circuit`)."""
    if isinstance(raw, sp.Eq):
        return raw
    if isinstance(raw, sp.Expr):
        return sp.Eq(raw, 0)
    text = expand_shorthand(str(raw))
    if "=" in text:
        lhs, rhs = text.split("=", 1)
        return sp.Eq(safe_sympify(lhs, reserve_imaginary=reserve_imaginary),
                     safe_sympify(rhs, reserve_imaginary=reserve_imaginary))
    return sp.Eq(safe_sympify(text, reserve_imaginary=reserve_imaginary), 0)


# Prefixes `analysis._derived` uses when it names a post-solve quantity.
# Ordered longest-first so "ap_e" is read as ap/e, not as a/p_e.
_DERIVED_PREFIXES = ("ap", "v", "p", "r", "z", "s")


def _ac_power_unsupported(name: str, element: str) -> "CircuitError":
    """The error raised when an expert-mode equation is written on one of
    the AC power quantities. They are the one family of derived quantity
    with no polynomial definition -- s = v * conj(i), and the real powers
    are its real part -- and complex conjugation is not something
    sympy.solve can carry through a system. Refusing here is deliberate:
    the alternative is the silent phantom-unknown this whole function
    exists to prevent."""
    return CircuitError(
        f"Cannot add an expert-mode equation on '{name}': the AC power "
        f"quantities (s_, p_, ap_) are defined through complex conjugation, "
        f"which cannot be solved as part of the system. Restate the "
        f"constraint using node voltages (v_1, v_2, ...) and element "
        f"currents (i_{element}, ...), which are solved for directly."
    )


def _derived_definition(circuit: "Circuit", name: str, domain: str):
    """If `name` is one of the quantities `analysis._derived` computes
    *after* the solve -- a branch voltage v_<element>, a power p_<element>,
    or the impedance r_/z_<element> a source sees -- return
    `(defining_equation, symbol)` expressing it in terms of the system's
    own unknowns. Return None if `name` is not one of those, in which case
    the caller treats it as an ordinary new unknown, as before.

    This exists because those quantities are derived after the fact and so
    are invisible to the solver. Without a definition, an equation like
    "r_e = 12000" introduces a free variable that happens to share the
    name, satisfies itself, and quietly leaves the circuit one equation
    short -- the answer comes back correct but parametrized, with no
    indication that the constraint did nothing. With the definition
    stamped in, the equation constrains the circuit as the user meant.

    The formulas here mirror `analysis._derived` exactly; test_expert.py
    checks the two agree by asking for a quantity both ways."""
    for prefix in _DERIVED_PREFIXES:
        head = prefix + "_"
        if not name.startswith(head):
            continue
        element = circuit._by_name.get(name[len(head):])
        if element is None:
            continue          # not an element of this circuit; keep looking

        i_key = f"i_{element.name}"
        # A `j` source's current, and a capacitor's in AC, are recorded in
        # `known` rather than solved for; either way this is the same
        # expression `_derived` would later use.
        i = circuit.known.get(i_key) or circuit.i_symbol(element.name)
        sym = _sym(name)

        if element.kind == "o":
            # Op-amp power is measured at the output node, on the current
            # the amplifier drives out of it.
            if prefix == "p" and domain != "ac":
                return sp.Eq(sym, circuit.v(element.fields[2]) * (-i)), sym
            if domain == "ac" and prefix in ("s", "ap", "p"):
                raise _ac_power_unsupported(name, element.name)
            return None

        if element.kind not in "ejrcl":
            return None
        vdiff = circuit.v(element.n1) - circuit.v(element.n2)

        if prefix == "v":
            return sp.Eq(sym, vdiff), sym
        if prefix == "p":
            if domain == "ac":
                raise _ac_power_unsupported(name, element.name)
            return sp.Eq(sym, vdiff * i), sym
        if prefix in ("s", "ap"):
            if domain == "ac":
                raise _ac_power_unsupported(name, element.name)
            return None       # s_/ap_ are not derived outside ac
        if prefix in ("r", "z"):
            # Only sources get one, and only in their own domain. Written
            # as sym * (-i) = vdiff rather than sym = vdiff / (-i) so the
            # equation stays polynomial and a zero current does not put a
            # division by zero into the system.
            if element.kind not in "ej":
                return None
            if (prefix == "r") != (domain != "ac"):
                return None
            return sp.Eq(sym * (-i), vdiff), sym
    return None


def _diagnose_unsolvable(circuit: "Circuit") -> Optional[str]:
    """Name the two classic contradictions that leave a linear circuit
    with no solution, so the user gets "e1 and e2 are in parallel" rather
    than a generic "could not solve".

    1. A loop of voltage-defining elements -- voltage sources, shorts,
       zero-valued resistors, and (in dc) inductors -- whose voltages
       cannot all hold at once, e.g. two sources across the same nodes or
       a 0 ohm resistor across a source.
    2. A node fed only by current-defining elements -- current sources
       and (in dc) capacitors -- so KCL at that node has no free current
       to balance with.

    Returns a message, or None when neither pattern is present (then the
    caller's generic message stands)."""
    dc = circuit.domain == "dc"

    def is_zero(raw: str) -> bool:
        try:
            return bool(sp.simplify(circuit._value(raw)) == 0)
        except Exception:
            return False

    voltage_edges: List[Tuple[str, str, str]] = []
    current_only: Dict[str, List[str]] = {}   # node -> [element names]
    other_at: Dict[str, int] = {}
    for e in circuit.elements:
        if e.kind == "m":
            continue
        n1, n2 = e.fields[0], e.fields[1]
        if e.kind == "e" or e.kind == "s" \
                or (e.kind == "r" and is_zero(e.fields[2])) \
                or (dc and e.kind == "l"):
            voltage_edges.append((e.name, n1, n2))
        if e.kind == "j" or (dc and e.kind == "c"):
            for n in (n1, n2):
                current_only.setdefault(n, []).append(e.name)
        else:
            nodes = e.fields[:3] if e.kind == "o" else (n1, n2)
            for n in nodes:
                other_at[n] = other_at.get(n, 0) + 1

    # 1. voltage loop: union-find; an edge whose ends are already joined
    #    closes a loop, and the path between them names its members.
    parent: Dict[str, str] = {}
    adj: Dict[str, List[Tuple[str, str]]] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for name, a, b in voltage_edges:
        if find(a) == find(b):
            # BFS a -> b through the edges added so far
            prev = {a: None}
            queue = [a]
            while queue and b not in prev:
                x = queue.pop(0)
                for y, via in adj.get(x, ()):
                    if y not in prev:
                        prev[y] = (x, via)
                        queue.append(y)
            loop = [name]
            x = b
            while prev.get(x):
                x, via = prev[x]
                loop.append(via)
            members = ", ".join(sorted(loop))
            return (
                f"Elements {members} form a loop that fixes the same voltage "
                "more than once (voltage sources, shorts, zero-ohm resistors"
                + (" and inductors, which are shorts in dc" if dc else "")
                + "). Their values contradict each other, so no solution exists."
            )
        parent[find(a)] = find(b)
        adj.setdefault(a, []).append((b, name))
        adj.setdefault(b, []).append((a, name))

    # 2. current-only node
    for node, names in current_only.items():
        if node != "0" and other_at.get(node, 0) == 0:
            members = ", ".join(sorted(names))
            return (
                f"Node {node} connects only to {members}, which fix the "
                "current into it (current sources"
                + (" and capacitors, which are open in dc" if dc else "")
                + "). Those currents cannot sum to zero, so no solution exists."
            )
    return None


def solve_circuit(elements: List[Element], domain: str, omega=None,
                  params: Optional[Dict[str, Dict[str, object]]] = None,
                  equations=None, unknowns=None, conditions=None,
                  suffix: str = "ask") -> Dict[str, sp.Expr]:
    """The preferred solution of `solve_circuit_all` (see there). Kept as
    the name every caller already uses; only circuits whose expert-mode
    equations are quadratic in an unknown ever have more than one."""
    return solve_circuit_all(elements, domain, omega=omega, params=params,
                             equations=equations, unknowns=unknowns,
                             conditions=conditions, suffix=suffix)[0]


def _rank_solutions(solutions: List[Dict[sp.Symbol, sp.Expr]]) -> List[Dict[sp.Symbol, sp.Expr]]:
    """Order several solutions so the physically likely one comes first.
    Node voltages and currents may legitimately be negative, so only the
    *other* unknowns -- element values and source magnitudes introduced
    by expert mode, like `rx` or `e` -- are judged: solutions where they
    are all real rank above ones with complex values, and all
    non-negative above any negative. Ties keep SymPy's order, so a
    single solution is untouched."""
    def is_design(sym) -> bool:
        n = str(sym)
        return not (n.startswith("v_") or n.startswith("i_"))

    def key(sol):
        vals = [v for k, v in sol.items() if is_design(k)]
        complex_ = sum(1 for v in vals if v.is_real is False)
        negative = sum(1 for v in vals if v.is_real and v.is_negative)
        return (complex_, negative)

    return sorted(solutions, key=key)


def solve_circuit_all(elements: List[Element], domain: str, omega=None,
                      params: Optional[Dict[str, Dict[str, object]]] = None,
                      equations=None, unknowns=None, conditions=None,
                      suffix: str = "ask") -> List[Dict[str, sp.Expr]]:
    """Build and solve the KCL system for `elements`. Returns a list of
    dicts {symbol name: solved sympy expression}, one per solution, for
    every node voltage and element/branch current, mirroring what
    Symbulator stores in calculator variables `v<node>` / `i<name>` after
    a simulation. A plain circuit is linear and has exactly one; an
    expert-mode equation on a power (quadratic in the unknowns) can have
    two, and both are returned, ranked by `_rank_solutions`.

    Expert mode (ports the `ex()` "Add equations / Add unknowns / Add
    conditions" prompts):
    - `equations`: extra equations joined into the system before solving
      (strings like "v_2 = 6" or "i_r1 - i_r2", or sympy Eq objects).
    - `unknowns`: extra unknown names to solve for. Any new symbol
      appearing in the extra equations themselves is picked up
      automatically; a symbolic value that only appears in the *circuit*
      (e.g. a resistor whose value is `r_b`) must be listed here
      explicitly for the solver to treat it as an unknown -- same as the
      original's separate "Add unknowns" prompt.
    - `conditions`: substitutions applied to the whole system at solve
      time, the TI's `|` ("with") operator -- strings like "r_a = 1000",
      applied in order.

    `suffix` controls bare engineering-notation values like "1k", which
    could mean either the SI unit (1'k = 1000) or one times a variable
    named k (1*k): "ask" (default) raises AmbiguousValueError listing
    every such value so the caller can ask the user; "si" reads them
    all as SI units; "var" reads them all as number*variable. The two
    explicit spellings (1'k / 1*k) are never ambiguous and always work
    regardless of this setting.
    """
    if suffix == "ask":
        from .elements import ambiguous_in_elements
        from .si_prefix import AmbiguousValueError
        found = ambiguous_in_elements(elements)
        if found:
            raise AmbiguousValueError(found)
        suffix = "si"  # nothing ambiguous left; expansion choice is moot

    # i, I, j and J are only reserved as the imaginary unit in AC -- see
    # Circuit.reserve_imaginary. Expert-mode equations/conditions are
    # parsed against the same rule as the circuit they're attached to.
    reserve_imaginary = (domain == "ac")

    circuit = Circuit(elements, domain, omega=omega, params=params,
                      suffix=suffix)
    circuit.stamp_all()

    if unknowns:
        for name in unknowns:
            # An unknown may be spelled calculator-style too: listing
            # `re` as an unknown means `r_e` when there is a source e.
            canon = circuit.alias_map.get(_norm_name(str(name)), str(name))
            sym = sp.Symbol(canon)
            if sym not in circuit.unknowns:
                circuit.unknowns.append(sym)
    if equations:
        extra_eqs = [_canonicalize(
                         _parse_extra_equation(e, reserve_imaginary=reserve_imaginary),
                         circuit.alias_map)
                     for e in equations]
        # Quantities recorded in `known` (a `j` source's current, a
        # capacitor's current in AC) are not solved for, so an equation
        # naming one would otherwise introduce a same-named free variable
        # instead of constraining anything. Substituting the stamped
        # expression turns "i_j1 = 5" into a real constraint on whatever
        # that current is made of.
        if circuit.known:
            known_map = {_sym(k): v for k, v in circuit.known.items()}
            extra_eqs = [eq.subs(known_map) for eq in extra_eqs]
        # That substitution can leave an equation with nothing left in it
        # ("i_j1 = 5" on a 5 A source): either a tautology, which sympy
        # cannot accept in a system and which constrains nothing anyway,
        # or a flat contradiction, which is worth saying plainly rather
        # than letting it surface as "could not solve the system". Only
        # equations with no symbols left are checked, so the simplify
        # cost never falls on a real one.
        kept = []
        for raw, eq in zip(equations, extra_eqs):
            if eq.free_symbols:
                kept.append(eq)
                continue
            if sp.simplify(eq) is sp.false:
                raise CircuitError(
                    f"Equation '{raw}' contradicts the circuit as described."
                )
        extra_eqs = kept
        circuit.equations.extend(extra_eqs)
        # Convenience beyond the original: a brand-new symbol appearing
        # in an extra equation (e.g. "pout = v_2*i_r2") becomes an
        # unknown automatically, so simple derived-quantity equations
        # don't require the separate unknowns list. A symbol that names
        # one of the quantities computed after the solve gets its
        # defining equation stamped in as well, so that the equation
        # constrains the circuit rather than a variable of the same name
        # -- see `_derived_definition`. Anything the caller listed under
        # `unknowns` is already in `existing` and is left alone, so an
        # explicit list still wins over this inference.
        reserved = {"s", "t", str(circuit.omega)}
        existing = {str(u) for u in circuit.unknowns}
        for eq in extra_eqs:
            for sym in sorted(eq.free_symbols, key=str):
                if str(sym) in existing or str(sym) in reserved:
                    continue
                definition = _derived_definition(circuit, str(sym), domain)
                if definition is not None:
                    def_eq, def_sym = definition
                    circuit.equations.append(def_eq)
                    circuit.unknowns.append(def_sym)
                else:
                    circuit.unknowns.append(sym)
                existing.add(str(sym))

    # Conditions come in two forms, both spellings of the calculator's
    # `|` ("with") operator. An equality (`r_a = 1000`) is a
    # substitution applied to the whole system before solving, as
    # always. An INEQUALITY (`Vs > 0`) is a restriction on which
    # solutions may be returned -- exactly what `solve(...) | vs>0` did
    # on the TI -- applied as a filter after the solve, which is the
    # only place it can act: a quadratic constraint yields its
    # sign-symmetric solution pairs regardless, and the inequality is
    # what picks among them.
    # A two-port's parameter term (`z,1,2,[100,0,0,50]`, #163) is the
    # calculator's "store the values in the variables first" made part
    # of the description: each entry binds its parameter symbol through
    # the same conditions machinery as the `|` operator. They go FIRST,
    # so an explicit user condition on the same name still wins -- the
    # dict below keeps the last entry per symbol.
    from .elements import two_port_param_conditions
    auto_conds = two_port_param_conditions(elements)
    if auto_conds:
        conditions = auto_conds + list(conditions or [])

    filters: List[Tuple[str, sp.Rel]] = []
    if conditions:
        subs_map = {}
        for raw in conditions:
            text = expand_shorthand(str(raw))
            rel = _parse_inequality(text, reserve_imaginary)
            if rel is not None:
                filters.append((str(raw), _canonicalize(rel, circuit.alias_map)))
                continue
            if "=" not in text:
                raise CircuitError(
                    f"Condition '{raw}' must have the form name = value, "
                    f"or be an inequality such as name > 0.")
            lhs, rhs = text.split("=", 1)
            lhs_expr = _canonicalize(
                safe_sympify(lhs, reserve_imaginary=reserve_imaginary),
                circuit.alias_map)
            subs_map[lhs_expr] = _canonicalize(
                safe_sympify(rhs, reserve_imaginary=reserve_imaginary),
                circuit.alias_map)
        circuit.equations = [eq.subs(subs_map) for eq in circuit.equations]
        circuit.known = {k: safe_sympify(str(v), reserve_imaginary=reserve_imaginary).subs(subs_map)
                         for k, v in circuit.known.items()}
        circuit.unknowns = [u for u in circuit.unknowns if u not in subs_map]

    if not circuit.unknowns:
        results = [{k: sp.simplify(v) for k, v in circuit.known.items()}]
    else:
        unknowns = list(dict.fromkeys(circuit.unknowns))  # de-dup, preserve order
        solutions = sp.solve(circuit.equations, unknowns, dict=True)
        if not solutions:
            hint = ""
            if equations:
                hint = (" If your extra equation constrains a symbolic "
                        "component value, list that symbol under unknowns "
                        "so the solver may vary it.")
            why = _diagnose_unsolvable(circuit)
            if why:
                raise CircuitError(why)
            raise CircuitError(
                "Could not solve the system of equations. If you used exact "
                "numeric values, try again using symbolic values only." + hint
            )
        results = []
        for sol in _rank_solutions(solutions):
            results.append(_expand_solution(circuit, sol))

    if filters:
        results = _filter_solutions(results, filters)
    return results


# The four relational operators an inequality condition may use, longest
# spellings first so `>=` is not read as `>` followed by a stray `=`.
_INEQ_OPS = ((">=", sp.Ge), ("<=", sp.Le), (">", sp.Gt), ("<", sp.Lt))


def _parse_inequality(text: str, reserve_imaginary: bool):
    """`Vs > 0` as a SymPy relational, or None when `text` is not an
    inequality. Parsed by hand rather than through safe_sympify, whose
    syntax gate deliberately refuses comparisons inside *values*."""
    for op, make in _INEQ_OPS:
        if op in text:
            lhs, rhs = text.split(op, 1)
            return make(
                safe_sympify(lhs, reserve_imaginary=reserve_imaginary),
                safe_sympify(rhs, reserve_imaginary=reserve_imaginary))
    return None


def _filter_solutions(results, filters):
    """Keep the solutions that satisfy every inequality condition.

    Each relation is evaluated with the solution's own values
    substituted in. A relation that comes back False drops the
    solution; one that cannot be decided (symbols remain -- a symbolic
    circuit) keeps it, since the restriction has nothing definite to
    say there. Filtering everything out is reported as its own error:
    it means the circuit's mathematics and the caller's restriction
    genuinely disagree, which is an answer, not a failure to solve."""
    kept = []
    for r in results:
        smap = {_sym(k): v for k, v in r.items()}
        ok = True
        for raw, rel in filters:
            try:
                verdict = sp.simplify(rel.subs(smap))
            except Exception:                                 # noqa: BLE001
                continue          # undecidable: the filter stays silent
            if verdict is sp.false or verdict is False:
                ok = False
                break
        if ok:
            kept.append(r)
    if not kept:
        names = ", ".join(f"'{raw}'" for raw, _ in filters)
        raise CircuitError(
            f"No solution satisfies the condition(s) {names}. The system "
            f"solves, but every solution violates the restriction.")
    return kept


def _expand_solution(circuit: "Circuit", sol: Dict[sp.Symbol, sp.Expr]) -> Dict[str, sp.Expr]:
    """Turn one raw SymPy solution into the full {name: value} dict."""
    result = {str(k): sp.simplify(v) for k, v in sol.items()}
    # Second-level quantities in `circuit.known` (a capacitor's
    # current and a current source's -- the only two kinds that go in
    # there; an op-amp's output current is *solved*, not substituted,
    # and this comment used to say otherwise) are stamped
    # in terms of the node-voltage symbols `Circuit.v()` hands out
    # *before* the system is solved -- so without this substitution
    # they'd carry raw unknowns like v_3 straight into the answer,
    # even though v_3 itself was solved to a number two lines above.
    # On the original calculator these were evaluated on the fly as
    # soon as they were asked for; here they only get one evaluation
    # pass (this one), so it has to be the pass that plugs in the
    # final solved values, not the raw stamp-time expression.
    result.update({k: sp.simplify(v.subs(sol)) for k, v in circuit.known.items()})

    # Make sure every element and every node shows up in the result, even
    # elements whose current was solved as part of the system already.
    for name, val in circuit.known.items():
        result.setdefault(name, sp.simplify(val))

    return result
