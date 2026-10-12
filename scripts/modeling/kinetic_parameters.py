"""
kinetic_parameters.py — Build and serialise a self-contained kinetics mapper for PSS reactions.

Usage:
    python kinetic_parameters.py --species    <pss_species.json>
                                 --reactions  <pss_reactions.json>
                                 --gathered   <kinetic_parameters.xlsx>
                                 --nib        <kinetic_nib_parameters.xlsx>
                                 --nib-sheet  <sheet_name>
                                 --output     <./>

Loading the saved mapper later (no other files needed):
    import dill
    kinetics = dill.load(open("kinetic_parameter_mapper.pkl", "rb"))
    kinetics("rx00041")   # → {'s_LHB1_cyt_p': (0.14, 3.2e-3), ...}
"""

from typing  import Optional
from pathlib import Path

import pandas as pd
import numpy  as np
import statistics
import argparse
import dill                 # type: ignore
import json


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_values(series: pd.Series) -> list:
    """Flatten a Series of newline-separated value strings into a flat list."""
    return sum(series.apply(lambda x: str(x).split('\n')).values.tolist(), [])


def _first_numeric(*candidates) -> Optional[float]:
    """Return the first candidate that converts to float, or None."""
    for v in candidates:
        try:
            return float(v)
        except (ValueError, TypeError):
            continue
    return None


def _parse_lun(raw) -> Optional[str]:
    """Extract the nominal (or best available) value from a 'lower/upper/nominal' string."""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    for p in reversed(str(raw).split('/')):
        try:
            float(p)
            return p
        except ValueError:
            continue
    return None


def _flat_km_kcat(values_dict: dict) -> tuple[list[float], list[float]]:
    """Extract flat lists of non-None Km and Kcat from a {reactant: (km, kcat)} dict."""
    kms   = [km   for km,   _    in values_dict.values() if km   is not None]
    kcats = [kcat for _,    kcat in values_dict.values() if kcat is not None]
    return kms, kcats


# ── Unit conversions ───────────────────────────────────────────────────────────

def km_unit_conversion(
    value, unit,
    molar_mass: Optional[float] = None,
    verbose:    bool = False,
    reaction:   Optional[str] = None,
) -> Optional[float]:
    """Convert Km to mM from various source units.

    Args:
        value:      Raw Km value.
        unit:       Unit string (e.g. 'nM', 'uM', 'ppm', 'µMol/L').
        molar_mass: Required for ppm conversion (g/mol).
        verbose:    Print warnings for unsupported/unconvertible units.
        reaction:   Reaction ID for warning messages.

    Returns:
        Km in mM, or None if conversion is not possible.
    """
    if not (value or unit):
        return value
    unit = unit.strip().lower()
    if unit == 'nm':                    return float(value) / 1e6
    if unit in ('um', 'μm', 'µmol/l'): return float(value) / 1e3
    if unit == 'mm':                    return float(value)
    if unit == 'ppm':
        if molar_mass:                  return float(value) / molar_mass
        if verbose: print(f"{reaction}: molar mass required to convert ppm → mM.")
        return None
    if verbose: print(f"{reaction}: unsupported Km unit '{unit}'.")
    return None


def kcat_unit_conversion(
    value, unit,
    enzyme_molar_mass: Optional[float] = None,
    enzyme_conc:       Optional[float] = None,
    verbose:           bool = False,
    reaction:          Optional[str] = None,
) -> Optional[float]:
    """Convert Kcat to 1/s from various source units.

    Args:
        value:             Raw Kcat value.
        unit:              Unit string (e.g. '1/min', 'µM/mg*min').
        enzyme_molar_mass: In g/mol — required for µM/mg*min conversion.
        enzyme_conc:       In nM — required for nM/min conversion.
        verbose:           Print warnings.
        reaction:          Reaction ID for warning messages.

    Returns:
        Kcat in 1/s, or None if conversion is not possible.
    """
    if not (value or unit):
        return value
    unit = unit.strip().lower()
    if unit == '1/s':   return float(value)
    if unit == '1/min': return float(value) / 60
    if unit in ('µm/mg*min', 'um/mg*min'):
        if enzyme_molar_mass: return (float(value) * enzyme_molar_mass) / 60
        if verbose: print(f"{reaction}: molar mass required for µM/mg*min conversion.")
        return None
    if unit == 'nm/min':
        if enzyme_conc: return float(value) / enzyme_conc
        if verbose: print(f"{reaction}: enzyme concentration required for nM/min conversion.")
        return None
    if verbose: print(f"{reaction}: unsupported Kcat unit '{unit}'.")
    return None


