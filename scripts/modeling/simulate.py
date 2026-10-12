"""
simulate.py — Simulate the PSS Antimony model and export results.

Usage:
    python simulate.py   --antimony  <Antimony.txt>
                         --output    <./>
                         --start     0
                         --end       10000
                         --points    30000
                         --suffix    default
                         --events    <events.json>   # optional

Events file format
------------------
Two formats are supported, auto-detected at load time.

Intervention (list) — time-triggered assignments applied during simulation:
    [
        {"trigger": "time > 1000",
         "assignments": {"s_JA_cyt_m": "s_JA_cyt_m + 5.0"}}
    ]

Knockout (dict) — species and all reactions involving them are fully removed
before simulation. Optionally combined with time-triggered events:
    {
        "knockouts": ["s_COI1_nuc_g", "s_COI1_cyt_p", ...],
        "events":    [{"trigger": "...", "assignments": {...}}]
    }
"""

from typing  import Optional
from pathlib import Path

import matplotlib.pyplot as plt     # type: ignore
import plotly.express    as px      # type: ignore
import tellurium         as te      # type: ignore
import pandas            as pd
import numpy             as np
import argparse
import json
import re



# ── Helpers ────────────────────────────────────────────────────────────────────

def sim_to_df(sim) -> pd.DataFrame:
    """Convert a Tellurium simulation array to a tidy wide DataFrame.

    Args:
        sim: RoadRunner simulation result array.

    Returns:
        Wide DataFrame with a 'time' column and one column per species.
    """
    df         = pd.DataFrame(sim[:, 1:], columns=sim.colnames[1:])
    df['time'] = sim[:, 0]
    return df


def remove_species(antimony: str, species_ids: list[str]) -> str:
    """Completely remove species from the Antimony model.

    For each species, removes:
      - Species declaration line
      - Initial concentration line
      - Decay rate reaction (if present)
      - Every reaction whose stoichiometry line or rate-law expression
        references the species (as reactant, product, or modifier)
      - Rate-law lines for all removed reactions
      - Kinetic parameter lines for all removed reactions

    Args:
        antimony:    Antimony model string.
        species_ids: Species IDs to remove entirely.

    Returns:
        Modified Antimony model string with species and dependent reactions excised.
    """
    removed = set(species_ids)

    # ── 1. Collect reaction IDs that must be removed ───────────────────────────

    reactions_to_remove: set[str] = set()

    # Check stoichiometry lines:  \trx00041: <reactants> -> <products>; v_rx00041;
    # Gene species appear as $sid in the reaction body, so match both forms.
    for m in re.finditer(r'^\t(rx\w+):\s*(.+?);', antimony, re.MULTILINE):
        rxn_id, body = m.group(1), m.group(2)
        for sid in removed:
            if re.search(r'\$?' + re.escape(sid) + r'\b', body):
                reactions_to_remove.add(rxn_id)
                break

    # Check rate-law lines:  \tv_rx00041 := <expression>;
    # Catches reactions where the species appears only as a modifier.
    for m in re.finditer(r'^\tv_(rx\w+)\s*:=\s*(.+?);', antimony, re.MULTILINE):
        rxn_id, expr = m.group(1), m.group(2)
        for sid in removed:
            if re.search(r'\b' + re.escape(sid) + r'\b', expr):
                reactions_to_remove.add(rxn_id)
                break

    # ── 2. Filter line-by-line ─────────────────────────────────────────────────

    kept: list[str] = []
    for line in antimony.splitlines():

        # Species declaration:  "\tspecies s_xxx    in c_xxx;"
        #                   or: "\tspecies $s_xxx   in c_xxx;"  (boundary-marked)
        if any(
            re.match(rf'\s*species\s+\$?{re.escape(sid)}\b', line)
            for sid in removed
        ):
            continue

        # Concentration:  "\ts_xxx   = <num>;"
        if any(
            re.match(rf'\s*{re.escape(sid)}\s*=\s*[\d.e+\-]+\s*;', line)
            for sid in removed
        ):
            continue

        # Decay rate:  "\td_s_xxx: s_xxx -> ; ...;"
        if any(
            re.match(rf'\s*d_{re.escape(sid)}\s*:', line)
            for sid in removed
        ):
            continue

        # Reaction stoichiometry line:  "\trx00041: ... -> ...; v_rx00041;"
        m = re.match(r'\s*(rx\w+)\s*:', line)
        if m and m.group(1) in reactions_to_remove:
            continue

        # Rate-law line:  "\tv_rx00041 := ...;"
        m = re.match(r'\s*v_(rx\w+)\s*:=', line)
        if m and m.group(1) in reactions_to_remove:
            continue

        # Kinetic parameter lines for removed reactions.
        # Parameters always end with _<rxn_id>:
        #   kcat_rx00041 = ...;  /  km_rx00041 = ...;  /  Kd_s_xxx_rx00041 = ...;
        if any(
            re.search(rf'\w+_{re.escape(rxn_id)}\s*=', line)
            for rxn_id in reactions_to_remove
        ):
            continue

        kept.append(line)

    print(
        f"Knockout: removed {len(removed)} species "
        f"and {len(reactions_to_remove)} dependent reactions."
    )
    return '\n'.join(kept)


