"""Build and execute one notebook per built-in example book.

PILOT. The circuits are not copied here: every entry is read from the
app's own `repos/server/examples/<Book>.cir`, the file the app's Built-in
Examples menu lists, so a notebook cannot drift from the app. Each entry
becomes its problem statement (the entry's `note:` and picture), the
circuit drawn, and the run the entry describes, written as the package
call a person would type: `dc`, `ac`, `fd`, `tr`, `th`, `er` or `port`,
with the entry's Expert Mode lines, rounding and plot.

What an entry asks of the app's Evaluate box and Solve card becomes the
package's `evaluate()` and `solve()` on the run's result; a Define line
becomes a condition; the Thevenin tool's load answers, `irl`, `vrl` and
`prl`, are names `evaluate()` knows on a `th()` result.

Run from the solver repo root, with nbformat, nbclient, ipykernel and
matplotlib installed (`pip install symbulator[notebook] nbclient`):

    python notebooks/build_books.py Showcase Lesson_13
    python notebooks/build_books.py --all

A notebook is written with its outputs, and only if every cell ran.
"""

from __future__ import annotations

import glob
import os
import re
import sys

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook
from nbclient import NotebookClient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(os.path.dirname(ROOT), "server")
EXAMPLES = os.path.join(SERVER, "examples")
OUT = os.path.join(HERE, "books")

if not os.path.isdir(EXAMPLES):
    raise SystemExit(
        f"Cannot find {EXAMPLES}.\nThis script expects the project's "
        "layout, repos/solver beside repos/server, and reads the circuits "
        "from the server's examples folder.")

sys.path.insert(0, SERVER)
sys.path.insert(0, ROOT)            # this tree's symbulator, not pip's
from circuitbook import parse_book                      # noqa: E402
import symbulator_ui as ui                              # noqa: E402

md, code = new_markdown_cell, new_code_cell


def own_kernel() -> str:
    """A kernelspec for the interpreter running this script.

    The machine's `python3` kernelspec may point anywhere -- on Roberto's
    laptop it named a virtualenv in Temp that was long gone, and the kernel
    died before replying. The notebook should run on the interpreter that
    has this working tree's symbulator, which is this one."""
    import json
    import tempfile

    base = tempfile.mkdtemp(prefix="symbulator_kernel_")
    spec = os.path.join(base, "kernels", "symbulator_build")
    os.makedirs(spec)
    with open(os.path.join(spec, "kernel.json"), "w", encoding="utf-8") as fh:
        json.dump({"argv": [sys.executable, "-m", "ipykernel_launcher",
                            "-f", "{connection_file}"],
                   "display_name": "Python 3", "language": "python"}, fh)
    os.environ["JUPYTER_PATH"] = base
    return "symbulator_build"


KERNEL = own_kernel()

# Entries whose chapter teaches them *as* a failure: the run is shown with
# the message it gives. Named one by one -- any other entry that raises
# stops the build, which is the point of executing the notebooks.
MEANT_TO_FAIL = {"Bo2's Example 3.11 (Tricky, as it comes)"}


def as_list(value) -> list:
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split("\n") if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def extras(e: dict) -> list:
    """The Expert Mode keyword arguments, as source text.

    The app reads these boxes more generously than the package does: one
    line may hold `a and b`, the unknowns come as one comma list, and an
    answer may be named without its underscore (`re`, which SymPy would
    read as the real-part function). So they go through the app's own
    `prepare_inputs`, and the notebook shows the underscored names the
    package's README asks for inside an expression."""
    eqs = ui._expand_and(as_list(e.get("equations")))
    # A Define line is the calculator's `Define x=3`: the name stands for
    # its value everywhere. The package's form of that is a condition, the
    # `|` operator, which substitutes through the whole system.
    conds = (ui._expand_and(as_list(e.get("conditions")))
             + as_list(e.get("defines")))
    unks = [u for line in as_list(e.get("unknowns"))
            for u in re.split(r"\s*,\s*", line) if u]
    _d, eqs, unks, conds, _ev, _n = ui.prepare_inputs(
        e["desc"].replace("\n", ":"), eqs, unks, conds)
    out = []
    for field, items in (("equations", eqs), ("unknowns", unks),
                         ("conditions", conds)):
        if items:
            out.append(f"{field}={list(items)!r}")
    return out


def omega_arg(e: dict) -> str:
    """omega as source text: a number as typed, a symbol as a string, and
    an expression such as `2*pi*2e3` as SymPy, which is how the app reads
    the box."""
    w = str(e.get("omega") or "omega").strip()
    if is_number(w):
        return w
    if re.fullmatch(r"[A-Za-z_]\w*", w):
        return repr(w)
    return f"sp.sympify({w!r})"


