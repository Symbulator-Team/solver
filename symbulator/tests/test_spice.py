"""SPICE translation, both directions (#160).

The round-trip tests are the load-bearing ones: what to_spice writes,
from_spice must read back into a circuit the solver gives the same
answers for -- equivalence is checked by solving, not by comparing text.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
import sympy as sp

from symbulator import dc, to_spice, from_spice
from symbulator.elements import CircuitError


# --- Symbulator -> SPICE -------------------------------------------------

def test_basic_export():
    net, warns = to_spice("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k")
    lines = net.splitlines()
    assert lines[0].startswith("*")            # title line
    assert "V1 1 0 5" in lines
    assert "R1 1 2 1K" in lines
    assert "R2 2 0 1K" in lines
    assert lines[-1] == ".end"
    assert warns == []


def test_mega_is_MEG_and_milli_is_M():
    # The classic SPICE trap: 1'M (mega) must never come out as "1M",
    # which SPICE reads as one millionth of what was meant.
    net, _ = to_spice("e1,1,0,5:r1,1,0,2.2'M")
    assert "R1 1 0 2.2MEG" in net
    # And milli never comes out as "M" at all -- plain decimal instead,
    # so our output cannot feed the confusion in either direction.
    net, _ = to_spice("j1,0,1,12'm:r1,1,0,1'k")
    assert "I1 0 1 0.012" in net


def test_initial_conditions_export():
    net, _ = to_spice("e1,1,0,5:r1,1,2,1000:c1,2,0,1'u,3")
    assert "C1 2 0 1U IC=3" in net


def test_short_becomes_zero_volt_source():
    net, _ = to_spice("e1,1,0,5:r1,1,2,1'k:s1,2,3:r2,3,0,1'k")
    assert "Vs1 2 3 0" in net


def test_mutual_inductance_export():
    net, warns = to_spice(
        "e1,1,0,5:l1,1,0,0.1:l2,2,0,0.4:m1,l1,l2,0.1:r1,2,0,50")
    assert "K1 L1 L2 0.5" in net
    assert warns == []


def test_symbolic_value_warns_and_comments():
    net, warns = to_spice("e1,1,0,5:r1,1,2,1'k:r2,2,0,rb")
    assert any("rb" in w for w in warns)
    assert "* r2,2,0,rb" in net            # kept as a comment, not dropped
    assert "R2" not in net


def test_dependent_source_translates_since_161():
    # Under #160 this warned; #161 translates it to a VCVS instead.
    net, warns = to_spice("e1,1,0,5:r1,1,2,1'k:e2,3,0,2*v_2:r3,3,0,1'k")
    assert "E2 3 0 2 0 2" in net
    assert warns == []


def test_parameterless_twoport_warns():
    # Without a parameter term there is nothing numeric to realise.
    net, warns = to_spice("e1,1,0,10:y1,1,2:rl,2,0,1'k")
    assert any("without numeric parameters" in w for w in warns)


# --- Op-amp, transformer, two-ports -> SPICE (#162) ----------------------

def test_opamp_becomes_huge_gain_vcvs():
    desc = "e1,1,0,1:r1,1,2,1'k:r2,2,3,2'k:o1,0,2,3"
    net, warns = to_spice(desc)
    assert "Eo1 3 0 0 2 1G" in net
    assert any("finite-gain" in w for w in warns)
    # Round trip: the finite gain makes the answers approximate, so
    # compare numerically rather than symbolically.
    back, _ = from_spice(net)
    a, b = dc(desc), dc(back)
    assert a["v_3"] == -2
    assert abs(float(b["v_3"]) - (-2)) < 1e-6


def test_ideal_transformer_exact_pair():
    desc = "e1,1,0,10:r0,1,2,1:t1,2,3,2,1:r1,3,0,100"
    net, warns = to_spice(desc)
    assert "Et1 t1_s 0 2 0 0.5" in net
    assert "Vi_t1 t1_s 3 0" in net
    assert "Ft1 2 0 Vi_t1 0.5" in net
    # This realisation is exact, DC included -- the answers round-trip
    # symbolically equal.
    back, _ = from_spice(net)
    a, b = dc(desc), dc(back)
    for key in ("v_2", "v_3", "i_r0", "i_r1"):
        assert sp.simplify(a[key] - b[key]) == 0


def test_transformer_symbolic_turns_warn():
    net, warns = to_spice("e1,1,0,10:t1,1,2,n,1:r1,2,0,100")
    assert any("numeric turns" in w for w in warns)


def test_twoport_with_parameters_becomes_conductances():
    desc = "e1,1,0,10:z,1,2,[100,10,20,50]:rl,2,0,200"
    net, warns = to_spice(desc)
    assert any("conductance-form" in w for w in warns)
    # Round trip through from_spice solves identically: the G elements
    # encode the same admittance reduction the engine itself uses.
    back, _ = from_spice(net)
    a, b = dc(desc), dc(back)
    for key in ("v_1", "v_2"):
        assert abs(float(a[key]) - float(b[key])) < 1e-9 * max(
            1, abs(float(a[key])))


def test_twoport_symbolic_parameters_warn():
    net, warns = to_spice("e1,1,0,10:z,1,2,[za,10,20,zb]:rl,2,0,200")
    assert any("not a plain number" in w for w in warns)


# --- SPICE -> Symbulator -------------------------------------------------

def test_basic_import():
    desc, warns = from_spice("""\