def _inject_event_list(antimony: str, events: list[dict]) -> str:
    """Replace the Events section in an Antimony model string with the given event list.

    Each event dict must have:
      - 'trigger':     Antimony trigger expression (e.g. 'time > 1000').
      - 'assignments': {variable: expression} dict of assignments to fire.

    Args:
        antimony: Antimony model string.
        events:   Parsed list of event dicts.

    Returns:
        Antimony model string with the Events section replaced.
    """
    event_lines = ["\t# Events:"]
    for i, event in enumerate(events):
        trigger     = event['trigger']
        # Antimony separates multiple event assignments with commas; a semicolon
        # would terminate the event and turn the rest into assignment rules
        # (which then fail as self-referential loops).
        assignments = ', '.join(
            f"{var} = {expr}"
            for var, expr in event['assignments'].items()
        )
        event_lines.append(f"\tE{i}: at ({trigger}): {assignments};")

    new_events_block = "\n".join(event_lines)

    # Replace everything from '# Events:' up to (but not including) 'end'
    return re.sub(
        r'\t# Events:.*?(?=^end)',
        new_events_block + "\n\n",
        antimony,
        flags=re.DOTALL | re.MULTILINE,
    )


def inject_events(antimony: str, events_path: Path) -> str:
    """Apply knockouts and/or time-triggered events from a JSON file.

    Detects the format automatically:
      - List  → intervention events (time-triggered assignments).
      - Dict  → knockout format; may also include an 'events' list.

    Args:
        antimony:    Antimony model string.
        events_path: Path to the events JSON file.

    Returns:
        Modified Antimony model string.
    """
    payload = json.loads(events_path.read_text())

    if isinstance(payload, list):
        # Legacy intervention format
        antimony = _inject_event_list(antimony, payload)
        print(f"Injected {len(payload)} intervention event(s) from {events_path.name}.")
        return antimony

    # Knockout / combined format
    knockouts = payload.get("knockouts", [])
    events    = payload.get("events",    [])

    if knockouts:
        antimony = remove_species(antimony, knockouts)
    if events:
        antimony = _inject_event_list(antimony, events)
        print(f"Injected {len(events)} intervention event(s) from {events_path.name}.")
    if not knockouts and not events:
        print(f"[WARN] {events_path.name}: no 'knockouts' or 'events' keys found — file has no effect.")

    return antimony


def simulate(sim, species_list: Optional[list] = None) -> None:
    """Plot simulation trajectories interactively with Plotly.

    Args:
        sim:          RoadRunner simulation result array.
        species_list: Optional subset of species column names to display.
                      Displays all species when None.
    """
    df = sim_to_df(sim)
    if species_list:
        df = df[['time'] + [s for s in species_list if s in df.columns]]

    fig = px.line(
        df.melt(id_vars='time', var_name='species', value_name='concentration'),
        x='time', y='concentration', color='species',
        title='Simulation Trajectories',
        labels={'time': 'Time', 'concentration': 'Concentration'},
        height=800,
    )
    fig.show()


