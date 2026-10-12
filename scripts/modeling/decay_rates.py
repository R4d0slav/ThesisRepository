"""
decay_rates.py — Build and serialise a self-contained decay-rate mapper for PSS species.

Usage:
    python decay_rates.py --species <pss_species.json>
                          --reactions <pss_reactions.json>
                          --decay-file <ath_decay_rates.tsv>
                          --output <decay_rate_mapper.dill>

Loading the saved mapper later (no other files needed):
    import dill
    decay_rate = dill.load(open("decay_rate_mapper.dill", "rb"))
    decay_rate("s_LHB1_cyt_p")   # → 2.31e-7  or  None
"""

from pathlib import Path

import pandas as pd
import argparse
import dill                 # type: ignore
import json


# ── Constants ──────────────────────────────────────────────────────────────────

PROTEIN_DECAY    = 2.31e-7   # s⁻¹  (0.08 / 0.2 / 0.02 day⁻¹ → converted)
METABOLITE_DECAY = 0.001     # TODO: confirm appropriate value

RNA_TYPES     = frozenset(("mrna", "ncrna", "mirna"))
PROTEIN_TYPES = frozenset(("protein", "protein_active"))


# ── Helpers ────────────────────────────────────────────────────────────────────

def rna_median(decay_file: pd.DataFrame) -> float:
    """Return the median RNA decay rate over all loci in the ATH decay file."""
    return float(decay_file["decay"].median())


def terminal_set(reactions: dict, species: dict, types: frozenset) -> frozenset:
    """Return IDs of species that are products but never reactants, filtered by type."""
    # all_substrates = {s for r in reactions.values() for s in r["reactants"]}
    all_products   = {s for r in reactions.values() for s in r["products"]}
    return frozenset(
        s for s in all_products
        if species[s]["type"] in types
    )


# ── Mapper builder ─────────────────────────────────────────────────────────────

def build_decay_mapper(species: dict, reactions: dict, rna_rate: float) -> callable:
    """Build a self-contained decay-rate callable and return it.

    All data the function needs is captured in its closure at build time,
    so the returned callable can be serialised with dill and loaded
    independently — no external files or imports required at call time.

    Args:
        species:   Species dict keyed by ID (from pss_species.json).
        reactions: Reactions dict keyed by ID (from pss_reactions.json).
        rna_rate:  Median RNA decay rate in s⁻¹.

    Returns:
        decay_rate(specie_id: str) → float | None
    """
    # Pre-compute terminal sets once; bake them into the closure.
    _terminal_proteins    = terminal_set(reactions, species, PROTEIN_TYPES)
    _terminal_metabolites = terminal_set(reactions, species, frozenset(("metabolite",)))
    _terminal_rnas        = terminal_set(reactions, species, RNA_TYPES)

    # Snapshot every value the inner function needs — no external references.
    _species_type     = {sid: info["type"] for sid, info in species.items()}
    _rna_rate         = rna_rate
    _protein_decay    = PROTEIN_DECAY
    _metabolite_decay = METABOLITE_DECAY
    _rna_types        = RNA_TYPES
    _protein_types    = PROTEIN_TYPES

    def decay_rate(specie: str):
        """Return the decay rate (s⁻¹) for *specie*, or None if no degradation needed.

        Self-contained: all data is baked into this closure at build time.
        Safe to serialise with dill and load in a fresh Python session.
        """
        stype = _species_type[specie]
        if stype in _protein_types and specie in _terminal_proteins:
            return _protein_decay
        if stype in _rna_types and specie in _terminal_rnas:
            return _rna_rate
        if stype == "metabolite" and specie in _terminal_metabolites:
            return _metabolite_decay
        return None

    return decay_rate


# ── Main ───────────────────────────────────────────────────────────────────────
import numpy as np

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--species",    required=True, help="Path to pss_species.json.")
    parser.add_argument("--reactions",  required=True, help="Path to pss_reactions.json.")
    parser.add_argument("--decay-file", required=True, help="Path to ath_decay_rates.tsv.")
    parser.add_argument("--output",     default = "./", help="Output path for .dill mapper.")
    args = parser.parse_args()

    species    = json.loads(Path(args.species).read_text())
    reactions  = json.loads(Path(args.reactions).read_text())
    decay_file = pd.read_csv(args.decay_file, sep="\t")

    rna_rate     = rna_median(decay_file)
    decay_mapper = build_decay_mapper(species, reactions, rna_rate)

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    with open((out / "decay_rate_mapper.pkl"), "wb") as f:
        dill.dump(decay_mapper, f)

    print(f"RNA decay rate (all-loci median) → {rna_rate:.4e}")
    print("Example stats mapping PSS species:\n", "\n".join(f"\t{u[0]} ({u[1]}): {c}" for u, c in zip(*np.unique([(str(decay_mapper(s)), species[s]['type']) for s in species], return_counts=True, axis=0))))
    print(f"Saved decay-rate mapper          → {out}")


if __name__ == "__main__":
    main()