# ── Data loaders ───────────────────────────────────────────────────────────────

def load_gathered(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load gathered (BRENDA/SABIO-RK) and estimated (CatPred) kinetics sheets."""
    xl = pd.ExcelFile(path)
    return (
        pd.read_excel(xl, 'Gathered_Final', header=[0, 1]),
        pd.read_excel(xl, 'Estimated',      header=[0, 1, 2]),
    )


def load_nib(path: Path, sheet: str) -> pd.DataFrame:
    """Load and normalise the NIB annotated kinetics Excel sheet."""
    nib = pd.read_excel(path, sheet_name=sheet, header=[0, 1])
    nib.columns = pd.MultiIndex.from_tuples([
        (c[0] if c[0] in ('Km', 'Kcat') else ' ', *c[1:])
        for c in nib.columns
    ])
    return nib[[
        (' ', 'reaction_id'),
        (' ', 'reaction_type'),
        (' ', 'reaction_effect'),
        (' ', 'rate_law (SBO)'),
        (' ', 'Equation (Antimony)'),
        ('Km',   'lowerBound/upperBound/nominalValue 2'),
        ('Km',   'Units 2'),
        ('Kcat', 'lowerBound/upperBound/nominalValue'),
        ('Kcat', 'Units'),
    ]]


# ── Per-reaction lookup functions ──────────────────────────────────────────────

def get_kinetics(
    reaction:           str,
    reactions:          dict,
    gathered_kinetics:  pd.DataFrame,
    estimated_kinetics: pd.DataFrame,
) -> dict[str, tuple]:
    """Look up Km and Kcat for each reactant from gathered/estimated data.

    Priority per value: BRENDA → SABIO-RK → CatPred estimate.

    Args:
        reaction:           Reaction ID.
        reactions:          PSS reactions dict.
        gathered_kinetics:  DataFrame from 'Gathered_Final' sheet.
        estimated_kinetics: DataFrame from 'Estimated' sheet.

    Returns:
        {reactant_id: (km_mM, kcat_1s)} — value is None where unavailable.

    Raises:
        ValueError: If reaction is not present in gathered_kinetics.
    """
    rxn_col = ('Unnamed: 0_level_0', 'reaction')
    if reaction not in gathered_kinetics[rxn_col].values:
        raise ValueError(f'{reaction} not found in gathered/estimated kinetics!')

    gathered  = gathered_kinetics[gathered_kinetics[rxn_col] == reaction]
    estimated = estimated_kinetics[
        estimated_kinetics[('reaction', 'Unnamed: 0_level_1', 'Unnamed: 0_level_2')] == reaction
    ]

    km_brenda    = _parse_values(gathered[('BRENDA',   'km (mM)')])
    kcat_brenda  = _parse_values(gathered[('BRENDA',   'kcat (1/s)')])
    km_sabiork   = _parse_values(gathered[('SABIO-RK', 'km (mM)')])
    kcat_sabiork = _parse_values(gathered[('SABIO-RK', 'kcat (1/s)')])
    km_est       = _parse_values(estimated[('CatPred', 'Km [mM]',    'Unnamed: 17_level_2')])
    kcat_est     = _parse_values(estimated[('CatPred', 'Kcat [1/s]', 'Unnamed: 18_level_2')])

    return {
        reactant: (
            _first_numeric(km_brenda[i],   km_sabiork[i],   km_est[i]   if i < len(km_est)   else None),
            _first_numeric(kcat_brenda[i], kcat_sabiork[i], kcat_est[i] if i < len(kcat_est) else None),
        )
        for i, reactant in enumerate(reactions[reaction]['reactants'])
    }


def get_kinetics_nib(
    reaction:      str,
    reactions:     dict,
    nib_reactions: pd.DataFrame,
    verbose:       bool = False,
) -> dict[str, tuple]:
    """Look up Km and Kcat for each reactant from the NIB file.

    A single Km/Kcat pair is shared across all reactants of the reaction,
    since the NIB file provides at most one value per reaction.

    Args:
        reaction:      Reaction ID.
        reactions:     PSS reactions dict.
        nib_reactions: DataFrame from the NIB Excel sheet.
        verbose:       Forward to unit conversion functions.

    Returns:
        {reactant_id: (km_mM, kcat_1s)} — value is None where unavailable.

    Raises:
        ValueError: If reaction is not present in nib_reactions.
    """
    if reaction not in nib_reactions[' ', 'reaction_id'].values:
        raise ValueError(f'{reaction} not found in NIB reactions!')

    row       = nib_reactions[nib_reactions[(' ', 'reaction_id')] == reaction]
    km_unit   = row['Km',   'Units 2'].values[0]
    kcat_unit = row['Kcat', 'Units'].values[0]

    km_unit   = None if (km_unit   is None or (isinstance(km_unit,   float) and np.isnan(km_unit)))   else km_unit
    kcat_unit = None if (kcat_unit is None or (isinstance(kcat_unit, float) and np.isnan(kcat_unit))) else kcat_unit

    km   = km_unit_conversion(
        _parse_lun(row['Km',   'lowerBound/upperBound/nominalValue 2'].values[0]),
        km_unit, verbose=verbose, reaction=reaction,
    )
    kcat = kcat_unit_conversion(
        _parse_lun(row['Kcat', 'lowerBound/upperBound/nominalValue'].values[0]),
        kcat_unit, verbose=verbose, reaction=reaction,
    )

    return {reactant: (km, kcat) for reactant in reactions[reaction]['reactants']}


# ── Global fallbacks ───────────────────────────────────────────────────────────

def compute_global_kinetics(reactions: dict, nib_reactions: pd.DataFrame) -> dict[str, dict]:
    """Compute median Km and Kcat per reaction type from the NIB file."""
    global_kinetics: dict[str, dict] = {}
    for rxn_type in nib_reactions[' ', 'reaction_type'].unique():
        type_rxns = nib_reactions[
            nib_reactions[' ', 'reaction_type'] == rxn_type
        ][' ', 'reaction_id'].tolist()
        kms, kcats = [], []
        for rxn_id in type_rxns:
            if rxn_id not in reactions:
                continue
            try:
                kms_r, kcats_r = _flat_km_kcat(get_kinetics_nib(rxn_id, reactions, nib_reactions))
                kms.extend(kms_r)
                kcats.extend(kcats_r)
            except ValueError:
                continue
        global_kinetics[rxn_type] = {
            'km_median':   statistics.median(kms)   if kms   else None,
            'kcat_median': statistics.median(kcats) if kcats else None,
        }
    return global_kinetics


def compute_global_medians(
    reactions:          dict,
    gathered_kinetics:  pd.DataFrame,
    estimated_kinetics: pd.DataFrame,
    nib_reactions:      pd.DataFrame,
) -> tuple[float, float]:
    """Compute overall median Km and Kcat across all gathered + NIB values."""
    all_kms, all_kcats = [], []
    for rxn_id in reactions:
        try:
            kms_r, kcats_r = _flat_km_kcat(
                get_kinetics(rxn_id, reactions, gathered_kinetics, estimated_kinetics)
            )
        except ValueError:
            try:
                kms_r, kcats_r = _flat_km_kcat(
                    get_kinetics_nib(rxn_id, reactions, nib_reactions)
                )
            except ValueError:
                continue
        all_kms.extend(kms_r)
        all_kcats.extend(kcats_r)

    return (
        float(np.median([v for v in all_kms   if v is not None])),
        float(np.median([v for v in all_kcats if v is not None])),
    )


# ── Mapper builder ─────────────────────────────────────────────────────────────

def build_kinetics_mapper(
    reactions:          dict,
    gathered_kinetics:  pd.DataFrame,
    estimated_kinetics: pd.DataFrame,
    nib_reactions:      pd.DataFrame,
    global_kinetics:    dict,
    global_median_km:   float,
    global_median_kcat: float,
) -> callable:
    """Build a self-contained kinetics callable and return it.

    Lookup priority: gathered → NIB → type median → global median.
    All data and helper functions are baked into the closure at build time.

    Args:
        reactions:           PSS reactions dict.
        gathered_kinetics:   DataFrame from 'Gathered_Final' sheet.
        estimated_kinetics:  DataFrame from 'Estimated' sheet.
        nib_reactions:       DataFrame from the NIB Excel sheet.
        global_kinetics:     {reaction_type: {km_median, kcat_median}}.
        global_median_km:    Overall Km fallback (mM).
        global_median_kcat:  Overall Kcat fallback (1/s).

    Returns:
        kinetics(reaction_id: str) → {reactant_id: (km_mM, kcat_1s)}
    """
    # Bake data into plain locals — no external references survive dill.load().
    _reactions       = {rid: dict(r) for rid, r in reactions.items()}
    _gathered        = gathered_kinetics.copy()
    _estimated       = estimated_kinetics.copy()
    _nib             = nib_reactions.copy()
    _global_kinetics = {k: dict(v) for k, v in global_kinetics.items()}
    _global_km       = global_median_km
    _global_kcat     = global_median_kcat

    def kinetics(reaction_id: str, verbose: bool = False) -> dict[str, tuple]:
        """Return {reactant_id: (km_mM, kcat_1s)} for *reaction_id*.

        Falls back through NIB → type median → global median as needed.
        Fully self-contained: all helpers are defined inline so this function
        is safe to serialise with dill and load in a fresh Python session.
        """
        import numpy as np

        # ── inline helpers ─────────────────────────────────────────────────────

        def parse_values(series):
            return sum(series.apply(lambda x: str(x).split('\n')).values.tolist(), [])

        def first_numeric(*candidates):
            for v in candidates:
                try:    return float(v)
                except (ValueError, TypeError): continue
            return None

        def parse_lun(raw):
            if raw is None or (isinstance(raw, float) and np.isnan(raw)):
                return None
            for p in reversed(str(raw).split('/')):
                try:    float(p); return p
                except ValueError: continue
            return None

        def km_conv(value, unit):
            if not (value or unit): return value
            unit = unit.strip().lower()
            if unit == 'nm':                    return float(value) / 1e6
            if unit in ('um', 'μm', 'µmol/l'):  return float(value) / 1e3
            if unit == 'mm':                    return float(value)
            if verbose: print(f"{reaction_id}: unsupported Km unit '{unit}'.")
            return None

        def kcat_conv(value, unit):
            if not (value or unit): return value
            unit = unit.strip().lower()
            if unit == '1/s':   return float(value)
            if unit == '1/min': return float(value) / 60
            if verbose: print(f"{reaction_id}: unsupported Kcat unit '{unit}'.")
            return None

        def lookup_gathered(rxn_id):
            rxn_col = ('Unnamed: 0_level_0', 'reaction')
            if rxn_id not in _gathered[rxn_col].values:
                raise ValueError(f'{rxn_id} not found in gathered kinetics.')
            g = _gathered[_gathered[rxn_col] == rxn_id]
            e = _estimated[
                _estimated[('reaction', 'Unnamed: 0_level_1', 'Unnamed: 0_level_2')] == rxn_id
            ]
            km_b   = parse_values(g[('BRENDA',   'km (mM)')])
            kcat_b = parse_values(g[('BRENDA',   'kcat (1/s)')])
            km_s   = parse_values(g[('SABIO-RK', 'km (mM)')])
            kcat_s = parse_values(g[('SABIO-RK', 'kcat (1/s)')])
            km_e   = parse_values(e[('CatPred',  'Km [mM]',    'Unnamed: 17_level_2')])
            kcat_e = parse_values(e[('CatPred',  'Kcat [1/s]', 'Unnamed: 18_level_2')])
            return {
                reactant: (
                    first_numeric(km_b[i],   km_s[i],   km_e[i]   if i < len(km_e)   else None),
                    first_numeric(kcat_b[i], kcat_s[i], kcat_e[i] if i < len(kcat_e) else None),
                )
                for i, reactant in enumerate(_reactions[rxn_id]['reactants'])
            }

        def lookup_nib(rxn_id):
            if rxn_id not in _nib[' ', 'reaction_id'].values: 
                raise ValueError(f'{rxn_id} not found in NIB reactions.')
            row       = _nib[_nib[(' ', 'reaction_id')] == rxn_id]
            km_unit   = row['Km',   'Units 2'].values[0]
            kcat_unit = row['Kcat', 'Units'].values[0]
            km_unit   = None if (km_unit   is None or (isinstance(km_unit,   float) and np.isnan(km_unit)))   else km_unit
            kcat_unit = None if (kcat_unit is None or (isinstance(kcat_unit, float) and np.isnan(kcat_unit))) else kcat_unit
            km   = km_conv(parse_lun(row['Km',     'lowerBound/upperBound/nominalValue 2'].values[0]), km_unit)
            kcat = kcat_conv(parse_lun(row['Kcat', 'lowerBound/upperBound/nominalValue'].values[0]), kcat_unit)
            return {reactant: (km, kcat) for reactant in _reactions[rxn_id]['reactants']}

        # ── lookup chain ───────────────────────────────────────────────────────

        if reaction_id not in _reactions:
            return {'global': (_global_km, _global_kcat)}

        values = None
        try:    values = lookup_gathered(reaction_id)
        except ValueError: pass

        if values is None:
            try:    values = lookup_nib(reaction_id)
            except ValueError: pass

        if not values:
            return {'global': (_global_km, _global_kcat)}

        type_row  = _nib[_nib[(' ', 'reaction_id')] == reaction_id]
        rxn_type  = type_row[(' ', 'reaction_type')].values[0] if not type_row.empty else None
        type_km   = _global_kinetics.get(rxn_type, {}).get('km_median',   _global_km)
        type_kcat = _global_kinetics.get(rxn_type, {}).get('kcat_median', _global_kcat)

        return {
            reactant: (
                km   if km   is not None else (type_km   if type_km   is not None else _global_km),
                kcat if kcat is not None else (type_kcat if type_kcat is not None else _global_kcat),
            )
            for reactant, (km, kcat) in values.items()
        }
    
    return kinetics


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--species",    required=True, help="Path to pss_species.json.")
    parser.add_argument("--reactions",  required=True, help="Path to pss_reactions.json.")
    parser.add_argument("--gathered",   required=True, help="Path to kinetic_parameters.xlsx.")
    parser.add_argument("--nib",        required=True, help="Path to kinetic_nib_parameters.xlsx.")
    parser.add_argument("--nib-sheet",  required=True, help="Sheet name in the NIB Excel file.")
    parser.add_argument("--output",     default="./",  help="Output directory (default: cwd).")
    args = parser.parse_args()

    reactions = json.loads(Path(args.reactions).read_text())

    gathered_kinetics, estimated_kinetics = load_gathered(Path(args.gathered))
    nib_reactions = load_nib(Path(args.nib), args.nib_sheet)

    global_kinetics                      = compute_global_kinetics(reactions, nib_reactions)
    global_median_km, global_median_kcat = compute_global_medians(
        reactions, gathered_kinetics, estimated_kinetics, nib_reactions,
    )

    kinetics_mapper = build_kinetics_mapper(
        reactions,
        gathered_kinetics,
        estimated_kinetics,
        nib_reactions,
        global_kinetics,
        global_median_km,
        global_median_kcat,
    )

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "kinetic_parameter_mapper.pkl", "wb") as f:
        dill.dump(kinetics_mapper, f)

    print(f"Global median Km   → {global_median_km:.4e} mM")
    print(f"Global median Kcat → {global_median_kcat:.4e} 1/s")
    for key, val in global_kinetics.items(): print(f'{key:<45}: {"km_median: " + str(val["km_median"]):<40}; {"kcat_median: " + str(val["kcat_median"])}')
    print(f"Saved kinetics mapper → {out / 'kinetic_parameter_mapper.pkl'}")


if __name__ == "__main__":
    main()