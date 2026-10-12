"""
antimony.py — Build and save an Antimony model string for a PSS reaction subset.

Usage:
    python antimony.py --reactions   <pss_reactions.json>
                       --species     <pss_species.json>
                       --decay       <decay_rate_mapper.pkl>
                       --conc        <initial_concentration_mapper.pkl>
                       --rate-laws   <rate_law_mapper.pkl>
                       --kinetics    <kinetic_parameter_mapper.pkl>
                       --subset      ja          # ja | aba | all
                       --output      <./PSS.ant>
"""

from pathlib import Path

import numpy as np
import argparse
import dill                 # type: ignore
import json
import re


# ── Helpers ────────────────────────────────────────────────────────────────────

def extract_rate_components(expr: str) -> dict[str, list[str]]:
    """Extract unique parameter tokens from a kinetic rate expression.

    Args:
        expr: RHS kinetic rate expression string.

    Returns:
        Dict mapping token type → list of unique matched tokens (order preserved).
        Keys: 'kcat', 'km', 'Kd', 'vmax', 'kmc', 'kic', 'vmag', 'kG',
              'substrates', 'compartments'.
    """
    def unique(seq: list[str]) -> list[str]:
        seen: set[str] = set()
        result = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                result.append(x)
        return result

    patterns = {
        'kcat':  r'\bkcat_[A-Za-z0-9_]+|\bkcat\b',
        'km':    r'\bkm_[A-Za-z0-9_]+|\bkm\b',
        'Kd':    r'Kd_[A-Za-z0-9_]+',
        'vmax':  r'vmax_[A-Za-z0-9_]+',
        'kmc':   r'kmc_[A-Za-z0-9_]+',
        'kic':   r'kic_[A-Za-z0-9_]+',
        'vmag':  r'vmag_[A-Za-z0-9_]+',
        'kG':    r'kG_[A-Za-z0-9_]+',
    }

    components = {key: unique(re.findall(pat, expr)) for key, pat in patterns.items()}
    components['substrates']   = unique(re.findall(r'\b(s\d+)\b', expr))
    components['compartments'] = unique(re.findall(r'\b(c\d+)\b', expr))
    return components


def decay_type_group(stype: str) -> str:
    """Collapse the PSS species types into the groups that share a degradation scale."""
    if stype in ("protein", "protein_active"):
        return "protein"
    if stype in ("complex", "complex_active"):
        return "complex"
    if stype in ("mrna", "ncrna", "mirna"):
        return "rna"
    return stype


# ── Builder ────────────────────────────────────────────────────────────────────

