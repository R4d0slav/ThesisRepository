import os
import re
import json
import numpy as np
import pandas as pd


# ── Utilities ─────────────────────────────────────────────────────────────────

def safe_float(value):
    try:
        value = float(value)
        if pd.isna(value):
            return None
        return value
    except Exception:
        return None


def load_simulation_table(full_path):
    ext = os.path.splitext(full_path)[1].lower()
    if ext == '.csv':
        df = pd.read_csv(full_path)
    elif ext in ('.xlsx', '.xls'):
        df = pd.read_excel(full_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    if df.empty:
        raise ValueError("Simulation table is empty")

    time_col = None
    for col in df.columns:
        if str(col).strip().lower() in ('time', 't'):
            time_col = col
            break
    if time_col is None:
        time_col = df.columns[0]

    df = df.copy()
    df = df.rename(columns={time_col: 'time'})
    df['time'] = pd.to_numeric(df['time'], errors='coerce')
    df = df.dropna(subset=['time']).sort_values('time')

    value_cols = [c for c in df.columns if c != 'time']
    for col in value_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    return df[['time'] + value_cols]


def parse_sensitivity_label(name):
    base = os.path.splitext(os.path.basename(name))[0]
    cleaned = re.sub(r'(?i)(sensitivity|sens|simulation|sim|result|output|baseline|default)', '', base).strip('_-. ')
    pct_match = re.search(r'([+-]?\d+(?:\.\d+)?)\s*(?:pct|percent|%)', cleaned, re.I)
    fold_match = re.search(r'(?:x|fold[_-]?)(\d+(?:\.\d+)?)', cleaned, re.I)
    perturbation = None
    if pct_match:
        perturbation = f"{float(pct_match.group(1)):+g}%"
    elif fold_match:
        perturbation = f"×{float(fold_match.group(1)):g}"
    parameter = cleaned
    if pct_match:
        parameter = cleaned[:pct_match.start()] + cleaned[pct_match.end():]
    elif fold_match:
        parameter = cleaned[:fold_match.start()] + cleaned[fold_match.end():]
    parameter = re.sub(r'[_\-.]+', ' ', parameter).strip() or base
    return parameter, perturbation


# ── Comparison helpers ────────────────────────────────────────────────────────

def _value_at_time(df, col, t):
    clean = df[['time', col]].dropna()
    if clean.empty:
        return None, None
    idx = (clean['time'] - t).abs().idxmin()
    row = clean.loc[idx]
    return safe_float(row['time']), safe_float(row[col])


def _pct_diff(a, b):
    if a is None or b is None or abs(a) < 1e-12:
        return None
    return safe_float(((b - a) / a) * 100.0)


def _cumulative_difference(base_df, cmp_df, species):
    merged = pd.merge(
        base_df[['time', species]], cmp_df[['time', species]],
        on='time', suffixes=('_base', '_cmp')
    ).dropna()
    if merged.empty:
        return None
    return safe_float((merged[f'{species}_cmp'] - merged[f'{species}_base']).abs().sum())


def _auc_difference(base_df, cmp_df, species):
    merged = pd.merge(
        base_df[['time', species]], cmp_df[['time', species]],
        on='time', suffixes=('_base', '_cmp')
    ).dropna()
    if len(merged) < 2:
        return None
    try:
        return safe_float(np.trapz(
            (merged[f'{species}_cmp'] - merged[f'{species}_base']).abs(),
            merged['time']
        ))
    except Exception:
        return None


def _peak_info(df, species):
    clean = df[['time', species]].dropna()
    if clean.empty:
        return None, None
    peak_idx = clean[species].idxmax()
    return safe_float(clean.loc[peak_idx, 'time']), safe_float(clean.loc[peak_idx, species])


# ── Comparison ────────────────────────────────────────────────────────────────

def compare_simulations(loaded, baseline_item, time_point, focus_species):
    """Compute sensitivity metrics comparing all loaded simulations against baseline.

    loaded: list of {'path', 'name', 'df'}
    baseline_item: one element from loaded (the reference)
    Returns a dict ready for jsonify().
    """
    baseline_df = baseline_item['df']
    shared_species = set(baseline_df.columns) - {'time'}
    for item in loaded:
        if item['path'] != baseline_item['path']:
            shared_species &= (set(item['df'].columns) - {'time'})
    shared_species = sorted(shared_species)

    if not shared_species:
        raise ValueError('No shared species found across selected simulations')

    baseline_peak_times = {sp: _peak_info(baseline_df, sp)[0] for sp in shared_species}

    metrics = []
    for item in loaded:
        if item['path'] == baseline_item['path']:
            continue
        parameter, perturbation = parse_sensitivity_label(item['name'])
        for sp in shared_species:
            base_clean = baseline_df[sp].dropna()
            cmp_clean  = item['df'][sp].dropna()
            base_ss = safe_float(base_clean.iloc[-1]) if not base_clean.empty else None
            cmp_ss  = safe_float(cmp_clean.iloc[-1])  if not cmp_clean.empty  else None
            _, base_tp      = _value_at_time(baseline_df, sp, time_point)
            actual_t, cmp_tp = _value_at_time(item['df'],  sp, time_point)
            peak_time, peak_value = _peak_info(item['df'], sp)
            base_peak_time = baseline_peak_times.get(sp)
            metrics.append({
                'species': sp, 'simulation': item['name'], 'baseline': baseline_item['name'],
                'parameter': parameter, 'perturbation': perturbation,
                'time_point_requested': time_point, 'time_point_used': actual_t,
                'steady_state_pct_diff':     _pct_diff(base_ss, cmp_ss),
                'timepoint_pct_diff':        _pct_diff(base_tp, cmp_tp),
                'cumulative_difference':     _cumulative_difference(baseline_df, item['df'], sp),
                'auc_difference':            _auc_difference(baseline_df, item['df'], sp),
                'final_concentration_change': None if base_ss is None or cmp_ss is None else safe_float(cmp_ss - base_ss),
                'peak_time': peak_time, 'peak_value': peak_value,
                'peak_shift': None if peak_time is None or base_peak_time is None else safe_float(peak_time - base_peak_time),
            })

    species_rankings = []
    for sp in shared_species:
        rows = [m for m in metrics if m['species'] == sp]
        species_rankings.append({
            'species': sp,
            'max_abs_steady_state_pct_diff': max([abs(x['steady_state_pct_diff']) for x in rows if x['steady_state_pct_diff'] is not None] or [0.0]),
            'max_abs_timepoint_pct_diff':    max([abs(x['timepoint_pct_diff'])    for x in rows if x['timepoint_pct_diff']    is not None] or [0.0]),
            'max_cumulative_difference':     max([x['cumulative_difference']      for x in rows if x['cumulative_difference']  is not None] or [0.0]),
            'max_auc_difference':            max([x['auc_difference']             for x in rows if x['auc_difference']         is not None] or [0.0]),
            'max_abs_final_change':          max([abs(x['final_concentration_change']) for x in rows if x['final_concentration_change'] is not None] or [0.0]),
            'max_peak_shift':                max([abs(x['peak_shift'])            for x in rows if x['peak_shift']             is not None] or [0.0]),
        })
    species_rankings.sort(key=lambda x: x['max_abs_steady_state_pct_diff'], reverse=True)

    sensitivity_rows = []
    for item in loaded:
        if item['path'] == baseline_item['path']:
            continue
        rows = [m for m in metrics if m['simulation'] == item['name']]
        parameter, perturbation = parse_sensitivity_label(item['name'])
        valid_ss = [abs(r['steady_state_pct_diff']) for r in rows if r['steady_state_pct_diff'] is not None]
        top_row = max(rows, key=lambda r: r['cumulative_difference'] or 0.0, default=None)
        sensitivity_rows.append({
            'simulation': item['name'], 'parameter': parameter, 'perturbation': perturbation,
            'mean_abs_steady_state_pct_diff': safe_float(sum(valid_ss) / max(1, len(valid_ss))),
            'max_abs_steady_state_pct_diff':  max(valid_ss or [0.0]),
            'max_auc_difference':             max([r['auc_difference']             for r in rows if r['auc_difference']         is not None] or [0.0]),
            'max_abs_final_change':           max([abs(r['final_concentration_change']) for r in rows if r['final_concentration_change'] is not None] or [0.0]),
            'top_species':                    top_row['species'] if top_row else None,
            'top_species_cumulative_difference': top_row['cumulative_difference'] if top_row else None,
        })
    sensitivity_rows.sort(key=lambda x: x['max_auc_difference'] or 0.0, reverse=True)

    if not focus_species or focus_species not in shared_species:
        focus_species = species_rankings[0]['species'] if species_rankings else shared_species[0]

    focused_plot = {'species': focus_species, 'simulations': [], 'delta_from_baseline': []}
    base_focus = baseline_df[['time', focus_species]].dropna()
    focused_plot['simulations'].append({
        'name': baseline_item['name'],
        'points': [{'t': safe_float(t), 'y': safe_float(y)} for t, y in zip(base_focus['time'], base_focus[focus_species])],
    })
    for item in loaded:
        df_focus = item['df'][['time', focus_species]].dropna()
        if item['path'] != baseline_item['path']:
            focused_plot['simulations'].append({
                'name': item['name'],
                'points': [{'t': safe_float(t), 'y': safe_float(y)} for t, y in zip(df_focus['time'], df_focus[focus_species])],
            })
            merged = pd.merge(
                base_focus.rename(columns={focus_species: 'baseline'}),
                df_focus.rename(columns={focus_species: 'compare'}),
                on='time'
            ).dropna()
            focused_plot['delta_from_baseline'].append({
                'name': item['name'],
                'points': [{'t': safe_float(t), 'y': safe_float(c - b)}
                           for t, b, c in zip(merged['time'], merged['baseline'], merged['compare'])],
            })

    focused_metrics = [m for m in metrics if m['species'] == focus_species]
    strongest = {
        'max_steady_state_pct_diff': max([abs(x['steady_state_pct_diff']) for x in focused_metrics if x['steady_state_pct_diff'] is not None] or [0.0]),
        'max_timepoint_pct_diff':    max([abs(x['timepoint_pct_diff'])    for x in focused_metrics if x['timepoint_pct_diff']    is not None] or [0.0]),
        'max_cumulative_difference': max([x['cumulative_difference']      for x in focused_metrics if x['cumulative_difference']  is not None] or [0.0]),
        'max_auc_difference':        max([x['auc_difference']             for x in focused_metrics if x['auc_difference']         is not None] or [0.0]),
        'max_abs_final_change':      max([abs(x['final_concentration_change']) for x in focused_metrics if x['final_concentration_change'] is not None] or [0.0]),
        'max_peak_shift':            max([abs(x['peak_shift'])            for x in focused_metrics if x['peak_shift']             is not None] or [0.0]),
    }
    for row in focused_metrics:
        row.update(strongest)

    return {
        'baseline': baseline_item['name'],
        'available_species': shared_species,
        'focus_species': focus_species,
        'species_rankings': species_rankings,
        'focused_plot': focused_plot,
        'focused_metrics': focused_metrics,
        'sensitivity': sensitivity_rows,
    }


# ── Quality Check ─────────────────────────────────────────────────────────────

def quality_check_files(loaded_files, reactions_path=None):
    """Analyse simulation DataFrames for numerical quality issues.

    loaded_files: list of {'rel_path', 'df'} or {'rel_path', 'error'}
    Returns {'file_reports', 'dead_end_species', 'dead_end_count'}.
    """
    file_reports = []
    for entry in loaded_files:
        rel_path = entry['rel_path']
        if 'error' in entry:
            file_reports.append({'file': rel_path, 'error': entry['error']})
            continue

        df = entry['df']
        species_cols = [c for c in df.columns if c != 'time']

        neg_mask         = (df[species_cols] < 0).any()
        negative_species = sorted(c for c in species_cols if neg_mask[c])
        first_neg_times  = {}
        for sp in negative_species:
            idx = (df[sp] < 0).idxmax()
            first_neg_times[sp] = safe_float(df.loc[idx, 'time'])

        last_n   = max(10, int(len(df) * 0.1))
        last_df  = df.tail(last_n)[species_cols]
        rel_std  = last_df.std() / (last_df.abs().mean() + 1e-30)
        unstable_species = sorted(rel_std[rel_std > 0.01].index.tolist())

        max_vals        = df[species_cols].abs().max()
        extreme_species = sorted(max_vals[max_vals > 1e6].index.tolist())

        file_reports.append({
            'file':             rel_path,
            'total_species':    len(species_cols),
            'total_timepoints': len(df),
            'negative_species': negative_species,
            'negative_count':   len(negative_species),
            'first_neg_times':  first_neg_times,
            'unstable_species': unstable_species,
            'unstable_count':   len(unstable_species),
            'extreme_species':  extreme_species,
            'extreme_count':    len(extreme_species),
        })

    dead_end_species = []
    if reactions_path and os.path.exists(reactions_path):
        with open(reactions_path) as f:
            rxdata = json.load(f)
        all_reactants = set()
        all_products  = set()
        for rxn in rxdata.values():
            all_reactants.update(rxn.get('reactants', []))
            all_products.update(rxn.get('products', []))
        dead_end_species = sorted(all_products - all_reactants)

    return {
        'file_reports':     file_reports,
        'dead_end_species': dead_end_species,
        'dead_end_count':   len(dead_end_species),
    }


# ── Reaction Activity ─────────────────────────────────────────────────────────

def reaction_activity_files(loaded_files):
    """Compute ranked reaction totals from pre-located flux CSV files.

    loaded_files: list of {'rel_path', 'name', 'totals_path'} or {'rel_path', 'error'/'missing'}
    Returns list of result dicts.
    """
    results = []
    for entry in loaded_files:
        rel_path = entry['rel_path']
        if 'error' in entry:
            results.append({'path': rel_path, 'error': entry['error']})
            continue
        if entry.get('missing'):
            results.append({'path': rel_path, 'name': entry['name'], 'missing': True})
            continue
        try:
            df     = pd.read_csv(entry['totals_path'])
            totals = {k: safe_float(v) for k, v in df.iloc[0].items()}
            ranked = sorted(totals.items(), key=lambda x: (x[1] or 0), reverse=True)
            results.append({
                'path':   rel_path,
                'name':   entry['name'],
                'totals': [{'reaction': k, 'total': v} for k, v in ranked],
            })
        except Exception as e:
            results.append({'path': rel_path, 'error': str(e)})
    return results


# ── Parameter Scan Finalisation ───────────────────────────────────────────────

def finalize_scan_results(param_name, values, scan_results):
    """Compute sensitivity rankings from raw per-value scan results.

    Returns the full result dict (param_name, values, scan_results, sensitivity,
    top_sensitive_species).
    """
    baseline = (scan_results[0]['concentrations']
                if scan_results and scan_results[0]['concentrations'] else {})
    all_species = set()
    for sr in scan_results:
        if sr['concentrations']:
            all_species.update(sr['concentrations'].keys())

    sensitivity = {}
    for sp in all_species:
        base_val = baseline.get(sp) or 0
        changes  = []
        for sr in scan_results:
            if sr['concentrations'] and sp in sr['concentrations']:
                v = sr['concentrations'].get(sp) or 0
                if abs(base_val) > 1e-12:
                    changes.append(abs((v - base_val) / base_val) * 100)
                else:
                    changes.append(abs(v) * 1000)
        sensitivity[sp] = safe_float(max(changes)) if changes else 0.0

    top_species = sorted(sensitivity.items(), key=lambda x: x[1] or 0, reverse=True)[:60]

    return {
        'param_name':   param_name,
        'values':       values,
        'scan_results': scan_results,
        'sensitivity':  {sp: safe_float(v) for sp, v in sensitivity.items()},
        'top_sensitive_species': [
            {'species': sp, 'max_pct_change': v} for sp, v in top_species
        ],
    }


# ── Embedding Functions ───────────────────────────────────────────────────────

def _embed_vectors(vectors, loaded):
    """Return a safe 2D embedding for one vector per simulation."""
    if len(loaded) < 2 or not vectors:
        return {'method': 'not_available', 'points': []}

    X = np.nan_to_num(np.asarray(vectors, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    if X.ndim != 2 or X.shape[0] < 2:
        return {'method': 'not_available', 'points': []}

    std  = X.std(axis=0)
    keep = std > 1e-12
    if keep.any():
        X   = X[:, keep]
        std = std[keep]
    else:
        X   = np.zeros((X.shape[0], 1), dtype=float)
        std = np.ones(1, dtype=float)
    X = (X - X.mean(axis=0)) / std

    n      = X.shape[0]
    method = 't-SNE'
    try:
        if n == 2:
            coords = np.array([[-1.0, 0.0], [1.0, 0.0]])
            method = 'direct-2-point'
        elif n == 3:
            method = 'PCA fallback'
            Xc = X - X.mean(axis=0)
            _, _, vt = np.linalg.svd(Xc, full_matrices=False)
            coords = Xc @ vt[:2].T
            if coords.shape[1] == 1:
                coords = np.column_stack([coords[:, 0], np.zeros(n)])
        else:
            from sklearn.manifold import TSNE
            perplexity = max(1, min(30, n - 1, n // 2))
            coords = TSNE(n_components=2, perplexity=perplexity, init='random',
                          learning_rate='auto', random_state=42).fit_transform(X)
    except Exception:
        method = 'PCA fallback'
        try:
            Xc = X - X.mean(axis=0)
            _, _, vt = np.linalg.svd(Xc, full_matrices=False)
            coords = Xc @ vt[:2].T
            if coords.ndim == 1 or coords.shape[1] == 1:
                coords = np.column_stack([coords.reshape(-1), np.zeros(n)])
        except Exception:
            coords = np.column_stack([np.arange(n, dtype=float), np.zeros(n)])

    return {
        'method': method,
        'points': [
            {'name': item['name'], 'path': item['path'],
             'x': safe_float(xy[0]), 'y': safe_float(xy[1])}
            for item, xy in zip(loaded, coords)
        ],
    }


def pca_trajectory(loaded, shared_species, max_pts=100):
    """Project each simulation's time course onto the top 2 PCA components."""
    if not shared_species or not loaded:
        return {'method': 'not_available', 'points': []}

    t_min = max(float(item['df']['time'].min()) for item in loaded)
    t_max = min(float(item['df']['time'].max()) for item in loaded)
    if not np.isfinite(t_min) or not np.isfinite(t_max) or t_max <= t_min:
        t_min = min(float(item['df']['time'].min()) for item in loaded)
        t_max = max(float(item['df']['time'].max()) for item in loaded)
    grid = np.linspace(t_min, t_max, max_pts)

    all_blocks, all_labels = [], []
    for item in loaded:
        df    = item['df'].sort_values('time')
        t_arr = df['time'].to_numpy(float)
        block = np.zeros((max_pts, len(shared_species)), dtype=float)
        for si, sp in enumerate(shared_species):
            col  = df[sp].to_numpy(float)
            mask = ~np.isnan(col)
            if mask.any():
                block[:, si] = np.interp(grid, t_arr[mask], col[mask])
        all_blocks.append(block)
        for ti, t in enumerate(grid):
            all_labels.append({'sim': item['name'], 'path': item['path'], 'ti': ti, 't': float(t)})

    X    = np.nan_to_num(np.vstack(all_blocks), nan=0.0, posinf=0.0, neginf=0.0)
    std  = X.std(axis=0)
    keep = std > 1e-12
    if not keep.any():
        return {'method': 'not_available', 'points': []}
    X_norm = (X[:, keep] - X[:, keep].mean(axis=0)) / std[keep]

    try:
        Xc  = X_norm - X_norm.mean(axis=0)
        _, s, vt = np.linalg.svd(Xc, full_matrices=False)
        coords = Xc @ vt[:2].T
        if coords.shape[1] == 1:
            coords = np.column_stack([coords[:, 0], np.zeros(len(all_labels))])
        var_total   = float((s ** 2).sum())
        var_explained = [float(s[i] ** 2 / var_total * 100) if var_total > 0 else 0
                         for i in range(min(2, len(s)))]
        while len(var_explained) < 2:
            var_explained.append(0.0)
    except Exception:
        coords = np.column_stack([np.arange(len(all_labels), dtype=float), np.zeros(len(all_labels))])
        var_explained = [0.0, 0.0]

    return {
        'method':      'PCA',
        'var_explained': var_explained,
        'n_timepoints': max_pts,
        'n_species':   int(keep.sum()),
        'points': [
            {**lbl, 'x': safe_float(xy[0]), 'y': safe_float(xy[1])}
            for lbl, xy in zip(all_labels, coords)
        ],
    }


def species_at_timepoint_embedding(loaded, shared_species, time_point, max_pts=40):
    """Embed every (species, simulation) pair using the time-series up to time_point."""
    if not shared_species or not loaded:
        return {'method': 'not_available', 'points': []}

    t_min        = max(float(item['df']['time'].min()) for item in loaded)
    t_max_common = min(float(item['df']['time'].max()) for item in loaded)
    if not np.isfinite(t_min) or not np.isfinite(t_max_common):
        t_min = 0.0; t_max_common = 1.0
    t_end = float(np.clip(time_point, t_min, t_max_common))
    grid  = np.linspace(t_min, t_end, max_pts)

    all_vectors, all_labels = [], []
    for item in loaded:
        df    = item['df'].sort_values('time')
        t_arr = df['time'].to_numpy(float)
        for sp in shared_species:
            col  = df[sp].to_numpy(float)
            mask = ~np.isnan(col)
            row  = np.interp(grid, t_arr[mask], col[mask]) if mask.any() else np.zeros(max_pts)
            all_vectors.append(row)
            all_labels.append({'species': sp, 'sim': item['name'], 'path': item['path']})

    X    = np.nan_to_num(np.asarray(all_vectors, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    std  = X.std(axis=0)
    keep = std > 1e-12
    if not keep.any():
        return {'method': 'not_available', 'points': []}
    X = (X[:, keep] - X[:, keep].mean(axis=0)) / std[keep]

    n      = X.shape[0]
    method = 't-SNE'
    try:
        from sklearn.manifold import TSNE
        perplexity = max(5, min(50, n // 10))
        coords = TSNE(n_components=2, perplexity=perplexity, init='pca',
                      learning_rate='auto', n_iter=500, random_state=42).fit_transform(X)
    except Exception:
        method = 'PCA'
        try:
            Xc = X - X.mean(axis=0)
            _, _, vt = np.linalg.svd(Xc, full_matrices=False)
            coords = Xc @ vt[:2].T
            if coords.ndim == 1 or coords.shape[1] == 1:
                coords = np.column_stack([coords.reshape(-1), np.zeros(n)])
        except Exception:
            coords = np.column_stack([np.arange(n, dtype=float), np.zeros(n)])

    return {
        'method':    method,
        'time_point': float(t_end),
        'n_species': len(shared_species),
        'n_sims':    len(loaded),
        'points': [
            {**lbl, 'x': safe_float(xy[0]), 'y': safe_float(xy[1])}
            for lbl, xy in zip(all_labels, coords)
        ],
    }


def compute_full_tsne_embedding(loaded, shared_species, max_pts=40):
    """t-SNE over the complete time-series of every (sim, species) pair."""
    if not shared_species or not loaded:
        return {'method': 'not_available', 'points': []}

    t_min = max(float(item['df']['time'].min()) for item in loaded)
    t_max = min(float(item['df']['time'].max()) for item in loaded)
    if not np.isfinite(t_min) or not np.isfinite(t_max) or t_max <= t_min:
        t_min = min(float(item['df']['time'].min()) for item in loaded)
        t_max = max(float(item['df']['time'].max()) for item in loaded)
    grid = np.linspace(t_min, t_max, max_pts)

    all_labels, all_rows = [], []
    for item in loaded:
        df    = item['df'].sort_values('time')
        t_arr = df['time'].to_numpy(float)
        for sp in shared_species:
            col  = df[sp].to_numpy(float)
            mask = ~np.isnan(col)
            row  = np.interp(grid, t_arr[mask], col[mask]) if mask.any() else np.zeros(max_pts)
            all_rows.append(row)
            all_labels.append({'species': sp, 'sim': item['name'], 'path': item['path']})

    X    = np.nan_to_num(np.array(all_rows, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    std  = X.std(axis=0)
    keep = std > 1e-12
    if not keep.any():
        return {'method': 'not_available', 'points': []}
    X = (X[:, keep] - X[:, keep].mean(axis=0)) / std[keep]

    n      = X.shape[0]
    method = 't-SNE'
    try:
        from sklearn.manifold import TSNE
        perplexity = max(5, min(50, n // 10))
        coords = TSNE(n_components=2, perplexity=perplexity, init='pca',
                      learning_rate='auto', n_iter=500, random_state=42).fit_transform(X)
    except Exception:
        method = 'PCA'
        try:
            Xc = X - X.mean(axis=0)
            _, _, vt = np.linalg.svd(Xc, full_matrices=False)
            coords = Xc @ vt[:2].T
            if coords.ndim == 1 or coords.shape[1] == 1:
                coords = np.column_stack([coords.reshape(-1), np.zeros(n)])
        except Exception:
            coords = np.column_stack([np.arange(n, dtype=float), np.zeros(n)])

    return {
        'method':    method,
        'n_species': len(shared_species),
        'n_sims':    len(loaded),
        'points': [
            {**lbl, 'x': safe_float(xy[0]), 'y': safe_float(xy[1])}
            for lbl, xy in zip(all_labels, coords)
        ],
    }


def precompute_species_embeddings(loaded, shared_species, n_frames=25, max_pts=40):
    """Precompute species-space PCA embeddings at n_frames time points sharing one global basis."""
    if not shared_species or not loaded:
        return []

    t_min = max(float(item['df']['time'].min()) for item in loaded)
    t_max = min(float(item['df']['time'].max()) for item in loaded)
    if not np.isfinite(t_min) or not np.isfinite(t_max) or t_max <= t_min:
        t_min = min(float(item['df']['time'].min()) for item in loaded)
        t_max = max(float(item['df']['time'].max()) for item in loaded)
    full_grid = np.linspace(t_min, t_max, max_pts)

    all_labels, all_rows = [], []
    for item in loaded:
        df    = item['df'].sort_values('time')
        t_arr = df['time'].to_numpy(float)
        for sp in shared_species:
            col  = df[sp].to_numpy(float)
            mask = ~np.isnan(col)
            row  = np.interp(full_grid, t_arr[mask], col[mask]) if mask.any() else np.zeros(max_pts)
            all_rows.append(row)
            all_labels.append({'species': sp, 'sim': item['name'], 'path': item['path']})

    X        = np.nan_to_num(np.array(all_rows, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    col_mean = X.mean(axis=0)
    col_std  = X.std(axis=0)
    keep     = col_std > 1e-12
    if not keep.any():
        return []

    X_norm = (X[:, keep] - col_mean[keep]) / col_std[keep]
    Xc     = X_norm - X_norm.mean(axis=0)
    try:
        _, _, vt = np.linalg.svd(Xc, full_matrices=False)
        V = vt[:2].T
    except Exception:
        V = np.eye(X_norm.shape[1], 2)

    time_points  = np.linspace(t_min, t_max, n_frames)
    kept_indices = np.where(keep)[0]
    frames = []
    for t in time_points:
        k = max(1, min(int(np.searchsorted(full_grid, t, side='right')), max_pts))
        X_frame = X_norm.copy()
        X_frame[:, kept_indices >= k] = 0.0
        Xfc    = X_frame - X_frame.mean(axis=0)
        coords = Xfc @ V
        frames.append({
            'time_point': float(t),
            'n_species':  len(shared_species),
            'n_sims':     len(loaded),
            'points': [
                {**lbl, 'x': safe_float(xy[0]), 'y': safe_float(xy[1])}
                for lbl, xy in zip(all_labels, coords)
            ],
        })

    return frames
