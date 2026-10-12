import os
import signal
import subprocess
import threading
import json
import uuid
import time
import re
import mimetypes
import shutil
import glob
import tempfile
import traceback

import pandas as pd
import numpy as np
from flask import Flask, render_template, request, jsonify, Response, stream_with_context, send_file
from flask_cors import CORS

from analysis import (
    safe_float, load_simulation_table,
    compare_simulations, quality_check_files, reaction_activity_files,
    finalize_scan_results,
    pca_trajectory, precompute_species_embeddings,
    compute_full_tsne_embedding,
)

app = Flask(__name__)
CORS(app)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TMP_DIR   = os.path.join(REPO_ROOT, 'tmp')
os.makedirs(TMP_DIR, exist_ok=True)

jobs = {}

PROCESS_TO_STAGE = {
    'processPSS':          1,
    'decayRates':          2,
    'initialConcentrations': 2,
    'rateLaws':            2,
    'kineticParameters':   2,
    'buildAntimony':       3,
    'simulatePSS':         4,
}

TRUNC_MAP = {
    'ini…ncentrations': 'initialConcentrations',
    'ini…centrations':  'initialConcentrations',
    'kin…icParameters': 'kineticParameters',
    'kin…cParameters':  'kineticParameters',
}

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
# Nextflow 25.x non-TTY stdout uses two formats depending on whether tasks are fresh or cached:
#   Fresh:  [71/369d77] processPSS             [100%] 1 of 1 ✔
#   Cached: [59/92d81a] processPSS             | 1 of 1, cached: 1 ✔
# In both cases the separator after the name is either "[" (percent bracket) or "|" (pipe).
# DONE: line with a real hash that ends with ✔ anywhere after the separator.
RAW_PROCESS_RE = re.compile(
    r'^\[[a-f0-9]{2}/[a-f0-9]+\]\s*([^\[|\n]+?)\s+(?:\[|\|)[^\n]*✔'
)
# RUNNING: same structure but NO ✔ on the line.
RUNNING_PROCESS_RE = re.compile(
    r'^\[[a-f0-9]{2}/[a-f0-9]+\]\s*([^\[|\n]+?)\s+(?:\[|\|)(?![^\n]*✔)'
)


def is_meaningful(line):
    line = line.strip().lower()
    if not line:
        return False
    keywords = ('executing:', 'pipeline finished', 'stage', 'launching', 'executor >', 'error', 'warn', 'failed', 'model loaded')
    return any(kw in line for kw in keywords)


# ── Pipeline Job ──────────────────────────────────────────────────────────────

