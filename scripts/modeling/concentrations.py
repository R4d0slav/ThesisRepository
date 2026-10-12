"""
concentrations.py — Build and serialise a self-contained initial-concentration mapper for PSS species.

Usage:
    python concentrations.py --species   <pss_species.json>
                             --concentrations <initial_concentrations.csv>
                             --output    <./>

Loading the saved mapper later (no other files needed):
    import dill
    initial_concentration = dill.load(open("initial_concentration_mapper.pkl", "rb"))
    initial_concentration("s_LHB1_cyt_p")   # → 3.14e-5  (mM)
"""

from pathlib import Path

import pandas as pd
import numpy  as np
import argparse
import dill                 # type: ignore
import json
import re


# ── Constants ──────────────────────────────────────────────────────────────────

RNA_CONC    = 4.9  # TODO: Needs to be smaller!
GENE_CONC   = 1e-5


# ── Helpers ────────────────────────────────────────────────────────────────────

def extract_ath_homologues(item: str) -> tuple[list[str], str]:
    """Parse 'Name[AT1G01010, AT2G12345]' into (homologue_ids, name).

    Args:
        item: A PSS protein name string, optionally containing ATH IDs in brackets.

    Returns:
        A tuple of (list of ATH gene IDs, base name before the bracket).
    """
    match = re.match(r'^([^\[]+)\[([^\]]+)\]', item.strip())
    if not match:
        return [], item

    name       = match.group(1)
    genes      = [g.strip() for g in match.group(2).split(',')]
    homologues = [g for g in genes if re.match(r'^AT[0-9A-Za-z.]+$', g)]
    return homologues, name



def protein_conc_from_file(
    concentrations: pd.DataFrame,
    species:        dict,
) -> dict[str, list[float]]:
    """Build per-protein concentration lists from the PaxDb slice of the concentrations file.

    Args:
        concentrations: Full concentrations DataFrame (both protein and metabolite rows).
        species:        Species dict keyed by ID.

    Returns:
        {protein_name: [conc_mM, ...]} — one value per matched homologue.
    """
    protein_concentrations = concentrations[concentrations['specie'] == 'protein'].copy()
    protein_concentrations['string_external_id'] = (
        protein_concentrations['string_external_id'].str.split('.').str[0]
    )

    pax_ids      = set(protein_concentrations['string_external_id'])
    pss_proteins = [s for s in species if species[s]['type'] in ('protein', 'protein_active')]

    protein_conc: dict[str, list[float]] = {}
    for specie_id in pss_proteins:
        name = species[specie_id]['name']
        for ath_id in extract_ath_homologues(name)[0]:
            if ath_id in pax_ids:
                conc_mM = protein_concentrations.loc[
                    protein_concentrations['string_external_id'] == ath_id,
                    'conc. [mM]'
                ].values[0]
                protein_conc.setdefault(name, []).append(conc_mM)

    return protein_conc


def metabolite_conc_from_file(
    concentrations: pd.DataFrame,
    species:        dict,
) -> dict[str, float]:
    """Build per-metabolite concentration dict from the metabolite slice of the concentrations file.

    Args:
        concentrations: Full concentrations DataFrame.
        species:        Species dict keyed by ID.

    Returns:
        {metabolite_name: conc_mM} — entries with unparseable or NaN values dropped.
    """
    metabolite_concentrations = concentrations[concentrations['specie'] == 'metabolite']
    pss_metabolites           = {species[s]['name'] for s in species if species[s]['type'] == 'metabolite'}

    metabolite_conc = {
        compound: conc
        for compound, conc in (
            metabolite_concentrations[metabolite_concentrations['compound'].isin(pss_metabolites)]
            [['compound', 'conc. [mM]']]
            .values
        )
    }
    return {
        m: c for m, c in metabolite_conc.items()
        if c is not None and pd.notna(c)
    }


# ── Mapper builder ─────────────────────────────────────────────────────────────

def build_concentration_mapper(
    species:                       dict,
    protein_conc:                  dict,
    metabolite_conc:               dict,
    protein_conc_global_median:    float,
    metabolite_conc_global_median: float,
) -> callable:
    """Build a self-contained initial-concentration callable and return it.

    All data is captured in the closure at build time so the returned
    callable can be serialised with dill and loaded independently —
    no external files or imports required at call time.

    Args:
        species:                       Species dict keyed by ID.
        protein_conc:                  {name: [conc, ...]} for mapped proteins (mM).
        metabolite_conc:               {name: conc} for mapped metabolites (mM).
        protein_conc_global_median:    Fallback for unmapped proteins (mM).
        metabolite_conc_global_median: Fallback for unmapped metabolites (mM).

    Returns:
        initial_concentration(specie_id: str) → float
    """
    # Bake all data into plain local variables — no external references.
    _species_type = {sid: info['type'] for sid, info in species.items()}
    _species_name = {sid: info['name'] for sid, info in species.items()}
    _protein_conc = {k: list(v) for k, v in protein_conc.items()}
    _met_conc     = dict(metabolite_conc)
    _prot_median  = protein_conc_global_median
    _met_median   = metabolite_conc_global_median
    _gene_conc    = GENE_CONC
    _rna_conc     = RNA_CONC

    def initial_concentration(specie: str) -> float:
        """Return the initial concentration (mM) for *specie*.

        Self-contained: all lookup data is baked into this closure.
        Safe to serialise with dill and load in a fresh Python session.
        """
        stype = _species_type[specie]
        name  = _species_name[specie]

        if stype in ('protein', 'protein_active'):
            return np.median(_protein_conc[name]) if name in _protein_conc else _prot_median
        if stype == 'metabolite':
            return _met_conc[name] if name in _met_conc else _met_median
        if stype == 'gene':
            return _gene_conc
        if stype in ('mrna', 'ncrna', 'mirna'):
            return _rna_conc
        return _prot_median / 2   # complex, abiotic, process, process_active

    return initial_concentration


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--species",          required=True, help="Path to pss_species.json.")
    parser.add_argument("--concentrations",   required=True, help="Path to initial_concentrations.csv.")
    parser.add_argument("--output",           default="./",  help="Output directory (default: cwd).")
    args = parser.parse_args()

    species    = json.loads(Path(args.species).read_text())
    concentrations = pd.read_csv(args.concentrations)

    protein_conc    = protein_conc_from_file(concentrations, species)
    metabolite_conc = metabolite_conc_from_file(concentrations, species)

    protein_conc_global_median    = np.median(sum(protein_conc.values(), []))
    metabolite_conc_global_median = np.median(list(metabolite_conc.values()))

    concentration_mapper = build_concentration_mapper(
        species,
        protein_conc,
        metabolite_conc,
        protein_conc_global_median,
        metabolite_conc_global_median,
    )

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "initial_concentration_mapper.pkl", "wb") as f:
        dill.dump(concentration_mapper, f)

    print(f"Mapped {len(protein_conc):>4} / {len([s for s in species if species[s]['type'] in ('protein', 'protein_active')])} proteins")
    print(f"Mapped {len(metabolite_conc):>4} / {len([s for s in species if species[s]['type'] == 'metabolite'])} metabolites")
    print(f"Protein global median  → {protein_conc_global_median:.4e} mM")
    print(f"Metabolite global median → {metabolite_conc_global_median:.4e} mM")
    print(f"Saved concentration mapper → {out / 'initial_concentration_mapper.pkl'}")


if __name__ == "__main__":
    main()