"""
Circuit-description parser and validator.

Ports the parsing/validation half of Symbulator: `symbv8s1` (element name
sanity check), `symbv8s2` (per-element field-count check), and `symbv8s3`
(topology validation: grounding, duplicate names, node conflicts).

Circuit description syntax (unchanged from the calculator, minus the
leading colon it required):

    "r1,1,0,1k:e1,1,0,5:c1,1,2,10'u"

Elements are separated by `:` and fields within an element by `,`. The
first character of an element's name selects its type:

    r  resistor            name,n1,n2,value
    l  inductor             name,n1,n2,value[,initial_current]
    c  capacitor             name,n1,n2,value[,initial_voltage]
    e  voltage source (indep. or dependent)   name,n1,n2,value
    j  current source (indep. or dependent)   name,n1,n2,value
    o  ideal op-amp (nullor)     name,n_plus,n_minus,n_out
    m  mutual inductance          name,Lname1,Lname2,M
    s  short circuit               name,n1,n2
    t  ideal transformer            name,n1,n2,turns1,turns2
                                 or name,n1,n2,n1b,n2b,turns1,turns2
    z,y,h,g,a,b  two-port block     name,n1,n2[,[p11,p12,p21,p22]]
                                 or name,n1,n2,n1b,n2b[,[p11,p12,p21,p22]]

Node "0" is the ground/reference node.

A transformer and a two-port block have two ports, and each port has
two terminals. The calculator's form names only the *top* terminal of
each port -- n1 on the left, n2 on the right -- and grounds the other
two. Version X (X2, 6 Sep 2026) also takes all four: top-left,
top-right, bottom-left, bottom-right, in that order, so that a port may
sit between two live nodes. The two-node form is the four-node form
with both bottoms on 0, and the engine treats it exactly so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .si_prefix import expand_shorthand

VALID_PREFIXES = "abceghjlmorstyz"

# name,n1,n2[,...]  -- total field count including the element's own name.
FIELD_COUNTS = {
    "r": 4,
    "l": 4,
    "c": 4,
    "e": 4,
    "j": 4,
    "o": 4,
    "m": 4,
    "s": 3,
    "t": 5,
    "z": 3,
    "y": 3,
    "h": 3,
    "g": 3,
    "a": 3,
    "b": 3,
}

# l/c may optionally carry one extra field -- an initial condition
# (initial inductor current / initial capacitor voltage) -- used only by
# s-domain (fd) and transient (tr) analysis; ignored in dc/ac. Unlike the
# original, which switched the *expected* field count based on which
# analysis tool was running, this port always accepts either count for
# l/c and simply treats a missing initial condition as 0.
OPTIONAL_IC_KINDS = {"l", "c"}

TWO_PORT_KINDS = set("zyghab")
# The two-port elements: a transformer and the six parameter blocks.
# In their two-node form these ground their own lower terminals, so the
# circuit is grounded by their presence and neither named node may be
# "0". In the four-node form (X2) they ground nothing and name their
# bottoms themselves; `Element.four_node` says which form an element
# took, and the checks below ask it rather than the kind alone.
PORT_KINDS = set("tzyghab")
GROUNDED_ELEMENT_KINDS = PORT_KINDS   # kept for readers of the old name

from . import messages as M

class CircuitError(ValueError):
    """Raised for any issue found while parsing/validating a circuit.

    Since #199 it carries a **code and its arguments** as well as its
    English:

        raise CircuitError(E_TWOPORT_LIST_LEN, name=el.name, n=len(items))

    `exc.code` is the number, `exc.args_map` the arguments by name, and
    `str(exc)` the English rendered from `messages.CATALOGUE`. The
    interface reads the code and puts it into whichever of thirteen
    languages is on; everything else -- a traceback, a bug report, the
    `.txt` export, verify_lesson.py's output -- reads the English, which
    is why the package keeps it rather than shipping bare numbers.

    **A plain string still works**, and is what `str(exc)` gives you
    back: `CircuitError("some sentence")` sets `code` to None. That is
    not a transition shim to be removed later. It is how an exception
    re-raised from elsewhere, or one this package has not got round to
    coding, keeps flowing through unchanged -- and it is what let #199
    land without the app and the package having to deploy in step.
    """

    def __init__(self, code_or_message, **args):
        if isinstance(code_or_message, int):
            from .messages import render, severity
            self.code = code_or_message
            self.args_map = args
            self.severity = severity(code_or_message)
            super().__init__(render(code_or_message, args))
        else:
            self.code = None
            self.args_map = {}
            self.severity = "error"
            super().__init__(code_or_message)


@dataclass
class Element:
    """One parsed circuit element -- a resistor, source, op-amp, etc.
    `fields` holds every field after the name, still as raw strings
    (nodes, values, or references to other elements depending on `kind`);
    `n1`/`n2`/`value`/`ic` below are convenience accessors into it, since
    "which field means what" differs by element kind (see FIELD_COUNTS
    and the syntax table in the module docstring)."""
    name: str            # full element name, e.g. "r1"
    kind: str             # first letter of name, e.g. "r"
    fields: List[str] = field(default_factory=list)  # fields after the name
    #: The same fields as the reader typed them, before `[a,b]` became
    #: `pr(a,b)` and `1'k` became `1*10**3`. Kept only so that a value the
    #: parser cannot read can be quoted back the way it was written; empty
    #: when nothing was rewritten. See engine's `_value`.
    raw_fields: List[str] = field(default_factory=list)

    @property
    def n1(self) -> str:
        """First node/terminal (fields[0]) -- every element kind has one
        in the same position, so this accessor is always safe to use."""
        return self.fields[0]

    @property
    def n2(self) -> str:
        """Second node/terminal (fields[1]) -- same as n1, always in the
        same position regardless of element kind."""
        return self.fields[1]

    @property
    def value(self) -> Optional[str]:
        """Raw value expression, for element kinds that have one."""
        if self.kind in ("r", "l", "c", "e", "j", "m"):
            return self.fields[2]
        return None

    @property
    def ic(self) -> str:
        """Initial condition (initial inductor current / capacitor
        voltage), for l/c elements only. "0" if not given."""
        if self.kind in OPTIONAL_IC_KINDS and len(self.fields) >= 4:
            return self.fields[3]
        return "0"

    # -- the two-port elements: transformer and parameter blocks (X2) --

    @property
    def param_idx(self) -> Optional[int]:
        """Index of a two-port block's parameter term, or None when it
        carries none. The term is always the *last* field, and it is
        recognised by its `pr(` encoding rather than by position, since
        the block may name two nodes or four in front of it."""
        if self.kind not in TWO_PORT_KINDS or not self.fields:
            return None
        last = self.fields[-1].strip()
        # `pr(` is the parser's encoding of the typed brackets; the
        # brackets themselves appear when the app materialises the
        # tacit term into an element it has already parsed.
        if (len(self.fields) in (3, 5)
                and (last.startswith("pr(") or last.startswith("["))):
            return len(self.fields) - 1
        return None

    @property
    def four_node(self) -> bool:
        """True when a transformer or two-port block names all four of
        its terminals (X2); False for the calculator's two-node form,
        whose lower terminals are ground."""
        if self.kind == "t":
            return len(self.fields) == 6
        if self.kind in TWO_PORT_KINDS:
            n = len(self.fields) - (1 if self.param_idx is not None else 0)
            return n == 4
        return False

    @property
    def port_nodes(self):
        """((top_left, bottom_left), (top_right, bottom_right)) for a
        transformer or two-port block -- the two terminals of each port,
        with "0" standing in for the bottoms of the two-node form. None
        for every other kind."""
        if self.kind not in PORT_KINDS:
            return None
        if self.four_node:
            return ((self.fields[0], self.fields[2]),
                    (self.fields[1], self.fields[3]))
        return ((self.fields[0], "0"), (self.fields[1], "0"))

    @property
    def turns(self):
        """(turns1, turns2) as typed, for a transformer: the last two
        fields in either form. None for every other kind."""
        if self.kind != "t":
            return None
        return (self.fields[-2], self.fields[-1])

    @property
    def node_idx(self):
        """Field indices (after the name) that hold node names, for this
        element as written -- two or four for the two-port kinds, and
        the kind's fixed set otherwise."""
        if self.kind in PORT_KINDS and self.four_node:
            return (0, 1, 2, 3)
        return _IDENTIFIER_FIELD_IDX.get(self.kind, ())


