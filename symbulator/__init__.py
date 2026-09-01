"""
symbulator: a Python/SymPy port of Roberto Perez-Franco's "Symbulator 8"
TI-Nspire CX II CAS circuit simulator.

The port is complete: every analysis and tool the calculator offered is
here, working on linear circuits built from resistors, inductors,
capacitors, independent and dependent sources, ideal op-amps, mutual
inductance, ideal transformers, short circuits, and grounded two-port
(z/y/h/g/a/b) blocks.

    dc(), ac()          DC and AC (phasor) analysis
    fd()                s-domain (Laplace) analysis
    tr()                transient analysis, via fd() and an inverse transform
    t2s(), s2t()        Laplace transform and its inverse
    th(), er()          Thevenin/Norton equivalent, equivalent impedance
    port()              two-port parameter extraction
    ex()                expert mode: choose the analysis at run time, and
                        add your own equations, unknowns and conditions
    pr(), pf(), gain()  power, power factor, transfer functions
    time_samples()      numeric (t, y) samples of a tr() result, for plotting
    bode_samples()      numeric (freq, mag_dB, phase_deg) samples of a fd()
                        result across a frequency sweep, for a Bode plot
    to_spice(),         translate a circuit description to a SPICE netlist
    from_spice()        and read the linear subset of one back

Time-domain answers are written in `symbulator.t` (a SymPy symbol declared
nonnegative, which the Laplace transform pair needs) and s-domain ones in
`symbulator.s`. Use those, or `Result.at(t=0.001)`, to substitute -- a
freshly made `Symbol("t")` is a different symbol and will not match.

Answers are symbolic. Component values may be numbers, SI-prefixed
values (`4.7'k`), or symbols -- give a resistance as `x` and the answers
come back in terms of `x`.
"""

from .analysis import dc, ac, fd
from .si_prefix import AmbiguousValueError, UnsafeExpressionError
from .elements import find_ambiguous_values
from .utils import pr, pf, gain
from .equiv import th, er, port
from .laplace import tr, t2s, s2t, t, s
from .dispatch import ex
from .plotting import time_samples, bode_samples, PlotError
from .schematic import to_svg, draw
from .spice import to_spice, from_spice

#: The single source of truth for the version: pyproject.toml reads this
#: attribute at build time, so the two cannot disagree.
__version__ = "0.5.26"

__all__ = ["dc", "ac", "fd", "tr", "t2s", "s2t", "t", "s", "pr", "pf", "gain", "th", "er", "port", "ex",
           "time_samples", "bode_samples", "PlotError",
           "to_svg", "draw", "to_spice", "from_spice",
           "AmbiguousValueError", "UnsafeExpressionError", "find_ambiguous_values", "__version__"]
