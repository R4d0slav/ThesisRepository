"""
rate_laws.py — Build and serialise a self-contained kinetic rate-law mapper for PSS reactions.

Usage:
    python rate_laws.py --reactions <pss_reactions.json>
                        --excel     <reactions_by_combination.xlsx>
                        --sheet     <sheet_name>
                        --output    <./>

Loading the saved mapper later (no other files needed):
    import dill
    rate_law = dill.load(open("rate_law_mapper.pkl", "rb"))
    rate_law("rx00608")   # → "kcat*M*S / (S + Kd_s_LHB1_cyt_p)"  or  None
"""

from pathlib import Path

import pandas as pd
import argparse
import dill                 # type: ignore
import json


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_rid_sbo(excel_path: Path, sheet: str) -> dict[str, tuple[str, str]]:
    """Load the reaction ID → (SBO term, functional form) mapping from Excel.

    Args:
        excel_path: Path to the reactions_by_combination Excel file.
        sheet:      Sheet name to read.

    Returns:
        {reaction_id: (sbo, func_form)} — last entry wins on duplicates.
    """
    excel = pd.read_excel(excel_path, sheet_name=sheet, skiprows=0)

    rid_sbo: dict[str, tuple[str, str]] = {}
    for sbo, ids, func_form in excel[['rate_law_SBO', 'reaction_ids', 'Function_form']].values:
        for rid in ids.split(', '):
            rid = rid.strip()
            if rid in rid_sbo:
                print(f"⚠️  Duplicate reaction ID: {rid} (SBO={sbo})")
            rid_sbo[rid] = (sbo, func_form)

    return rid_sbo


def replace_species(equation: str, reaction_id: str, reaction_data: dict) -> str:
    """Replace S/P/M tokens in a kinetic equation RHS with PSS species IDs.

    Only tokens on the RHS are validated. A `dP` on the LHS is ignored —
    the function returns only the RHS expression, ready for use in Antimony.

    Args:
        equation:      Functional form string, e.g. 'dP = kcat*[M]*[S] / ([S] + Kd_S)'.
        reaction_id:   Reaction ID (used in error messages only).
        reaction_data: Dict with keys 'reactants', 'products', 'modifiers'.

    Returns:
        Cleaned RHS string with species IDs substituted.

    Raises:
        ValueError: If the equation has no '=', or if any RHS token is unmapped.
    """
    import re

    reactants = reaction_data.get('reactants', []) or []
    products  = reaction_data.get('products',  []) or []
    modifiers = reaction_data.get('modifiers', []) or []

    def lookup(token: str):
        """Resolve a bare S/P/M token to the corresponding species ID."""
        m = re.fullmatch(r'([SPM])(\d*)', token)
        if not m:
            return None
        letter, num = m.groups()
        idx = (int(num) if num else 1) - 1
        if letter == 'S': return reactants[idx] if idx < len(reactants) else None
        if letter == 'P': return products[idx]  if idx < len(products)  else None
        if letter == 'M': return modifiers[idx] if idx < len(modifiers) else None
        return None

    if '=' not in equation:
        raise ValueError(f"Reaction {reaction_id}: equation has no '=' → {equation!r}")

    _, rhs = equation.split('=', 1)

    used:   set[str] = set()
    mapped: set[str] = set()

    def repl(match) -> str:
        token = match.group(0)
        if token.startswith('Kd_'):
            base = token[3:]
            used.add(base)
            species = lookup(base)
            if species:
                mapped.add(base)
                return f'Kd_{species}'
            return token
        used.add(token)
        species = lookup(token)
        if species:
            mapped.add(token)
            return species
        return token

    new_rhs  = re.sub(r'\bKd_[SPM]\d*\b|\b[SPM]\d*\b', repl, rhs)
    unmapped = sorted(used - mapped)

    if unmapped:
        raise ValueError(
            f"Reaction {reaction_id}: unmapped tokens → {unmapped}"
        )

    return (
        new_rhs
        .replace('k_cat', 'kcat')
        .replace('**',    '^')
        .replace('[',     '')
        .replace(']',     '')
    )


# ── Mapper builder ─────────────────────────────────────────────────────────────

def build_rate_law_mapper(rid_sbo: dict, reactions: dict) -> callable:
    """Build a self-contained rate-law callable and return it.

    All data is captured in the closure at build time so the returned
    callable can be serialised with dill and loaded independently.

    Args:
        rid_sbo:   {reaction_id: (sbo, func_form)} mapping.
        reactions: PSS reactions dict keyed by reaction ID.

    Returns:
        rate_law(reaction_id: str) → str | None

    Raises:
        ValueError: Propagated from replace_species if any RHS token is unmapped.
        KeyError:   If reaction_id is not found in rid_sbo or reactions.
    """
    import numpy as np

    _rid_sbo   = dict(rid_sbo)
    _reactions = {rid: dict(r) for rid, r in reactions.items()}
    _replace_species  = replace_species  # bake the function into the closure


    def rate_law(reaction_id: str):
        """Return the parsed kinetic rate-law RHS for *reaction_id*.

        Returns None only if the functional form is missing (NaN) in the
        Excel sheet — all other failure modes raise.
        """
        func_form = _rid_sbo[reaction_id][1]
        if isinstance(func_form, float) and np.isnan(func_form):
            return None

        return _replace_species(func_form, reaction_id, _reactions[reaction_id])

    return rate_law


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reactions",  required=True, help="Path to pss_reactions.json.")
    parser.add_argument("--excel",      required=True, help="Path to reactions_by_combination.xlsx.")
    parser.add_argument("--sheet",      required=True, help="Sheet name in the Excel file.")
    parser.add_argument("--output",     default="./",  help="Output directory (default: cwd).")
    args = parser.parse_args()

    reactions = json.loads(Path(args.reactions).read_text())
    rid_sbo   = load_rid_sbo(Path(args.excel), args.sheet)

    rate_law_mapper = build_rate_law_mapper(rid_sbo, reactions)

    # Validate all reactions before saving — fail fast on any unmapped tokens.
    resolved = []
    missing  = []
    errors   = []

    for rxn_id in reactions:
        try:
            eq = rate_law_mapper(rxn_id)
            if eq is None:
                missing.append(rxn_id)
            else:
                resolved.append(rxn_id)
        except (ValueError, KeyError) as e:
            errors.append((rxn_id, str(e)))

    print(f"Resolved : {len(resolved):>5}")
    print(f"Missing  : {len(missing):>5}  (NaN functional form in Excel)")
    print(f"Errors   : {len(errors):>5}  (unmapped tokens / not in rid_sbo)")

    if errors:
        print("\nFailed reactions:")
        for rxn_id, msg in errors:
            print(f"  {msg}")
        raise SystemExit("Fix the errors above before saving the mapper.")

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "rate_law_mapper.pkl", "wb") as f:
        dill.dump(rate_law_mapper, f)

    print(f"Saved rate-law mapper → {out / 'rate_law_mapper.pkl'}")


if __name__ == "__main__":
    main()