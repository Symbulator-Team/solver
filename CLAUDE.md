# Symbulator version X — the solver

**You are holding version X, not version 9.** Version 9 is canonical,
created and maintained by Roberto Perez-Franco in the `Symbulator` GitHub
account. Version X is a collaborative fork, created 30 Aug 2026, where new
ideas are tried before any of them is proposed back.

`README.md` and `llms.txt` in this repository are excellent and still
accurate — read the README start to finish before writing a circuit
description; it is written to be followed directly. Note that the links
inside them point at `github.com/Symbulator/solver`, which is version 9's
repository, not this one. That is deliberate: this fork is kept as close
to version 9 as possible so `git merge v9/main` stays clean.

## The one thing that catches everybody

**Changing this repository does not change the running app.** Version X's
server pins `symbulator` from **PyPI** in its `requirements.txt`, which is
version 9's published package. Your edits here are inert until the
checkout is installed over it:

    pip install -e .        # in the environment the server runs in

Locally that is `Application\vX\.venv` (X1, 6 Sep 2026 — X's own
interpreter, so the machine's Python that version 9's dev server uses
stays untouched). On the `symbulatorx` PythonAnywhere account it is
`symbulator-venv`, and installing there means the deployed app stops
tracking PyPI — Roberto took that decision on 6 Sep 2026 (X1 in
`repos/local/NEXT_X.md` has the console commands).

**This checkout's version carries a local label: `0.5.28+xN`** (`+x4`
since X4, 6 Sep 2026, the merge of version 9's #314; X2's four-node
forms are superseded by 9's bracketed ones and gone from this tree). It is version 9's release plus a PEP 440 local
segment, so it satisfies the server's `symbulator>=0.5.26` pin, cannot
be uploaded to PyPI, and `/healthz` shows which solver a site is
running. Keep the label on a merge from 9 (it is X's line in
`symbulator/__init__.py`) and bump it with each X item that changes
the solver. The packaging test accepts the label.

**X2: the four-node forms.** `t,n1,n2,n1b,n2b,N1,N2` and
`z,n1,n2,n1b,n2b[,[p11,p12,p21,p22]]` beside the two-node forms. Read
`Element.port_nodes`, `four_node`, `turns`, `param_idx` and `node_idx`
rather than indexing `fields` by position for these kinds; the kind
table `_IDENTIFIER_FIELD_IDX` is right only for the two-node form.
`tests/test_four_node_ports.py` and the nine four-node cases in
`test_spice_groundtruth.py` (ahkab, installed `--no-deps` in
`Application\vX\.venv`) are the proof.

**Do not publish a second package to PyPI.** `symbulator` on PyPI is
version 9's name and Roberto's to release, and he declined a
`symbulatorx` distribution on 6 Sep 2026: under the same import name
two packages would overwrite each other silently, and under a new
import name every `from symbulator …` line would conflict on every
merge from 9.

**X14: `symbulator/byhand.py`**, the by-hand nodal and mesh systems
behind X's By-Hand Equations card. It states **no component rule of its
own** — it runs the real `Circuit.stamp_all()` and reads each branch's
v-i relation back out of the equations the engine produced, by
differentiation, so a domain rule added to `engine.py` appears there for
free and the two cannot drift. It changes nothing in `engine.py`: the
per-element bookkeeping comes from wrapping the `_stamp_<kind>` methods
as instance attributes for one circuit. `schematic.py`'s `to_svg` gained
an opt-in `loops=` argument that draws the mesh currents; with no
`loops` the drawing is byte-identical to what it always produced. The
account is X14 in the `local` repository's `NEXT_X.md`.

## Version 9 in, experiments out

    git fetch v9 && git merge v9/main        # a version 9 improvement, in

The `v9` remote's **push** URL is deliberately broken
(`DO-NOT-PUSH-TO-VERSION-9`) so a mistyped push cannot reach the canonical
repository. Do not repair it.

An experiment goes the other way as a **pull request** on GitHub, from
`Symbulator-Team/solver` to `Symbulator/solver`, which Roberto reviews.
These are GitHub forks precisely so that route exists.

## Tests

18 test modules under `symbulator/tests/`, including ground-truth checks
against an independent simulator. Run them before proposing anything:

    pip install -e . && python -m pytest symbulator/tests -q

The solver's answers are also checked indirectly by the server's
`tools/verify_lesson.py`, which runs 330 worked examples from the tutorial
through the real app and compares them with the answers the book prints.
A solver change that passes pytest can still move one of those; if you
change anything in `engine.py`, `elements.py`, `analysis.py`, `laplace.py`
or `equiv.py`, run that sweep too.

## Numbering

`NEXT.md` in the `local` repository is version 9's record, numbered #59 to
#182 and still running. Version X numbers its own items **X1, X2, X3…** so
the two can never collide.