def build_antimony(
    reactions_2use:      list[str],
    reactions:           dict,
    species:             dict,
    decay_rate_mapper:   callable,
    concentration_mapper: callable,
    rate_law_mapper:     callable,
    kinetics_mapper:     callable,
    decay_form:          str = "first-order",
    decay_k:             dict = None,
) -> str:
    """Assemble an Antimony model string for the given reaction subset.

    Args:
        reactions_2use:       Ordered list of reaction IDs to include.
        reactions:            Full PSS reactions dict.
        species:              Full PSS species dict.
        decay_rate_mapper:    specie → decay rate (or None).
        concentration_mapper: specie → initial concentration (or None).
        rate_law_mapper:      reaction_id → rate law expression (or None).
        kinetics_mapper:      (reaction_id, reactions) → {reactant: (km, kcat)}.
        decay_form:           'first-order' (default), 'saturating' (per-type
                              half-saturation constant), or 'saturating-global' (single 1e-4).
        decay_k:              Optional {type_group: K} overrides for the saturating form.

    Returns:
        Complete Antimony model string ready to write to file.
    """
    reactions_all = reactions.copy()
    reactions     = {k: v for k, v in reactions.items() if k in reactions_2use}
    species_ids   = {
        s for rxn in reactions_2use if rxn in reactions
        for vals in reactions[rxn].values() for s in vals
    }
    species = {k: v for k, v in species.items() if k in species_ids}

    compartments = sorted({species[s]['compartment'] for s in species})

    # ── Half-saturation constants of the degradation law, one per species type ──
    # The saturating degradation law  v = delta*[X] / (K + [X])  needs a scale K at
    # which removal saturates. A single global K cannot serve species whose baseline
    # concentrations span more than six orders of magnitude (metabolites ~1e-7 mM,
    # proteins ~1e-3 mM, RNAs higher still), so K is set PER SPECIES TYPE to the
    # median initial concentration of that type within the model being built.
    # Every species therefore starts at the half-saturation point of its own law.
    type_conc: dict[str, list[float]] = {}
    for s in species:
        c = concentration_mapper(s)
        if c is not None:
            type_conc.setdefault(decay_type_group(species[s]['type']), []).append(float(c))
    decay_K = {g: float(np.median(v)) for g, v in type_conc.items()}
    if decay_k:
        decay_K.update(decay_k)

    used_K: set[str] = set()   # type groups that actually receive a degradation law

    sections: dict[str, str] = {
        'global': (
            "\t# Global:\n"
            "\tn = 1;\n"
        ),
        'compartments': (
            "\t# Compartments:\n" +
            "\n".join(f"\t{c:<10} = 1;" for c in compartments)
        ),
        'species': (
            "\t# Species Definitions:\n" +
            "\n".join(
                f"\tspecies {s:<35} in {species[s]['compartment']};"
                for s in species
            )
        ),
        'reactions':      "\t# Reactions:\n",
        'rate_laws':      "\t# Rate Laws:\n",
        'decay_rates':    "\t# Decay Rates:\n",
        'concentrations': "\t# Concentrations:\n",
        'kinetics':       "\t# Kinetics:\n",
        'events': (
            "\t# Events:\n"
            # "\tE1: at time >= 1000: s_JA_cyt_m = s_JA_cyt_m + 100;\n"
        ),
    }

    # ── Species-level sections (one pass, no duplicates possible) ─────────────
    for s in species:
        conc = concentration_mapper(s)
        if conc is not None:
            sections['concentrations'] += f"\t{s:<35} = {conc};\n"

        decay = decay_rate_mapper(s)
        if decay is not None:
            if decay_form == "saturating":
                # Saturating removal with a PER-TYPE half-saturation constant (see the
                # Global section): the law approaches the maximal rate `decay` above
                # Kdeg_<type> and falls smoothly to zero as [s] -> 0.
                g   = decay_type_group(species[s]['type'])
                used_K.add(g)
                law = f"({s} * {decay}) / (Kdeg_{g} + {s})"
            elif decay_form == "saturating-global":
                # Legacy form: the same law with one global constant of 1e-4 mM for
                # every species. Kept for comparison only — metabolites sit ~1e-7 mM,
                # four orders below it, where the term degenerates to first-order
                # removal at decay/1e-4, i.e. 10,000x the nominal rate.
                law = f"({s} * {decay}) / (1e-4 + {s})"
            else:
                # First-order removal (default): d[s]/dt = -decay * [s]; decay is a
                # rate constant, removal scales with the pool and cannot overshoot zero.
                law = f"{decay} * {s}"
            sections['decay_rates'] += (
                f"\td_{s:<35}: {s:<35} -> ; {law};\n"
            )

    # Declare the per-type half-saturation constants actually used, as named globals.
    sections['global'] += "".join(
        f"\tKdeg_{g:<12} = {decay_K[g]:.6g};\n" for g in sorted(used_K)
    )

    # ── Reaction-level sections ────────────────────────────────────────────────
    for rxn in reactions:
        rxn_data  = reactions[rxn]
        reactants = [
            f"${r}" if species.get(r, {}).get('type') == 'gene' else r
            for r in rxn_data['reactants']
        ]
        products = rxn_data['products']

        sections['reactions'] += (
            f"\t{rxn}: {' + '.join(reactants):<50} -> "
            f"{' + '.join(products):<30}; v_{rxn};\n"
        )

        try:
            rate_law = rate_law_mapper(rxn)
        except TypeError as e:
            print(f"  [WARN] {rxn}: rate_law_mapper raised {e}")
            continue
        if not rate_law:
            print(f"  [WARN] {rxn}: no rate law — skipping.")
            continue

        rate_law = re.sub(r'\bkcat\b', f'kcat_{rxn}', rate_law)
        rate_law = re.sub(r'\bkm\b',   f'km_{rxn}',   rate_law)
        # Scope Kd tokens to this reaction: Kd_s_JA_cyt_m → Kd_s_JA_cyt_m_rx00041
        rate_law = re.sub(r'Kd_([A-Za-z0-9_]+)', lambda m: f'Kd_{m.group(1)}_{rxn}', rate_law)
        components = extract_rate_components(rate_law)

        sections['rate_laws'] += f"\tv_{rxn} := {rate_law} + 1e-16;\n"

        # kinetics_mapper returns {reactant_id: (km_mM, kcat_1s)}
        kinetics = kinetics_mapper(rxn, reactions_all)
        kms      = {r: km   for r, (km,   _)    in kinetics.items() if km   is not None}
        kcats    = {r: kcat for r, (_,    kcat) in kinetics.items() if kcat is not None}
        km_min   = float(np.min(list(kms.values())))   if kms   else None
        kcat_min = float(np.min(list(kcats.values()))) if kcats else None

        if components['kcat'] and kcat_min is not None:
            sections['kinetics'] += f"\tkcat_{rxn:<40} = {kcat_min};\n"

        # Kd tokens are now reaction-scoped: Kd_<substrate>_<rxn>
        for kd_token in components['Kd']:
            substrate = kd_token.split('Kd_', 1)[1].rsplit(f'_{rxn}', 1)[0]
            km_val    = kms.get(substrate, km_min)
            if km_val is not None:
                sections['kinetics'] += f"\t{kd_token:<40} = {km_val};\n"

        if components['km'] and km_min is not None:
            sections['kinetics'] += f"\tkm_{rxn:<40} = {km_min};\n"

    return "model PSS\n\n" + "\n\n".join(sections.values()) + "\nend"


