"""Ground truth for the SPICE exporter, against an independent simulator.

These tests run the exporter's netlists through `ahkab`, a pure-Python
SPICE-style simulator that implements MNA independently of this package,
and compare every node voltage against `dc()`. They are the only tests
here that can catch a sign-convention error the round-trip tests cannot:
a flip encoded symmetrically into to_spice and from_spice cancels in a
round trip and is invisible to it.

ahkab (0.18, unmaintained) needs three shims on a current toolchain --
the removed `imp` module, scipy's relocated window functions, and its
numpy-2-incompatible op report printers. If it cannot be imported and
patched, the whole module skips: the assertions are extras on top of
test_spice.py, not a dependency of the package.

One documented quirk, verified directly: ahkab's H (CCVS) senses with
the OPPOSITE sign to its own F (CCCS) and to the ngspice manual, which
defines the two identically ("positive controlling current flows from
the positive node, through the source, to the negative node"). The
loader below flips H gains to express the manual's semantics in ahkab's
dialect; the netlists under test target the manual, which is what
ngspice, LTspice and PSpice implement.
"""
import pytest

try:
    import sys
    import types
    import importlib.machinery
    import importlib.util

    if "imp" not in sys.modules:
        _imp = types.ModuleType("imp")

        def _load_source(name, path):
            loader = importlib.machinery.SourceFileLoader(name, path)
            spec = importlib.util.spec_from_file_location(name, path,
                                                          loader=loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            return mod

        _imp.load_source = _load_source
        sys.modules["imp"] = _imp

    import scipy.signal as _ss
    import scipy.signal.windows as _w
    for _n in ("bartlett", "hann", "hamming", "blackman", "blackmanharris",
               "gaussian", "kaiser"):
        if not hasattr(_ss, _n):
            setattr(_ss, _n, getattr(_w, _n))

    import ahkab
    from ahkab import devices as _devices

    for _attr in dir(_devices):
        _cls = getattr(_devices, _attr)
        if isinstance(_cls, type) and hasattr(_cls, "get_op_info"):
            _cls.get_op_info = lambda self, *a, **k: ["", ""]

    from ahkab import circuit as _circuit, results as _results, ahkab as _ak
    _results.op_solution.write_to_file = lambda *a, **k: None
    _results.op_solution.print_short = lambda *a, **k: None
except Exception:                                          # noqa: BLE001
    pytest.skip("ahkab is not available on this toolchain",
                allow_module_level=True)

from symbulator import dc, to_spice
from symbulator.spice import _parse_spice_number


def _load_netlist(c, net):
    """Build an ahkab circuit from the subset of SPICE to_spice emits."""
    def n(x):
        return c.gnd if x == "0" else x

    def val(tok):
        v = _parse_spice_number(tok)
        assert v is not None, f"unreadable value {tok!r}"
        return float(v)

    for line in net.splitlines():
        line = line.strip()
        if not line or line.startswith("*") or line.startswith("."):
            continue
        tok = [t for t in line.split()
               if not t.upper().startswith("IC=")]
        kind = tok[0][0].upper()
        if kind == "R":
            c.add_resistor(tok[0], n(tok[1]), n(tok[2]), val(tok[3]))
        elif kind == "L":
            c.add_inductor(tok[0], n(tok[1]), n(tok[2]), val(tok[3]))
        elif kind == "C":
            c.add_capacitor(tok[0], n(tok[1]), n(tok[2]), val(tok[3]))
        elif kind == "V":
            c.add_vsource(tok[0], n(tok[1]), n(tok[2]), dc_value=val(tok[3]))
        elif kind == "I":
            c.add_isource(tok[0], n(tok[1]), n(tok[2]), dc_value=val(tok[3]))
        elif kind == "E":
            c.add_vcvs(tok[0], n(tok[1]), n(tok[2]), n(tok[3]), n(tok[4]),
                       val(tok[5]))
        elif kind == "G":
            c.add_vccs(tok[0], n(tok[1]), n(tok[2]), n(tok[3]), n(tok[4]),
                       val(tok[5]))
        elif kind == "F":
            c.add_cccs(tok[0], n(tok[1]), n(tok[2]), tok[3], val(tok[4]))
        elif kind == "H":
            # ahkab's H sign quirk -- see the module docstring.
            c.add_ccvs(tok[0], n(tok[1]), n(tok[2]), tok[3], -val(tok[4]))
        else:
            raise AssertionError("unhandled netlist line: " + line)


def _assert_matches_ground_truth(desc):
    net, _warns = to_spice(desc)
    sym = dc(desc)
    c = _circuit.Circuit("groundtruth")
    _load_netlist(c, net)
    res = _ak.run(c, an_list=[_ak.new_op(verbose=0)])["op"]
    keys = list(res.keys())
    compared = 0
    for key, expr in sym.values.items():
        if not key.startswith("v_"):
            continue
        rkey = "V" + key[2:].upper()
        if rkey not in keys:
            continue          # element drops (v_r1) have no node key
        want, got = float(expr), float(res[rkey][0][0])
        assert abs(want - got) <= 1e-6 * max(1.0, abs(want)), (
            f"{key}: symbulator {want}, ahkab {got}\n{net}")
        compared += 1
    assert compared > 0, "nothing compared -- key mismatch?\n" + net


CASES = [
    "e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:e2,3,0,2*v_2:r3,3,0,1'k",
    "e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:e2,3,0,4*(v_1 - v_2):r3,3,0,1'k",
    "e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:j2,0,3,0.005*v_2:r3,3,0,1'k",
    "e1,1,0,5:r1,1,0,1'k:j2,0,3,10*i_e1:r3,3,0,100",
    "e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:e2,3,0,50*i_r1:r3,3,0,1'k",
    "e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:e2,3,0,1 + 2*v_2 - 3000*i_r1:r3,3,0,1'k",
    "e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:j2,0,3,0.001*v_2 + 0.002*v_1:r3,3,0,1'k",
    "e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:e2,3,0,2*v_r1:r3,3,0,1'k",
    "e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:e2,3,0,2*v_2:r3,3,0,1'k:"
    "e3,4,0,10*i_e2:r4,4,0,1'k",
    "j1,0,1,0.01:r1,1,0,1'k:e2,2,0,400*i_j1:r2,2,0,1'k",
    "e1,1,0,36:r1,1,2,1'k:r2,2,3,3'k:r3,3,0,2'k",
    # #162: the op-amp as a huge-gain VCVS (1e-9 relative error, far
    # inside the 1e-6 tolerance) and the exact transformer pair.
    "e1,1,0,1:r1,1,2,1'k:r2,2,3,2'k:o1,0,2,3",
    "e1,1,0,10:r0,1,2,1:t1,2,3,2,1:r1,3,0,100",
    # #162/#163: every two-port kind through its admittance reduction.
    "e1,1,0,10:z,1,2,[100,10,20,50]:rl,2,0,200",
    "e1,1,0,10:y,1,2,[0.02,-0.01,-0.01,0.02]:rl,2,0,200",
    "e1,1,0,10:h,1,2,[100,0.1,-2,0.01]:rl,2,0,200",
    "e1,1,0,10:g,1,2,[0.01,0.1,2,50]:rl,2,0,200",
    "e1,1,0,10:a,1,2,[2,100,0.01,1.5]:rl,2,0,200",
    "e1,1,0,10:b,1,2,[2,100,0.01,1.5]:rl,2,0,200",
    # X2: the four-node forms. The transformer's primary sits between
    # two live nodes and its secondary is grounded on its own side
    # through a resistor; the two-port's ports each span a live pair.
    "e1,1,0,10:r0,1,2,1:t1,2,3,4,5,2,1:r4,4,0,3:r1,3,5,100:r5,5,0,7",
    "e1,1,0,10:r0,1,2,1:t1,2,3,0,0,2,1:r1,3,0,100",
    "e1,1,0,10:r0,1,2,3:z,2,3,4,5,[100,10,20,50]:r4,4,0,2:rl,3,5,200:"
    "r5,5,0,9",
    "e1,1,0,10:r0,1,2,3:y,2,3,4,5,[0.02,-0.01,-0.01,0.02]:r4,4,0,2:"
    "rl,3,5,200:r5,5,0,9",
    "e1,1,0,10:r0,1,2,3:h,2,3,4,5,[100,0.1,-2,0.01]:r4,4,0,2:rl,3,5,200:"
    "r5,5,0,9",
    "e1,1,0,10:r0,1,2,3:g,2,3,4,5,[0.01,0.1,2,50]:r4,4,0,2:rl,3,5,200:"
    "r5,5,0,9",
    "e1,1,0,10:r0,1,2,3:a,2,3,4,5,[2,100,0.01,1.5]:r4,4,0,2:rl,3,5,200:"
    "r5,5,0,9",
    "e1,1,0,10:r0,1,2,3:b,2,3,4,5,[2,100,0.01,1.5]:r4,4,0,2:rl,3,5,200:"
    "r5,5,0,9",
    "e1,1,0,10:z,1,2,0,0,[100,10,20,50]:rl,2,0,200",
]


@pytest.mark.parametrize("desc", CASES)
def test_exported_netlist_matches_independent_simulator(desc):
    _assert_matches_ground_truth(desc)