def identifier_field_idx(el: "Element"):
    """The structural (non-value) field indices of `el`: nodes, or the
    inductors a mutual inductance names. `_IDENTIFIER_FIELD_IDX` is the
    same map by *kind*; this is the map by *element*, which differs for
    a transformer or two-port written with four nodes (X2). Readers that
    decide which fields are values should ask this, not the table."""
    return el.node_idx


# Field indices (0-based, *after* the element name) that hold structural
# identifiers -- node names, or references to other elements. These fold
# to lowercase along with the element's own name, so `R1` and `r1` are
# one resistor and node `A` and node `a` are one node. Value fields are
# deliberately absent: case matters there ('M vs 'm, Heaviside vs
# heaviside).
_IDENTIFIER_FIELD_IDX = {
    "r": (0, 1), "l": (0, 1), "c": (0, 1), "e": (0, 1), "j": (0, 1),
    "s": (0, 1), "t": (0, 1),
    "o": (0, 1, 2),          # n+, n-, output node
    "m": (0, 1),             # the two inductors it couples
    "z": (0, 1), "y": (0, 1), "h": (0, 1), "g": (0, 1),
    "a": (0, 1), "b": (0, 1),
}


def _split_elements(desc: str) -> List[str]:
    """Break a raw circuit-description string into one raw substring per
    element, tolerating either separator style (see comment below) and
    stray leading/trailing separators or blank lines. Raises CircuitError
    if nothing is left after splitting (an empty or whitespace-only
    description)."""
    # Newlines work the same as ":" -- a circuit can be written one
    # element per line (natural in a file or a web textarea) or all on
    # one line separated by colons (the original calculator syntax).
    desc = desc.replace("\r\n", ":").replace("\r", ":").replace("\n", ":")
    desc = desc.strip()
    if desc.startswith(":"):
        desc = desc[1:]
    parts = [p.strip() for p in desc.split(":") if p.strip() != ""]
    if not parts:
        raise CircuitError(M.E_EMPTY_DESCRIPTION)
    return parts


