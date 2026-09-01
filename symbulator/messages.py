"""
The one place the package's words live.

Roberto's ruling, 31 Aug 2026:

    Let's standardise the error format. Let's modify the package this one
    time, so that all messages, warnings, errors, etc, are returned in a
    structured manner, with a message code and arguments. When I think
    about the package, I do not worry about readability by humans. I do
    not expect any human to use the package directly. The package is
    meant to be under the hood. So, create a running list of all the
    messages shared by the package, give each a number and a format for
    it to pass the arguments (variables, numbers) needed to communicate
    this message to the human, and let the interface do the work of
    putting the message into words.

This is #199, the third and last of the three items that carry it out --
after #198 did `eqsheet.py` (9xx) and #200 did `symbulator_ui.py` (8xx),
both of which proved the shape on surfaces where a mistake costs a
redeploy rather than a version number that can never be reused.

**Three rules, and they outlive any particular message.**

* **A code is permanent once published.** Never reused, never
  renumbered; a retired code stays retired. The same rule as the item
  numbers in NEXT.md, for the same reason: someone quoting "E214" in a
  bug report should mean one thing forever. Gaps in the numbering are
  normal and are not to be tidied.
* **Severity is a field, not a range.** A warning and an error about the
  same thing want one code, not two.
* **The English stays here.** It is the generation source for the app's
  `i18n/en.json`, it is what a traceback or a bug report can quote, and
  a second hand-kept copy in a JSON file is precisely the drift this
  scheme exists to prevent.

**Ranges follow the modules**, because that is how the package already
divides:

    1xx  reserved for parsing (si_prefix's own exceptions; see below)
    2xx  elements.py     -- descriptions, names, nodes, two-port terms
    3xx  engine.py       -- stamping, solving, conditions
    4xx  laplace.py      -- t2s / s2t and the bracket shorthand
    5xx  equiv.py        -- Thevenin, Norton, equivalents
    6xx  spice.py        -- the netlist translator

**What is deliberately not here yet.** `spice.py`'s *warnings* -- the
seventeen `warnings.append` sites -- are **#211**. They looked like
seventeen messages and are not: seven are `f"{el.name}: {why}"` where
`why` comes from elsewhere, `skip()` alone has seven distinct reasons,
and the `described` map names eleven element kinds. Coding them properly
is thirty-odd more codes, and the SPICE translator is still labelled
beta in the app, which makes its wording the most likely in the package
to change. A code is permanent; beta prose is not. So they wait.

`si_prefix.py`'s `AmbiguousValueError` and `UnsafeExpressionError` are
their own classes with their own contract and are not CircuitError; 1xx
is held for them.

**Slots are `%{name}`**, matching `symbulator_ui.py` and `eqsheet.py`,
so that the app has one renderer for all three catalogues rather than
three.
"""

# --- 2xx: elements.py -------------------------------------------------
E_EMPTY_DESCRIPTION   = 201
E_MALFORMED_ELEMENT   = 202
E_UNKNOWN_KIND        = 203
E_BAD_NAME_CHAR       = 204
E_DUPLICATE_NAME      = 205
E_BRACKETS_MISUSED    = 206
E_TERMS_WITH_IC       = 207
E_TERMS_TWO_PORT      = 208
E_TERMS_EXACT         = 209
E_TWOPORT_LAST_TERM   = 210
E_TWOPORT_LIST_LEN    = 211
E_TOP_NODE_GROUND     = 212
E_SAME_NODE           = 213
E_NEED_REFERENCE_NODE = 214
E_INPUT_SAME_NODE     = 215
E_NO_SUCH_NODE        = 216
E_FLOATING_NODES      = 217

# --- 3xx: engine.py ---------------------------------------------------
E_NO_STAMPING_RULE    = 301
E_UNKNOWN_TWOPORT     = 302
E_EQUATION_CONTRADICTS = 303
E_CONDITION_FORM      = 304
E_UNSOLVABLE          = 305
E_UNSOLVABLE_HINT     = 306
E_NO_SOLUTION_FILTER  = 307
E_VOLTAGE_LOOP        = 308
E_VOLTAGE_LOOP_DC     = 309
E_CURRENT_NODE        = 310
E_CURRENT_NODE_DC     = 311

