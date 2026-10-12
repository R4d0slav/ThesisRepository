# PSS Kinetic Modelling Pipeline — UI Documentation

This document describes the purpose, architecture, and inner workings of the web-based user interface for the PSS (Plant Stress Signalling) Kinetic Modelling Pipeline, built as part of a Master's thesis at [Faculty/University].

---

## Table of Contents

1. [Overview](#1-overview)
2. [Repository Structure](#2-repository-structure)
3. [Running the UI](#3-running-the-ui)
4. [The Pipeline Itself](#4-the-pipeline-itself)
5. [Backend (app.py)](#5-backend-apppy)
6. [Frontend Panels](#6-frontend-panels)
7. [API Reference](#7-api-reference)
8. [Data Formats](#8-data-formats)
9. [Key Concepts and Conventions](#9-key-concepts-and-conventions)

---

## 1. Overview

The UI is a single-page web application that wraps a **Nextflow DSL2** pipeline for building and simulating ODE (ordinary differential equation) kinetic models of plant stress signalling networks. It lets you:

- Configure and launch the full pipeline with a point-and-click interface
- Monitor live log output as the pipeline runs
- Browse all result files (tables, plots, interactive HTML outputs)
- Compare multiple simulation runs side-by-side with statistical metrics and t-SNE/PCA embedding visualisation
- Run parameter scans to assess sensitivity of the model to any kinetic parameter or initial concentration
- Overlay simulation data on the network graph and animate signal flow through time
- Check simulation quality (negative concentrations, unstable species, dead-end species)

**Stack:**
- Backend: Python 3 / Flask, pandas, sklearn (t-SNE), tellurium/libRoadRunner (ODE simulation)
- Frontend: Vanilla JS, Chart.js (plots), Cytoscape.js (network graph)
- Pipeline runner: Nextflow DSL2
- ODE model format: Antimony (text-based, loaded by tellurium)

---

## 2. Repository Structure

```
ThesisRepository/
├── main.nf                        Nextflow workflow definition
├── nextflow.config                Profile and parameter defaults
├── thesis_env.yml                 Conda environment specification
├── reset.sh                       Convenience script to wipe runtime outputs
├── modules/                       One .nf file per pipeline stage
│   ├── processPSS.nf
│   ├── decayRates.nf
│   ├── initialConcentrations.nf
│   ├── rateLaws.nf
│   ├── kineticParameters.nf
│   ├── buildAntimony.nf
│   └── simulatePSS.nf
├── scripts/                       Python scripts called by Nextflow modules
│   ├── utils.py                   Shared constants and paths (used by notebooks)
│   ├── pss_adapter_cli.py         Upstream tool: export PSS/Neo4j → SBML
│   └── modeling/
│       ├── process_pss.py
│       ├── decay_rates.py
│       ├── concentrations.py
│       ├── rate_laws.py
│       ├── kinetic_parameters.py
│       ├── build_antimony.py
│       └── simulate.py
├── data/                          All input data files
│   ├── output-laws-RISC_ignored_10112025.sbml   PSS network (SBML)
│   ├── ath_decay_rates.tsv        RNA/protein decay rate table
│   ├── Initial_Concentrations.csv Protein and metabolite concentrations
│   ├── reactions_by_combination.xlsx  Rate law assignments per reaction type
│   ├── Reactions_kinetics.xlsx    Gathered kinetic parameters (BRENDA/SABIO-RK)
│   ├── reactions-2025.02.04.xlsx  NIB annotated kinetic parameters
│   └── events/                    Perturbation event files
│       ├── intervention/          Stress treatments (drought, heat, JA, ABA, …)
│       └── knockout/              Gene knockouts (coi1, …)
├── notebooks/                     Jupyter notebooks mirroring each pipeline step
├── documentation/                 Thesis planning documents and disposition
├── ui/                            Web UI — see ui/DOCUMENTATION.md
│   ├── app.py                     Flask backend
│   ├── scan_worker.py             Subprocess worker for parameter scans
│   ├── sbml_network_viewer.html   Standalone Cytoscape.js network viewer
│   ├── DOCUMENTATION.md           Full UI reference (this file)
│   ├── templates/index.html       Single-page app HTML
│   └── static/
│       ├── css/style.css
│       └── js/script.js
├── results/                       Pipeline outputs — gitignored, created on first run
│   ├── processPSS/                species.json, reactions.json
│   ├── decayRates/                decay_rate_mapper.pkl
│   ├── initialConcentrations/     concentration_mapper.pkl
│   ├── rateLaws/                  rate_law_mapper.pkl
│   ├── kineticParameters/         kinetic_parameter_mapper.pkl
│   ├── buildAntimony/             Antimony.txt
│   └── simulatePSS/
│       ├── tables/                Time-series CSVs, one per condition
│       ├── images/                Static PNG plots
│       └── interactive/           Interactive HTML plots
├── work/                          Nextflow scratch space — gitignored
├── tmp/                           Temporary files created by the UI — gitignored
└── logs/                          Nextflow trace, report, and timeline — gitignored
```

---

## 3. Running the UI

```bash
cd ui
python3 app.py
```

Open `http://localhost:5000` in a browser. The server runs in debug mode on port 5000.

**Dependencies** (install with pip):
```
flask flask-cors pandas openpyxl scikit-learn numpy tellurium
```

Nextflow must also be installed and available on `PATH` for the pipeline to run.

**Note on tellurium:** The Flask server does not need to be started from an environment that has tellurium installed. At scan time, `ParamScanJob` will search all conda environments for one that has tellurium and delegate to it automatically via subprocess. The recommended setup is a conda environment named `thesis_env` (or any name) with tellurium 2.2.10 installed. If tellurium is available in the Flask server's own interpreter, that is used directly (faster, no subprocess overhead).

---

## 4. The Pipeline Itself

The pipeline takes a plant stress signalling (PSS) network described in SBML format and builds a kinetic ODE model, then simulates it under user-specified stress conditions. It runs in four stages:

### Stage 1 — Parse PSS Network (`processPSS`)
Reads the SBML file. Outputs two JSON files:
- `species.json`: all species, each with keys `name`, `type` (protein, metabolite, gene, mrna, complex, …), `compartment` (c_cyt, c_nuc, etc.)
- `reactions.json`: all reactions, each with `reactants`, `products`, `reaction_type`

### Stage 2 — Build Parameter Mappers (4 parallel processes)
All four run simultaneously using the species/reactions JSON from Stage 1:

| Process | Input files | Output |
|---|---|---|
| `decayRates` | `ath_decay_rates.tsv` | `decay_rate_mapper.json` |
| `initialConcentrations` | `Initial_Concentrations.csv` | `concentration_mapper.json` |
| `rateLaws` | `reactions_by_combination.xlsx` | `rate_law_mapper.json` |
| `kineticParameters` | `Reactions_kinetics.xlsx`, `reactions-*.xlsx` | `kinetics_mapper.json` |

### Stage 3 — Assemble Antimony Model (`buildAntimony`)
Merges the network topology and all four mappers into a single Antimony-format text file (`Antimony.txt`). This file defines:
- Compartments and their sizes
- Species declarations (with compartment membership)
- ODE rate laws (velocity expressions like Michaelis-Menten, Hill functions, mass-action)
- Kinetic parameter values (`kcat_*`, `Kd_*`, …)
- Initial concentrations for all species

### Stage 4 — Simulate (`simulatePSS`)
Loads `Antimony.txt` into tellurium and runs one simulation per condition (each condition is a row in the `sim_conditions` JSON). Outputs per condition:
- A CSV table of species concentrations over time (columns: `time`, `[s_xxx]`, `[s_yyy]`, …)
- PNG plots
- Interactive HTML plots

**Simulation conditions** are configured in the UI and written to a temporary JSON file at `tmp/sim_<job_id>.json` before the pipeline runs. Each condition entry:
```json
{ "start": 0, "end": 1000, "points": 1000, "suffix": "drought",
  "events": "/absolute/path/to/drought.json" }
```
Events are optional perturbation files that modify species concentrations at specific time points during the simulation.

---

## 5. Backend (app.py)

### `PipelineJob` class
Created per pipeline run. Spawns `nextflow run main.nf ...` as a subprocess, reads stdout character-by-character, strips ANSI codes, and classifies lines into two log streams:
- **clean log**: only meaningful lines (stage completion, errors, key events)
- **raw log**: every line of Nextflow output

Stage completion is detected by regex-matching Nextflow's `[100%] process > ProcessName` lines and mapping them through `PROCESS_TO_STAGE`.

Stage 2 is special: it tracks all four sub-processes individually and only marks Stage 2 complete when all four finish.

### `ParamScanJob` class
Created per parameter scan request. Runs in a background thread. Has two execution paths:

**Direct path** (`_run_direct`): used when `import tellurium` succeeds in the Flask process. Loads the Antimony model and iterates through scan values in-process, updating `self.progress` after each step.

**Subprocess path** (`_run_subprocess`): used when tellurium is not available in the Flask process. `_find_tellurium_python()` is called first — it scans common conda environment locations (`~/miniconda3/envs/`, `~/miniforge3/envs/`, etc.) and also queries `conda env list`, probing each candidate Python until one that can `import tellurium` is found. The scan then runs in `scan_worker.py` under that interpreter. The spec is passed as JSON via stdin; progress updates are written to stderr as `{"progress": N}` lines and read by a background thread so the UI progress bar stays live; the final result arrives on stdout as `{"ok": true, "result": {...}}`.

In both paths, after all values are run, `_finalize` computes a sensitivity score per species (maximum absolute percentage change from baseline) and returns the top 60 most sensitive species.

For initial concentrations, tellurium uses bracket notation: `r["[s_VPg_cyt_p]"] = 0.001` sets the starting concentration, then `r.reset()` at the next iteration restores the original before the new value is applied.

### Key helper functions

**`load_simulation_table(path)`** — Reads a CSV/Excel simulation output into a pandas DataFrame. Auto-detects the time column (column named `time` or `t`, otherwise the first column). Converts all values to float, drops NaN time rows, sorts by time.

**`parse_table_file(path)`** — Reads input data files (CSV/TSV/Excel) for display in the file browser. Truncates to 200 rows and 20 columns for performance.

**`pca_trajectory(loaded, shared_species, max_pts=100)`** — Builds a simulation trajectory embedding for the **Focus species** charts. For each simulation, the concentration time-series of all shared species is sampled at up to `max_pts` evenly-spaced time points. All `[n_sims × max_pts, n_species]` samples are stacked, standardised, and projected onto the top 2 principal components via SVD. Returns a list of `{sim, path, ti, t, x, y}` points annotated with the variance explained by each PC. Connecting points within a simulation traces how the full species state vector evolves over time.

**`precompute_species_embeddings(loaded, shared_species, n_frames=25, max_pts=40)`** — Precomputes all time-slider frames for the **Species space** chart. Each row of the feature matrix represents one `(species, simulation)` pair; its feature vector is the species' concentration time-series resampled to `max_pts` columns. A single global PCA basis (via SVD on the full matrix) is computed once so all frames share the same axes. For each of the `n_frames` time points, columns corresponding to future time steps are zeroed out before projection — this means early frames show only initial dynamics while late frames show the full trajectory, with dots moving smoothly as the slider advances.

**`_embed_vectors(vectors, loaded)`** — Standardises a matrix of row vectors and runs the embedding. Zero-variance columns are dropped before standardisation.

**`parse_sensitivity_label(name)`** — Parses a filename like `simulation_kcat_50pct.csv` into a `(parameter, perturbation)` tuple for display in comparison tables.

---

## 6. Frontend Panels

The UI is a single HTML page (`index.html`) with a fixed sidebar for navigation and a main content area. Pressing a sidebar item calls `showPanel(id)` which hides all panels and shows the selected one. Keyboard shortcuts 1–9 do the same.

### Panel: Pipeline (id=`pipeline`)
The main run panel. Shows:
- **Configuration grid**: profile (local/conda/docker), data/script/events paths, result subset filter
- **Simulation conditions table**: each row is one simulation run. Columns: start, end, points, suffix, events file. Rows can be added/removed; each generates a unique suffix automatically.
- **Command preview**: a live-rendered `nextflow run ...` command reflecting current settings
- **Run / Clean buttons**: Run launches the pipeline; Clean deletes `results/`, `work/`, and `.nextflow*` files

When the pipeline is running the status indicator in the top bar changes to `RUNNING` (yellow) and to `DONE`/`ERROR` on finish.

### Panel: Logs (id=`logs`)
Streams logs from the active pipeline job via **Server-Sent Events** (`/logs/<job_id>`). Shows two views:
- **Clean** (default): only stage-completion messages and meaningful events
- **Raw**: every Nextflow output line

Stage progress indicators (Stage 1–4) at the top update as the pipeline reports completion.

### Panel: Results (id=`results`)
A file browser for `results/` (and optionally `data/`). Clicking a file loads its content into the viewer:
- Tables (CSV/TSV/XLSX): rendered as a paginated HTML table with optional row/column truncation warnings
- Text files (`.txt`, `.py`, `.json`, `.nf`, Antimony models): shown in a `<pre>` block
- Images (PNG/JPG): shown as `<img>`
- HTML files: rendered inside a sandboxed `<iframe>`

Input data files in `data/` can be edited in-browser and saved back to disk via `/save_table_file`.

### Panel: Comparison (id=`compare`)
Compares two or more simulation result CSVs side by side. Workflow:
1. Select ≥2 files from `results/simulatePSS/tables/`
2. Select a baseline file
3. Click **Run comparison** — calls `/compare_simulations`

The panel is divided into two independent sections:

**Focus species section** — driven by the searchable species picker at the top of the panel. Selecting a species updates:
- **KPI chips**: steady-state % change, peak shift, AUC difference, and cumulative difference vs baseline for the focused species
- **Trajectories chart**: time-series overlay of the focused species across all simulations
- **Delta chart**: difference from baseline at each time point

The focus species picker preserves the user's selection across repeated comparison runs.

**Species space section** (visually separated, blue-tinted border) — independent of the focus species. Shows all species across all simulations simultaneously:
- 1 dot per `(species, simulation)` pair — e.g. 1000 species × 3 simulations = 3000 dots, coloured by simulation
- Axes are the top 2 principal components of the `[n_species × n_sims, max_pts]` concentration time-series matrix (global PCA basis, computed once)
- A **time slider** (T) scrubs through 25 precomputed frames. All frames share the same axes so dots move smoothly without axis-flipping. At T=0 only initial conditions matter; at T_max the full dynamics are revealed
- Species with similar dynamic behaviour cluster together; species that respond differently between simulations have their coloured dots far apart

Below both sections: a **species ranking table** (all shared species sorted by max absolute steady-state % difference) and a **per-file metrics table** (one row per non-baseline simulation). Clicking a row in the ranking table switches the focus species.

Comparison metrics computed per species per simulation:
| Metric | Meaning |
|---|---|
| Steady-state % diff | `(final_cmp - final_base) / final_base × 100` |
| Timepoint % diff | Same but at the requested time point |
| Cumulative difference | Sum of `\|cmp - base\|` across all matched time rows |
| AUC difference | Trapezoidal integral of `\|cmp - base\|` over time |
| Final concentration change | Absolute change at last time point |
| Peak shift | Difference in time-to-peak between simulation and baseline |

### Panel: Network Viewer (id=`network`)
Launches the standalone SBML network viewer (`sbml_network_viewer.html`) in an iframe. This is a pre-built cytoscape.js visualisation of the PSS network structure directly from the SBML source file.

### Panel: Quality Checks (id=`quality`)
Runs structural and numerical quality checks on one or more simulation output CSVs.

**How to use:**
1. Click **Refresh** to list available simulation tables
2. Click files to select them (multiple selection supported)
3. Click **Analyse** — calls `/quality_check`

**Checks performed:**
- **Negative species**: any species whose concentration goes below zero at any time point. Reports the time when it first goes negative. These indicate numerical instability or missing reactions.
- **Non-stable species**: species whose relative standard deviation in the final 10% of the time series exceeds 1%. These have not reached steady state.
- **Extreme species**: species whose maximum absolute concentration exceeds 10⁶ (likely numerical blow-up).
- **Dead-end species**: purely structural check from `reactions.json`. Any species that appears as a reaction product but never as a reactant — signal arrives but never propagates further. These are computed once regardless of which simulation files are selected.

Results are shown as counts and coloured chip lists. The quality badge in the sidebar updates to show the number of negative species (or ✓ if none).

### Panel: Parameter Scan (id=`param-scan`)
Varies a single model parameter across a range of values and measures the effect on all species steady-state concentrations. This is the **sensitivity analysis** tool.

**How to use:**
1. Select an Antimony model file (from `results/buildAntimony/`)
2. Click **Load Parameters** — parses the model text for all parameters and initial concentrations (3176 in the current model: 1139 species ICs + 2037 kinetic params)
3. Search/filter and click a parameter to select it, or type it manually
4. Set scan type (linear or log₁₀ spacing), min, max, and number of steps (max 25)
5. Click **Run Scan** — launches an async job, polls every 1.5 s for progress

**Results:**
- **Sensitivity ranking**: top 20 most-affected species with percentage bars (red = >50% change, orange = >10%)
- **Heatmap**: rows = top 20 species, columns = scan values. Cell colour shows absolute concentration (log₁₀ blue→white→red) or fold-change vs baseline (blue = decreased, red = increased)

Parameter names follow tellurium/libRoadRunner conventions:
- Kinetic parameters: `kcat_rx00669`, `Kd_s_VPg_cyt_p_rx00669`, etc.
- Initial concentrations: `[s_VPg_cyt_p]` (brackets indicate IC in roadrunner)

### Panel: Simulation Flow (id=`sim-flow`)
Overlays simulation time-series data on the PSS network graph and animates it. Node size and colour represent species concentration at the current time point.

**How to use:**
1. The network graph loads automatically when the panel opens (filtered to `c_cyt` by default — the cytoplasm compartment, which contains the most species)
2. Use the **Compartment** filter to switch to other cellular compartments
3. Use the **Type** filter to show only proteins, metabolites, genes, etc.
4. Select a simulation file and click **Load** — downloads and stores the time-series data
5. Drag the **Time** slider to scrub through time, or click **▶ Play** to animate
6. Click any node to see its ID, name, type, compartment, current concentration, and a mini time-series chart

**Colour modes:**
- **Abs concentration** (default): log₁₀ concentration mapped blue (low) → white (mid) → red (high). The range adapts to the min/max values currently visible.
- **Fold-change vs t=0**: deviation from initial concentration. Blue = lower than t=0, red = higher.
- **Species type**: nodes are coloured by biological type (protein = blue, metabolite = orange, gene = purple, etc.)

**Concentration threshold slider**: hides nodes below a log₁₀ concentration threshold (default 10⁻⁸) to reduce clutter. This updates the visibility of nodes without rebuilding the graph layout.

The graph uses a Cytoscape.js `cose` (force-directed) layout computed once per filter change. Edges connect reactants to products within visible reactions. The layout is stable between time steps — only node colour and size change.

---

## 7. API Reference

All endpoints are served by Flask on `http://localhost:5000`.

| Method | Path | Description |
|---|---|---|
| GET | `/` | Serves the main HTML page |
| POST | `/run` | Starts a pipeline job. Body: `{profile, data_path, script_path, events_path, subset, sim_conditions, resume}`. Returns `{job_id}` |
| POST | `/clean` | Deletes `results/`, `work/` Nextflow scratch, and `.nextflow*` state |
| GET | `/status/<job_id>` | Returns `{status, completed_stages, stage_2_details, error, errors}` for a pipeline job |
| GET | `/logs/<job_id>` | SSE stream of log lines. Each event is `{source: "clean"|"raw", msg, type}` |
| GET | `/ls` | Lists files. Params: `category=results|data`, `path=sub/dir`. Returns `{type:"dir", items:[{name,path,type}]}` |
| GET | `/get_file` | Returns file content. Params: `category`, `path`. Auto-detects format: returns `{type:"table",headers,rows}` for CSV/Excel, `{type:"text",content}` for text, `{type:"html",content}` for HTML, or serves image directly |
| POST | `/save_table_file` | Saves edited table back to disk. Body: `{path, category:"data", headers, rows}` |
| GET | `/work_tasks` | Lists Nextflow work directory tasks. Returns `{tasks:[{hash,process_name,exit_code,available_files}]}` |
| GET | `/work_file` | Returns content of a Nextflow work task file (`.command.sh`, `.command.out`, etc.). Params: `hash`, `file` |
| GET | `/sbml_viewer` | Serves the standalone SBML network viewer HTML |
| POST | `/compare_simulations` | Compares simulation files. Body: `{files, baseline, time_point, focus_species}`. Returns full metrics, rankings, focused plot, and t-SNE embedding |
| GET | `/network_data` | Returns `{species, reactions, compartments, types}` from `results/processPSS/` |
| GET | `/get_sim_data` | Returns downsampled (≤200 points) simulation time-series. Param: `path` (relative to `results/`). Returns `{time, species:{[s_xxx]:[...]}, n_points, n_species}` |
| POST | `/quality_check` | Runs quality analysis on simulation files. Body: `{files:[...relative paths...]}`. Returns `{file_reports, dead_end_species, dead_end_count}` |
| GET | `/list_antimony` | Lists `.txt`/`.ant` files in `results/buildAntimony/`. Returns `{files:[...]}` |
| POST | `/param_scan` | Starts an async parameter scan job. Body: `{model_file, param_name, values, start, end, points}`. Returns `{job_id}` |
| GET | `/param_scan_result/<job_id>` | Polls scan job. Returns `{status, progress, error, result}` where `result` contains `{values, scan_results, sensitivity, top_sensitive_species}` |

---

## 8. Data Formats

### `species.json`
```json
{
  "s_VPg_cyt_p": {
    "name": "VPg",
    "type": "protein",
    "compartment": "c_cyt"
  },
  ...
}
```
Keys are species IDs (no brackets). The PSS network has 1139 species.

### `reactions.json`
```json
{
  "rx00669": {
    "reactants": ["s_VPg_cyt_p", "s_LHB1B1_cyt_p"],
    "products":  ["s_LHB1B1VPg_cyt_c"],
    "reaction_type": "binding"
  },
  ...
}
```
864 reactions in total.

### Simulation CSVs (output of `simulatePSS`)
Column headers are `time`, `[s_xxx]`, `[s_yyy]`, ... — species IDs wrapped in square brackets (roadrunner convention). Rows are time points. Up to ~1005 species columns per file.

### Antimony model (`Antimony.txt`)
Plain text, Antimony syntax. Structure:
1. `model PSS` header
2. Global constants (`n = 1;`)
3. Compartment sizes (`c_cyt = 1;`, etc.)
4. Species declarations (`species s_xxx in c_cyt;`)
5. Rate law equations (`v_rx00669 := ...;`)
6. Species initial concentrations (`s_xxx = 0.0007;`)
7. Kinetic parameter values (`kcat_rx00669 = 0.79;`, `Kd_s_xxx_rx00669 = 0.00036;`)

All parameter assignment lines are tab-indented.

### Simulation conditions JSON (`tmp/sim_<job_id>.json`)
```json
[
  { "start": 0, "end": 1000, "points": 1000, "suffix": "default" },
  { "start": 0, "end": 1000, "points": 1000, "suffix": "drought",
    "events": "/abs/path/to/drought.json" }
]
```

### Event files (`events/intervention/*.json`)
Define perturbations during simulation. Example:
```json
[
  { "time": 100, "species": "s_Drought_cyt_ab", "value": 1.0 }
]
```

---

## 9. Key Concepts and Conventions

### Species ID format
Species IDs are strings like `s_VPg_cyt_p`. The pattern is roughly `s_<name>_<compartment>_<type_hint>`. In the simulation CSV headers they appear as `[s_VPg_cyt_p]` (roadrunner bracket notation). Everywhere in the frontend code, the brackets are added or removed explicitly:
```javascript
const key = '[' + node.id() + ']';   // node.id() = 's_xxx', key for sim lookup
```

### Tellurium / roadrunner parameter notation
- `r["kcat_rx00669"] = 0.5` — sets a global kinetic parameter
- `r["[s_VPg_cyt_p]"] = 0.001` — sets current concentration of species (not stored IC)
- After `r.reset()`, concentrations return to stored initial conditions

When scanning initial concentrations: first reset to baseline, then set the new value, then simulate. The value is in effect for that simulation run without permanently changing the stored IC.

### Compartment abbreviations
| ID | Compartment |
|---|---|
| c_ap | apoplast |
| c_chl | chloroplast |
| c_cyt | cytoplasm |
| c_er | endoplasmic reticulum |
| c_ga | Golgi apparatus |
| c_mit | mitochondria |
| c_nuc | nucleus |
| c_per | peroxisome |
| c_vac | vacuole |
| c_x | extracellular |

The `c_cyt` compartment is the default view in the Simulation Flow panel because it contains the highest density of signalling species.

### Dead-end species
A species is considered a dead-end if it appears as a product in at least one reaction but never as a reactant. This means the signal arrives at that node but does not propagate further in the network. Dead-ends are not necessarily errors — many are terminal effectors (e.g. transcription factors that activate genes downstream of the modelled network) or outputs not included in the SBML file. The check is purely structural and independent of simulation data.

### Species space embedding (Comparison panel)
The species space chart shows one dot per `(species, simulation)` pair. The embedding is computed as follows:

1. **Feature matrix** — shape `[n_species × n_sims, max_pts]`. Each row is the concentration time-series of one species in one simulation, resampled to `max_pts` (default 40) evenly-spaced time points by linear interpolation.
2. **Normalisation** — each row is z-scored (zero mean, unit variance) so species with very different absolute concentrations are still separated by their dynamic shape rather than their magnitude.
3. **Global PCA** — one SVD is computed over the full matrix. The top 2 right-singular vectors form the projection basis `V` (`[max_pts, 2]`). This is done once and reused for all 25 time-slider frames so the axes are stable across the animation.
4. **Per-frame projection** — for each of the 25 frame time points `t_k`, all columns in the feature matrix corresponding to time > `t_k` are zeroed out (masking future information). The modified matrix is then projected onto `V` to obtain `(x, y)` coordinates for each dot. At `t_k = 0` only initial concentrations contribute; at `t_k = T_max` the full time-series is used.

**Interpreting the chart:**
- **PC1 (x-axis)** captures the dominant dynamic pattern across all species and simulations (e.g. early-peaking vs late-peaking behaviour).
- **PC2 (y-axis)** captures the next most important independent pattern.
- The axis labels show the percentage of total variance explained by each PC.
- Species that behave similarly across all simulations cluster together regardless of which simulation they belong to.
- Species that respond differently between simulations have their same-simulation dots close together but inter-simulation dots spread apart.
- Dragging the slider left shows structure driven purely by initial conditions; dragging right progressively reveals dynamics.

### Log streaming
Pipeline logs are streamed to the browser using **Server-Sent Events** (SSE) rather than WebSockets. The `/logs/<job_id>` endpoint returns a `text/event-stream` response that keeps the connection open and pushes new lines as the subprocess writes them. The frontend uses `EventSource` and appends each message to the log panel in real time.