def _split_fields(raw: str) -> List[str]:
    """Split one element's raw text on `,` into fields, the way
    `str.split(",")` does -- except a comma inside parentheses or
    square brackets doesn't count as a separator. Parentheses because
    the `[...]` parallel-impedance shortcut (see
    si_prefix.expand_shorthand) has already been expanded to
    `pr(a,b,c)` by the time the *solve* path runs; brackets because the
    as-typed copy of the line (raw_fields, the #59 machinery) is split
    too, and its `[100,10,20,50]` two-port parameter term (#163) must
    stay one field there as well, or the typed and rewritten splits
    stop lining up and the typed spelling is lost."""
    fields: List[str] = []
    depth = 0
    current = ""
    for ch in raw:
        if ch in "([":
            depth += 1
            current += ch
        elif ch in ")]":
            depth = max(0, depth - 1)
            current += ch
        elif ch == "," and depth == 0:
            fields.append(current.strip())
            current = ""
        else:
            current += ch
    fields.append(current.strip())
    return fields


def parse_circuit(desc: str, expand_si: bool = True) -> List[Element]:
    """Parse a Symbulator-style circuit description string into a list
    of Element objects. Raises CircuitError on malformed input (mirrors
    symbv8s1 + symbv8s2).

    `expand_si=False` parses the same elements but leaves SI-prefix
    shorthand (`4.7'M`) in each field as typed, instead of expanding it
    to a literal number. Use this when the parsed elements are only
    going to be echoed back to the user (e.g. to rebuild the circuit
    description after normalizing i/I to j or resolving an ambiguous
    bare suffix) -- the SI notation is worth more to a person reading it
    back than the number it stands for, and it still gets expanded the
    normal way (`expand_si=True`, the default) at actual solve time."""
    raw_elements = _split_elements(desc)

    elements: List[Element] = []
    seen_names = set()

    for raw in raw_elements:
        typed = raw
        raw = expand_shorthand(raw, si=expand_si)
        parts = _split_fields(raw)
        typed_parts = _split_fields(typed) if typed != raw else parts
        if not parts or parts[0] == "":
            raise CircuitError(M.E_MALFORMED_ELEMENT, raw=raw)

        # Element names, element letters and node names are all
        # case-insensitive: they fold to lowercase here so that R1 and
        # r1 are the same resistor, and node A and node a are the same
        # node. Folding before the duplicate check means writing both
        # spellings is correctly reported as a duplicate rather than
        # silently creating two elements.
        name = parts[0].lower()
        kind = name[0] if name else ""

        if kind not in VALID_PREFIXES:
            raise CircuitError(M.E_UNKNOWN_KIND, kind=kind, name=parts[0])

        # A name must survive being embedded in a symbol: the answers are
        # written as i_<name>, v_<name>, p_<name>, and a reader must be
        # able to type those inside a value or an added equation. A name
        # like `r-x` parses fine on its own, but `2*i_r-x` silently reads
        # as `2*i_r - x` and solves to an answer full of phantom symbols
        # -- so the characters that would do that are refused here, where
        # the message can still point at the right element.
        if not name.isidentifier():
            raise CircuitError(M.E_BAD_NAME_CHAR, name=parts[0])

        if name in seen_names:
            raise CircuitError(M.E_DUPLICATE_NAME, name=name)
        seen_names.add(name)

        # `[...]` means exactly two things (#165, restoring the
        # calculator's scope): the parallel-resistor shorthand, in a
        # RESISTOR's value, and a two-port's parameter term. Anywhere
        # else it used to be silently passed to pr() -- turning
        # `e1,1,0,[4,4]` into a meaningless "2 V" source -- so it now
        # stops with a message instead. The typed text is what is
        # checked: by the time `parts` exists the brackets have been
        # rewritten to pr(...), and a pr(...) the user *typed* is a
        # legitimate function call, allowed anywhere.
        if typed != raw and ("[" in typed or "]" in typed):
            # A two-port's parameter term is its *last* field, wherever
            # that falls: after two nodes or after four (X2).
            if kind == "r":
                value_term = 3
            elif kind in TWO_PORT_KINDS:
                value_term = len(typed_parts) - 1
            else:
                value_term = None
            for i, tp in enumerate(typed_parts):
                if ("[" in tp or "]" in tp) and i != value_term:
                    raise CircuitError(M.E_BRACKETS_MISUSED,
                                       value=typed.strip())

        expected = FIELD_COUNTS[kind]
        if kind in OPTIONAL_IC_KINDS:
            allowed = {expected, expected + 1}
        elif kind in TWO_PORT_KINDS:
            # name + 2 nodes, or name + 4 nodes (X2), each with or
            # without the parameter term as the last field.
            allowed = {3, 4, 5, 6}
        elif kind == "t":
            # name + 2 nodes + 2 turns, or name + 4 nodes + 2 turns (X2).
            allowed = {5, 7}
        else:
            allowed = {expected}
        if kind in TWO_PORT_KINDS and len(parts) in allowed:
            # A term where a node should be: `z,1,[..]` or
            # `z,1,2,3,[..]` has the right count and the wrong shape.
            has_term = parts[-1].strip().startswith("pr(")
            if has_term and len(parts) in (3, 5):
                allowed = set()
        if len(parts) not in allowed:
            if kind in OPTIONAL_IC_KINDS:
                raise CircuitError(M.E_TERMS_WITH_IC, name=name,
                                   got=len(parts), expected=expected,
                                   expected_ic=expected + 1, kind=kind)
            if kind in TWO_PORT_KINDS:
                raise CircuitError(M.E_TERMS_TWO_PORT, name=name,
                                   got=len(parts), expected="3 or 5",
                                   expected_params="4 or 6")
            if kind == "t":
                raise CircuitError(M.E_TERMS_EXACT, name=name,
                                   got=len(parts), expected="5 or 7",
                                   kind=kind)
            raise CircuitError(M.E_TERMS_EXACT, name=name, got=len(parts),
                               expected=expected, kind=kind)

        fields = list(parts[1:])
        # Only when the rewrite actually changed something, and only when
        # it split the same way -- a mismatch means the two cannot be lined
        # up field by field, and a wrong original is worse than none.
        raw_fields = (list(typed_parts[1:])
                      if typed != raw and len(typed_parts) == len(parts)
                      else [])
        element = Element(name=name, kind=kind, fields=fields,
                          raw_fields=raw_fields)
        # Which fields are nodes depends on the element as written (a
        # four-node transformer has four), so fold case through the
        # element rather than the kind table.
        for idx in element.node_idx:
            if idx < len(fields):
                fields[idx] = fields[idx].lower()
        if kind in TWO_PORT_KINDS and len(fields) in (3, 5):
            two_port_param_texts(element)   # validates; raises if malformed
        elements.append(element)

    _validate_topology(elements)
    return elements


