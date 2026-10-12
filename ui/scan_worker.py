#!/usr/bin/env python3
"""Standalone scan worker: reads a JSON job spec from stdin, runs a Tellurium
parameter scan, and prints a single JSON object to stdout.
Progress lines are written to stderr so the parent can parse them live.
"""
import sys
import json
import math


def safe_float(v):
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def run(spec):
    import tellurium as te
    import pandas as pd

    model_path = spec['model_path']
    param_name = spec['param_name']
    values     = spec['values']
    sim_start  = spec['sim_start']
    sim_end    = spec['sim_end']
    sim_points = spec['sim_points']

    antimony_text = open(model_path).read()
    r = te.loada(antimony_text)

    try:
        original_val = r[param_name]
    except Exception:
        return {'error': f'Parameter "{param_name}" not found in model'}

    scan_results = []
    for i, val in enumerate(values):
        try:
            r.reset()
            r[param_name] = val
            sim = r.simulate(sim_start, sim_end, sim_points)
            df  = pd.DataFrame(sim, columns=sim.colnames)
            final = df.iloc[-1]
            concs = {col: safe_float(final[col]) for col in df.columns if col != 'time'}
            scan_results.append({'param_value': val, 'concentrations': concs, 'error': None})
        except Exception as e:
            scan_results.append({'param_value': val, 'concentrations': None, 'error': str(e)})
        finally:
            try:
                r[param_name] = original_val
            except Exception:
                pass
        pct = int((i + 1) / len(values) * 100)
        print(json.dumps({'progress': pct}), file=sys.stderr, flush=True)

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
        'param_name': param_name,
        'values':     values,
        'scan_results': scan_results,
        'sensitivity':  {sp: safe_float(v) for sp, v in sensitivity.items()},
        'top_sensitive_species': [
            {'species': sp, 'max_pct_change': v} for sp, v in top_species
        ]
    }


if __name__ == '__main__':
    spec = json.loads(sys.stdin.read())
    try:
        result = run(spec)
        if 'error' in result:
            print(json.dumps({'ok': False, 'error': result['error']}))
            sys.exit(1)
        print(json.dumps({'ok': True, 'result': result}))
    except ImportError as e:
        print(json.dumps({'ok': False, 'error': f'tellurium not available in this interpreter: {e}'}))
        sys.exit(1)
    except Exception as e:
        import traceback
        print(json.dumps({'ok': False, 'error': str(e), 'traceback': traceback.format_exc()}))
        sys.exit(1)
