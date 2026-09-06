"""Build and execute `the_monograph.ipynb` from the eight exemplar
circuits of *The Internal Logic of Symbulator* (#316).

The circuits are not copied here: they are read by name from the app's
`The_Monograph.cir`, the same file the app's Built-in Examples menu and
the monograph's Appendix B are generated from, so the notebook cannot
drift from either. The layout this expects is the project's --
`repos/solver` and `repos/server` side by side -- and it stops with a
message naming that layout when the file is not there.

Run from the solver repo root, with an interpreter that has nbformat,
nbclient, ipykernel and matplotlib (`pip install symbulator[notebook]
nbclient`):

    python notebooks/build_monograph.py

The notebook is written with its outputs, so GitHub and nbviewer show
the answers without a kernel.
"""

from __future__ import annotations

import os
import sys

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook
from nbclient import NotebookClient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(os.path.dirname(ROOT), "server")
CIR = os.path.join(SERVER, "examples", "The_Monograph.cir")

if not os.path.exists(CIR):
    raise SystemExit(
        f"Cannot find {CIR}.\nThis script expects the project's layout, "
        "repos/solver beside repos/server, and reads the exemplar "
        "circuits from the server's examples folder.")

sys.path.insert(0, ROOT)
sys.path.insert(0, SERVER)
from circuitbook import parse_book                      # noqa: E402

entries, _, _ = parse_book(open(CIR, encoding="utf-8").read())
BY_NAME = {e["name"]: e for e in entries}


def desc(name: str) -> str:
    return BY_NAME[name]["desc"].strip()


def note(name: str) -> str:
    n = BY_NAME[name]["note"]
    return "\n\n".join(n) if isinstance(n, list) else n


md, code = new_markdown_cell, new_code_cell


def circuit_cell(var: str, name: str) -> str:
    """A cell that binds the circuit text to `var` and draws it."""
    return f"{var} = '''\n{desc(name)}\n'''\ndraw({var})"


