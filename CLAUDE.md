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

Locally that is your virtualenv. On the `SymbulatorX` PythonAnywhere
account it is `symbulator-venv`, and installing there means the deployed
app stops tracking PyPI — a real decision, not a step to take absently.

**Do not publish a second package to PyPI** as a first move. `symbulator`
on PyPI is version 9's name and Roberto's to release. If an experiment
needs a changed solver, install the checkout; only if that stops being
enough is a separate name worth discussing with him.

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