My divider
V1 1 0 5
R1 1 2 1k
R2 2 0 1k
.end
""")
    assert "read as the netlist's title line" in warns[0]
    res = dc(desc)
    assert res.v("2") == sp.Rational(5, 2)


def test_import_milli_and_meg():
    desc, _ = from_spice("V1 1 0 5\nR1 1 0 1MEG\nR2 1 0 2m\n")
    assert "r1,1,0,1'M" in desc
    assert "r2,1,0,2'm" in desc


def test_import_dc_keyword_and_waveform_warning():
    desc, warns = from_spice("V1 1 0 DC 12\nR1 1 0 1k\n")
    assert "e1,1,0,12" in desc
    desc, warns = from_spice("V1 1 0 SIN(0 1 1k)\nR1 1 0 1k\nV2 2 0 5\nR2 2 0 1k")
    assert any("SIN" in w for w in warns)
    assert "e2,2,0,5" in desc and "e1" not in desc.splitlines()[0]


def test_import_controlled_sources():
    # VCVS: E1 3 0 2 0 2.0 -> ee1 (the SPICE letter rides along, so it
    # cannot collide with V1 -> e1).
    desc, warns = from_spice(
        "V1 1 0 5\nR1 1 2 1k\nR2 2 0 1k\nE1 3 0 2 0 2\nR3 3 0 1k\n")
    assert "ee1,3,0,2*v_2" in desc
    res = dc(desc)
    assert res.v("3") == 5           # 2 * v_2 = 2 * 2.5

    # CCCS: F1 references V1's current; V1 maps to e1, so i_e1.
    desc, _ = from_spice(
        "V1 1 0 5\nR1 1 0 1k\nF1 0 2 V1 10\nR2 2 0 100\n")
    assert "jf1,0,2,10*i_e1" in desc


def test_import_coupling():
    desc, warns = from_spice(
        "V1 1 0 5\nL1 1 0 0.1\nL2 2 0 0.4\nK1 L1 L2 0.5\nR1 2 0 50\n")
    assert "m1,l1,l2,0.1" in desc
    assert warns == []


def test_import_drops_nonlinear_with_warning():
    desc, warns = from_spice(
        "V1 1 0 5\nR1 1 2 1k\nD1 2 0 mydiode\n.model mydiode D\n")
    assert any("diodes" in w for w in warns)
    assert any("directive ignored" in w for w in warns)
    assert "d1" not in desc


def test_import_subckt_skipped_whole():
    desc, warns = from_spice("""\