# ── Reaction subsets ───────────────────────────────────────────────────────────

REACTIONS_JA = [
    'rx00693', 'rx00026', 'rx00029', 'rx00030', 'rx00031', 'rx00694',
    'rx00033', 'rx00034', 'rx00035', 'rx00036', 'rx00037', 'rx00038',
    'rx00039', 'rx00040', 'rx00696', 'rx00041', 'rx00042', 'rx00043',
    'rx00044', 'rx00018', 'rx00047', 'rx00028',
]

REACTIONS_ABA = [
    'rx00568', 'rx00710', 'rx00709', 'rx00748', 'rx00750', 'rx00749',
    'rx00474', 'rx00751', 'rx00752', 'rx00753', 'rx00552', 'rx00553',
    'rx00554', 'rx00549', 'rx00555', 'rx00556', 'rx00557', 'rx00558',
    'rx00559', 'rx00560',
]


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reactions",  required=True, help="Path to pss_reactions.json.")
    parser.add_argument("--species",    required=True, help="Path to pss_species.json.")
    parser.add_argument("--decay",      required=True, help="Path to decay_rate_mapper.pkl.")
    parser.add_argument("--conc",       required=True, help="Path to initial_concentration_mapper.pkl.")
    parser.add_argument("--rate-laws",  required=True, help="Path to rate_law_mapper.pkl.")
    parser.add_argument("--kinetics",   required=True, help="Path to kinetic_parameter_mapper.pkl.")
    parser.add_argument("--subset",     default="all",  choices=["ja", "aba", "all"],
                        help="Reaction subset to build (default: all).")
    parser.add_argument("--decay-form", default="first-order", dest="decay_form",
                        choices=["first-order", "saturating", "saturating-global"],
                        help="Degradation law. 'first-order' (default): delta*s. "
                             "'saturating': delta*s/(K+s) with K set per species type to that "
                             "type's median initial concentration. 'saturating-global': the "
                             "original form with a single K = 1e-4 mM for every species.")
    parser.add_argument("--decay-k", default=None, dest="decay_k",
                        help="Override half-saturation constants, e.g. "
                             "'metabolite=1e-7,protein=1e-3' (saturating form only).")
    parser.add_argument("--output",     default="./", help="Output file path (default: PSS.ant).")
    args = parser.parse_args()

    reactions = json.loads(Path(args.reactions).read_text())
    species   = json.loads(Path(args.species).read_text())

    decay_rate_mapper    = dill.load(open(args.decay,       'rb'))
    concentration_mapper = dill.load(open(args.conc,        'rb'))
    rate_law_mapper      = dill.load(open(args.rate_laws,   'rb'))
    kinetics_mapper      = dill.load(open(args.kinetics,    'rb'))

    subset_map = {
        'ja':  REACTIONS_JA,
        'aba': REACTIONS_ABA,
        'all': list(reactions.keys()),
    }
    reactions_2use = subset_map[args.subset]

    antimony = build_antimony(
        reactions_2use,
        reactions,
        species,
        decay_rate_mapper,
        concentration_mapper,
        rate_law_mapper,
        kinetics_mapper,
        decay_form=args.decay_form,
        decay_k=({k.strip(): float(v) for k, _, v in
                  (pair.partition('=') for pair in args.decay_k.split(','))}
                 if args.decay_k else None),
    )

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "Antimony.txt").write_text(antimony)
    print(f"Saved Antimony model ({args.subset}, decay={args.decay_form}) "
          f"→ {out / 'Antimony.txt'}")


if __name__ == "__main__":
    main()