def two_port_param_texts(el: Element) -> Optional[List[str]]:
    """The four parameter expressions from a two-port element's optional
    last term, or None when the element carries only its two nodes.

    The term is written `[p11,p12,p21,p22]`; by the time fields exist,
    `expand_shorthand` has rewritten the brackets to `pr(...)` (its
    universal internal encoding of `[...]`), which is also accepted
    typed directly. Raises CircuitError when the term is not a
    four-entry list."""
    if el.kind not in TWO_PORT_KINDS or len(el.fields) not in (3, 5):
        return None
    # The term is the last field, after two nodes or after four (X2).
    k = len(el.fields) - 1
    text = el.fields[k].strip()
    shown = (el.raw_fields[k].strip()
             if len(el.raw_fields) > k else text)
    if not (text.startswith("pr(") and text.endswith(")")):
        raise CircuitError(M.E_TWOPORT_LAST_TERM, name=el.name, shown=shown)
    inner = text[3:-1]
    parts: List[str] = []
    depth, current = 0, ""
    for ch in inner:
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    parts.append(current.strip())
    if len(parts) != 4 or any(p == "" for p in parts):
        raise CircuitError(M.E_TWOPORT_LIST_LEN, name=el.name,
                           n=len([p for p in parts if p]))
    return parts