class PipelineJob:
    def __init__(self, job_id, config):
        self.job_id   = job_id
        self.config   = config
        self.status   = 'pending'
        self.clean_logs = []
        self.raw_logs   = []
        self.process  = None
        self.stopped  = False
        self.error    = None
        self.errors   = []
        self.completed_stages   = set()
        self.sim_file           = None
        self.inline_event_files = []
        self.stage_2_subprocesses = {
            'decayRates': False, 'initialConcentrations': False,
            'rateLaws':   False, 'kineticParameters':     False,
        }
        self.running_reported = set()
        self.running_stages   = set()

    def append_log(self, msg, msg_type='info', source='clean'):
        if source == 'clean':
            self.clean_logs.append((msg, msg_type))
        else:
            self.raw_logs.append((msg, msg_type))

    def run(self):
        self.status = 'running'
        cmd = self.build_command()
        self.append_log(f"Executing: {' '.join(cmd)}", 'info', 'clean')
        try:
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=REPO_ROOT, env=env,
                # Own session/process group so stop() can kill the whole tree
                # (Nextflow + Java + spawned task processes), not just the launcher.
                start_new_session=True,
            )
            buf = []
            while True:
                char = self.process.stdout.read(1)
                if not char:
                    break
                if char in ('\r', '\n'):
                    self.process_line(''.join(buf))
                    buf = []
                else:
                    buf.append(char)
            self.process.wait()
            if self.stopped:
                self.status = 'stopped'
            elif self.process.returncode == 0:
                self.status = 'done'
                # Force all stages complete — multi-instance processes (simulatePSS)
                # emit one ✔ line per task so the regex may mark stage 4 done too
                # early (or miss it entirely depending on Nextflow's output format).
                # The subprocess exiting 0 is the ground truth that everything finished.
                for stage_num in [1, 2, 3, 4]:
                    self.completed_stages.add(stage_num)
                for key in self.stage_2_subprocesses:
                    self.stage_2_subprocesses[key] = True
            else:
                self.status = 'error'
        except Exception as e:
            self.status = 'error'
            self.error  = str(e)
        finally:
            if self.process and self.process.stdout:
                self.process.stdout.close()
            if self.sim_file and os.path.exists(self.sim_file):
                try:
                    os.remove(self.sim_file)
                except Exception:
                    pass
            for f in self.inline_event_files:
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except Exception:
                    pass

    def stop(self):
        """Terminate a running pipeline and its whole process tree.

        Sends SIGTERM to the process group first (lets Nextflow's JVM shutdown
        hook clean up locks and kill task processes), then SIGKILL as a
        fallback if it hasn't exited. Idempotent and safe to call after the
        job has already finished.
        """
        proc = self.process
        if not proc or proc.poll() is not None:
            return False
        self.stopped = True
        self.append_log('■ Stop requested — terminating pipeline…', 'warn', 'clean')
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=8)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        return True

    def process_line(self, raw_line):
        clean_line = ANSI_ESCAPE.sub('', raw_line).strip()
        if not clean_line:
            return
        self.append_log(clean_line, 'info', 'raw')

        lower_line = clean_line.lower()
        if any(kw in lower_line for kw in ['error', 'failed', 'terminated']):
            self.errors.append(clean_line)

        run_match = RUNNING_PROCESS_RE.match(clean_line)
        if run_match:
            rname = re.sub(r'\s*\([^)]*\)', '', run_match.group(1)).strip()
            rname = TRUNC_MAP.get(rname, rname)
            if rname in PROCESS_TO_STAGE and rname not in self.running_reported:
                self.running_reported.add(rname)
                self.running_stages.add(PROCESS_TO_STAGE[rname])
                self.append_log(f"→ {rname}: running…", 'dim', 'clean')

        match = RAW_PROCESS_RE.match(clean_line)
        if match:
            proc_name     = re.sub(r'\s*\([^)]*\)', '', match.group(1)).strip()
            original_name = TRUNC_MAP.get(proc_name, proc_name)
            stage_num     = PROCESS_TO_STAGE.get(original_name)

            if stage_num == 2:
                if original_name in self.stage_2_subprocesses:
                    if not self.stage_2_subprocesses[original_name]:
                        self.stage_2_subprocesses[original_name] = True
                        self.append_log(f"Sub-process complete: {original_name}", 'ok', 'clean')
                if all(self.stage_2_subprocesses.values()) and 2 not in self.completed_stages:
                    self.completed_stages.add(2)
                    self.append_log("✅ Stage 2: All parameters finalized", 'ok', 'clean')
            elif stage_num and stage_num not in self.completed_stages:
                self.completed_stages.add(stage_num)
                self.append_log(f"✅ Stage {stage_num} Complete ({original_name})", 'ok', 'clean')

        if is_meaningful(clean_line):
            self.append_log(clean_line, 'info', 'clean')

    def build_command(self):
        cfg = self.config
        os.makedirs(TMP_DIR, exist_ok=True)
        self.sim_file = os.path.join(TMP_DIR, f"sim_{self.job_id}.json")
        with open(self.sim_file, 'w') as f:
            json.dump(cfg.get('sim_conditions', []), f)
        cmd  = ['nextflow', 'run', 'main.nf', '-profile', cfg.get('profile', 'local')]
        cmd += ['--data_path', cfg.get('data_path'), '--script_path', cfg.get('script_path')]
        cmd += ['--events_path', cfg.get('events_path'), '--subset', cfg.get('subset')]
        if cfg.get('decay_form'):
            cmd += ['--decay_form', cfg.get('decay_form')]
        cmd += ['--sim_conditions', self.sim_file]
        # Optional CVODE integrator overrides — only pass what the user set.
        for key, flag in (('rel_tol', '--rel_tol'), ('abs_tol', '--abs_tol'), ('max_steps', '--max_steps')):
            val = cfg.get(key)
            if val not in (None, ''):
                cmd += [flag, str(val)]
        if cfg.get('resume'):
            cmd += ['-resume']
        return cmd