def run_cell(e: dict, c: str, r: str) -> str:
    """The cell that runs entry `e` on the circuit bound to `c`."""
    domain, tool = e["domain"], e.get("tool") or ""
    args = [c]
    if tool:
        args += [repr(e["n1"]), repr(e["n2"])]
        if tool == "port":
            args.append(repr(e["kind"]))
        args.append(f"domain={domain!r}")
        if domain == "ac":
            args.append(f"omega={omega_arg(e)}")
            if e.get("rms") and tool == "th":
                args.append("use_rms=True")
        fn = tool
    else:
        if domain == "ac":
            args.append(f"omega={omega_arg(e)}")
            if e.get("rms"):
                args.append("use_rms=True")
        if domain == "tr" and e.get("vars"):
            # As the app reads the box: `vc` finds `v_c`, and an element's
            # voltage becomes the node voltages it spans, since tr()
            # answers in node voltages and currents and skips any other
            # name without a word.
            from symbulator.elements import parse_circuit
            typed = [v for v in re.split(r"[,\s]+", e["vars"]) if v]
            names, _unknown = ui._wanted_solver_keys(
                typed, parse_circuit(e["desc"].replace("\n", ":")))
            args.append(f"variables={names!r}")
        fn = domain
    args += extras(e)
    if e["name"] in MEANT_TO_FAIL:
        return (f"try:\n    {fn}({', '.join(args)})\n"
                "except Exception as exc:\n    print(exc)")
    lines = [f"{r} = {fn}({', '.join(args)})"]
    digits = str(e.get("rounding") or "")
    if digits.isdigit() and not tool:
        lines.append(f"{r}.rounded({digits})")
    else:
        lines.append(r)
    return "\n".join(lines)


def plot_cell(e: dict, c: str) -> str | None:
    kind = e.get("plottool")
    if kind not in ("plot_time", "time", "bode"):
        return None
    from symbulator.elements import parse_circuit
    elements = parse_circuit(e["desc"].replace("\n", ":"))
    key = ui._resolve_name(e["plotkey"], elements)
    lo, hi = e["plotmin"], e["plotmax"]
    n = e.get("plotpoints") or "300"
    more = "".join(", " + x for x in extras(e))
    pair = ui._voltage_drop_nodes(key, elements) if kind != "bode" else None
    if pair:
        # An element's voltage in time: tr() answers in node voltages, so
        # the drop is the difference of the two the element spans.
        wanted = [k for k in pair if k]
        drop = " - ".join(f"d[{k!r}]" if k else "0" for k in pair)
        return (f"d = tr({c}, variables={wanted!r}{more})\n"
                f"sp.plot({drop}, (t, {lo}, {hi}), xlabel='t (s)', "
                f"ylabel={key!r});")
    if kind == "bode":
        return (f"f, mag, phase = bode_samples({c}, {key!r}, {lo}, {hi}, "
                f"n={n}{more})\n"
                "fig, (a1, a2) = plt.subplots(2, 1, sharex=True, "
                "figsize=(6.5, 4.5))\n"
                "a1.semilogx(f, mag); a1.set_ylabel('dB'); "
                "a1.grid(True, which='both')\n"
                "a2.semilogx(f, phase); a2.set_ylabel('degrees'); "
                "a2.set_xlabel('Hz'); a2.grid(True, which='both')\n"
                f"a1.set_title({key!r}); plt.tight_layout()")
    return (f"ts, ys = time_samples({c}, {key!r}, {hi}, t_min={lo}, "
            f"n={n}{more})\n"
            "plt.figure(figsize=(6.5, 3))\n"
            f"plt.plot(ts, ys); plt.xlabel('t (s)'); plt.ylabel({key!r}); "
            "plt.grid(True); plt.tight_layout()")


def drawable(desc: str) -> bool:
    """Whether the package's draw() takes this circuit as typed. A network
    with no node 0 -- a two-port whose ports are `[top,bottom]` pairs --
    is solved by port() but refused by draw(); the app draws it with the
    tool's own reference as the rail, which draw() cannot be told."""
    sys.path.insert(0, ROOT)
    from symbulator import draw
    try:
        draw(desc)
        return True
    except Exception:                                   # noqa: BLE001
        return False


def entry_cells(i: int, e: dict) -> list:
    c, r = f"c{i}", f"r{i}"
    text = [f"## {i}. {e['name']}", *as_list(e.get("note"))]
    image = (e.get("image") or "").split(" [")[0].strip()
    if image:
        text.append(f"![The circuit of {e['name']}]({image})")
    cells = [md("\n\n".join(text)),
             code(f"{c} = '''\n{e['desc'].strip()}\n'''"
                  + (f"\ndraw({c})" if drawable(e["desc"]) else "")),
             code(run_cell(e, c, r))]
    plot = plot_cell(e, c)
    if plot:
        cells.append(code(plot))
    if e["name"] not in MEANT_TO_FAIL:
        cells += [code(src) for src in card_cells(e, r)]
    return cells