def two_port_param_conditions(elements: List[Element]) -> List[str]:
    """The `name = value` bindings implied by every two-port parameter
    term in `elements`, ready to run through the solver's conditions
    machinery -- which is what "the values are stored in the parameter
    variables" means operationally: the same substitution the TI's `|`
    operator applied to stored variables.

    A self-referential entry (`z,1,2,[z11,z12,z21,z22]` -- the tacit
    default written out) binds nothing: the parameter is already the
    free symbol it names."""
    conds: List[str] = []
    for el in elements:
        texts = two_port_param_texts(el)
        if not texts:
            continue
        for ij, text in zip(("11", "12", "21", "22"), texts):
            name = f"{el.name}{ij}"
            if text.replace("_", "").lower() == name.replace("_", "").lower():
                continue
            conds.append(f"{name} = {text}")
    return conds


def _validate_topology(elements: List[Element], two_port_nodes: Optional[tuple] = None) -> None:
    """Whole-circuit sanity checks that can't be done element-by-element
    (ports `symbv8s3`): the circuit must be grounded (some node is 0, or
    a grounded-kind element like a two-port block is present), and no
    element may have both terminals on the same node -- a rule that
    binds even the short circuit, whose job is joining two *distinct*
    nodes: a self-loop's current enters and leaves the same KCL sum
    and so is indeterminate.

    `two_port_nodes`, when given, additionally checks that the two named
    port nodes (n1, n2) both actually appear somewhere in the circuit --
    used by tools like `equiv.port()` that ask the caller for two nodes
    by name and need to catch a typo before wasting a solve on it."""
    has_ground = False
    node1_seen = node2_seen = False
    n1_target, n2_target = (two_port_nodes or (None, None))

    for el in elements:
        if el.kind in PORT_KINDS and el.four_node:
            # Four named terminals (X2): the element grounds nothing, so
            # any of them may be 0 -- `z,1,2,0,0` is the two-node form
            # written out -- but a port whose two terminals are the same
            # node is shorted, the same fault as a self-looped resistor.
            for top, bottom in el.port_nodes:
                if top == bottom:
                    raise CircuitError(M.E_PORT_SAME_NODE, name=el.name)
                if top == "0" or bottom == "0":
                    has_ground = True
        else:
            if el.kind in PORT_KINDS:
                if el.n1 == "0" or el.n2 == "0":
                    raise CircuitError(M.E_TOP_NODE_GROUND, name=el.name)

            if el.n1 == el.n2 and el.kind != "m":
                raise CircuitError(M.E_SAME_NODE, name=el.name)

            if el.kind in PORT_KINDS or el.n1 == "0" or el.n2 == "0":
                has_ground = True

        if n1_target is not None:
            nodes = [el.fields[i] for i in el.node_idx if i < len(el.fields)]
            if n1_target in nodes:
                node1_seen = True
            if n2_target in nodes:
                node2_seen = True

    if two_port_nodes is None:
        if not has_ground:
            raise CircuitError(M.E_NEED_REFERENCE_NODE)
        _check_connected(elements)
    else:
        if n1_target == n2_target:
            raise CircuitError(M.E_INPUT_SAME_NODE)
        if not node1_seen:
            raise CircuitError(M.E_NO_SUCH_NODE, node=n1_target)
        if not node2_seen:
            raise CircuitError(M.E_NO_SUCH_NODE, node=n2_target)


