"""Check every notebook run against the real app, answer by answer.

`build_books.py` proves a notebook's cells execute. That is not the same
as proving they are right: the package and the app are two callers of one
engine and they disagree at the edges (#425 in the app's NEXT.md). So each
entry's generated run is executed here, the same entry is posted through
the app's own `/api/solve`, and every answer the two have in common is
compared numerically, at random values of whatever symbols it still holds.

    python notebooks/check_books.py                 every book
    python notebooks/check_books.py Lesson_13 ...   the books named
    python notebooks/check_books.py --prove-red     damage one answer on
                                                    purpose; must report it

Ends with the counts and exits 1 on any difference. An entry meant to fail
(build_books.MEANT_TO_FAIL) must fail in the app too.
"""

from __future__ import annotations

import glob
import os
import random
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def app_payload(e: dict) -> dict:
    """The entry as the app's own harness, verify_lesson.py, posts it."""
    return {
        "desc": e["desc"], "domain": e.get("domain", "dc"),
        "omega": e.get("omega", ""), "tool": e.get("tool") or "solve",
        "n1": e.get("n1", ""), "n2": e.get("n2", ""),
        "kind": e.get("kind", "z"), "use_rms": bool(e.get("rms")),
        "equations": e.get("equations", []),
        "unknowns": e.get("unknowns", ""),
        "conditions": e.get("conditions", []),
        "variables": ([v.strip() for v in str(e.get("vars", "")).split(",")
                       if v.strip()] or None),
    }


def package_values(e: dict, result) -> dict:
    """The run's answers under the names the app gives them."""
    tool = e.get("tool") or ""
    if tool == "th":
        z = "zeq" if e["domain"] in ("ac", "fd") else "req"
        return {"vth": result.vth, "ino": result.ino, z: result.z,
                "pmax": result.pmax}
    if tool == "er":
        return {("zeq" if e["domain"] in ("ac", "fd") else "req"): result}
    if tool == "port":
        return {f"{e['kind']}{k}": v for k, v in result.items()}
    return dict(result.values)


_FUNCTIONS = {"exp", "sin", "cos", "tan", "atan", "atan2", "sqrt", "log",
              "sinh", "cosh", "tanh", "Abs", "re", "im", "arg", "conjugate",
              "I", "pi", "E", "oo", "zoo", "nan", "Heaviside", "DiracDelta",
              "Piecewise", "Rational", "Float", "Integer", "sign", "erf"}


def same(sp, a, b) -> bool:
    """a == b at three random points of their free symbols.

    Both sides are re-read from text, so the package's symbols and the
    app's strings meet as the same plain symbols whatever assumptions they
    carried. Every identifier is a symbol unless it is one of the few
    functions an answer can hold: left to itself sympify reads `rf` as the
    rising factorial and refuses `is` as a Python keyword, and both are
    ordinary names in the example books."""
    if str(a) == str(b):
        return True
    import re
    text_a, text_b = str(a), str(b)
    names = set(re.findall(r"[^\W\d]\w*", text_a + " " + text_b))
    local = {n: sp.Symbol(n) for n in names if n not in _FUNCTIONS}
    # a keyword cannot be looked up by name even from local_dict
    import keyword
    for n in [n for n in local if keyword.iskeyword(n)]:
        local[n + "_kw"] = local.pop(n)
        text_a = re.sub(rf"\b{n}\b", n + "_kw", text_a)
        text_b = re.sub(rf"\b{n}\b", n + "_kw", text_b)
    a = sp.sympify(text_a, locals=local)
    b = sp.sympify(text_b, locals=local)
    names = sorted({str(x) for x in a.free_symbols | b.free_symbols})
    rng = random.Random(466)
    for _ in range(3):
        at = {sp.Symbol(n): sp.Float(rng.uniform(0.5, 2.0)) for n in names}
        x, y = (complex(sp.N(v.subs(at))) for v in (a, b))
        if abs(x - y) > 1e-8 * max(1.0, abs(x), abs(y)):
            return False
    return True


def main() -> int:
    warnings.filterwarnings("ignore")
    import matplotlib
    matplotlib.use("Agg")
    import build_books as bb
    import sympy as sp

    os.chdir(bb.SERVER)
    import app as flask_app
    client = flask_app.app.test_client()

    prove_red = "--prove-red" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    if prove_red and not only:
        only = ["Showcase"]

    ns: dict = {}
    exec("import sympy as sp\nfrom symbulator import (dc, ac, fd, tr, th, "
         "er, port, polar, t, s)", ns)
    entries_n = answers_n = 0
    problems = []
    for path in sorted(glob.glob(os.path.join(bb.EXAMPLES, "*.cir"))):
        stem = os.path.splitext(os.path.basename(path))[0]
        if only and stem not in only:
            continue
        entries, _, _ = bb.parse_book(open(path, encoding="utf-8").read())
        before = len(problems)
        for i, e in enumerate(entries, 1):
            entries_n += 1
            where = f"{stem} [{i}] {e['name']}"
            got = client.post("/api/solve", json=app_payload(e)).get_json()
            if e["name"] in bb.MEANT_TO_FAIL:
                if got.get("ok"):
                    problems.append(f"{where}: meant to fail, and the app "
                                    "solves it")
                continue
            if not got.get("ok"):
                problems.append(f"{where}: the app refuses it -- "
                                f"{got.get('error')}")
                continue
            try:
                ns["c"] = e["desc"]
                exec(bb.run_cell(e, "c", "r"), ns)
                mine = package_values(e, ns["r"])
            except Exception as exc:                    # noqa: BLE001
                problems.append(f"{where}: the notebook's run raises -- "
                                f"{str(exc).splitlines()[0][:120]}")
                continue
            theirs = got.get("values") or {}
            if prove_red and theirs:
                k = sorted(theirs)[0]
                theirs[k] = f"({theirs[k]}) + 1"
            shared = [k for k in theirs if k in mine]
            if not shared:
                problems.append(f"{where}: no answer in common "
                                f"(app {sorted(theirs)[:4]}, "
                                f"notebook {sorted(mine)[:4]})")
            for k in shared:
                answers_n += 1
                try:
                    ok = same(sp, mine[k], theirs[k])
                except Exception as exc:                # noqa: BLE001
                    ok = False
                    theirs[k] = f"{theirs[k]}  [{type(exc).__name__}]"
                if not ok:
                    problems.append(f"{where}: {k} differs -- notebook "
                                    f"{str(mine[k])[:60]}, app "
                                    f"{str(theirs[k])[:60]}")
        print(f"{stem}: {len(entries)} entries, "
              f"{len(problems) - before} problem(s)", flush=True)
    for p in problems:
        print("  **", p)
    print(f"{entries_n} entries, {answers_n} answers compared, "
          f"{len(problems)} problem(s)")
    if prove_red:
        print("prove-red:", "RED, as it should be" if problems
              else "STILL GREEN -- this check cannot fail")
        return 0 if problems else 1
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
