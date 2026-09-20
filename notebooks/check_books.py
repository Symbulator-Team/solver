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
        "defines": e.get("defines", []),
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


def app_card(client, e: dict, src: str, values: dict):
    """The entry's Evaluate box or Solve card through the app's own route,
    exact and without units: a string, or a list of {name: string}."""
    defines = e.get("defines") or []
    base = {"values": values, "domain": e.get("domain", "dc"),
            "units": False,
            "defines": defines if isinstance(defines, str)
            else "\n".join(defines)}
    if src.startswith("evaluate("):
        got = client.post("/api/evaluate", json={
            **base, "expr": e["evaluate"],
            "conditions": e.get("evaluate_conditions", [])}).get_json()
        if not got.get("ok"):
            raise ValueError(f"the app's Evaluate refuses it: "
                             f"{got.get('error')}")
        return got["plain"]
    got = client.post("/api/solveq", json={
        **base, "equations": e["solve_equations"],
        "unknowns": e.get("solve_unknowns", ""),
        "conditions": e.get("solve_conditions", []),
        "real_only": bool(e.get("solve_real_only"))}).get_json()
    if not got.get("ok"):
        raise ValueError(f"the app's Solve refuses it: {got.get('error')}")
    return [{x["name"]: x["plain"] for x in sol}
            for sol in got.get("solutions") or []]


def damage(card):
    if isinstance(card, str):
        return f"({card}) + 1"
    return [{k: f"({v}) + 1" for k, v in sol.items()} for sol in card] \
        or [{"x": "1"}]


def from_plain(text: str) -> str:
    """The app's display text back as SymPy can read it: `5.0j` is 5.0*I."""
    import re
    text = re.sub(r"(?<![\w.])(\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)j(?![\w])",
                  r"\1*I", text)
    return text.replace("∞", "oo")


def card_differs(sp, mine, theirs) -> str:
    """'' when the notebook's card answer is the app's."""
    if isinstance(theirs, str):
        ok = same(sp, mine, from_plain(theirs))
        return "" if ok else f"notebook {str(mine)[:50]}, app {theirs[:50]}"
    if len(mine) != len(theirs):
        return (f"notebook finds {len(mine)} solution(s), "
                f"the app {len(theirs)}")
    left = list(mine)
    for sol in theirs:
        for cand in left:
            if set(cand) == set(sol) and all(
                    same(sp, cand[k], from_plain(sol[k])) for k in sol):
                left.remove(cand)
                break
        else:
            return f"the app's solution {sol} is not among {mine}"
    return ""


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
         "er, port, polar, evaluate, solve, t, s)", ns)
    entries_n = answers_n = cards_n = 0
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
            # the entry's Evaluate box and Solve card, against the app's
            # own two routes, fed the values its solve just returned
            for src in bb.card_cells(e, "r"):
                cards_n += 1
                try:
                    got_mine = eval(src, ns)
                    got_app = app_card(client, e, src, got.get("values") or {})
                    if prove_red:
                        got_app = damage(got_app)
                    why = card_differs(sp, got_mine, got_app)
                except Exception as exc:                # noqa: BLE001
                    why = f"{type(exc).__name__}: " \
                          f"{str(exc).splitlines()[0][:100]}"
                if why:
                    problems.append(f"{where}: {src[:50]}... -- {why}")
        print(f"{stem}: {len(entries)} entries, "
              f"{len(problems) - before} problem(s)", flush=True)
    for p in problems:
        print("  **", p)
    print(f"{entries_n} entries, {answers_n} answers and {cards_n} "
          f"Evaluate/Solve cards compared, {len(problems)} problem(s)")
    if prove_red:
        print("prove-red:", "RED, as it should be" if problems
              else "STILL GREEN -- this check cannot fail")
        return 0 if problems else 1
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