V1 1 0 5
R1 1 0 1k
.subckt opamp 1 2 3
R9 1 2 1k
.ends
""")
    assert "r9" not in desc
    assert any("subcircuit" in w for w in warns)


def test_import_empty_raises():
    with pytest.raises(CircuitError):
        from_spice("* nothing here\n.end\n")


# --- Round trip ----------------------------------------------------------

def test_round_trip_solves_identically():
    original = "e1,1,0,36:r1,1,2,1'k:r2,2,3,3'k:r3,3,0,2'k"
    net, warns = to_spice(original)
    assert warns == []
    back, warns2 = from_spice(net)
    assert warns2 == []
    a, b = dc(original), dc(back)
    for key in ("v_2", "v_3", "i_r1", "p_r3"):
        assert sp.simplify(a[key] - b[key]) == 0


def test_round_trip_with_ic_and_coupling():
    original = ("e1,1,0,5:r1,1,2,1000:c1,2,0,1'u,3:"
                "l1,2,0,0.1:l2,3,0,0.4:m1,l1,l2,0.1:r2,3,0,50")
    net, warns = to_spice(original)
    assert warns == []
    back, warns2 = from_spice(net)
    assert warns2 == []
    a, b = dc(original), dc(back)
    assert sp.simplify(a["v_2"] - b["v_2"]) == 0


# --- Dependent sources, Symbulator -> SPICE (#161) -----------------------

def _round_trip_matches(desc, checks):
    """to_spice -> from_spice -> both circuits solve identically."""
    net, _ = to_spice(desc)
    back, _ = from_spice(net)
    a, b = dc(desc), dc(back)
    for key in checks:
        assert sp.simplify(a[key] - b[key]) == 0, (key, a[key], b[key], net)


def test_vcvs_single_node_control():
    net, warns = to_spice("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:"
                          "e2,3,0,2*v_2:r3,3,0,1'k")
    assert "E2 3 0 2 0 2" in net
    assert warns == []
    _round_trip_matches("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:e2,3,0,2*v_2:r3,3,0,1'k",
                        ["v_2", "v_3"])


def test_vcvs_difference_pairs_into_one_element():
    net, warns = to_spice("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:"
                          "e2,3,0,4*(v_1 - v_2):r3,3,0,1'k")
    assert "E2 3 0 1 2 4" in net
    assert warns == []


def test_element_drop_control():
    # v_r2 is r2's drop = v(2) - 0, so the control is r2's own nodes.
    net, warns = to_spice("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:"
                          "e2,3,0,2*v_r2:r3,3,0,1'k")
    assert "E2 3 0 2 0 2" in net
    assert warns == []


def test_vccs():
    net, warns = to_spice("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:"
                          "j2,0,3,0.005*v_2:r3,3,0,1'k")
    assert "G2 0 3 2 0 0.005" in net
    assert warns == []
    _round_trip_matches("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:j2,0,3,0.005*v_2:r3,3,0,1'k",
                        ["v_2", "v_3"])


def test_cccs_on_a_source_current_references_it_directly():
    desc = "e1,1,0,5:r1,1,0,1'k:j2,0,3,10*i_e1:r3,3,0,100"
    net, warns = to_spice(desc)
    assert "F2 0 3 V1 10" in net
    assert warns == []
    _round_trip_matches(desc, ["v_3"])


def test_ccvs_on_a_resistor_splices_a_sense_source():
    desc = "e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:e2,3,0,50*i_r1:r3,3,0,1'k"
    net, warns = to_spice(desc)
    assert "R1 1 r1_s 1K" in net
    assert "Vi_r1 r1_s 2 0" in net
    assert "H2 3 0 Vi_r1 50" in net
    _round_trip_matches(desc, ["v_2", "v_3", "i_r1"])


def test_affine_mix_expands_in_series():
    desc = ("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:"
            "e2,3,0,1 + 2*v_2 - 3000*i_r1:r3,3,0,1'k")
    net, warns = to_spice(desc)
    assert any("translated as 3 SPICE elements in series" in w for w in warns)
    assert "e2_x1" in net and "e2_x2" in net
    _round_trip_matches(desc, ["v_2", "v_3"])


def test_current_of_independent_current_source_folds_to_constant():
    desc = "j1,0,1,0.01:r1,1,0,1'k:e2,2,0,400*i_j1:r2,2,0,1'k"
    net, warns = to_spice(desc)
    assert "V2 2 0 4" in net         # 400 * 0.01, a plain constant
    assert warns == []
    _round_trip_matches(desc, ["v_1", "v_2"])


def test_dependent_spelling_equivalence():
    # ir1 means i_r1 to the solver, so the exporter reads it the same way.
    desc = "e1,1,0,5:r1,1,0,1'k:e2,2,0,100*ir1:r3,2,0,1'k"
    net, warns = to_spice(desc)
    assert "H2 2 0 Vi_r1 100" in net
    _round_trip_matches(desc, ["v_2"])


def test_dependent_current_source_stack_is_parallel():
    desc = ("e1,1,0,5:r1,1,2,1'k:r2,2,0,1'k:"
            "j2,0,3,0.001*v_2 + 0.002*v_1:r3,3,0,1'k")
    net, warns = to_spice(desc)
    # Terms come out sorted by control-symbol name, so v_1's part first.
    assert "G2a 0 3 1 0 0.002" in net
    assert "G2b 0 3 2 0 0.001" in net
    _round_trip_matches(desc, ["v_3"])


def test_nonlinear_value_still_warns():
    net, warns = to_spice("e1,1,0,5:r1,1,0,1'k:e2,2,0,v_1*i_r1:r2,2,0,1'k")
    assert any("not linear" in w for w in warns)
    assert "E2" not in net and "H2" not in net


def test_symbolic_gain_still_warns():
    # `k` names neither a node nor an element, so the whole source warns.
    net, warns = to_spice("e1,1,0,5:r1,1,0,1'k:e2,2,0,k*v_1:r2,2,0,1'k")
    assert any("references 'k'" in w for w in warns)


def test_current_of_untranslatable_element_cascades():
    # rx is symbolic, so i_rx is unavailable, so e2 must warn too.
    net, warns = to_spice("e1,1,0,5:rx,1,0,rb:e2,2,0,5*i_rx:r2,2,0,1'k")
    assert any(w.startswith("rx:") for w in warns)
    assert any("current of 'rx'" in w for w in warns)


# --- no representation noise in a netlist -----------------------------

def test_a_prefixed_value_translates_without_noise():
    """`js,0,d,397.3'm` is `Is 0 d 0.3973`, the same as `.3973` gives.

    Roberto found it on 1 Sep 2026: the two spellings are the same
    current and a netlist that prints one of them as
    0.39730000000000004 is showing the reader a binary artefact. The
    cause was upstream of this module -- see
    si_prefix._scaled_literal -- but the netlist is where it surfaced,
    so the test lives here too."""
    plain, _ = to_spice("js,0,d,.3973:r1,d,0,10")
    prefixed, _ = to_spice("js,0,d,397.3'm:r1,d,0,10")
    assert plain == prefixed
    assert "Is 0 d 0.3973\n" in prefixed


def test_every_prefix_survives_the_round_trip_cleanly():
    """Out to SPICE and back, for every prefix SPICE has a suffix for,
    on every kind that carries a plain value. No token may grow the
    seventeen digits of a float's repr."""
    for typed in ("397.3'm", "4.7'u", "2.2'n", "1.1'k", "6.6'M", "3.3'G",
                  "7.7'p", "5.5'f", "9.9'T", "100'p", "123.456'u"):
        for kind, letter in (("r", "R"), ("c", "C"), ("l", "L")):
            net, _ = to_spice("e1,1,0,5:%s1,1,0,%s" % (kind, typed))
            line = [x for x in net.splitlines() if x.startswith(letter)][0]
            token = line.split()[-1]
            assert len(token) < 12, (typed, kind, token)
            back, _ = from_spice("%s1 1 0 %s" % (letter, token))
            assert "0000000" not in back, (typed, kind, back)


def test_a_spice_suffix_is_read_in_base_ten():
    """`397.3m` read back is 0.3973 -- the same shift, the same way."""
    back, _ = from_spice("R1 1 0 397.3m")
    assert "0.3973" in back and "0.39730000" not in back