def simulate_static(sim, species_list: Optional[list] = None) -> None:
    """Plot simulation trajectories as a static Matplotlib figure.

    Args:
        sim:          RoadRunner simulation result array.
        species_list: Optional subset of species column names to display.
                      Displays all species when None.
    """
    df = sim_to_df(sim)
    if species_list:
        df = df[['time'] + [s for s in species_list if s in df.columns]]

    plt.figure(figsize=(12, 6))
    for col in df.columns:
        if col != 'time':
            plt.plot(df['time'], df[col])
    plt.xlabel('Time')
    plt.ylabel('Concentration')
    plt.title('Simulation Trajectories')
    plt.legend().remove()
    plt.tight_layout()
    plt.show()


def diagnose(r, species_df: pd.DataFrame) -> None:
    """Print diagnostics: negative concentrations and max rate-of-change ranking.

    Args:
        r:          RoadRunner model instance.
        species_df: Species-only DataFrame (columns: time + [s_xxx]).
    """
    neg_rows = species_df[(species_df.drop(columns='time') < 0).any(axis=1)]
    if neg_rows.empty:
        print("No negative concentrations detected.")
    else:
        first_idx = neg_rows.index[0]
        print(f"First negative value at row {first_idx} "
              f"(time = {species_df.loc[first_idx, 'time']:.2f})")
        neg_cols = species_df.loc[first_idx].drop('time')
        print(neg_cols[neg_cols < 0])

    # Max rate-of-change per species
    time_points = species_df['time'].values
    derivatives = np.array([r.getRatesOfChange() for _ in time_points])
    species_ids = r.getFloatingSpeciesIds()
    max_deriv   = np.max(np.abs(derivatives), axis=0)
    ranked      = sorted(zip(species_ids, max_deriv), key=lambda x: x[1], reverse=True)

    print(f"\n{'Species':<35}  max |dS/dt|")
    print("-" * 55)
    for sp, val in ranked:
        print(f"{sp:<35}  {val:.3e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--antimony", required=True,  help="Path to Antimony.txt.")
    parser.add_argument("--start",    type=float, default=0,      help="Simulation start time (default: 0).")
    parser.add_argument("--end",      type=float, default=10_000, help="Simulation end time (default: 10000).")
    parser.add_argument("--points",   type=int,   default=30_000, help="Number of simulation points (default: 30000).")
    parser.add_argument("--suffix",   default="default",          help="Suffix appended to output file names (default: 'default').")
    parser.add_argument("--events",   default=None,               help="Path to events JSON file (optional).")
    parser.add_argument("--set-initial", default=None, dest="set_initial",
                        help="Perturbed initial conditions as 'species=value,...', applied at t=0 before a "
                             "single integration. Also read from an events file with a top-level 'set_initial' object.")
    parser.add_argument("--output",   default="./",               help="Output directory (default: cwd).")
    # ── CVODE integrator tuning (optional; omit to keep solver defaults) ──
    parser.add_argument("--rel_tol",   type=float, default=None, help="CVODE relative tolerance. Loosen (e.g. 1e-4) to help very stiff models converge; default: solver default (~1e-6).")
    parser.add_argument("--abs_tol",   type=float, default=None, help="CVODE absolute tolerance (e.g. 1e-10); default: solver default (~1e-12).")
    parser.add_argument("--max_steps", type=int,   default=None, help="CVODE max internal steps per output interval; raise for stiff models; default: solver default.")
    args = parser.parse_args()

    antimony = Path(args.antimony).read_text()

    # Collect perturbed initial conditions from an events file and/or the CLI.
    set_initial: dict = {}
    if args.events:
        payload = json.loads(Path(args.events).read_text())
        if isinstance(payload, dict) and "set_initial" in payload:
            set_initial.update(payload["set_initial"])
        else:
            antimony = inject_events(antimony, Path(args.events))
    if args.set_initial:
        for pair in args.set_initial.split(","):
            name, _, value = pair.partition("=")
            set_initial[name.strip()] = float(value)

    r = te.loada(antimony)
    species_ids  = list(r.getFloatingSpeciesIds())
    reaction_ids = list(r.getReactionIds())
    print(f"Model loaded — {len(species_ids)} floating species, {len(reaction_ids)} reactions.")

    r.reset()

    # Perturbed initial conditions: mid-run event injection is infeasible for stiff
    # models like PSS (see documentation/EVENT_INJECTION_FINDINGS.md), so a stress is
    # applied by setting the target species' starting concentration and running a
    # single uninterrupted integration from t=0.
    for sp, val in set_initial.items():
        try:
            r[f"[{sp}]"] = val
            print(f"Set initial [{sp}] = {val}")
        except Exception as e:
            print(f"[WARN] could not set initial [{sp}]: {e}")

    r.setIntegrator('cvode')
    # Apply optional integrator overrides — only touch what was passed so the
    # solver keeps its defaults otherwise.
    if args.rel_tol is not None:
        r.integrator.setValue('relative_tolerance', args.rel_tol)
    if args.abs_tol is not None:
        r.integrator.setValue('absolute_tolerance', args.abs_tol)
    if args.max_steps is not None:
        r.integrator.setValue('maximum_num_steps', args.max_steps)
    tol_msg = ", ".join(
        f"{k}={v}" for k, v in
        (('rel_tol', args.rel_tol), ('abs_tol', args.abs_tol), ('max_steps', args.max_steps))
        if v is not None
    )
    if tol_msg:
        print(f"Integrator overrides: {tol_msg}")
    r.selections = ['time'] + [f'[{s}]' for s in species_ids] + reaction_ids

    try:
        sim = r.simulate(args.start, args.end, points=args.points)
    except Exception as e:
        print(f"[WARN] Simulation failed for suffix='{args.suffix}': {e}")
        print("[WARN] Skipping outputs — other simulations will continue.")
        return

    print(f"Simulation complete ({args.points} points, t={args.start}–{args.end}, suffix='{args.suffix}').")

    out        = Path(args.output)
    dir_tables = out / "tables"
    dir_images = out / "images"
    dir_html   = out / "interactive"
    for d in (dir_tables, dir_images, dir_html):
        d.mkdir(parents=True, exist_ok=True)

    # Split combined result into species and flux DataFrames
    df_all      = pd.DataFrame(sim, columns=sim.colnames)
    species_cols = ['time'] + [c for c in df_all.columns if c.startswith('[')]
    flux_cols    = ['time'] + [c for c in df_all.columns if c in set(reaction_ids)]
    species_df   = df_all[species_cols].copy()
    flux_df      = df_all[flux_cols].copy()

    # Rename bracketed columns to bare species IDs for downstream use
    species_df.columns = ['time'] + species_ids

    species_df.to_csv(dir_tables / f"simulation_{args.suffix}.csv", index=False)
    print(f"Saved trajectories    → {dir_tables / f'simulation_{args.suffix}.csv'}")

    # ── Reaction fluxes ────────────────────────────────────────────────────────
    if reaction_ids:
        flux_df.to_csv(dir_tables / f"reaction_fluxes_{args.suffix}.csv", index=False)

        totals = {rxn: float(np.trapz(flux_df[rxn].abs(), flux_df['time']))
                  for rxn in reaction_ids if rxn in flux_df.columns}
        pd.DataFrame([totals]).to_csv(
            dir_tables / f"reaction_totals_{args.suffix}.csv", index=False)
        print(f"Saved reaction fluxes  → {dir_tables / f'reaction_fluxes_{args.suffix}.csv'}")
        print(f"Saved reaction totals  → {dir_tables / f'reaction_totals_{args.suffix}.csv'}")

    # ── Plots ──────────────────────────────────────────────────────────────────
    long_df = species_df.melt(id_vars='time', var_name='species', value_name='concentration')

    fig = px.line(
        long_df,
        x='time', y='concentration', color='species',
        title=f'Simulation Trajectories [{args.suffix}]',
        labels={'time': 'Time', 'concentration': 'Concentration'},
        height=800,
    )
    fig.write_html(str(dir_html   / f"simulation_{args.suffix}.html"))
    fig.write_image(str(dir_images / f"simulation_{args.suffix}.jpg"))
    print(f"Saved interactive plot → {dir_html   / f'simulation_{args.suffix}.html'}")
    print(f"Saved static plot      → {dir_images / f'simulation_{args.suffix}.jpg'}")

    diagnose(r, species_df)


if __name__ == "__main__":
    main()
