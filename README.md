# symbulator — Symbulator 9

**Symbulator 9** is a port of Symbulator 8 — Roberto Perez-Franco's symbolic
linear-circuit simulator for the TI-Nspire CAS — to Python and SymPy, with
minor improvements. This package is its solver core.

All of the original's analysis tools are now ported: DC, AC (phasor),
s-domain (Laplace), and transient analysis; Thevenin/Norton equivalents;
two-port parameter extraction; and the expert-mode dispatcher. See
**Scope** below for the handful of things that are intentionally
simplified relative to the calculator version, and why.

*AI coding agent?* This README is written to be read start to finish and
followed directly — the Quick start and Circuit description syntax
sections below have everything needed to write a correct circuit
description on the first try. See also [llms.txt](https://github.com/Symbulator/solver/blob/main/llms.txt) for a short
index and the three details that are easiest to get wrong.

## Install

```
pip install symbulator
```

From a checkout of the repository: `pip install -e .`

## Quick start

```python
from symbulator import dc, ac, fd, tr, th, er, port

# 5V source through a 1k/1k voltage divider
res = dc("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k")
print(res.v("2"))     # 5/2
print(res.i("r1"))    # 1/400 (2.5 mA)
print(res["p_r1"])    # power dissipated in r1

# Series RLC driven at omega = 1000 rad/s
res = ac("e1,1,0,10:r1,1,2,100:l1,2,3,0.1:c1,3,0,1e-6", omega=1000)
print(res.v("2"))
print(res["z_e1"])    # input impedance seen by the source

# Thevenin equivalent between node 2 and ground
eq = th("e1,1,0,12:r1,1,2,4'k:r2,2,0,2'k", "2", "0", domain="dc")
print(eq.vth, eq.z, eq.pmax)

# Step response of an RC circuit, in the time domain
res = tr("e1,1,0,5:r1,1,2,1000:c1,2,0,1e-6", variables=["v_2"])
print(res["v_2"])      # 5 - 5*exp(-1000*t)
```

## Circuit description syntax

Unchanged from the calculator (minus the leading `:`): elements are
separated by `:`, fields within an element by `,`. Node `0` is ground.

| Prefix | Element | Fields |
|---|---|---|
| `r` | resistor | name,n1,n2,value |
| `l` | inductor | name,n1,n2,value[,initial_current] |
| `c` | capacitor | name,n1,n2,value[,initial_voltage] |
| `e` | voltage source (indep. or dependent) | name,n1,n2,value |
| `j` | current source (indep. or dependent) | name,n1,n2,value |
| `o` | ideal op-amp (nullor) | name,n_plus,n_minus,n_out |
| `m` | mutual inductance | name,Lname1,Lname2,M |
| `s` | short circuit | name,n1,n2 |
| `t` | ideal transformer | name,n1,n2,turns1,turns2 |
| `z,y,h,g,a,b` | grounded two-port block | name,n1,n2[,[p11,p12,p21,p22]] |

The optional initial-condition field on `l`/`c` (initial inductor
current / capacitor voltage) is only meaningful for `fd()`/`tr()`; it's
ignored by `dc()`/`ac()`. Unlike the original -- which required a
different field count per element depending on which analysis tool was
running -- this port always accepts the extra field and just treats it
as 0 if omitted, regardless of which function you call.

**Dependent (controlled) sources** work "for free": a value field can be
any SymPy-parseable expression referencing other node-voltage/current
symbols (`v_<node>`, `i_<element>`), e.g. `e2,3,0,2*v_2` for a VCVS.
This mirrors how the original evaluated value strings through the
calculator's own expression engine.

**Case matters for variables, but not for names.** Element and node
names fold to lowercase (`R1` and `r1` are one resistor, node `A` is
node `a`), and so does any reference *derived from them*: `2*VR1`,
`2*vr1` and `2*v_r1` all mean r1's voltage drop, the same spelling
equivalence that makes `ir1` ≡ `i_r1` ≡ `IR1`. A variable that names
nothing in the circuit is an ordinary SymPy symbol and IS
case-sensitive: `e,a,b,c` and `e,a,b,C` are two different sources, and
a condition `c = 5` does not touch `C`. The boundary is exact: a name
folds when (and only when) its folded spelling matches an answer of
the circuit -- a `v_...`/`i_...`/`p_...` of some element or node.

**Unit shorthand:** the calculator's own `'k`/`'M`/`'u`/... syntax
(`1'k` = 1000) is always unambiguous, as is an explicit product with a
symbol (`1*k`). A *bare* suffix like `1k` could mean either one, so by
default (`suffix="ask"`) it raises `AmbiguousValueError` listing every
such value; pass `suffix="si"` to read them all as SI units, or
`suffix="var"` to read them all as number-times-variable. Use
`find_ambiguous_values(desc)` to scan a description without solving --
that's what the web front end uses to ask the user interactively.

**Two-port parameters** (`z/y/h/g/a/b`) ride in the description as an
optional last term, a four-entry list:

```python
res = dc("e1,1,0,10:y1,1,2,[0.001,-0.001,-0.001,0.001]:rl,2,0,1'k")
```

Entries may be numbers, SI-prefixed values or expressions (symbols
included); each binds the correspondingly-named variable (`y11`,
`y12`, ... for an element named `y`; `y111`, ... for one named `y1` --
the element's name prefixes the digits) through the same substitution
machinery as `conditions=`, so an explicit condition on the same name
still overrides the description. Without the term, the parameters are
free symbols of those names -- the tacit term `[y111,y112,y121,y122]`
-- matching the original's "leave them symbolic" default, and they can
be pinned via `conditions=` or the older `params` dict, which is still
accepted:

```python
params = {"y1": {"11": "0.001", "12": "-0.001", "21": "-0.001", "22": "0.001"}}
res = dc("e1,1,0,10:y1,1,2:rl,2,0,1'k", params=params)
```

Use `port()` (below) to go the other way and *extract* z/y/h/g/a/b
parameters from an actual sub-circuit.

## DC / AC / s-domain results

`dc()`, `ac()`, and `fd()` return a `Result` with:
- `res.v(node)` -- node voltage
- `res.i(name)` -- element/branch current
- `res["p_<name>"]` / `res["ap_<name>"]` -- real (average) power in watts,
  under one name or the other: `p_` in dc, and in ac with `use_rms=True`;
  `ap_` in ac under the default peak-amplitude convention. They are the same
  quantity, and **neither is apparent power** -- that is `abs(res["s_<name>"])`
- `res["s_<name>"]` -- complex power (AC only)
- `res["z_<name>"]` / `res["r_<name>"]` -- impedance / resistance seen by a source (AC / DC only)

(The power/impedance derived quantities are DC/AC-only, matching the
original -- `fd()` doesn't compute them either.)

`ac()` takes a `use_rms=True` flag to switch the power convention from
peak-amplitude phasors (default, dividing by 2) to RMS phasors, matching
the original's `userms` setting.

## Thevenin / Norton: `th()` and `er()`

```python
eq = th("e1,1,0,12:r1,1,2,4'k:r2,2,0,2'k", n1="2", n2="0", domain="dc")
eq.vth    # open-circuit (Thevenin) voltage
eq.ino    # short-circuit (Norton) current
eq.z      # Req (dc) or Zeq (ac) = vth/ino
eq.pmax   # max power transferable to a matched load
```

`th()` is for **active** circuits (ones with their own independent
sources) -- it raises if the open-circuit voltage comes out to 0, same
as the original's redirect message. For a **passive** (source-free)
network, use `er()` instead, which injects a single 1A test current and
reads the equivalent resistance/impedance directly:

```python
req = er("r1,1,2,1'k:r2,2,0,2'k", n1="1", n2="0", domain="dc")  # 3000
```

## Two-port extraction: `port()`

Extracts z/y/h/g/a/b parameters of a whole circuit between two grounded
ports (the inverse of feeding pre-defined parameters into a `z`/`y`/...
circuit *element*, described above):

```python
params = port("r1,1,3,100:r2,2,3,200:r3,3,0,50", n1="1", n2="2", kind="z", domain="dc")
params["11"], params["12"], params["21"], params["22"]
```

Works the same way in AC (pass `omega=...` and `domain="ac"`).

## s-domain and transient: `fd()` and `tr()`

**The two read their sources in different domains, and that is the whole
point of having both.** `tr()` reads a source value as a function of time;
`fd()` reads it as an expression in `s`. A value of `5` is a 5 V step to
`tr()` and a 5 V impulse to `fd()` -- different circuits, not different
notations for the same one.

```python
from symbulator import fd, tr, t2s, s2t

# Step response of an RC low-pass, starting from rest.
res_t = tr("e1,1,0,5:r1,1,2,1000:c1,2,0,1e-6")      # source in time
res_s = fd("e1,1,0,5/s:r1,1,2,1000:c1,2,0,1e-6")    # the same source, in s
res_t["v_2"]        # 5 - 5*exp(-1000*t)
res_s["v_2"]        # 5000/(s*(s + 1000))

# Natural response of a discharging inductor with an initial condition
res_t = tr("l1,0,2,0.2,3:r1,2,0,100", variables=["i_l1"])  # I0=3A, L=0.2H, R=100 ohm
res_t["i_l1"]   # 3*exp(-500*t)
```

`tr()` transforms each source for you, by what the value is:

| Source value | Read as |
|---|---|
| a function of `t` -- `u(t)`, `t`, `2*exp(-4*t)` | transformed with `t2s()` |
| a constant -- `12`, `vs` | a step of that amplitude (`value/s`) |
| already written in `s` -- `5/s` | left alone |
| a reference to another answer -- `2*i_r1` | left alone; a controlled source is a relation, not a waveform |

In `fd()` nothing is transformed, because `fd()` is the s-domain. To give
it a value written in time, wrap it in braces -- `{5}`, `{u(t)}`,
`{2*exp(-4*t)}` -- which is the calculator's shorthand for `t2s(...)` and
works only there.

`t2s()`/`s2t()` wrap SymPy's `laplace_transform`/`inverse_laplace_transform`
directly, for preparing a source value by hand or checking an answer. Both
are usable inside a circuit description, an `equations=` entry, or any
expression you hand back to the package.

`tr(desc, variables=[...])` lets you limit which answers get
inverse-Laplace-transformed -- useful since that step can be slow (or
fail to find a closed form) for complicated expressions; omit
`variables` to attempt every solved node voltage and element current.
Any individual variable that can't be transformed is silently left out
of the result rather than failing the whole call.

**History, in case you meet an older version:** releases before 0.5.5
skipped the forward transform. The original called out to a separate
`lf\\laplace` calculator library that was not in the document this was
ported from, so `tr()` inverse-transformed its answers but passed its
sources through untouched -- which made every transient result one
integration short, an impulse response where a step response was meant.
SymPy's own `laplace_transform` does the job, and 0.5.5 restored the
behaviour the calculator versions have always had. A description written
for Symbulator 7 or 8 now gives the same answer here.

## Working with the answers (SymPy)

Every answer is a SymPy expression — exact where the inputs were exact
(the quick start's `res.v("2")` really is the rational `5/2`, not the
float `2.5`) — so everything SymPy does applies to it directly:
`float()`, `simplify()`, `.subs()`, `limit()`, `plot()`, `lambdify()`.

```python
import sympy as sp
from symbulator import dc

res = dc("e1,1,0,vin:r1,1,2,ra:r2,2,0,rb")
sp.simplify(res.v("2"))          # rb*vin/(ra + rb)
```

**The one trap: the time symbol carries an assumption.** Time-domain
answers are written in `Symbol("t", nonnegative=True)`. To SymPy, a
bare `Symbol("t")` is a *different* symbol — same name, different
assumptions — so substituting with one does nothing, and does it
silently:

```python
from symbulator import tr, fd, t, s   # <- the package's own t and s

res = tr("e1,1,0,5:r1,1,2,1000:c1,2,0,1e-6")
res["v_2"]                            # 5.0 - 5.0*exp(-1000.0*t)

res["v_2"].subs(sp.Symbol("t"), 0.001)   # unchanged — silently a no-op
res["v_2"].subs(t, 0.001)                # 3.16060...
res.at("v_2", t=0.001)                   # 3.16060... — same, by name
```

Two escapes, either fine: `res.at(...)` substitutes **by name**, so it
can never miss (`res.at(t=0.001)` with no key returns a whole new
`Result` evaluated at that instant); or import the package's own `t`
and `s` symbols and use them wherever an expression leaves the package.
Everything below uses the imported symbols.

**Initial and final values** are a substitution and a limit:

```python
res["v_2"].subs(t, 0)                # 0 — starts from rest
sp.limit(res["v_2"], t, sp.oo)       # 5 — settles at the source voltage

# Same check on the s-domain answer, by the final-value theorem:
resf = fd("e1,1,0,5/s:r1,1,2,1000:c1,2,0,1e-6")
sp.limit(s * resf["v_2"], s, 0)      # 5
```

**Plotting a transient** works with SymPy's own `plot` — with the
imported `t`, not a fresh `Symbol("t")`, or the curve comes out
constant:

```python
sp.plot(res["v_2"], (t, 0, 0.005))
```

For anything beyond a quick look, `lambdify` turns an answer into a
plain numeric function for NumPy/Matplotlib:

```python
import numpy as np
f = sp.lambdify(t, res["v_2"], "numpy")
f(np.linspace(0, 0.005, 400))        # ready to plot, fit, export...
```

**Frequency response needs `bode_samples()`, not `plot()`.** An `ac()`
result is a phasor at one fixed `omega` — there is nothing in it to
sweep — so the package samples the frequency axis for you, returning
`(freq_hz, mag_db, phase_deg)` ready for any plotting library:

```python
from symbulator import bode_samples, time_samples

freq, mag_db, phase = bode_samples(
    "e1,1,0,1:r1,1,2,1000:c1,2,0,1e-6", "v_2", 10, 100_000)
```

Its twin `time_samples(desc, key, t_max)` samples a transient
numerically — useful when the inverse Laplace transform of a
complicated answer has no closed form and `tr()` leaves that variable
out: the sampler sidesteps the symbolic inversion entirely.

**A sign note on `pf()` at a source.** The package's convention is that
`v_*`/`i_*` describe power *consumed* by an element, which is the wrong
way round for reading a source's power factor as leading/lagging —
negate the current first:

```python
from symbulator import ac, pf

res = ac("e,1,0,30:r1,1,2,6:r2,2,0,-2j:r3,2,0,4",
         omega=sp.Symbol("omega"), use_rms=True)
pf(res["v_e"], -res["i_e"])          # pf: 0.97342 leading   — correct
pf(res["v_e"],  res["i_e"])          # pf: 0.97342 lagging   — backwards
```

`pf()` takes raw values and cannot know whether they came from a source
or a load, so it cannot do the flip for you (the calculator's version
special-cased element *names* and could).

## Expert mode: `ex()`

A single dispatcher over `dc`/`ac`/`fd`/`tr`, for callers that want to
pick the analysis type dynamically rather than calling a specific
function -- ports `ex()`. On the calculator this interactively asked
"1:DC 2:AC 3:FD 4:TR"; as a library there's no prompt to answer, so
`domain` is just a normal argument (the word, or the calculator's own
1-4 shorthand):

```python
ex("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k", domain="dc")
ex("e1,1,0,5:r1,1,0,100", domain="ac", omega=1000)   # omega required for ac
ex("l1,0,2,0.2,3:r1,2,0,100", domain="tr", variables=["i_l1"])
```

Expert mode's "Add equations / Add unknowns / Add conditions" prompts
are ported as keyword arguments, available on `ex()` and on
`dc`/`ac`/`fd`/`tr` directly:

```python
# Design problem: what r_b makes the divider output exactly 6 V?
res = dc("e1,1,0,12:r1,1,2,4'k:r2,2,0,r_b",
         equations=["v_2 = 6"], unknowns=["r_b"])
res.values["r_b"]        # 4000

# Derived quantity: a new symbol in an equation is auto-added as an
# unknown, so no unknowns list is needed for this style.
res = dc("e1,1,0,12:r1,1,2,4'k:r2,2,0,2'k", equations=["pout = v_2*i_r2"])

# Conditions -- the TI's "|" (with) operator: substitutions applied to
# the whole system at solve time.
res = dc("e1,1,0,vin:r1,1,2,r_a:r2,2,0,r_b",
         conditions=["vin = 12", "r_a = 4'k", "r_b = 2'k"])
```

Extra equations run through the same unit-prefix expander as circuit
values (so `6*4'k` works), and accept either `lhs = rhs` strings or a
bare expression (treated as `expr = 0`). A symbolic *component value*
you want solved (like `r_b` above) must be listed in `unknowns` -- the
solver otherwise treats it as a fixed parameter, matching the
original's separate "Add unknowns" prompt.

**If a solve leaves some values symbolic instead of resolving to plain
numbers**, the usual cause is one fewer independent equation than
unknowns: count the symbols in `unknowns=` and make sure there's a
matching equation for each, using every given/measured fact from the
problem rather than only the ones that seem to describe the unknown
you're focused on. A resistor's own equation (`V = R * I`) is nonlinear
once both are unknown, which can occasionally make a fully-specified
system harder to resolve symbolically in one call than the equation
count alone would suggest; if that happens, solving the unknowns by
hand from the given facts and then re-running the circuit with plain
numbers is a reliable fallback.

## Scope: what's simplified vs. the calculator version

- **`pf()`** is ported as a simplified, explicit-argument version (pass
  a voltage and current phasor directly); the original's implicit
  per-element-type sign convention, driven by reading calculator
  variables like `v<name>`/`i<name>` automatically, wasn't replicated.
- **`fd()`** requires s-domain source values, as the calculator's does;
  the `{...}` shorthand converts a time-domain one where you write it.
  `tr()` reads its sources in the time domain, also as the calculator's
  does. (Before 0.5.5 neither transformed anything -- see above.)
- **No interactive prompts anywhere** -- everything the calculator asked
  for via `RequestStr` (analysis type, which answers to save, expert-mode
  custom equations, two-port parameter values, etc.) is a plain function
  argument here instead.
- **No `Disp` progress narration** -- the calculator printed step-by-step
  status messages during a simulation; this port just returns the
  answer.

## Tests

```
pytest symbulator/tests/ -v
```

48 tests across six files:
- `test_circuits.py` (21): DC/AC voltage & current dividers, series RLC
  impedance, inverting/non-inverting op-amp gain, a voltage-controlled
  voltage source, an ideal transformer, mutual inductance (with and
  without coupling), a two-port block, derived power quantities,
  zero-valued-capacitor handling, and parser error handling.
- `test_equiv.py` (9): Thevenin voltage/impedance and its cross-check
  against directly solving with a load attached, `er()` on series/parallel
  passive networks, `port()` z/y/a-parameter extraction (including a
  z·y matrix-inverse consistency check and an a-parameter round trip
  through the Phase 1 two-port element), and an AC two-port case.
- `test_laplace.py` (5): `t2s`/`s2t` round trips, an RC step response
  checked numerically against the closed-form exponential, an RL natural
  response with a nonzero initial condition checked against its
  closed-form solution, and zero-valued-capacitor handling carried
  into `fd()`.
- `test_dispatch.py` (6): `ex()` dispatch to each of the four analysis
  modes, its numeric-shorthand domain aliases, and its error handling.
- `test_expert.py` (7): expert-mode extras -- solving for a symbolic
  component via an added equation + unknown, auto-added derived-quantity
  symbols, unit shorthand inside added equations, conditions as
  solve-time substitutions, and error handling.
