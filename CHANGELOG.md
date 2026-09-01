# Changelog

## 0.5.25 -- 1 Sep 2026

### Added
- **A dependent source's control is drawn on the element it reads
  (#213).** A schematic that shows `4*i_r1` only in the diamond's value
  leaves the reader to work out from the netlist text *which* r1 and
  which way round -- the one thing a schematic exists to save them.

  **A voltage control marks its drop.** `ed,3,0,2*v_r1` puts a + at
  r1's first node and a - at its second, just outside the body on the
  leads, where both books put them. The direction is not a choice:
  `v_r1` is v(n1) - v(n2) (`engine.stamp_all`), so the + is at n1 every
  time, whichever way round the layout drew the element.

  **A current control marks its direction.** `ed,3,0,4*i_r1` draws an
  arrow beside r1 running first node to second, head at the second, and
  labels it *i* with `R1` as its subscript -- a sloped lower-case i, the
  way every book sets a current. Again not a choice: the solver's
  positive `i_r1` flows n1 -> n2 through the element
  (`engine._stamp_r`).

  Which element a reference names is resolved exactly as
  `engine._alias_map` resolves it, and it has to be: node voltages are
  claimed first, so in a circuit with a node called `x` the token `vx`
  is that node's voltage and *not* the drop across an element called
  `x`, and nothing is marked. Every spelling the solver folds together
  is one reference to the drawing -- `5*vr1`, `5v_R1` and `5*V_r1` all
  mark r1.

  The marks take the side the labels did not: below a horizontal
  element, left of a vertical one, and over the name when a source's
  value already hangs below its circle. 48 of the 330 example circuits
  carry them. Only the two-terminal kinds are marked; an op-amp or a
  two-port block has no single pair of terminals for a sign to sit at.

  **And the value is typeset to match.** Whatever the reader typed,
  the drawing sets multiplication as implied rather than starred
  (`2*x*ir2` is `2x` and then the current; `2*3` keeps its star, since
  `23` is a different number), a voltage or a current as its own
  lower-case sloped letter, and what it names as a capitalised
  subscript. So `2*v_r1`, `2vr1` and `2*VR1` all read *v*_R1 in the
  diamond, exactly as the resistor beside it reads R_1. A symbol the
  circuit does not define -- `vs` where there is no node or element
  `s` -- is a parameter and is left as typed, the same reading that
  keeps its source a circle. `pi` is printed as the letter for the same
  reason the star went: spelled out and run together, `100+24*pi*j`
  becomes the unreadable word `24pij`, where `24πj` reads at a
  glance.

- **The resistor is 20% smaller and the dependent source 10% larger
  (#213).** Roberto's call, 1 Sep 2026, on seeing the marks in place: a
  zigzag was crowding its neighbours and a diamond was not reading as
  the larger, more deliberate symbol it is. Both are one scale factor
  on the single number each shape is built from -- `R_SCALE` on `BODY`,
  `DEP_SCALE` on `SRC_R` -- so nothing else in the geometry needed
  re-deriving: the zigzag's vertex angle, and so its mitre, is
  unchanged because its length and its amplitude scale together. The
  two source shapes are no longer the same size as each other, so the
  callers that need to know how far a source reaches -- `_body_extent`,
  the label reach, the wire keep-out and the reference marks -- are all
  told which one they have.

### Fixed
- **An SI prefix is a decimal shift, so it is done in decimal (#217).**
  `js,0,d,397.3'm` translated to SPICE as `Is 0 d 0.39730000000000004`
  where `js,0,d,.3973` gave `Is 0 d 0.3973` — the same current, two
  spellings, one of them showing the reader a binary artefact (Roberto,
  1 Sep 2026).

  The noise was born far upstream of the netlist. `397.3'm` expanded to
  the *expression* `397.3*10**-3`, and multiplying those two out in
  binary lands one unit in the last place from the decimal that was
  typed. Nothing downstream can undo that: it is a different double,
  and `repr` is right to print all seventeen digits of it. So the
  prefix is now folded into the number in base ten, with `decimal`,
  before anything binary sees it — for the quoted form (`397.3'm`) and
  the bare suffix (`397.3m`) alike, and for every prefix.

  **A whole-numbered mantissa still keeps the `n*10**e` form**, which
  SymPy reads as an exact Rational: `100'p` is 1/10000000000, not a
  float, and a circuit of whole-numbered values still solves exactly.
  Only a mantissa that already carries a decimal point — a Float
  either way — is folded.

  The same bug ran in the other direction, in the two number
  formatters: `2.2e-9 / 10**-9` is 2.1999999999999997 in binary, and
  that went out as `2.1999999999999997N`. Both now take the mantissa
  out in base ten and check it by reading the decimal back, not by
  multiplying floats. `100'p` gains from it too — it used to come back
  as `100.00000000000001P` and is now `100P`.

  Measured rather than assumed: of the 330 example circuits, seven use
  a decimal mantissa with a prefix (the only ones whose stored value
  can move at all), and all seven print byte-identical answers before
  and after. The stored double moves toward the value the reader
  typed; nothing the reader sees moves.

- **The stacked-row height was never measured (#213).** A lifted
  source hangs its value below its circle and the element on the row
  beneath stacks a value and a name above its own; that is 34.75px
  down against 45.35px up, so `STACK_H` needed 84 and had 78. The pair
  had been overlapping by 0.4px since #212 gave every name a
  subscript -- under the review harness's 2px tolerance, and so
  invisible until the values gained subscripts too and it grew to 2.1.
  Now 88.

## 0.5.24 -- 31 Aug 2026

### Changed
- **The schematics are drawn the way a textbook draws them (#212).**
  Roberto's brief, worked against Sadiku & Alexander's *Fundamentals of
  Electric Circuits* and Boylestad's *Introductory Circuit Analysis*.

  **Element names carry a subscript.** `rin` draws as R with a
  capitalised subscript IN, `r1` as R with a subscript 1 -- the kind
  letter full height, the rest below it, the way both books set them.
  One `<text>` per label with a `<tspan>` per run, the subscript shifted
  relative to the run before it so a caption can come back up to full
  size after `R1 = `. An underscore is a separator rather than a
  character to print (`r_a` is R sub A), which makes the *display*
  many-to-one -- `rab`, `rAB` and `r_a_b` all read alike. That is
  confined to the drawing: answers, exports and the description keep
  every name exactly as it was typed.

  **The inductor is a coil of loops.** Each turn is now an arc of more
  than a semicircle across a chord shorter than its own diameter, so it
  closes back past where it started and the turns overlap -- a written
  cursive `l`, repeated. It was four exact semicircles over chords of
  exactly 2r, which is a row of humps.

  **A controlled source is a diamond**, an independent one stays a
  circle (Sadiku, Fig. 1.13). A source counts as controlled when its
  value refers to another quantity in the circuit, tested against the
  circuit's own names and folded the way the SPICE exporter folds them,
  since `i_r1`, `ir1` and `IR1` have been one name to the solver since
  0.5.19.

  **Labels clear the symbols.** Placement now comes from each symbol's
  ink rather than one offset for every kind -- the old fixed offset
  cleared a resistor's zigzag and ran through a capacitor's plates.
  `REACH` is an ink figure, half a stroke outside its path and, for the
  resistor, 2.2px further still, because a mitred peak runs past its
  own vertex. That last figure came from measuring rendered pixels:
  labels the path geometry called 3px clear were 1px clear on screen.

  Also: the zigzag's peaks are mitred rather than taking the drawing's
  global round join, which was turning each peak into a blob.

  The label font's ink extents are measured rather than assumed
  (`LABEL_ASCENT`, `LABEL_DESCENT`, `CAP_DESCENT`), because a baseline
  is not an edge: a value like `-4j` or `1/gx`, or a node named `ag`,
  hangs below its baseline, and placement that ignored that left 21 of
  the 330 example circuits with 1-2px of air above a symbol.

  No API changed. `to_svg()` takes the same descriptions and returns
  the same kind of standalone SVG.

## 0.5.23 -- 31 Aug 2026

### Changed
- **The engine speaks in codes (#199).** Every `CircuitError` the
  package raises now carries a **message code and its arguments** as
  well as its English: `exc.code`, `exc.args_map`, and `str(exc)`
  rendering from the new `symbulator/messages.py`. Roberto's ruling of
  31 Aug 2026 -- the package is meant to be under the hood, so it
  should return structure and let the interface do the words. Thirty-six
  codes, numbered by module: 2xx `elements`, 3xx `engine`, 4xx
  `laplace`, 5xx `equiv`, 6xx `spice`.

  Three rules go with them. **A code is permanent once published** --
  never reused, never renumbered, gaps left alone. **Severity is a
  field, not a range**, so a warning and an error about one thing need
  one code. And **the English stays in the package**: it is what a
  traceback, a bug report or the `.txt` export can quote, and it is the
  generation source for the app's translations, which is the drift this
  scheme exists to prevent.

- **`CircuitError` still takes a plain string**, and that is not a
  transition shim. `CircuitError("some sentence")` sets `code` to None
  and behaves exactly as before, which is how an exception re-raised
  from elsewhere keeps flowing through -- and what let the app and the
  package deploy in either order rather than in step.

- Four messages became two codes apiece rather than one code with a
  glued-on clause, because the clause is prose a translator has to see
  whole: the two `_diagnose_unsolvable` diagnoses with and without
  their dc parenthetical, and "could not solve" with and without the
  extra-equation hint.

- `laplace._check_transform` takes `fn` (a function's name, or None for
  the `{...}` shorthand) where it used to take `origin`, a ready-made
  English phrase. A phrase cannot be translated from inside an
  argument; a function's name is the same in every language.

### Not in this release
- **`spice.py`'s warnings are #211.** They look like seventeen messages
  and are not: seven are `f"{el.name}: {why}"` with `why` built
  elsewhere, `skip()` alone has seven reasons, and the `described` map
  names eleven element kinds. That is thirty-odd more codes, and the
  SPICE translator is still labelled beta in the app -- its wording is
  the likeliest in the package to change, and a code is permanent.

## 0.5.22 -- 29 Aug 2026

### Changed
- **Brackets mean pr only in a resistor's value (#165).** `[...]` is
  the parallel-resistor shorthand (in an `r` element's value) or a
  two-port's parameter term; anywhere else it used to be silently
  passed to `pr()` -- `e1,1,0,[4,4]` became a meaningless "2 V"
  source, and on a capacitor it would have computed the *series*
  combination -- and now stops with a message naming both legitimate
  uses. Restores the calculator's scope, per Roberto. A `pr(...)`
  the user types remains a function call, allowed anywhere; nested
  brackets in a resistor value still work.

- The README documents the case rule (names fold, `2*VR1` is
  `2*v_r1`; free variables are case-sensitive, `c` is not `C`) and
  the two-port parameter term; llms.txt carries both among its
  easy-to-get-wrong notes.

## 0.5.21 -- 29 Aug 2026

### Added
- **Two-port parameters in the description (#163).** A two-port
  element takes an optional last term listing its four parameters:
  `z,1,2,[100,10,20,50]`. Entries are numbers, SI-prefixed values or
  expressions; each binds its correspondingly-named variable
  (`z11`...) through the same substitution machinery as
  `conditions=`, so the values are visible to expert equations and
  ride into solved answers -- the calculator's "store the values in
  the variables first", made part of the circuit text. Without the
  term the parameters stay free symbols (the tacit
  `[<name>11,...,<name>22]`), exactly as before; an explicit
  condition on the same name overrides the term; the `params=` dict
  still works. There is no clash with the `[a,b]` parallel shorthand:
  two-port elements have no value field where a parallel combination
  could appear, and the internal `pr(...)` encoding disambiguates by
  element kind. The app's Define field now reaches two-port
  parameters too (`symbulator_ui.expand_defines_in_desc` materialises
  the tacit term when a define names one of its entries).

- **Every element type now exports to SPICE (#162).** The ideal
  op-amp becomes a gain-1e9 VCVS (the universal SPICE idiom;
  parts-per-billion finite-gain error, and the warning says so). The
  ideal transformer becomes its *exact* realization -- a VCVS at the
  turns ratio, a 0 V current sense, and a CCCS reflecting the
  secondary current into the primary -- correct at DC, unlike the
  coupled-inductor approximation. A two-port block with a numeric
  parameter term becomes up to four grounded VCCS elements via the
  engine's own admittance reduction, transcribed verbatim so exporter
  and solver cannot disagree; parameter sets singular in admittance
  form warn. All verified against the independent simulator per node
  voltage, alongside the #161 cases.

### Fixed
- Numbers computed by the SPICE exporter (admittance coefficients,
  turns ratios, coupling factors) are now written with round-trip
  precision instead of 6 significant digits, which shifted solved
  voltages at the 1e-6 level. Values a person typed keep their short
  spelling.
- **Dependent sources now translate to SPICE (#161).** `to_spice()`
  reads a dependent source's value as an affine expression over node
  voltages (`v_2`), two-terminal element drops (`v_r1`) and element
  currents (`i_r1`) -- spelling equivalence included -- and emits one
  plain linear SPICE element per term: E/G for a voltage control
  (`+k`/`-k` node pairs fold into one textbook difference-controlled
  element), H/F for a current control, an independent V/I for a
  constant; terms chain in series for a voltage source and in
  parallel for a current source. A current control on anything that
  is not already a voltage source gets a 0 V sensing source spliced
  into that element's branch -- SPICE's own ammeter idiom -- shared
  across referencing sources, and working for chains of dependent
  sources sensing each other. The current of an independent current
  source is its value, so it folds into the constant instead. No
  behavioral or dialect-specific elements are ever emitted. Values
  that are not affine with numeric coefficients (nonlinear controls,
  symbolic gains) warn exactly as before, and a reference to a
  current that cannot translate poisons the referencing source with
  a warning naming the culprit, cascading as far as it reaches.

  Verified two independent ways: round trips through `from_spice()`
  re-solved and compared, and -- because a symmetric sign flip would
  cancel in a round trip -- every emitted netlist also runs through
  `ahkab`, an independently implemented MNA simulator, node voltage
  by node voltage (`test_spice_groundtruth.py`, self-skipping where
  ahkab is unavailable). That harness caught and documented ahkab's
  own quirk: its H senses with the opposite sign to its own F and to
  the ngspice manual, which ngspice, LTspice and PSpice follow and
  this exporter targets.

## 0.5.20 -- 29 Aug 2026

### Added
- **SPICE netlist translation, both directions (#160).**
  `to_spice(desc)` writes a Symbulator circuit description as a
  generic ngspice-compatible netlist; `from_spice(text)` reads the
  linear subset of one back. Both return `(text, warnings)`: an
  element or value the destination cannot express is never
  mistranslated -- it is kept as a `*` comment on export, dropped on
  import, and named in the warnings either way. r/l/c (with initial
  conditions as `IC=`), independent sources, the short circuit (a 0 V
  source, SPICE's own idiom), mutual inductance (K, computed from
  numeric inductances) and SPICE's E/G/F/H controlled sources all
  translate; op-amps, ideal transformers, two-port blocks, symbolic
  values, waveform sources and everything nonlinear warn instead.
  The mega/milli trap is handled by construction: `1'M` exports as
  `1MEG`, `1MEG`/`1M` import as `1'M`/`1'm`, and the exporter never
  writes a bare `M` at all (milli becomes a plain decimal). Feeds
  the app's SPICE Translator card.

- **Element names must be identifier-safe (#159).** A name like
  `r-x` used to parse, but referencing its current -- `2*i_r-x` --
  silently read as `2*i_r - x` and solved to an answer full of
  phantom symbols. `parse_circuit` now refuses such names with a
  message saying why. The never-enforced `RESERVED_NAMES` set is
  deleted; there are no reserved names.

### Fixed
- Stale `positive=True` wording in `Result.at()`'s docstring and the
  package docstring: the time symbol has been
  `Symbol("t", nonnegative=True)` since 0.5.11.

## 0.5.19 -- 28 Aug 2026

### Added
- **Inequality conditions -- the `|` operator's full breadth.** On the
  calculators, `solve(...) | vs>0` restricted which solutions came
  back; the port had narrowed conditions to `name = value`
  substitutions only. An expert-mode condition may now also be an
  inequality (`vs > 0`, `x <= 3`), applied as a filter on the
  solutions after the solve -- the natural way to select among the
  sign-symmetric roots a quadratic power constraint produces, in the
  same call. A restriction that excludes every solution is reported
  as such: the system solves, but the mathematics and the restriction
  disagree, which is an answer rather than a failure. Equality
  conditions substitute exactly as before, and the two kinds mix.
  Roberto's call, solving his 2013 four-dependent-source showcase for
  the monograph.

- **Python keywords work as variable names.** `is` -- the most
  natural name a source current can have -- was refused: values parse
  through Python's own grammar, and its reserved words leaked through
  to the circuits user. Keywords used as bare names are now shielded
  behind sentinel identifiers before parsing and restored as ordinary
  symbols after, so `j1,0,1,is` and `unknowns=['is']` work
  everywhere a name can appear. `True`/`False`/`None` stay excluded:
  those are literals, and refusing them beats quietly turning them
  into symbols.

- **Underscored and plain spellings are one name, everywhere.** On the
  TI calculators every answer variable was one flat word -- `ir1`,
  `vc2`, `is` -- and version 9's `i_r1` convention split each of them
  in two. The split is now healed: every circuit builds an alias map
  from its own inventory (nodes and elements), and any spelling that
  normalises to a known answer name -- case-insensitive, underscores
  ignored -- is canonicalised to it wherever expressions are read:
  element values (`j2,0,3,0.5*ir1` controls on the current through
  r1), expert equations, unknowns, and both kinds of condition. A
  1999-era netlist now runs verbatim. Names that match nothing in the
  circuit are left untouched, so free symbols still pass through; the
  one behavioural change is that a bare spelling which happens to
  collide with a real answer name now means that answer, as it always
  did on the calculator. Roberto's design call: "if there is a i_s,
  there is also an is."

## 0.5.18 -- 28 Aug 2026

### Changed
- **The schematic op-amp's + and - pin signs match the voltage
  source's polarity marks (#130).** They were 13px text glyphs, filled
  from the label font, sitting beside a source whose marks are stroked
  3.5px arms at the page's 1.7px stroke -- visibly heavier and a
  different drawing style in the same figure. Both now draw through
  one helper (`_sign_mark`), so they are identical in size and weight
  and cannot fall out of step again.

## 0.5.17 -- 28 Aug 2026

### Changed
- **A schematic op-amp's feedback wire leaves the tip the way the
  triangle points.** 0.5.16's follower loop rose vertically out of the
  tip vertex, which read wrong -- an op-amp's output exits in the
  direction the symbol points. The loop now runs 16px out of the tip
  before turning up, in both the corner-join and the occupied-row
  paths; the join at the output node's own corner is unchanged.
  Roberto's call, reviewing Bo2's Figure 6.23.

## 0.5.16 -- 28 Aug 2026

### Changed
- **The schematic drawer was reviewed against all 322 circuits in the
  version 9 tutorial and reworked.** The headline rules: a wire never
  crosses an element body; a crossing that is not a connection is
  drawn as the standard semicircular hop, and every T-joint gets its
  junction dot, so the two can never be confused; junctions coincide
  with node corners wherever the row allows; and values are shown the
  way the reader typed them.

  In detail, labels first: a phasor source keeps its angle notation
  (`110∠-120°`) instead of the 17-digit rectangular number it expands
  to; long float literals round to 5 significant digits; `30*pi/180`
  in a value reads back as `30°`; the `[a,b]` parallel shortcut is
  restored from its `pr(a,b)` rewrite; a value too long to letter at
  its element moves to a caption line below the drawing (`name =
  value`, the block the mutual inductances already used -- their
  captions move down there too); the viewBox accounts for text width,
  so nothing is clipped; and a source's value sits clear of its
  circle.

  Layout second, mostly op-amps: cascades draw left to right (the
  node walk follows the inverting-input-to-output link, defers an
  output node until its input is placed, and links n+ toward n-);
  grounded elements hanging on a column an op-amp occupies bump to a
  stub column outside its span; a non-inverting stage whose + input
  connects only to a grounded source draws that source in the input
  drop, under the triangle, the way a textbook does; an op-amp whose
  *inverting* input is ground mirrors its pins; the follower written
  `o1,1,2,2` springs its feedback straight up from the tip corner and
  joins the output node at the node's own corner dot; and stacked
  spans order narrow-below-wide so an outer element's risers land on
  junctions instead of slicing through an inner element's body.

## 0.5.15 -- 27 Aug 2026

### Fixed
- **An impulse-valued TR answer now says so.** `tr()` passed every
  s-free s-domain value through untransformed, so a circuit answer that
  was genuinely an impulse printed as a bare constant --
  `e,1,0,10*delta(t)` into a resistor reported `v_1 = 10`, identical to
  a 10 V step. But a circuit answer constant in s *is* an impulse: a
  step arrives as `k/s` and a waveform brings its own s, so a bare
  constant has nowhere else to come from. Those answers now come back
  multiplied by `DiracDelta(t)`: `v_1 = 10*DiracDelta(t)`.

  The pass-through was protecting two real cases, and both still pass
  through -- discriminated by provenance now, not by the expression's
  shape. A solved expert-mode unknown is a scalar (`k = 5` means the
  amplitude is 5), recognised by its key; a dependent source's echo of
  its controlling answer (`i_j = 2*ir3`) is a relation whose symbols
  name functions, so it reads identically in s and in t, recognised by
  `_is_controlled`. Zero answers are unaffected either way --
  `0*DiracDelta(t)` is 0.

  Found on 27 Aug 2026 while wiring TR answers into the app's
  Numerical Solver handover; every impulse example in the tutorial
  prints only s-bearing answers (`vc`, `ic`, `vo`), which is how the
  wrong constants went unnoticed. Answers mixing an impulse with a
  tail (`DiracDelta(t) - exp(-t)`) were already right.

## 0.5.14 -- 27 Aug 2026

### Fixed
- **An error about a value now quotes what was typed, not the machine's
  rewrite of it (#59).** Values are rewritten before they are parsed:
  `[a,b]` becomes `pr(a,b)` and `1'k` becomes `1*10**3`. The complaint
  came from after that, so typing `[1'k,2'k` was answered with

      Could not read the value 'pr(1*10**3,2*10**3': '(' was never closed.

  which is the machine's business and not the reader's. `safe_sympify` and
  `check_expression_syntax` now take the original text alongside the
  rewritten one and quote the original; `Element` keeps the fields as they
  were typed, so the engine can recover them.

  An unbalanced bracket is caught before the rewrite instead, because it
  cannot be recovered afterwards -- the typed text and the rewrite split
  into different numbers of fields, so the two can no longer be lined up:

      'r1,1,0,[1'k,2'k' is missing a closing bracket. A parallel
      combination is written [a,b], as in [1'k,2'k].

- **A name used as a function says so.** `rx[1'k]` has balanced brackets,
  so it rewrites into something shaped like a call, passes the syntax gate
  -- which legitimately allows calls -- and died inside SymPy as
  `'Symbol' object is not callable`, naming neither the value nor the
  circuit. It now names the value. It deliberately does *not* name the
  symbol in that case: the rewrite makes `rx[1'k]` into `rxpr(...)`, and
  the culprit SymPy reports is a name the reader never typed.

- **An unrecognised unit prefix names the value** and lists the prefixes
  that exist, instead of "Circuit description uses shorthand that
  Symbulator does not recognize" with nothing to go on.

None of these was ever mis-solved -- every case was refused, and the
syntax gate is untouched, so this is not a security change.

## 0.5.13 -- 27 Aug 2026

### Fixed
- **`th()` no longer throws away the half of the answer it found.** The
  tool runs two solves: the circuit as given, for the open-circuit
  voltage, and the circuit with a short across the terminals, for the
  Norton current. The second was allowed to take the first down with it,
  so a circuit whose short-circuit round has no solution returned
  nothing at all -- where the calculator versions left you holding the
  Thevenin voltage.

  An ideal op-amp output is the case that found this. Shorting it asks
  what current flows when a fixed voltage sits across zero resistance,
  and the system has no solution. But the question does have an answer,
  and it is the one the documentation asserts without ever showing: the
  current is unbounded, so the equivalent impedance is zero.

  So the short is now measured rather than assumed. When it will not
  solve, the terminals get a resistance `x_test` instead of a short and
  the current's limit is taken as `x_test` goes to zero -- which is what
  a short is. An unbounded limit means `ino` is infinite and `z` is 0,
  reported as a result rather than guessed at. A finite one means the
  short was merely awkward to solve and the answer was there all along.
  Either way `TheveninResult.note` says which happened; it is empty on
  an ordinary run, and ordinary runs are untouched.

  Only if the limit cannot settle it either does the call still fail --
  and the message now carries the open-circuit voltage, so the half that
  was found is not lost.

### Added
- `TheveninResult.note`, a sentence explaining how the short-circuit
  round was resolved when it was not simply solved. Empty otherwise.

## 0.5.12 -- 26 Aug 2026

### Added
- **Expert mode works with the equivalent tools.** `th()`, `er()` and
  `port()` now accept `equations`, `unknowns` and `conditions` and pass
  them to every solve they run. The original barred expert mode from
  these tools, but nothing in the physics required it: an equivalent is
  orchestration over the same solver, and the arguments simply were not
  being handed on.

  This is what lets a dependent source be defined against a derived
  name -- `vx` with `vx = va - vb` as an added equation and `vx` as an
  added unknown -- while asking for a Thevenin equivalent, which
  previously had to be written by substituting the difference into the
  source's value by hand.

  `th()` runs two rounds, an open-circuit solve and a short-circuit one,
  and the extras go into both. That is right for a condition on a
  parameter and for an equation naming a derived quantity, which mean
  the same thing in either round. It is not right for an equation that
  pins an unknown element value from a measurement: that measurement
  holds in the circuit as given, not in the shorted copy, so the two
  rounds are asking for different things. In practice the short-circuit
  round then has no consistent solution and the solve raises, which is
  the outcome you want -- but it is a consequence rather than a guard,
  so determine such a value with a plain solve first and put the number
  in the description. Documented on `th()` rather than guarded, since
  telling the two kinds of extra apart means guessing at intent.


## 0.5.11 -- 26 Aug 2026

### Changed
- **Every domain-sensitive input is read in the same domain as its
  analysis's answers.** FD reads in s, TR reads in t. Source values
  already did; added equations, added conditions, Evaluate expressions
  and the Solve card now do too.

  This completes a design choice made in version 8, not a new one. The
  calculator settled the awkward case by removing TR from expert mode
  altogether -- its prompt offers "1:DC 2:AC 3:FD" -- so there was no
  precedent to match, only a gap to close.

  A relation between plain parameters, `x = 3`, is left alone: it fixes a
  symbol in the circuit rather than describing a signal, and dividing it
  by s would turn a 3 V source into a ramp.

- **`{...}` works wherever the convention it escapes is enforced**, not
  only in the circuit description, and it is evaluated where it is
  written rather than rewritten to `t2s(...)`. That is what lets a
  failure name the brackets the reader typed instead of a function they
  never wrote.

### Fixed
- **The time symbol is non-negative, so impulses survive.** SymPy
  evaluates DiracDelta of a strictly positive argument to 0, so under the
  old symbol every impulse silently vanished: a `delta(t)` source, the
  scalar an expert-mode unknown solves to in TR, and `s2t(1)`, which
  answered 0 where the answer is `DiracDelta(t)`. t >= 0 is also what the
  one-sided transform is defined on, and the origin is where an impulse
  lives.

  `positive` had been chosen for tidy answers, since it lets the
  transforms drop `Heaviside(t)`. That is folded away afterwards instead,
  matching `Heaviside(t)` and nothing else -- `Heaviside(t - 1)` is a step
  delayed to t = 1 and genuinely zero before then. Every documented answer
  is unchanged.

  It also removed the reason the parser bound a separate neutral `t`, so
  `v_2 + t` in Evaluate no longer carries two identical-looking symbols
  that never combine.

- **Both ends of both transforms are checked, and a mismatch stops.**
  `t2s(x)` wants x valid in t and must produce s; `s2t(x)` the reverse.
  "Valid in t" means "does not mention s", not "mentions t" -- a constant
  is valid in either, and `{5}` meaning a step of 5 keeps working.

  The input check catches `s2t(exp(-t))`, which returned 0 with nothing
  to show anything was wrong. The output check catches `t2s(1/t)`, which
  passes the input check and comes back as an unevaluated
  LaplaceTransform.

- **`t2s` and `s2t` read strings properly.** Their docstrings advertise
  `t2s("5")`, but they used bare `sp.sympify`, so anything past a plain
  number was wrong: `"5*u(t)"` made an undefined function of u, `"2'k"`
  would not parse, and `"1-e^(-t/2)"` read the caret as XOR and e as a
  symbol. Those failures were silent, arriving as unevaluated transforms.

## 0.5.10 -- 26 Aug 2026

### Fixed
- **Expert mode in TR no longer answers zero.** An extra unknown solved
  for in a transient analysis -- the amplitude of a source, most often --
  came back as 0 with no error and no warning.

  `tr()` runs `fd()` and inverse-Laplace-transforms what it solved. Every
  node voltage and element current is a function of s and wants
  transforming. An expert-mode unknown is a plain number and does not:
  `inverse_laplace_transform(1, s, t)` is `DiracDelta(t)`, the time
  symbol is declared positive, and DiracDelta of a positive-only symbol
  evaluates to 0. So a step height the s-domain solve had correctly found
  to be 1 was reported as 0.

  Values with no `s` in them now pass through untransformed. AS2's
  step-source problem in the transient lesson -- find the amplitude given
  v_c(t) -- returns the book's 1 V again, and `fd` and `tr` agree on it.

  This is the third bug caused by DiracDelta collapsing under a positive
  t. It read as "expert mode does not work in TR" rather than as a
  transform problem, because what it ate was a scalar rather than a
  waveform.

## 0.5.9 -- 25 Aug 2026

### Fixed
- **A dependent source that reads a capacitor's current, or any element's
  voltage, is now actually connected to it.** Both were being left as free
  symbols that no equation constrained.

  Most references already worked: an element's *current* is normally one of
  the unknowns, so `2*i_r1` on a source resolves by itself. Two quantities
  are not unknowns. A capacitor's current is stamped straight into `known`
  as an expression in the node voltages, and an element's *voltage* is
  derived as v(n1) - v(n2) only when the answers are reported. Naming
  either from a source value produced a symbol with nothing behind it.

  What made this hard to see is that it did not look like a failure.
  sympy solved the system it was given and answered every quantity *in
  terms of* the loose symbol, so AS7's Example 10.1 came back as
  `i_cx = i_cx*(0.9655 + 0.4138j) + 2.897 + 1.241j` -- a closed-form
  equation, printed where a number belonged, in a circuit that reported
  itself solved. It now gives 7.59 angle 108.4 degrees, which is the
  answer in print.

  AS7's Practice Problem 10.1 (voltage-controlled) and Example 10.13
  (controlled by a capacitor current in a chain) are fixed by the same
  change and are now regression tests, along with Example 10.1.

  Example 10.14 also solves about twice as fast, because the free symbol
  had been enlarging the system it was carried through.

## 0.5.8 -- 25 Aug 2026

### Added
- **Polar phasors, written with the angle sign.** `(20∠ 30°)` is how
  every circuits textbook writes a phasor and how versions 7 and 8 accept
  one; it now works here too. Both degree characters are taken -- the real
  degree sign and the masculine ordinal, which looks identical and appears
  20 times in the 2023 documentation -- and a negative angle written with
  an en dash is read rather than refused.

  **It becomes a rectangular number, not `20*exp(I*pi/6)`**, and that is
  the point rather than a detail. SymPy cannot reduce
  `exp(I*pi*130/180)` to a closed form, so an exponential source is
  carried unevaluated through every mesh equation of the circuit. AS7's
  Example 12.12 written that way was killed by the web app's 25-second
  limit and did not converge in several minutes offline; with the angle
  sign it solves in under three, and matches the answer in print. Its
  Example 12.3 went from 94 seconds to two.

  Where an angle happens to simplify -- 120 degrees, say -- the
  exponential form was always fast, which is what made this look like a
  property of particular circuits rather than of the notation.

  Exactness is given up deliberately: `100∠ 0°` is 100.0, not 100.
  A phasor angle is a measurement, and the alternative is circuits that do
  not solve.

## 0.5.7 -- 25 Aug 2026

### Fixed
- **Mutual inductance between impedances given in jOhms.** A textbook
  writes a coupled pair one of two ways: two inductors in henries, or two
  impedances already in jOhms. The port stamped only the first. The second
  -- `r` elements with imaginary values, coupled by an `m` whose value is
  imaginary too -- was accepted, solved, and answered with **no current at
  all in the secondary**. No error, no warning, just a zero where the
  answer should be.

  `symbv8s8` couples `r` elements when the analysis is AC, adding the
  mutual term without a jw factor because a value in jOhms is already an
  impedance:

      v(n1) - v(n2) = Z*i_self + sum(M * i_other)

  Restored exactly. Checked against the two worked examples in the
  Symbulator 7 and 8 documentation, whose answers Roberto derived by hand:
  AS7's Example 13.1 gives 13.02 at -49.4 degrees and 2.910 at 14.04, and
  its Practice Problem 13.1 gives 20.00 at -134.43. All three match.

## 0.5.6 -- 25 Aug 2026

### Added
- **The `{...}` shorthand for a time-domain source in FD.** FD reads its
  source values in the s-domain -- `5/s` is a step, `5` is an impulse.
  Wrapping a value in braces says "this one is written in time", and it is
  transformed on the way in. `{5}`, `{u(t)}` and `{2*exp(-4*t)}` all work,
  and `{5}` is exactly `t2s(5)` in five fewer characters.

  Ported from `symbv8si`, which does the same two substitutions and only
  when the tool is fd -- TR converts its sources anyway, so there would be
  nothing for it to do there. Asking for it elsewhere now says so instead
  of letting the braces reach SymPy and come back as "contains a set".

  Not to be confused with `[...]`, which is the parallel-impedance
  shortcut (`[2,3]` is `pr(2,3)`), applies in every analysis, and has
  worked since the port. The two are pinned against each other by a test.

## 0.5.5 -- 25 Aug 2026

### Fixed
- **`tr()` reads its sources in the time domain again.** The original
  transforms a transient source into the s-domain on the way in and
  transforms the answers back on the way out. This port only ever did the
  second half, so every transient result was one integration short: a
  plain `12` gave the impulse response where the step response was meant,
  and it looked plausible enough to pass a glance.

  The rule restored here is `symbv8s5`'s, read out of the Symbulator 8
  document rather than inferred:

      If tool="tr" and the element is a source (e or j):
        value depends on t          -> t2s(value)
        value is a constant         -> value/s   (a step of that size)
        value refers to another
          element's answer          -> left alone

  That last branch matters: a controlled source's value is a relation, not
  a waveform, and transforming `2*i_r1` would be meaningless. A value
  already written in terms of `s` is also left alone, so every existing
  version 9 description keeps working -- all twelve of the app's bundled
  examples answer exactly as they did in 0.5.4.

- **`delta(t)` no longer vanishes.** 0.5.3 bound `t` in the parsing
  namespace to the solver's `Symbol("t", positive=True)`. That changes
  what an expression *means* rather than only which symbol it uses: SymPy
  evaluates `DiracDelta` of a strictly positive argument to zero, so an
  impulse source was gone before the transform ever saw it. `t` parses as
  a neutral symbol again, and `t2s()` now takes the time symbol from the
  expression it is given instead of assuming one -- forcing a symbol the
  expression does not contain made `laplace_transform` treat the whole
  thing as a constant, which is how `t` came back as `t/s` instead of
  `1/s**2`.

## 0.5.4 -- 24 Aug 2026

### Fixed
- **A digit inside a name is no longer read as a multiplication.** The
  implicit-multiplication rule added in 0.5.1 looked only at the character
  before the letter, so any name with a digit in the middle was split:
  `t2s(t)` became `t2*s(t)`, and the function vanished into a symbol called
  `t2` times a symbol called `s`. That broke `t2s` and `s2t` -- the two
  functions 0.5.3 had just made reachable, and the two most likely to be
  typed into a transient source. `i2r`, `v2` and any other name of that
  shape were affected the same way.

  A number now has to start where a name could not: `2ir3` and `.2v1` still
  gain their multiplication, `t2s(t)` and `i2r` are left alone.

## 0.5.3 -- 24 Aug 2026

### Added
- **`t2s()` and `s2t()` can be reached from a circuit.** Both have existed
  since the port and are exported, but a value, an Evaluate expression or a
  Solve equation is parsed against a deliberately small namespace, and
  neither was in it -- so `t2s(5)` was read as a variable being called and
  failed with "'Symbol' object is not callable". They are now available
  wherever an expression is, so a source can be written `t2s(5)` rather
  than hand-transformed to `5/s`.

- **`t` and `s` resolve to the symbols the solver itself uses.** This is
  the part that would have bitten quietly: `tr()` writes its answers in
  `Symbol("t", positive=True)`, and a hand-written `t` used to become a
  bare `Symbol("t")` -- a different symbol, which `subs()` ignores without
  complaint and which `t2s()` would integrate over instead of the real
  time variable.

`pf()` is deliberately not included. It returns a sentence -- "pf: 0.6
lagging" -- rather than an expression, so sympify hands back a Python str
and every formatter downstream expects a SymPy object. It belongs in the
interface as a tool with its own inputs and a text result, not as
something callable in a value.

## 0.5.2 -- 24 Aug 2026

### Fixed
- **The calculator's notation is kept, not replaced.** 0.5.1 expanded the
  new shorthands everywhere, including on the path that echoes a circuit
  back to the caller. The web app puts that echo straight into its Circuit
  Description box, so typing `V*u(t)` and pressing Run silently rewrote it
  to `V*Heaviside(t)` -- taking away the notation the user had deliberately
  chosen, on their first attempt, and leaving their circuit no longer
  matching the book they copied it from.

  The expansion now happens only on the way to being solved. Echoed back,
  `u(t)`, the Greek delta, `2ir3` and `2e^(-4t)` all survive exactly as
  typed. This puts them in the same category as the `'k` prefix, which has
  always been parsed and kept, rather than with the AC imaginary unit,
  which is deliberately normalised in view of the user because `J` and `j`
  meaning the same thing is worth making explicit.

  The `[...]` shortcut still expands unconditionally: `_split_fields`
  cannot tell its inner commas from an element's own field commas without
  it.

## 0.5.1 -- 24 Aug 2026

### Added
- **The calculator's syntax is read as written.** A circuit description
  copied out of the Symbulator 7 or 8 documentation used to fail in this
  package, which meant the version 9 documentation had to carry a second
  spelling of every circuit. Four habits are now understood, and each one
  was chosen so that nothing is taken away from anyone who was not using
  it:

  - `u(t)` is the unit step and the Greek delta is the impulse, becoming
    `Heaviside(t)` and `DiracDelta(t)`. `u` is also the micro prefix, and
    the two are told apart by whether a `(` follows: `7u` is micro, `7u(t)`
    is the step. A bare `u` is therefore untouched and still works as an
    ordinary variable -- unlike the names in the parsing namespace, which
    are taken from every user. ASCII `delta(t)` is accepted too, for
    keyboards without the character.

  - `^` is exponentiation. It was not merely unsupported before: `2^3` was
    rejected outright, because a caret is XOR in Python and the expression
    guard refuses it. It now reads as 8.

  - `e^x` is Euler's number raised to x, becoming `exp(x)`. Only a caret
    makes `e` special, so `e` on its own remains an ordinary variable and a
    source valued `e` still solves.

  - Multiplication may be implied: `2ir3`, `.2v1`, `2(a+b)`, `(a)(b)` and
    `10e^(-t)sin(2t)` all read as products.

  Two things are deliberately protected from that last rule. Scientific
  notation stays a number -- without the guard `2.5e3` becomes `2.5*e3`,
  quietly replacing a value with a symbol -- and so does a bare engineering
  suffix, since `1k` is a thousand rather than one times k. The difference
  is whether the letter is an SI prefix: `2m` is milli, `2t` is a product.

## 0.5.0 -- 23 Aug 2026

### Added
- **`to_svg()` and `draw()`: a circuit description can now be drawn, not
  only solved.** `symbulator.schematic` renders the same string `dc()`,
  `ac()`, `fd()` and `tr()` already take into a standalone SVG, using only
  the standard library -- no matplotlib, no LaTeX, no external toolchain --
  so it runs unchanged in CPython and under Pyodide in the browser builds.
  Every stroke is `currentColor`, so one drawing serves a light and a dark
  page without being redrawn.

  The layout is deliberately not a general graph-drawing algorithm.
  Force-directed placement, which most netlist viewers reach for, produces
  a physics-plausible blob rather than something that reads as a schematic.
  This assumes instead the shape nearly every linear teaching circuit
  already has: ground as one rail along the bottom, anything touching it
  hanging vertically from it, anything between two live nodes running
  along a top row, and node order taken from a depth-first walk so a chain
  comes out as a chain. Anything that would collide -- a parallel element,
  or one reaching over an intermediate node -- is lifted onto its own row
  with risers, which is interval-graph colouring over the span each
  element occupies. Op-amps use the same colouring, with the colours
  becoming lanes down the middle band.

  Two details are taken from the engine rather than chosen, and must not
  be "corrected" without reading it: a voltage source's **+** goes on `n1`,
  because `_stamp_e` stamps `v(n1) - v(n2) = value`; and a coupled
  inductor's dot goes on `n1` of **every** coupled inductor, always,
  because the coupling enters as `+M*i_other` with no orientation term --
  reversed coupling is expressed as a *negative* M, so the dots never
  move and the sign appears in the caption instead. Mutual inductance is
  captioned rather than drawn, since `m` couples two elements rather than
  two nodes and a dashed tie between coils just reads as another wire.

  `to_svg` parses with `expand_si=False`, so a bare `1k` draws where the
  solver would stop and ask which it meant. A circuit can therefore be
  drawn before, or without, being solved -- which is when a picture helps
  most.

  Known limits, documented in the module: a bridge draws as a ladder with
  a jumper rather than the textbook diamond; an inductor coupled to two
  others with opposite signs cannot be drawn faithfully, the dot
  convention having no notation for it; a non-grounded op-amp `+` input is
  routed but may cross wires; and two-port blocks and the transformer draw
  as labelled boxes without their port parameters.

## 0.4.6 -- 22 Aug 2026

### Added
- **Every root of a circuit is now returned, not just the first.** An
  expert-mode equation written on a power is quadratic in its unknown, so a
  circuit like `e,1,0,e` / `r1,1,0,1'k` with `p_r1 = 0.025` is solved by
  `e = 5` *and* `e = -5`; both satisfy every constraint given. `solve_circuit`
  kept whichever SymPy happened to list first, which was the negative one, and
  presented it as the answer. `solve_circuit_all()` now returns them all, and
  `Result.solutions` exposes the list (`[values]` when there is only one, so
  the shape never varies), with `Result.multiple` as the flag and a `repr`
  banner naming the count. `solve_circuit()` is a thin wrapper returning the
  first, so `equiv`, `plotting` and `laplace` are unchanged. The ranking is
  `_rank_solutions`: it judges **design unknowns only** -- symbols not named
  `v_*` or `i_*`, since a node voltage or a branch current may perfectly well
  be negative -- putting all-real ahead of complex and all-non-negative ahead
  of negative, with ties keeping SymPy's order. So the root a person would
  have chosen leads, and the others remain available rather than discarded.
- **`symbulator.t` and `symbulator.s` are exported**, and `Result.at()`
  substitutes by *name*. The solver builds its time variable as
  `Symbol("t", positive=True)`; a user's own bare `Symbol("t")` is a different
  symbol, so `.subs()` silently did nothing and returned the expression
  unchanged. `res.at("v_2", t=0.001)` gives one value, `res.at(t=0.001)` a new
  Result with everything evaluated, `.solutions` included. Assumptions no
  longer have to be guessed at.

### Fixed
- **A floating sub-circuit solved silently.** `e1,1,0,5:r1,2,3,1` has nodes 2
  and 3 with no path to the reference node, and came back with `v_2 = v_3` and
  `r_e1 = oo` rather than an error. `_validate_topology` now runs a union-find
  over element terminals (an op-amp joins all three of its terminals, grounded
  two-port blocks tie both nodes to 0, `m` is skipped since it names inductors
  rather than nodes) and names the orphaned nodes.
- **Contradictory circuits gave a generic error.** Sources in parallel, a 0 Ohm
  resistor across a source, current sources in series -- all produced "Could
  not solve the system of equations… try symbolic values only", which is not
  what is wrong. `_diagnose_unsolvable` now names a voltage loop and lists its
  members, or names a node fed only by current sources. It runs only when
  `sp.solve` returns nothing, so the ordinary path is untouched.

### Security
- **Values and equations are checked before `sympify` sees them.** `sympify`
  is `eval` underneath, and a restricted namespace constrains only *names* --
  conditional expressions, lambdas, attribute access and subscripting all still
  ran. This matters for the web app, which feeds it strangers' input.
  `check_expression_syntax` parses with `ast` first and admits only numeric
  constants, plain names, arithmetic, unary sign, tuples, and calls of a bare
  function name with no keyword arguments; anything else raises
  `UnsafeExpressionError`. It is called inside `safe_sympify`, so values,
  `equations=` and `conditions=` are all covered.

### Documentation
- README no longer tells PyPI users to `pip install -r requirements.txt`, a
  file the distribution does not contain, and no longer links `llms.txt`
  relatively -- the link only resolved inside a checkout. Project URLs now
  include the source repository and the documentation site instead of pointing
  `Homepage` at PyPI itself. The package is described as Symbulator 9, matching
  symbulator.com.

## 0.4.5 — 21 Aug 2026

### Fixed
- **An expert-mode equation written on a derived quantity was silently
  discarded** -- `r_e`, `p_r1`, `v_r2`, `z_e` and the like. Those are
  computed by `analysis._derived()` *after* `solve_circuit()` returns, as
  algebra on the finished solution, so the solver had never heard of the
  names. The convenience that turns a brand-new symbol in an extra
  equation into an unknown then registered each one as a free variable
  that happened to share the name: `Eq(r_e, 12000)` was satisfied by
  setting that phantom to 12000, which cost the system nothing and told
  the circuit nothing. The answer came back correct but one constraint
  short -- parametrized in a leftover node voltage instead of resolved to
  numbers -- with nothing raised, nothing logged, and a result object
  that looked entirely normal. `engine._derived_definition()` now
  recognises a symbol naming a derived quantity of an element actually
  present in the circuit and stamps in the equation defining it in terms
  of the system's own unknowns, so the constraint lands on the circuit.
  The `r_`/`z_` forms are written multiplied out rather than as a
  division, keeping the system polynomial and stopping a zero current
  from putting a division by zero into it. Matching is against real
  element names rather than a prefix pattern, so ordinary labels like
  `pout`, `vin` and `r_b` are untouched unless they genuinely collide
  with an element in that circuit, and a name listed in `unknowns` is
  still registered first and left alone -- an explicit list continues to
  win.
- **An equation on a current held in `circuit.known` was discarded the
  same way** -- a `j` source's current, and a capacitor's in AC, are
  recorded rather than solved for, so `i_j1 = 0.005` became another
  phantom. Extra equations are now substituted against `known` before
  being stamped, turning that into a real constraint on the source's
  symbolic value. An equation left with nothing in it afterwards is
  dropped as redundant; one left as a flat contradiction now says so
  plainly instead of surfacing later as "could not solve the system".

### Changed
- **The AC power quantities refuse an expert-mode equation instead of
  ignoring one.** `s_`, `p_` and `ap_` in the AC domain are defined
  through `v * conjugate(i)`, and conjugation is not something
  `sympy.solve` can carry through a system, so there is no definition to
  stamp. They now raise `CircuitError` naming the quantity and pointing
  at the `v_`/`i_` restatement that does work. No working call breaks:
  none of these ever produced a constrained answer.

## 0.4.4 — 21 Aug 2026

### Added
- **`parse_circuit()` and `expand_shorthand()` take a new `expand_si`
  / `si` flag (default `True`, unchanged behaviour)** that, when set to
  `False`, leaves SI-prefix shorthand (`4.7'M`) in each value field
  exactly as typed instead of expanding it to a literal number. This is
  for callers that only want to echo a circuit description back to the
  user -- e.g. after normalising `i`/`I` to `j`, or after resolving a
  bare ambiguous suffix -- where the notation the person actually typed
  is worth more to them than the number it stands for. Solving still
  goes through the normal expansion (`expand_si=True`) as its own,
  separate parse, so this has no effect on any circuit's actual answers.

### Fixed
- **An AC element whose complex power should come back purely real or
  purely imaginary could instead show a tiny leftover in the other
  part** -- e.g. a resistor's power reading `0.006098 + 4.445e-18j`
  instead of plain `0.006098`, or an inductor's reading
  `-7.589e-19 + 0.006098j` instead of plain `0.006098j`. That leftover
  is ordinary floating-point noise from multiplying already-computed
  complex floats, and every rounding/display mode showed its own
  version of it -- including "exact", which showed the ugliest,
  full-precision version. `dc()`/`ac()` now recognise when one part of
  a complex power or impedance is negligible next to the other (with
  plenty of margin above the actual noise floor) and zero it out, so
  both parts are held to the same standard instead of each showing
  whatever noise it happened to accumulate. Exact/rational answers
  (e.g. from a circuit with no floats in it at all) are untouched, since
  they can't carry this kind of noise in the first place.

## 0.4.3 — 21 Aug 2026

### Fixed
- **"Third-level" quantities (a capacitor's current, a dependent
  current source's value, and anything derived from them like complex
  power) could come back still containing a raw node-voltage or
  branch-current symbol** -- e.g. `i_c1 = 0.001j*v_3` even though `v_3`
  itself was correctly solved to a plain number. These quantities are
  stamped in terms of the symbols `Circuit.v()` hands out *before* the
  KCL system is solved, and were never substituted with the final
  solved values afterwards -- on the original calculator they were
  evaluated on the fly instead. `solve_circuit()` now substitutes the
  solved system into every such quantity as its one evaluation pass, so
  they always come back fully resolved, same as every other answer.

## 0.4.2 — 20 Aug 2026

### Changed
- **`i`, `I`, `j` and `J` are reserved for the imaginary unit in AC
  only** (and in the AC mode of `th()`/`er()`/`port()`). Outside AC --
  `dc()`, `fd()`, `tr()`, and DC-mode `th()`/`er()`/`port()` -- there is
  no such thing as a complex component value, so those four letters are
  now free to use as ordinary variable or element-value names there,
  same as any other name. `safe_sympify()` and `hijacked_names()` take a
  new `reserve_imaginary` keyword (default `True`, matching 0.4.0's
  always-reserved behaviour) for callers that want the same
  domain-aware rule.

## 0.4.1 — 19 Aug 2026

### Fixed
- The `[a,b,c]` parallel-resistor shorthand is expanded correctly again.

### Added
- `time_samples()` and `bode_samples()`, powering the web front end's
  "Plot vs. time" and "Bode plot" tools.

## 0.4.0 — 18 Aug 2026

### Changed (breaking)
- **`i`, `I`, `j` and `J` are reserved for the imaginary unit** and can
  no longer be variable names. Every spelling now agrees: `3j`, `3*j`,
  `3*i`, `3*I` and bare `j` all mean the same thing. Previously `3*j`
  silently produced a variable, which looked right and gave a wrong
  answer.
- **Values are parsed against a restricted namespace.** Names SymPy
  would otherwise reinterpret — `Q`, `S`, `N`, `beta`, `gamma`, `E` and
  the like — are now ordinary variables, which is what someone writing
  `Q` for quality factor intends. `pi` and the maths functions (`exp`,
  `sqrt`, `sin`, `Heaviside`, …) still mean what they say. Euler's
  number is now written `exp(1)` rather than `E`.
- **Element letters, element names and node names are case-insensitive**
  and fold to lowercase: `R1` and `r1` are one resistor, `A` and `a` one
  node. Writing both spellings is now correctly reported as a duplicate
  instead of silently creating two elements. Values keep their case,
  because `'M` and `'m` differ by a billion.
- **The exa prefix is gone.** `E` belongs to scientific notation
  (`8E3` = 8000), which is worth more in a circuit than exa-ohms.
- **`ex()` no longer accepts a transient mode**, matching the
  calculator, whose prompt reads "Analysis? 1:DC 2:AC 3:FD". The
  previous docstring wrongly claimed the TI offered a fourth option.
  Call `tr()` directly for transient analysis.

### Added
- Both micro characters are accepted: MICRO SIGN (U+00B5) and GREEK
  SMALL LETTER MU (U+03BC) look identical and which one a keyboard
  produces is arbitrary, so `4.7'µ` works either way.
- `safe_sympify()` and `hijacked_names()` for callers that want the same
  parsing rules or want to tell a user which names were reinterpreted.

## 0.3.0 — 17 Aug 2026

### Changed (breaking)
- **Bare unit suffixes are no longer guessed.** A value like `1k` is
  ambiguous — it could mean the SI unit (`1'k` = 1000) or one times a
  variable named `k` (`1*k`) — so the default policy `suffix="ask"` now
  raises `AmbiguousValueError` listing every such value instead of
  silently choosing. Pass `suffix="si"` or `suffix="var"` to decide for
  a whole circuit, or write the explicit form. Code written against
  0.1.0 that used bare suffixes needs one of those changes.

### Added
- `find_ambiguous_values(desc)` scans a description for ambiguous
  values without solving, for callers that want to ask a user.
- Expert mode: `equations`, `unknowns` and `conditions` arguments on
  `dc`/`ac`/`fd`/`tr`/`ex` (and the tools), porting the original's
  "Add equations / Add unknowns / Add conditions" prompts. Conditions
  are solve-time substitutions, the calculator's `|` operator.
- Branch voltages are now stored as `v_<element>`, matching the
  `v<name>` variables the calculator kept.
- Circuit descriptions may separate elements with newlines as well as
  `:`.

### Fixed
- A source carrying no current (for example one feeding only a
  capacitor in DC) raised `ZeroDivisionError` out of mpmath when
  computing the resistance/impedance it sees. It now reports infinity,
  and the genuinely undefined 0 V / 0 A case omits the quantity.

### Docs
- The `s` element is described as a short circuit.

## 0.2.0 — never published
Superseded by 0.3.0 before release; its changes are listed above.

## 0.1.0 — 13 Aug 2026
First release: DC, AC, s-domain and transient analysis; Thevenin/Norton
equivalents; equivalent impedance; two-port parameter extraction; the
`ex()` dispatcher; unit shorthand; dependent sources.