def _check_connected(elements: List[Element]) -> None:
    """Every node must have a conduction path to the reference. A part of
    the circuit with no such path (say `r1,2,3,1` hanging on its own) has
    no defined voltages, and the solver would otherwise return it quietly
    parametrized in one of its own node voltages (`v_2 = v_3`) rather
    than flag the mistake (ports `symbv8s3`'s "floating node" check).

    Connectivity is by terminals: r/l/c/e/j/s/t join their two nodes, an
    op-amp joins all three of its terminals (its nullor constraints tie
    them together), and a grounded two-port block ties both nodes to 0.
    Mutual inductances name inductors, not nodes, so they add nothing."""
    parent = {"0": "0"}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for el in elements:
        if el.kind == "m":
            continue
        if el.kind in PORT_KINDS:
            # Each port joins its own two terminals, and the two ports
            # do not join each other: an ideal transformer, or a
            # parameter block, conducts nothing from one side to the
            # other, so a secondary side with no path of its own to 0
            # has no defined voltages and is reported floating (X2,
            # Roberto's rule 3: the whole side of the circuit, not just
            # the element). The two-node form grounds both ports, which
            # is what it always did.
            for top, bottom in el.port_nodes:
                union(top, bottom)
            continue
        nodes = [el.fields[i] for i in el.node_idx]
        for n in nodes[1:]:
            union(nodes[0], n)

    root = find("0")
    floating = sorted(n for n in parent if find(n) != root)
    if floating:
        raise CircuitError(M.E_FLOATING_NODES, nodes=", ".join(floating))


# Which field indices (0-based, after the element name) hold *values*
# (as opposed to node names / element references), per element kind.
# Used by find_ambiguous_values -- node names are never treated as
# ambiguous, so "r1,1,2k,100" with a node literally named "2k" is safe.
_VALUE_FIELD_IDX = {
    "r": (2,), "l": (2, 3), "c": (2, 3), "e": (2,), "j": (2,),
    "m": (2,), "t": (2, 3),
}


def ambiguous_in_elements(elements: List[Element]) -> List[dict]:
    """Scan parsed elements for bare engineering-notation values -- see
    find_ambiguous_values."""
    from .si_prefix import bare_suffix_match

    found: List[dict] = []
    for e in elements:
        # A four-node transformer's turns sit after its four nodes (X2).
        value_idx = ((4, 5) if e.kind == "t" and e.four_node
                     else _VALUE_FIELD_IDX.get(e.kind, ()))
        for idx in value_idx:
            if idx >= len(e.fields):
                continue
            m = bare_suffix_match(e.fields[idx])
            if m:
                found.append({"element": e.name, "token": e.fields[idx].strip(),
                              "number": m[0], "letter": m[1]})
    return found


def find_ambiguous_values(desc: str) -> List[dict]:
    """Scan a circuit description for bare engineering-notation values
    ("1k", "4.7u") whose meaning is ambiguous between an SI unit (1'k)
    and number*variable (1*k). Returns one dict per occurrence:
    {"element", "token", "number", "letter"}. Parse errors propagate
    as CircuitError, same as parse_circuit."""
    return ambiguous_in_elements(parse_circuit(desc))