# --- 4xx: laplace.py --------------------------------------------------
# Two messages times two origins. The origin used to be English prose
# passed in as an argument ("between brackets" / "as an argument to
# t2s()"), which would have left one clause untranslated inside a
# translated sentence. As codes, the bracket form and the call form are
# separate sentences and `%{fn}` is a function name, which is the same
# in every language.
E_ALREADY_IN_DOMAIN_BRACKETS = 401
E_ALREADY_IN_DOMAIN_CALL     = 402
E_NOT_VALID_DOMAIN_BRACKETS  = 403
E_NOT_VALID_DOMAIN_CALL      = 404

# --- 5xx: equiv.py ----------------------------------------------------
E_NOT_ACTIVE          = 501
E_NO_SHORT_CIRCUIT    = 502

# --- 6xx: spice.py ----------------------------------------------------
E_SPICE_EMPTY         = 601
E_SPICE_NOTHING       = 602


CATALOGUE = {
    # --- 2xx elements -------------------------------------------------
    E_EMPTY_DESCRIPTION: ("error", "Circuit description is empty."),
    E_MALFORMED_ELEMENT: ("error",
                          "Malformed element description: '%{raw}'."),
    E_UNKNOWN_KIND: ("error",
                     "Element starting with '%{kind}' not recognised. "
                     "Give element '%{name}' a proper name."),
    E_BAD_NAME_CHAR: ("error",
                      "Element name '%{name}' contains a character that "
                      "cannot be part of a name. Use letters, digits and "
                      "underscores, so that the element's answers (its "
                      "'i_...', 'v_...', 'p_...') can be written inside a "
                      "value or an added equation."),
    E_DUPLICATE_NAME: ("error",
                       "More than one element has been named '%{name}'."),
    E_BRACKETS_MISUSED: ("error",
                         "'%{value}' uses [...] where it has no meaning. "
                         "Square brackets are the parallel-resistor "
                         "shorthand (in an r element's value) or a "
                         "two-port's parameter term ([p11,p12,p21,p22]); "
                         "for anything else, call pr(...) explicitly."),
    E_TERMS_WITH_IC: ("error",
                      "Your description of element '%{name}' has %{got} "
                      "terms. %{expected} or %{expected_ic} (with an "
                      "initial condition) terms are expected for an "
                      "element of type '%{kind}'."),
    E_TERMS_TWO_PORT: ("error",
                       "Your description of element '%{name}' has %{got} "
                       "terms. %{expected} terms are expected for a "
                       "two-port element, or %{expected_params} with its "
                       "parameters as the last term: [p11,p12,p21,p22]."),
    E_TERMS_EXACT: ("error",
                    "Your description of element '%{name}' has %{got} "
                    "terms. Exactly %{expected} terms are expected for an "
                    "element of type '%{kind}'."),
    E_TWOPORT_LAST_TERM: ("error",
                          "The last term of two-port '%{name}' is "
                          "'%{shown}'. A two-port's parameters are written "
                          "as a four-entry list: [p11,p12,p21,p22]."),
    E_TWOPORT_LIST_LEN: ("error",
                         "The parameter list of two-port '%{name}' has "
                         "%{n} entries. Exactly four are expected: "
                         "[p11,p12,p21,p22]."),
    E_TOP_NODE_GROUND: ("error",
                        "Neither top node in element '%{name}' can be "
                        "ground."),
    E_SAME_NODE: ("error",
                  "Both nodes of '%{name}' can't be the same node."),
    E_NEED_REFERENCE_NODE: ("error",
                            "Circuit must contain a reference node 0."),
    E_INPUT_SAME_NODE: ("error",
                        "Both nodes in the input cannot be the same node."),
    E_NO_SUCH_NODE: ("error",
                     "Circuit does not contain the node %{node} you "
                     "mentioned."),
    E_FLOATING_NODES: ("error",
                       "Node(s) %{nodes} have no path to the reference "
                       "node 0; that part of the circuit is floating and "
                       "its voltages are undefined."),

    # --- 3xx engine ---------------------------------------------------
    E_NO_STAMPING_RULE: ("error",
                         "No stamping rule implemented for element kind "
                         "'%{kind}'."),
    E_UNKNOWN_TWOPORT: ("error", "Unknown two-port kind '%{kind}'."),
    E_EQUATION_CONTRADICTS: ("error",
                             "Equation '%{equation}' contradicts the "
                             "circuit as described."),
    E_CONDITION_FORM: ("error",
                       "Condition '%{condition}' must have the form "
                       "name = value, or be an inequality such as "
                       "name > 0."),
    E_UNSOLVABLE: ("error",
                   "Could not solve the system of equations. If you used "
                   "exact numeric values, try again using symbolic values "
                   "only."),
    # The same sentence with the extra-equation hint. Two codes rather
    # than one with an optional slot: a slot that is sometimes empty is a
    # sentence that reads oddly in half its uses, and a translator cannot
    # see when it is filled.
    E_UNSOLVABLE_HINT: ("error",
                        "Could not solve the system of equations. If you "
                        "used exact numeric values, try again using "
                        "symbolic values only. If your extra equation "
                        "constrains a symbolic component value, list that "
                        "symbol under unknowns so the solver may vary it."),
    E_NO_SOLUTION_FILTER: ("error",
                           "No solution satisfies the condition(s) "
                           "%{names}. The system solves, but every "
                           "solution violates the restriction."),
    # The two diagnoses, each with and without its dc clause. Same
    # reasoning as the hint above: the clause is prose, so it is part of
    # the sentence a translator is given, not a fragment glued on after.
    E_VOLTAGE_LOOP: ("error",
                     "Elements %{members} form a loop that fixes the same "
                     "voltage more than once (voltage sources, shorts, "
                     "zero-ohm resistors). Their values contradict each "
                     "other, so no solution exists."),
    E_VOLTAGE_LOOP_DC: ("error",
                        "Elements %{members} form a loop that fixes the "
                        "same voltage more than once (voltage sources, "
                        "shorts, zero-ohm resistors and inductors, which "
                        "are shorts in dc). Their values contradict each "
                        "other, so no solution exists."),
    E_CURRENT_NODE: ("error",
                     "Node %{node} connects only to %{members}, which fix "
                     "the current into it (current sources). Those "
                     "currents cannot sum to zero, so no solution exists."),
    E_CURRENT_NODE_DC: ("error",
                        "Node %{node} connects only to %{members}, which "
                        "fix the current into it (current sources and "
                        "capacitors, which are open in dc). Those "
                        "currents cannot sum to zero, so no solution "
                        "exists."),

    # --- 4xx laplace --------------------------------------------------
    E_ALREADY_IN_DOMAIN_BRACKETS: ("error",
                                   "The expression provided between "
                                   "brackets is already in the "
                                   "%{into}-domain, so there is nothing to "
                                   "transform. Brackets convert from "
                                   "%{frm} to %{into}."),
    E_ALREADY_IN_DOMAIN_CALL: ("error",
                               "The expression provided as an argument to "
                               "%{fn}() is already in the %{into}-domain, "
                               "so there is nothing to transform. This "
                               "converts from %{frm} to %{into}."),
    E_NOT_VALID_DOMAIN_BRACKETS: ("error",
                                  "The expression provided between "
                                  "brackets does not evaluate to a valid "
                                  "%{into}-domain expression."),
    E_NOT_VALID_DOMAIN_CALL: ("error",
                              "The expression provided as an argument to "
                              "%{fn}() does not evaluate to a valid "
                              "%{into}-domain expression."),

    # --- 5xx equivalents ----------------------------------------------
    E_NOT_ACTIVE: ("error",
                   "This circuit is not active (open-circuit voltage is "
                   "0). Try er() instead."),
    E_NO_SHORT_CIRCUIT: ("error",
                         "The open-circuit voltage is %{vth}, but the "
                         "short-circuit current could not be found, either "
                         "directly or as the limit of a vanishing "
                         "resistance: %{reason}"),

    # --- 6xx SPICE ----------------------------------------------------
    E_SPICE_EMPTY: ("error", "The SPICE netlist is empty."),
    E_SPICE_NOTHING: ("error",
                      "No translatable elements found in the SPICE "
                      "netlist. %{warnings}"),
}


def render(code: int, args: dict) -> str:
    """The catalogue's English for one message, slots filled in.

    Used by `str(CircuitError)`, so that a package nobody is expected to
    read still says something when somebody reads it: a traceback, a bug
    report, `verify_lesson.py`'s output, the `.txt` export.
    """
    _severity, template = CATALOGUE[code]
    text = template
    for k, v in (args or {}).items():
        text = text.replace("%{" + k + "}", str(v))
    return text


def severity(code: int) -> str:
    """"error" or "warning" for one code, as a field rather than a range."""
    return CATALOGUE[code][0]