cells = [
md("""# The exemplars from *The Internal Logic of Symbulator*

The monograph [*The Internal Logic of Symbulator*](https://learn.symbulator.com/monograph.pdf) closes with seven exemplar circuits, chosen because each shows one thing the simulator is for. This notebook runs all of them, as the eight entries the app's Built-in Examples menu lists under *The Monograph* -- the circuits below are read from that same file, not retyped.

Each section gives the problem as the monograph poses it, draws the circuit, solves it, and reads the answer the text discusses. Run the cells in order; nothing here takes more than a few seconds.

If you are on Colab, run the first cell."""),
code("# !pip install symbulator matplotlib"),
code("""import sympy as sp
from symbulator import dc, ac, fd, tr, th, draw, polar, bode_samples, t, s
import symbulator
symbulator.__version__"""),

# 1 ---------------------------------------------------------------
md("""## 1. The two-stage amplifier of 1999

""" + note("The two-stage amplifier of 1999") + """

The analysis is in the s-domain, so `fd()`: every node voltage comes back as a function of `s`. A bare `a` would show all of them; the output node is enough here."""),
code(circuit_cell("amp", "The two-stage amplifier of 1999")),
code("""a = fd(amp)
a["v4"]"""),
md("The gain is the ratio of two of those answers. The source is the symbol `vg`, and it cancels:"),
code("""gain = sp.simplify(a["v4"] / a["v5"])
gain"""),
md("""For the Bode diagram Prof. Yee asked for, `bode_samples()` sweeps the frequency and returns arrays ready for Matplotlib. With `vg` set to 1 by a condition, `v_4` *is* the transfer function."""),
code("""import matplotlib.pyplot as plt

freq, mag_db, phase = bode_samples(amp, "v_4", 1.6e3, 1.6e12, n=400, conditions=["vg = 1"])
fig, (a1, a2) = plt.subplots(2, 1, sharex=True, figsize=(6.5, 4.5))
a1.semilogx(freq, mag_db); a1.set_ylabel("gain, dB"); a1.grid(True, which="both")
a2.semilogx(freq, phase);  a2.set_ylabel("phase, degrees"); a2.set_xlabel("Hz"); a2.grid(True, which="both")
a1.set_title("The 1999 amplifier: v4 / vg")
plt.tight_layout()"""),

# 2 ---------------------------------------------------------------
md("""## 2. One of each: the 2013 showcase

""" + note("One of each: the 2013 showcase") + """

This is Expert Mode: two equations about *power*, two unknowns that are the sources' own values, and two conditions that choose among the roots. Inside an equation the names are written the way the app writes them, `pjd1` for the power in `jd1`."""),
code(circuit_cell("showcase", "One of each: the 2013 showcase")),
code("""sc = dc(showcase,
        unknowns=["es", "js"],
        equations=["pjd1 = -80", "ped2 = 0"],
        conditions=["es > 0", "js > 0"])
sc.rounded(4)"""),
md("The three answers the text names, and the current the constraint drove to zero:"),
code("""sc.rounded(4)["es"], sc.rounded(4)["js"], sc["ir5"]"""),
md("Without the two conditions the system has more than one solution. `Result.solutions` holds all of them; `.values` is the first."),
code("""every = dc(showcase, unknowns=["es", "js"], equations=["pjd1 = -80", "ped2 = 0"])
len(every.solutions), [ (sp.N(sol["es"], 4), sp.N(sol["js"], 4)) for sol in every.solutions ]"""),

# 3 ---------------------------------------------------------------
md("""## 3. Prof. Boulet's switching transient

""" + note("Prof. Boulet's switching transient (DC, t < 0)") + """

Two entries, two analyses. First the DC state before the switch."""),
code(circuit_cell("before", "Prof. Boulet's switching transient (DC, t < 0)")),
code("""b0 = dc(before)
b0["va"], b0["vb"]"""),
md(note("Prof. Boulet's switching transient (TR, t > 0)") + """

The circuit after the switch carries those two voltages as the capacitors' initial conditions, the fifth field on each `c` line. This one uses the cell magic: after `%load_ext symbulator`, a cell headed `%%tr` is a circuit, drawn and solved, with the options on the first line."""),
code("%load_ext symbulator"),
code("%%tr variables=v_a into=after\n" + desc("Prof. Boulet's switching transient (TR, t > 0)")),
md("The initial value checks out, and the response is easy to plot with the package's own `t`:"),
code("""after.at("va", t=0)"""),
code("""sp.plot(after["va"], (t, 0, 10), xlabel="t (s)", ylabel="va (V)", title="Prof. Boulet's problem: v1(t) for t > 0");"""),

# 4 ---------------------------------------------------------------
md("""## 4. Three-phase wye-delta with line impedances

""" + note("Three-phase wye-delta with line impedances") + """

An AC solve at a symbolic `omega` (the impedances are given as numbers already, so the frequency never enters). `polar()` is the app's `aa` mini-tool."""),
code(circuit_cell("wye_delta", "Three-phase wye-delta with line impedances")),
code("""tp = ac(wye_delta, omega="omega")
tp.rounded(4)"""),
md("The line current the text reads, and the load's line voltage from two node voltages:"),
code("""polar(tp["iraa"])"""),
code("""polar(tp["vna2"] - tp["vnb2"], digits=5)"""),

# 5 ---------------------------------------------------------------
md("""## 5. The ideal transformer, symbolically

""" + note("The ideal transformer, symbolically") + """

`th()` takes the circuit and the two nodes of the port."""),
code(circuit_cell("xfmr", "The ideal transformer, symbolically")),
code("""th(xfmr, "2", "0", domain="dc")"""),

# 6 ---------------------------------------------------------------
md("""## 6. Coupled coils with initial conditions

""" + note("Coupled coils with initial conditions") + """

The `m` line couples the two inductors; the fourth field on `l1` is its initial current."""),
code(circuit_cell("coils", "Coupled coils with initial conditions")),
code("""cc = tr(coils)
cc"""),
code("""sp.plot(cc["il1"], cc["il2"], (t, 0, 5), xlabel="t (s)", ylabel="A", legend=True,
        title="il1: the sum of the modes; il2: their difference");"""),

# 7 ---------------------------------------------------------------
md("""## 7. Two op-amps, all symbols

""" + note("Two op-amps, all symbols") + """

The resistors are written as reciprocals of conductances, so the gain comes out in the conductances. `vs` is the source's *value*, a symbol, not an answer, so the gain divides by the symbol itself."""),
code(circuit_cell("opamps", "Two op-amps, all symbols")),
code("""oa = dc(opamps)
vs = sp.Symbol("vs")
sp.simplify(oa["vo"] / vs)"""),

md("""## Where next

* The monograph itself: https://learn.symbulator.com/monograph.pdf
* The same eight entries in the app, under *Built-in Examples › The Monograph*: https://symbulator.pythonanywhere.com/?input=monograph
* The package's quick start for notebooks, `quickstart.ipynb`, beside this file, and the README's *In a notebook* section."""),
]

nb = new_notebook(cells=cells)
nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3",
                             "language": "python"}
nb.metadata["language_info"] = {"name": "python"}
NotebookClient(nb, timeout=600, kernel_name="python3",
               allow_errors=False, resources={"metadata": {"path": ROOT}}).execute()
for c in nb.cells:
    if c.cell_type == "code":
        c.metadata.pop("execution", None)
out = os.path.join(HERE, "the_monograph.ipynb")
nbformat.write(nb, out)
n_code = sum(1 for c in nb.cells if c.cell_type == "code")
print(f"wrote {out}: {len(nb.cells)} cells, {n_code} code cells, all executed")