def answers_arg(e: dict, r: str) -> str:
    """What evaluate() and solve() are handed: the run's result, or for
    er(), which returns one expression, that expression under the name
    the app's card gives it."""
    if (e.get("tool") or "") == "er":
        return "{%r: %s}" % ("req" if e["domain"] == "dc" else "zeq", r)
    return r


def card_cells(e: dict, r: str) -> list:
    """The entry's Evaluate box and Solve card, as the package's calls."""
    out = []
    rms = ", use_rms=True" if e.get("rms") and e.get("tool") == "th" else ""
    if e.get("evaluate"):
        conds = as_list(e.get("evaluate_conditions"))
        out.append(f"evaluate({answers_arg(e, r)}, {e['evaluate']!r}"
                   + (f", conditions={conds!r}" if conds else "")
                   + rms + ")")
    if e.get("solve_equations"):
        unks = [u for line in as_list(e.get("solve_unknowns"))
                for u in re.split(r"\s*,\s*", line) if u]
        conds = as_list(e.get("solve_conditions"))
        out.append(f"solve({answers_arg(e, r)}, "
                   f"{as_list(e['solve_equations'])!r}"
                   + (f", {unks!r}" if unks else "")
                   + (f", conditions={conds!r}" if conds else "")
                   + (", real_only=True" if e.get("solve_real_only") else "")
                   + rms + ")")
    return out


#: The first cell of every notebook here. On Google Colab the package is not
#: installed, and a commented-out `pip` line -- what these opened with -- does
#: nothing there, so the imports below failed on the first run. Installed only
#: where Colab is, so a local session (which has it from `pip install
#: symbulator[notebook]`) is left alone and nothing is downloaded twice.
INSTALL_CELL = (
    "# On Google Colab the package is not installed yet; install it there.\n"
    "# A local Jupyter already has it: pip install symbulator[notebook]\n"
    "import sys\n"
    "if \"google.colab\" in sys.modules:\n"
    "    %pip install -q symbulator matplotlib")


def setup_cells() -> list:
    """The install cell and the imports every notebook here opens with."""
    return [
        code(INSTALL_CELL),
        code("import sympy as sp\nimport matplotlib.pyplot as plt\n"
             "from symbulator import (dc, ac, fd, tr, th, er, port, draw, "
             "polar,\n                        evaluate, solve, bode_samples, "
             "time_samples, t, s)\nimport symbulator\n"
             "symbulator.__version__"),
    ]


def execute_and_write(cells: list, stem: str) -> str:
    """Run `cells` on this interpreter's kernel and write them, outputs
    and all, to notebooks/books/<stem>.ipynb -- and only if every cell
    ran. Returns the path."""
    nb = new_notebook(cells=cells)
    nb.metadata["kernelspec"] = {"name": "python3", "language": "python",
                                 "display_name": "Python 3"}
    nb.metadata["language_info"] = {"name": "python"}
    # nbformat gives every cell a random id, so an unchanged notebook
    # rebuilt came out different in every cell and git could not say what
    # had really changed. Position is a stable id: a rebuild that changes
    # nothing now changes nothing.
    for n, cell in enumerate(nb.cells, 1):
        cell["id"] = f"cell-{n:04d}"
    NotebookClient(nb, timeout=900, kernel_name=KERNEL, allow_errors=False,
                   resources={"metadata": {"path": ROOT}}).execute()
    for cell in nb.cells:
        if cell.cell_type == "code":
            cell.metadata.pop("execution", None)
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, stem + ".ipynb")
    nbformat.write(nb, out)
    return out


def build(stem: str) -> str:
    path = os.path.join(EXAMPLES, stem + ".cir")
    entries, _warnings, title = parse_book(open(path, encoding="utf-8").read())
    cells = [
        md(f"# {title}\n\nThe {len(entries)} entries the Symbulator app "
           f"lists under *Built-in Examples › {title}*, each run with the "
           "`symbulator` package. The circuits are read from the app's own "
           "file, not retyped. The same entries open in the app at "
           "https://symbulator.pythonanywhere.com/\n\n"
           "If you are on Colab, run the first cell."),
    ] + setup_cells()
    for i, e in enumerate(entries, 1):
        cells += entry_cells(i, e)
    execute_and_write(cells, stem)
    return f"{stem}: {len(entries)} entries, {len(cells)} cells, all executed"


def main() -> int:
    stems = sys.argv[1:]
    if stems == ["--all"]:
        stems = sorted(os.path.splitext(os.path.basename(p))[0]
                       for p in glob.glob(os.path.join(EXAMPLES, "*.cir")))
    if not stems:
        raise SystemExit(__doc__)
    failed = 0
    for stem in stems:
        try:
            print(build(stem))
        except Exception as exc:                        # noqa: BLE001
            failed += 1
            last = str(exc).strip().splitlines()[-1:] or [repr(exc)]
            print(f"{stem}: FAILED -- {last[0]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
