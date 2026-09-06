"""Cell magics for Jupyter (#315): `%load_ext symbulator`, then

    %%dc
    e1,1,0,5
    r1,1,2,1'k
    r2,2,0,1'k

draws the circuit and shows the answers, the way the app's Input File
card and Results card do. `%%ac`, `%%fd` and `%%tr` are the other three
analyses. Options go on the magic's own line, `name=value` or a bare
flag, and are passed to the analysis function:

    %%ac omega=1000 rms          `omega` is required for ac; `rms` is use_rms
    %%tr variables=v_2,i_r1      the answers to invert (all, by default)
    %%dc into=res                also bind the Result to the name `res`
    %%dc nodraw                  answers only, no drawing

Every value is parsed as Python where it can be (`omega=1000`; a symbol
name such as `omega=w` is kept as text); a comma-separated value becomes
a list. This module imports IPython only when the extension is loaded,
so the package itself never depends on it.
"""

from __future__ import annotations

import ast
from typing import Any, Dict


def _literal(text: str):
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def _parse_options(line: str) -> Dict[str, Any]:
    """`omega=1000 rms variables=v_2,i_r1` -> the keyword arguments."""
    opts: Dict[str, Any] = {}
    for tok in line.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            if "," in v:
                opts[k] = [_literal(x) for x in v.split(",") if x]
            else:
                opts[k] = _literal(v)
        else:
            opts[tok] = True
    return opts


def run(domain: str, line: str, cell: str, user_ns: dict = None):
    """What a `%%dc` cell does: draw the circuit, run the analysis,
    return the Result (which the notebook then displays)."""
    from . import dc, ac, fd, tr, draw

    opts = _parse_options(line)
    into = opts.pop("into", None)
    nodraw = opts.pop("nodraw", False)
    if opts.pop("rms", False):
        opts["use_rms"] = True
    for key in ("variables", "equations", "unknowns", "conditions"):
        if key in opts and not isinstance(opts[key], list):
            opts[key] = [opts[key]]

    desc = cell.strip()
    if not nodraw:
        from IPython.display import display
        display(draw(desc))
    fn = {"dc": dc, "ac": ac, "fd": fd, "tr": tr}[domain]
    res = fn(desc, **opts)
    if into and user_ns is not None:
        user_ns[into] = res
    return res


def load_ipython_extension(ipython):
    for domain in ("dc", "ac", "fd", "tr"):
        def magic(line, cell, _d=domain):
            return run(_d, line, cell, ipython.user_ns)
        magic.__name__ = domain
        magic.__doc__ = (f"{domain} analysis of the circuit in the cell; "
                         "see symbulator.notebook for the options.")
        ipython.register_magic_function(magic, "cell", domain)