# ── Routes: UI ────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/sbml_viewer')
def sbml_viewer():
    viewer_path = os.path.join(REPO_ROOT, 'static', 'sbml_network_viewer.html')
    if not os.path.exists(viewer_path):
        viewer_path = os.path.join(os.path.dirname(__file__), 'sbml_network_viewer.html')
    if not os.path.exists(viewer_path):
        return 'SBML viewer not found. Place sbml_network_viewer.html in static/ next to app.py.', 404
    return send_file(viewer_path, mimetype='text/html')


# ── Routes: Pipeline ──────────────────────────────────────────────────────────

@app.route('/run', methods=['POST'])
def run_pipeline():
    job_id         = str(uuid.uuid4())
    config         = request.json
    sim_conditions = config.get('sim_conditions', [])

    os.makedirs(TMP_DIR, exist_ok=True)
    validation_errors = []
    for cond in sim_conditions:
        ev = (cond.get('events') or '').strip()
        if not ev:
            cond['events'] = None
            continue
        if ev[0] in ('{', '['):
            try:
                json.loads(ev)
            except json.JSONDecodeError as e:
                validation_errors.append(f"Simulation '{cond.get('suffix', '?')}': events JSON is invalid — {e}")
                continue
            fd, path = tempfile.mkstemp(suffix='.json', dir=TMP_DIR, text=True)
            with os.fdopen(fd, 'w') as f:
                f.write(ev)
            cond['events'] = path
        else:
            resolved = os.path.abspath(os.path.join(REPO_ROOT, ev))
            if not resolved.startswith(REPO_ROOT):
                validation_errors.append(f"Simulation '{cond.get('suffix', '?')}': events path escapes repo root — {ev}")
                continue
            if not os.path.isfile(resolved):
                validation_errors.append(f"Simulation '{cond.get('suffix', '?')}': events file not found — {ev}")
                continue
            cond['events'] = resolved

    if validation_errors:
        return jsonify({'error': '\n'.join(validation_errors)}), 400

    job = PipelineJob(job_id, config)
    for cond in sim_conditions:
        ev = cond.get('events') or ''
        if ev and ev.startswith(TMP_DIR):
            job.inline_event_files.append(ev)

    jobs[job_id] = job
    threading.Thread(target=job.run, daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route('/stop/<job_id>', methods=['POST'])
def stop_pipeline(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    killed = job.stop()
    return jsonify({'status': 'stopped' if killed else job.status, 'killed': killed})


@app.route('/clean', methods=['POST'])
def clean_workspace():
    try:
        results_dir = os.path.join(REPO_ROOT, 'results')
        if os.path.exists(results_dir):
            for item in os.listdir(results_dir):
                item_path = os.path.join(results_dir, item)
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)

        for pattern in ['.nextflow*', '.nextflow.log*', '.nextflow.pid']:
            for f in glob.glob(os.path.join(REPO_ROOT, pattern)):
                if os.path.isdir(f):
                    shutil.rmtree(f)
                else:
                    os.remove(f)

        work_dir = os.path.join(REPO_ROOT, 'work')
        if os.path.exists(work_dir):
            for item in os.listdir(work_dir):
                if item == 'conda':
                    continue
                item_path = os.path.join(work_dir, item)
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)

        logs_dir = os.path.join(REPO_ROOT, 'logs')
        if os.path.exists(logs_dir):
            for item in os.listdir(logs_dir):
                item_path = os.path.join(logs_dir, item)
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)

        tmp_dir = os.path.join(REPO_ROOT, 'tmp')
        if os.path.exists(tmp_dir):
            for item in os.listdir(tmp_dir):
                item_path = os.path.join(tmp_dir, item)
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)

        return jsonify({'status': 'success', 'message': 'Workspace cleaned successfully'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/status/<job_id>')
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': '404'}), 404
    return jsonify({
        'status':           job.status,
        'completed_stages': list(job.completed_stages),
        'running_stages':   list(job.running_stages),
        'stage_2_details':  job.stage_2_subprocesses,
        'error':            job.error,
        'errors':           job.errors,
    })


@app.route('/logs/<job_id>')
def stream_logs(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': '404'}), 404

    def generate():
        last_idx = {'clean': 0, 'raw': 0}
        heartbeat_counter = 0
        while True:
            for src in ['clean', 'raw']:
                logs = job.clean_logs if src == 'clean' else job.raw_logs
                for msg, typ in logs[last_idx[src]:]:
                    yield f"data: {json.dumps({'source': src, 'msg': msg, 'type': typ})}\n\n"
                last_idx[src] = len(logs)
            if job.status in ('done', 'error'):
                for src in ['clean', 'raw']:
                    logs = job.clean_logs if src == 'clean' else job.raw_logs
                    for msg, typ in logs[last_idx[src]:]:
                        yield f"data: {json.dumps({'source': src, 'msg': msg, 'type': typ})}\n\n"
                break
            heartbeat_counter += 1
            if heartbeat_counter >= 150:
                yield ": heartbeat\n\n"
                heartbeat_counter = 0
            time.sleep(0.1)

    resp = Response(stream_with_context(generate()), mimetype='text/event-stream')
    resp.headers['Cache-Control']    = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


# ── Routes: File browser ──────────────────────────────────────────────────────

@app.route('/ls')
def list_files():
    category = request.args.get('category', 'results')
    sub_path = request.args.get('path', '').strip('/')
    base_dir = os.path.abspath(os.path.join(REPO_ROOT, 'data' if category == 'data' else 'results'))
    full_path = os.path.abspath(os.path.join(base_dir, sub_path))

    if not full_path.startswith(REPO_ROOT) or not os.path.exists(full_path):
        return jsonify({'items': [], 'type': 'dir'})

    if os.path.isdir(full_path):
        items = []
        for item in os.listdir(full_path):
            if item.startswith('.'):
                continue
            items.append({
                'type': 'dir' if os.path.isdir(os.path.join(full_path, item)) else 'file',
                'name': item,
                'path': os.path.join(sub_path, item),
            })
        return jsonify({'type': 'dir', 'items': sorted(items, key=lambda x: (x['type'] != 'dir', x['name']))})
    return jsonify({'type': 'file'})


def _parse_table_file(filepath, max_rows=200, max_cols=20):
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == '.csv':
            df = pd.read_csv(filepath, nrows=max_rows)
            with open(filepath, 'rb') as f:
                total_rows = max(sum(1 for _ in f) - 1, 0)
        elif ext == '.tsv':
            df = pd.read_csv(filepath, sep='\t', nrows=max_rows)
            with open(filepath, 'rb') as f:
                total_rows = max(sum(1 for _ in f) - 1, 0)
        elif ext in ('.xlsx', '.xls'):
            df = pd.read_excel(filepath, nrows=max_rows)
            total_rows = None
        else:
            return None

        original_col_count = len(df.columns)
        truncated_cols = original_col_count > max_cols
        if truncated_cols:
            df = df.iloc[:, :max_cols]

        return {
            'type':           'table',
            'headers':        df.columns.tolist(),
            'rows':           df.fillna('').astype(object).values.tolist(),
            'truncated_rows': len(df) == max_rows,
            'truncated_cols': truncated_cols,
            'total_rows':     total_rows,
            'total_cols':     original_col_count,
        }
    except Exception as e:
        print(f"Error parsing {filepath}: {e}")
        traceback.print_exc()
        return None


@app.route('/get_file')
def get_file():
    path = request.args.get('path', '')
    cat  = request.args.get('category', 'results')
    base = os.path.join(REPO_ROOT, 'data' if cat == 'data' else 'results')
    full = os.path.abspath(os.path.join(base, path))

    if not os.path.isfile(full):
        return jsonify({'error': 'File not found'}), 404

    mime = mimetypes.guess_type(full)[0]
    if mime and mime.startswith('image/'):
        return send_file(full, mimetype=mime)

    ext = os.path.splitext(full)[1].lower()

    if ext in ('.csv', '.tsv', '.xlsx', '.xls'):
        table_data = _parse_table_file(full)
        if table_data:
            return jsonify(table_data)

    if ext == '.html':
        with open(full, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return jsonify({'type': 'html', 'content': content})

    with open(full, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    return jsonify({'type': 'text', 'content': content})


@app.route('/save_table_file', methods=['POST'])
def save_table_file():
    payload = request.get_json(force=True)
    path    = payload.get('path', '')
    cat     = payload.get('category', 'data')
    headers = payload.get('headers', [])
    rows    = payload.get('rows', [])

    if cat != 'data':
        return jsonify({'error': 'Only input/data files can be edited'}), 400

    base = os.path.join(REPO_ROOT, 'data')
    full = os.path.abspath(os.path.join(base, path))
    if not full.startswith(base):
        return jsonify({'error': 'Invalid path'}), 400
    if not os.path.isfile(full):
        return jsonify({'error': 'File not found'}), 404

    ext = os.path.splitext(full)[1].lower()
    try:
        df = pd.DataFrame(rows, columns=headers)
        if ext == '.csv':
            df.to_csv(full, index=False)
        elif ext == '.tsv':
            df.to_csv(full, index=False, sep='\t')
        elif ext in ('.xlsx', '.xls'):
            df.to_excel(full, index=False)
        else:
            return jsonify({'error': f'Unsupported table file type: {ext}'}), 400
        return jsonify({'status': 'ok', 'message': 'Table saved'})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ── Routes: Network & simulation data ────────────────────────────────────────

@app.route('/network_data')
def network_data():
    species_path   = os.path.join(REPO_ROOT, 'results', 'processPSS', 'species.json')
    reactions_path = os.path.join(REPO_ROOT, 'results', 'processPSS', 'reactions.json')
    if not os.path.exists(species_path) or not os.path.exists(reactions_path):
        return jsonify({'error': 'Network data not found. Run the pipeline first.'}), 404
    with open(species_path) as f:
        species = json.load(f)
    with open(reactions_path) as f:
        reactions = json.load(f)
    return jsonify({
        'species':      species,
        'reactions':    reactions,
        'compartments': sorted(set(v['compartment'] for v in species.values())),
        'types':        sorted(set(v['type'] for v in species.values())),
    })


@app.route('/get_sim_data')
def get_sim_data():
    rel_path     = request.args.get('path', '')
    results_base = os.path.join(REPO_ROOT, 'results')
    full_path    = os.path.abspath(os.path.join(results_base, rel_path))
    if not full_path.startswith(results_base) or not os.path.isfile(full_path):
        return jsonify({'error': 'File not found'}), 404
    try:
        df = load_simulation_table(full_path)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    wanted_raw = request.args.get('species', '')
    if wanted_raw:
        # Callers may pass either bare ids (s_ABA_cyt_m) or RoadRunner's bracketed
        # form ([s_ABA_cyt_m]); simulate.py writes the bare form. Match either.
        wanted = {s.strip().strip('[]') for s in wanted_raw.split(',') if s.strip()}
        species_cols = [c for c in df.columns
                        if c != 'time' and str(c).strip('[]') in wanted]
    else:
        species_cols = [c for c in df.columns if c != 'time']

    max_pts = 200
    if len(df) > max_pts:
        step = max(1, len(df) // max_pts)
        df   = df.iloc[::step].reset_index(drop=True)

    time_arr  = df['time'].to_numpy(dtype=float)
    time_vals = np.round(time_arr, 3).tolist()

    def _round_sig(v, sig=6):
        # Species concentrations span ~1e-9 to 1e2. Rounding to 6 *decimal places*
        # would flatten every metabolite to 0.0, so round to significant digits.
        if not np.isfinite(v) or v == 0.0:
            return 0.0 if np.isfinite(v) else None
        return float(f"%.{sig}g" % v)

    species_out = {}
    for sp in species_cols:
        arr = df[sp].to_numpy(dtype=float)
        species_out[str(sp).strip('[]')] = [_round_sig(v) for v in arr]

    return jsonify({
        'time':          time_vals,
        'species':       species_out,
        'n_points':      len(df),
        'n_species':     len(species_cols),
        'total_species': len([c for c in df.columns if c != 'time']),
    })


# ── Routes: Work directory inspector ─────────────────────────────────────────

@app.route('/work_tasks')
def work_tasks():
    work_dir = os.path.join(REPO_ROOT, 'work')
    if not os.path.exists(work_dir):
        return jsonify({'tasks': []})

    tasks = []
    for prefix in sorted(os.listdir(work_dir)):
        if prefix == 'conda':
            continue
        prefix_path = os.path.join(work_dir, prefix)
        if not os.path.isdir(prefix_path):
            continue
        for hash_dir in sorted(os.listdir(prefix_path)):
            task_path = os.path.join(prefix_path, hash_dir)
            if not os.path.isdir(task_path):
                continue

            exit_code = None
            exitcode_file = os.path.join(task_path, '.exitcode')
            if os.path.exists(exitcode_file):
                try:
                    exit_code = int(open(exitcode_file).read().strip())
                except Exception:
                    pass

            process_name = None
            cmd_sh = os.path.join(task_path, '.command.sh')
            if os.path.exists(cmd_sh):
                try:
                    with open(cmd_sh, 'r', errors='ignore') as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith('#') and not line.startswith('set '):
                                m = re.search(r'(\w+)\.py', line)
                                if m:
                                    process_name = m.group(1)
                                    break
                except Exception:
                    pass

            available_files = []
            for fname in ['.command.sh', '.command.out', '.command.err', '.command.log', '.command.run']:
                fpath = os.path.join(task_path, fname)
                if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
                    available_files.append(fname)

            tasks.append({
                'hash':            f'{prefix}/{hash_dir}',
                'short_hash':      f'{prefix[:2]}/{hash_dir[:6]}',
                'process_name':    process_name,
                'exit_code':       exit_code,
                'available_files': available_files,
                'mtime':           os.path.getmtime(task_path),
            })

    tasks.sort(key=lambda t: t['mtime'], reverse=True)
    return jsonify({'tasks': tasks})


@app.route('/work_file')
def work_file():
    task_hash = request.args.get('hash', '')
    filename  = request.args.get('file', '')
    allowed   = {'.command.sh', '.command.out', '.command.err', '.command.log', '.command.run', '.exitcode'}
    if filename not in allowed:
        return jsonify({'error': 'Not allowed'}), 403

    full = os.path.abspath(os.path.join(REPO_ROOT, 'work', task_hash, filename))
    if not full.startswith(os.path.join(REPO_ROOT, 'work')):
        return jsonify({'error': 'Forbidden'}), 403
    if not os.path.isfile(full):
        return jsonify({'content': '(empty)', 'size': 0})

    size = os.path.getsize(full)
    with open(full, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    return jsonify({'content': content, 'size': size})


# ── Routes: Analysis ──────────────────────────────────────────────────────────

def _load_sim_files(rel_files, results_base):
    """Validate paths and load simulation DataFrames. Raises ValueError on bad input."""
    loaded = []
    for rel_path in rel_files:
        full_path = os.path.abspath(os.path.join(results_base, rel_path))
        if not full_path.startswith(results_base) or not os.path.isfile(full_path):
            raise ValueError(f'Invalid file: {rel_path}')
        try:
            df = load_simulation_table(full_path)
        except Exception as e:
            raise ValueError(f'Failed to load {rel_path}: {e}')
        loaded.append({'path': rel_path, 'name': os.path.basename(rel_path), 'df': df})
    return loaded


def _shared_species(loaded):
    species = set(loaded[0]['df'].columns) - {'time'}
    for item in loaded[1:]:
        species &= (set(item['df'].columns) - {'time'})
    return sorted(species)


@app.route('/compare_simulations', methods=['POST'])
def compare_simulations_route():
    payload      = request.get_json(force=True)
    rel_files    = payload.get('files', [])
    baseline_rel = payload.get('baseline')
    time_point   = float(payload.get('time_point', 0))
    focus_species = payload.get('focus_species')

    if len(rel_files) < 2:
        return jsonify({'error': 'At least 2 files are required'}), 400

    results_base = os.path.abspath(os.path.join(REPO_ROOT, 'results'))
    try:
        loaded = _load_sim_files(rel_files, results_base)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    baseline_item = next((x for x in loaded if x['path'] == baseline_rel), loaded[0])
    try:
        result = compare_simulations(loaded, baseline_item, time_point, focus_species)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(result)


@app.route('/compare_embeddings', methods=['POST'])
def compare_embeddings():
    payload   = request.get_json(force=True)
    rel_files = payload.get('files', [])
    if len(rel_files) < 2:
        return jsonify({'error': 'At least 2 files are required'}), 400

    results_base = os.path.abspath(os.path.join(REPO_ROOT, 'results'))
    try:
        loaded = _load_sim_files(rel_files, results_base)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    shared = _shared_species(loaded)
    if not shared:
        return jsonify({'error': 'No shared species'}), 400

    return jsonify({
        'embedding_trajectory':     pca_trajectory(loaded, shared),
        'embedding_species_frames': precompute_species_embeddings(loaded, shared),
        'embedding_species_tsne':   compute_full_tsne_embedding(loaded, shared),
    })



@app.route('/quality_check', methods=['POST'])
def quality_check():
    payload      = request.get_json(force=True)
    rel_paths    = payload.get('files', [])
    results_base = os.path.join(REPO_ROOT, 'results')

    loaded_files = []
    for rel_path in rel_paths:
        full_path = os.path.abspath(os.path.join(results_base, rel_path))
        if not full_path.startswith(results_base) or not os.path.isfile(full_path):
            continue
        try:
            df = load_simulation_table(full_path)
            loaded_files.append({'rel_path': rel_path, 'df': df})
        except Exception as e:
            loaded_files.append({'rel_path': rel_path, 'error': str(e)})

    reactions_path = os.path.join(REPO_ROOT, 'results', 'processPSS', 'reactions.json')
    return jsonify(quality_check_files(loaded_files, reactions_path))


@app.route('/reaction_activity', methods=['POST'])
def reaction_activity():
    payload      = request.get_json(force=True)
    rel_files    = payload.get('files', [])
    results_base = os.path.abspath(os.path.join(REPO_ROOT, 'results'))

    loaded_files = []
    for rel_path in rel_files:
        full_path = os.path.abspath(os.path.join(results_base, rel_path))
        if not full_path.startswith(results_base) or not os.path.isfile(full_path):
            loaded_files.append({'rel_path': rel_path, 'error': 'File not found'})
            continue
        name = os.path.basename(full_path)
        if not name.startswith('simulation_'):
            loaded_files.append({'rel_path': rel_path, 'error': 'Not a simulation file'})
            continue
        suffix      = name[len('simulation_'):-len('.csv')]
        totals_path = os.path.join(os.path.dirname(full_path), f'reaction_totals_{suffix}.csv')
        if not os.path.isfile(totals_path):
            loaded_files.append({'rel_path': rel_path, 'name': name, 'missing': True})
            continue
        loaded_files.append({'rel_path': rel_path, 'name': name, 'totals_path': totals_path})

    return jsonify({'results': reaction_activity_files(loaded_files)})


@app.route('/list_antimony')
def list_antimony():
    base = os.path.join(REPO_ROOT, 'results', 'buildAntimony')
    if not os.path.exists(base):
        return jsonify({'files': []})
    files = sorted(f for f in os.listdir(base) if f.endswith('.txt') or f.endswith('.ant'))
    return jsonify({'files': files})


# ── Parameter Scan ────────────────────────────────────────────────────────────

param_scan_jobs = {}


def _find_tellurium_python():
    import sys as _sys
    import importlib
    try:
        importlib.import_module('tellurium')
        return _sys.executable
    except ImportError:
        pass

    candidates = []
    home = os.path.expanduser('~')
    for root in [
        os.path.join(home, 'miniconda3', 'envs'),
        os.path.join(home, 'anaconda3', 'envs'),
        os.path.join(home, 'miniforge3', 'envs'),
        '/opt/conda/envs', '/opt/miniconda3/envs', '/opt/anaconda3/envs',
    ]:
        if os.path.isdir(root):
            for env in os.listdir(root):
                py = os.path.join(root, env, 'bin', 'python')
                if os.path.isfile(py):
                    candidates.append(py)

    try:
        out = subprocess.check_output(['conda', 'env', 'list'], stderr=subprocess.DEVNULL, text=True, timeout=5)
        for line in out.splitlines():
            if line.startswith('#') or not line.strip():
                continue
            env_path = line.split()[-1].rstrip('*').strip()
            py = os.path.join(env_path, 'bin', 'python')
            if os.path.isfile(py) and py not in candidates:
                candidates.append(py)
    except Exception:
        pass

    for py in candidates:
        try:
            r = subprocess.run([py, '-c', 'import tellurium; print("ok")'],
                               capture_output=True, text=True, timeout=10)
            if r.stdout.strip() == 'ok':
                return py
        except Exception:
            continue

    return None


class ParamScanJob:
    def __init__(self, job_id, model_path, param_name, values, sim_start, sim_end, sim_points):
        self.job_id     = job_id
        self.model_path = model_path
        self.param_name = param_name
        self.values     = values
        self.sim_start  = sim_start
        self.sim_end    = sim_end
        self.sim_points = sim_points
        self.status     = 'pending'
        self.progress   = 0
        self.result     = None
        self.error      = None

    def run(self):
        self.status = 'running'
        try:
            import tellurium as te
            self._run_direct(te)
            return
        except ImportError:
            pass

        py = _find_tellurium_python()
        if py is None:
            self.status = 'error'
            self.error  = ('tellurium not found in any Python environment — '
                           'activate thesis_env or install tellurium: '
                           'conda install -c conda-forge tellurium')
            return
        self._run_subprocess(py)

    def _run_direct(self, te):
        try:
            r = te.loada(open(self.model_path).read())
        except Exception as e:
            self.status = 'error'
            self.error  = f'Failed to load model: {e}'
            return

        try:
            original_val = r[self.param_name]
        except Exception:
            self.status = 'error'
            self.error  = f'Parameter "{self.param_name}" not found in model'
            return

        scan_results = []
        for i, val in enumerate(self.values):
            try:
                r.reset()
                r[self.param_name] = val
                sim = r.simulate(self.sim_start, self.sim_end, self.sim_points)
                df  = pd.DataFrame(sim, columns=sim.colnames)
                final = df.iloc[-1]
                concs = {col: safe_float(final[col]) for col in df.columns if col != 'time'}
                scan_results.append({'param_value': val, 'concentrations': concs, 'error': None})
            except Exception as e:
                scan_results.append({'param_value': val, 'concentrations': None, 'error': str(e)})
            finally:
                try:
                    r[self.param_name] = original_val
                except Exception:
                    pass
            self.progress = int((i + 1) / len(self.values) * 100)

        self.result = finalize_scan_results(self.param_name, self.values, scan_results)
        self.status = 'done'

    def _run_subprocess(self, python_exe):
        worker_path = os.path.join(os.path.dirname(__file__), 'scan_worker.py')
        spec = {
            'model_path': self.model_path, 'param_name': self.param_name,
            'values':     self.values,     'sim_start':  self.sim_start,
            'sim_end':    self.sim_end,    'sim_points': self.sim_points,
        }
        try:
            proc = subprocess.Popen(
                [python_exe, worker_path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            proc.stdin.write(json.dumps(spec))
            proc.stdin.close()

            def _read_progress():
                for line in proc.stderr:
                    try:
                        msg = json.loads(line.strip())
                        if 'progress' in msg:
                            self.progress = msg['progress']
                    except Exception:
                        pass

            t = threading.Thread(target=_read_progress, daemon=True)
            t.start()
            stdout_data = proc.stdout.read()
            proc.wait(timeout=300)
            t.join(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.status = 'error'
            self.error  = 'Parameter scan timed out after 5 minutes'
            return
        except Exception as e:
            self.status = 'error'
            self.error  = f'Subprocess error: {e}'
            return

        try:
            payload = json.loads(stdout_data)
        except Exception:
            self.status = 'error'
            self.error  = 'Scan subprocess returned invalid output'
            return

        if not payload.get('ok'):
            self.status = 'error'
            self.error  = payload.get('error', 'Unknown scan error')
            return

        self.result   = payload['result']
        self.status   = 'done'
        self.progress = 100


@app.route('/param_scan', methods=['POST'])
def start_param_scan():
    payload    = request.get_json(force=True)
    param_name = payload.get('param_name', '').strip()
    values     = [float(v) for v in payload.get('values', [])]
    sim_start  = float(payload.get('start', 0))
    sim_end    = float(payload.get('end', 1000))
    sim_points = int(payload.get('points', 500))
    model_file = payload.get('model_file', 'Antimony.txt')

    if not param_name or not values:
        return jsonify({'error': 'param_name and values are required'}), 400
    if len(values) > 25:
        return jsonify({'error': 'Maximum 25 scan steps allowed'}), 400

    model_path = os.path.abspath(os.path.join(REPO_ROOT, 'results', 'buildAntimony', model_file))
    if not model_path.startswith(REPO_ROOT) or not os.path.isfile(model_path):
        return jsonify({'error': 'Model file not found in results/buildAntimony/'}), 404

    job_id = str(uuid.uuid4())
    job    = ParamScanJob(job_id, model_path, param_name, values, sim_start, sim_end, sim_points)
    param_scan_jobs[job_id] = job
    threading.Thread(target=job.run, daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route('/param_scan_result/<job_id>')
def param_scan_result(job_id):
    job = param_scan_jobs.get(job_id)
    if not job:
        return jsonify({'error': '404'}), 404
    return jsonify({
        'status':   job.status,
        'progress': job.progress,
        'error':    job.error,
        'result':   job.result,
    })


if __name__ == '__main__':
    app.run(debug=True, port=5001)
