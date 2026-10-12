/* ══════════════════════════════════════════════════════════════
   PSS Pipeline — Frontend Logic
   ══════════════════════════════════════════════════════════════ */

const state = {
    running: false,
    logSource: 'clean',
    cleanLogs: [],
    rawLogs: [],
    pollingInterval: null,
    eventSource: null,
    networkViewerLoaded: false,
    networkViewerLoading: false,
    simConditions: [
        { enabled: true, start: 0, end: 1000, points: 1000, suffix: 'default',      events: '' },
        { enabled: true, start: 0, end: 1000, points: 1000, suffix: 'drought',      events: 'data/events/intervention/drought.json' },
        { enabled: true, start: 0, end: 1000, points: 1000, suffix: 'waterlogging', events: 'data/events/intervention/waterlogging.json' },
        { enabled: true, start: 0, end: 1000, points: 1000, suffix: 'heat',         events: 'data/events/intervention/heat.json' },
        { enabled: true, start: 0, end: 1000, points: 1000, suffix: 'coi1',         events: 'data/events/knockout/coi1.json' },
    ],
    stage2Details: {
        decayRates: false,
        initialConcentrations: false,
        rateLaws: false,
        kineticParameters: false
    },
    compareSelection: [],
    compareCandidates: [],
    compareChart: null,
    compareDeltaChart: null,
    compareTsneSnapChart: null,
    compareTsneFullChart: null,
    compareSpeciesFrames: [],
    comparePayload: null,
    compareFocusSpecies: null,
    compareSpeciesAll: [],
    tableEditMode: false,
    openTablePath: null,
    openTableCategory: null
};

// Distinct colours for up to 8 simulations
const SIM_PALETTE = [
    [74,  126, 247],
    [247, 76,  76 ],
    [76,  200, 120],
    [247, 196, 76 ],
    [196, 76,  247],
    [76,  196, 247],
    [247, 140, 76 ],
    [140, 247, 76 ],
];
function simRgba(i, alpha = 1) {
    const [r, g, b] = SIM_PALETTE[i % SIM_PALETTE.length];
    return `rgba(${r},${g},${b},${alpha})`;
}

const $ = id => document.getElementById(id);

// ── Theme toggle (dark / light) ──────────────────────────────
// The saved theme is applied in <head> before first paint; this just
// flips it at runtime and persists the choice.
function toggleTheme() {
    const root = document.documentElement;
    const light = root.getAttribute('data-theme') === 'light';
    if (light) {
        root.removeAttribute('data-theme');
    } else {
        root.setAttribute('data-theme', 'light');
    }
    try { localStorage.setItem('pss-theme', light ? 'dark' : 'light'); } catch (e) {}
}

// ── Network viewer loader ────────────────────────────────────
function ensureNetworkViewerLoaded() {
    const iframe = $('network-iframe') || $('network-frame');
    if (!iframe) return;

    const targetSrc = iframe.dataset.src || '/sbml_viewer';
    const currentSrc = iframe.getAttribute('src') || '';

    if (state.networkViewerLoaded && currentSrc.includes(targetSrc)) return;
    if (state.networkViewerLoading) return;

    state.networkViewerLoading = true;

    iframe.onload = () => {
        state.networkViewerLoaded = true;
        state.networkViewerLoading = false;
    };

    iframe.onerror = () => {
        state.networkViewerLoading = false;
        state.networkViewerLoaded = false;
        const wrap = iframe.parentElement;
        if (wrap && !wrap.querySelector('.network-load-error')) {
            const err = document.createElement('div');
            err.className = 'network-load-error';
            err.style.cssText = 'padding:16px;color:#f24b5a;font-family:var(--font-mono);font-size:12px;';
            err.textContent = 'Failed to load /sbml_viewer.';
            wrap.appendChild(err);
        }
    };

    if (!currentSrc || currentSrc === 'about:blank' || !currentSrc.includes(targetSrc)) {
        iframe.src = targetSrc;
    } else {
        state.networkViewerLoaded = true;
        state.networkViewerLoading = false;
    }
}


// ── Panel navigation ──────────────────────────────────────────
function showPanel(id) {
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

    const panel = $(`panel-${id}`);
    if (panel) panel.classList.add('active');

    const navItem = document.querySelector(`.nav-item[data-panel="${id}"]`);
    if (navItem) navItem.classList.add('active');

    if (id === 'data-browser') {
        browse('', 'data-tree', 'data');
    }

    if (id === 'results-browser') {
        browse('', 'results-tree', 'results');
        renderCompareSelection();
        updateBaselineOptions();
    }

    if (id === 'compare') {
        loadCompareCandidates();
        renderCompareSelection();
        updateBaselineOptions();

        if (state.comparePayload) {
            renderCompareKpis(state.comparePayload);
            renderSpeciesRanking(state.comparePayload);
            renderCompareSummary(state.comparePayload);
            renderTsnePlot(state.comparePayload);
            populateFocusSpecies(state.compareSpeciesAll || [], state.compareFocusSpecies);
        }
    }

    if (id === 'task-inspector') {
        loadWorkTasks();
    }

    if (id === 'network')   ensureNetworkViewerLoaded();
    if (id === 'quality')   loadQualityFiles();
    if (id === 'param-scan') loadAntimonyFiles();
    if (id === 'sim-flow')   initSimflow();
}

// ── FILE SYSTEM ───────────────────────────────────────────────
async function browse(path, containerId, category) {
    const res  = await fetch(`/ls?category=${category}&path=${encodeURIComponent(path)}`);
    const data = await res.json();

    let html = `<ul class="file-tree">`;
    if (path) {
        const parent = path.split('/').slice(0, -1).join('/');
        html += `<li onclick="browse('${parent}','${containerId}','${category}')">📁 ..</li>`;
    }

    data.items.forEach(item => {
        if (item.type === 'dir') {
            html += `<li onclick="browse('${item.path}','${containerId}','${category}')">📁 ${item.name}</li>`;
            return;
        }

        const isTable = /\.(csv|tsv|xlsx|xls)$/i.test(item.name);

        html += `
            <li>
                <span style="flex:1;cursor:pointer" onclick="previewFile('${item.path}','${category}','${containerId}', this.parentElement)">📄 ${item.name}</span>
            </li>
        `;
    });

    $(containerId).innerHTML = html + `</ul>`;
    renderCompareSelection();
    updateBaselineOptions();
}

async function previewFile(path, category, treeId, el) {
    document.querySelectorAll(`#${treeId} li`).forEach(li => li.classList.remove('selected'));
    if (el) el.classList.add('selected');
    await preview(path, category);
}

function renderTablePreview(container, data, path = '', category = 'data') {
    const { headers, rows } = data;
    if (!headers || !rows) {
        container.innerHTML = '<div style="color: var(--text-dim);">Empty or unsupported table.</div>';
        return;
    }

    const editableFile = category === 'data';
    const editMode =
        editableFile &&
        state.tableEditMode &&
        state.openTablePath === path &&
        state.openTableCategory === category;

    let html = `
        <div style="display:flex;flex-direction:column;gap:10px;height:100%;">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
                <div style="font-size:12px;color:var(--text-dim);font-family:var(--font-mono);">
                    ${escapeHtml(path)}
                </div>
                <div style="display:flex;gap:8px;">
                    ${editableFile && !editMode ? `<button class="btn-tab" onclick="enableTableEditMode('${escapeAttr(path)}','${escapeAttr(category)}')">Edit mode</button>` : ''}
                    ${editableFile && editMode ? `<button class="btn-tab active" onclick="saveEditedTable('${escapeAttr(path)}','${escapeAttr(category)}')">Save table</button>` : ''}
                    ${editableFile && editMode ? `<button class="btn-tab" onclick="cancelTableEditMode('${escapeAttr(path)}','${escapeAttr(category)}')">Cancel</button>` : ''}
                    <button class="btn-tab" onclick="preview('${escapeAttr(path)}','${escapeAttr(category)}')">Reload</button>
                    ${editableFile && editMode ? `<button class="btn-tab" onclick="addEmptyTableRow()">+ Row</button>` : ''}
                </div>
            </div>

            <div style="overflow:auto;max-height:700px;border:1px solid var(--border2);border-radius:6px;">
                <table class="preview-table" id="editable-table">
                    <thead>
                        <tr>
                            ${headers.map((h, i) => `
                                <th>
                                    ${editMode
                                        ? `<input data-header-index="${i}" value="${escapeAttr(String(h))}" style="width:100%;background:transparent;border:none;color:inherit;font:inherit;">`
                                        : escapeHtml(String(h))
                                    }
                                </th>
                            `).join('')}
                            ${editMode ? '<th style="width:40px;">&nbsp;</th>' : ''}
                        </tr>
                    </thead>
                    <tbody>
                        ${rows.map((row, rIdx) => `
                            <tr>
                                ${headers.map((_, cIdx) => `
                                    <td>
                                        ${editMode
                                            ? `<input data-row="${rIdx}" data-col="${cIdx}" value="${escapeAttr(String(row[cIdx] ?? ''))}" style="width:100%;background:transparent;border:none;color:inherit;font:inherit;">`
                                            : escapeHtml(String(row[cIdx] ?? ''))
                                        }
                                    </td>
                                `).join('')}
                                ${editMode ? `<td><button class="btn-tab" style="padding:2px 6px;" onclick="removeTableRow(${rIdx})">−</button></td>` : ''}
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
    `;

    if (data.truncated_rows || data.truncated_cols) {
        html += '<div class="truncation-warning">';
        if (data.truncated_rows) {
            html += data.total_rows
                ? `⚠️ Showing first ${rows.length} of ${data.total_rows} rows. `
                : `⚠️ Showing first ${rows.length} rows. `;
        }
        if (data.truncated_cols) html += `⚠️ Showing first ${headers.length} of ${data.total_cols} columns. `;
        html += '</div>';
    }

    html += '</div>';
    container.innerHTML = html;
}

function enableTableEditMode(path, category) {
    state.tableEditMode = true;
    state.openTablePath = path;
    state.openTableCategory = category;
    preview(path, category);
}

function cancelTableEditMode(path, category) {
    state.tableEditMode = false;
    state.openTablePath = path;
    state.openTableCategory = category;
    preview(path, category);
}

function collectEditedTable() {
    const table = $('editable-table');
    if (!table) return null;

    const headerInputs = [...table.querySelectorAll('thead input[data-header-index]')];
    const headers = headerInputs.map(input => input.value);

    const bodyRows = [...table.querySelectorAll('tbody tr')];
    const rows = bodyRows.map(tr => {
        const inputs = [...tr.querySelectorAll('input[data-row][data-col]')];
        return inputs.map(input => input.value);
    });

    return { headers, rows };
}

async function saveEditedTable(path, category) {
    const data = collectEditedTable();
    if (!data) {
        showToast('No editable table is open.', 'error');
        return;
    }

    try {
        const res = await fetch('/save_table_file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                path,
                category,
                headers: data.headers,
                rows: data.rows
            })
        });

        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || 'Failed to save table');

        showToast('Table saved successfully.', 'ok');
        state.tableEditMode = false;
        preview(path, category);
    } catch (err) {
        console.error(err);
        showToast(`Save failed: ${err.message || err}`, 'error', 5000);
    }
}

function addEmptyTableRow() {
    const table = $('editable-table');
    if (!table) return;

    const tbody = table.querySelector('tbody');
    const effectiveColCount = [...table.querySelectorAll('thead input[data-header-index]')].length || table.querySelectorAll('thead th').length;
    const rowIndex = tbody.querySelectorAll('tr').length;

    const tr = document.createElement('tr');
    let html = '';
    for (let c = 0; c < effectiveColCount; c++) {
        html += `
            <td>
                <input data-row="${rowIndex}" data-col="${c}" value="" style="width:100%;background:transparent;border:none;color:inherit;font:inherit;">
            </td>
        `;
    }
    html += `<td><button class="btn-tab" style="padding:2px 6px;" onclick="removeTableRow(${rowIndex})">−</button></td>`;
    tr.innerHTML = html;
    tbody.appendChild(tr);

    refreshTableRowIndices();
}

function removeTableRow(rowIndex) {
    const table = $('editable-table');
    if (!table) return;

    const rows = [...table.querySelectorAll('tbody tr')];
    if (rows[rowIndex]) rows[rowIndex].remove();

    refreshTableRowIndices();
}

function refreshTableRowIndices() {
    const table = $('editable-table');
    if (!table) return;

    const rows = [...table.querySelectorAll('tbody tr')];
    rows.forEach((tr, rIdx) => {
        [...tr.querySelectorAll('input[data-col]')].forEach(input => {
            input.setAttribute('data-row', rIdx);
        });

        const btn = tr.querySelector('button');
        if (btn) btn.setAttribute('onclick', `removeTableRow(${rIdx})`);
    });
}

async function preview(path, category) {
    const target = $(`${category}-preview`);
    target.innerHTML = `<span style="color:var(--text-dim);font-size:12px"><span class="spinner"></span> Loading…</span>`;

    if (path.match(/\.(png|jpg|jpeg|gif|svg)$/i)) {
        target.innerHTML = `<img src="/get_file?category=${category}&path=${encodeURIComponent(path)}" style="max-width:100%;border-radius:4px">`;
        return;
    }

    const res = await fetch(`/get_file?category=${category}&path=${encodeURIComponent(path)}`);
    const contentType = res.headers.get('content-type');

    if (contentType && contentType.includes('application/json')) {
        const data = await res.json();
        if (data.type === 'table') { renderTablePreview(target, data, path, category); return; }
        if (data.type === 'html')  { renderHtmlPreview(target, data.content); return; }
        if (data.type === 'text')  { target.innerHTML = `<pre>${escapeHtml(data.content)}</pre>`; return; }
    }

    const text = await res.text();
    if (text.trim().toLowerCase().startsWith('<!doctype html>') || text.trim().toLowerCase().startsWith('<html')) {
        renderHtmlPreview(target, text); return;
    }
    target.innerHTML = `<pre>${escapeHtml(text)}</pre>`;
}

function renderHtmlPreview(container, htmlContent) {
    const safeHtml = escapeAttr(htmlContent);
    container.innerHTML = `<iframe srcdoc="${safeHtml}" style="width:100%;height:100%;border:none;min-height:600px;" sandbox="allow-same-origin allow-scripts"></iframe>`;
}

function escapeHtml(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function setBtnLoading(id, loading) {
    const btn = $(id);
    if (!btn) return;
    if (loading) {
        btn.dataset.origHtml = btn.innerHTML;
        btn.disabled = true;
        btn.style.opacity = '0.7';
        btn.innerHTML = `<span class="spinner btn-spinner"></span>${btn.dataset.origHtml.replace(/^[▶✕✓↻]\s*/,'')}`;
    } else {
        btn.disabled = false;
        btn.style.opacity = '';
        if (btn.dataset.origHtml !== undefined) btn.innerHTML = btn.dataset.origHtml;
    }
}
function escapeAttr(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function toggleCompareSelection(path, name) {
    const idx = state.compareSelection.findIndex(x => x.path === path);
    if (idx >= 0) {
        state.compareSelection.splice(idx, 1);
    } else {
        state.compareSelection.push({ path, name });
    }
    renderCompareSelection();
    updateBaselineOptions();

    const activePanel = document.querySelector('.nav-item.active')?.dataset?.panel;
    if (activePanel === 'results-browser') browse('', 'results-tree', 'results');
}

function clearSimulationCompare() {
    state.compareSelection = [];
    state.comparePayload = null;
    state.compareFocusSpecies = null;
    state.compareSpeciesAll = [];
    state.compareSpeciesFrames = [];

    setStatus('compare-status', null);
    renderCompareSelection();
    updateBaselineOptions();
    renderCompareSummary(null);
    renderCompareKpis(null);
    renderTsnePlot(null);
    renderSpeciesRanking(null);
    populateFocusSpecies([], null);

    if (state.compareChart) {
        state.compareChart.destroy();
        state.compareChart = null;
    }
    if (state.compareDeltaChart) {
        state.compareDeltaChart.destroy();
        state.compareDeltaChart = null;
    }
    if (state.compareTsneSnapChart) {
        state.compareTsneSnapChart.destroy();
        state.compareTsneSnapChart = null;
    }
    if (state.compareTsneFullChart) {
        state.compareTsneFullChart.destroy();
        state.compareTsneFullChart = null;
    }

    const search = $('compare-species-search');
    if (search) search.value = '';

    browse('', 'results-tree', 'results');
}

function renderCompareSelection() {
    const el = $('compare-selected');
    if (el) {
        if (!state.compareSelection.length) {
            el.innerHTML = `<span class="browser-empty">No simulations selected yet.</span>`;
        } else {
            el.innerHTML = state.compareSelection.map(item => `
                <span class="compare-chip">
                    ${escapeHtml(item.name)}
                    <button onclick="toggleCompareSelection('${item.path}','${escapeAttr(item.name)}')" title="Remove">✕</button>
                </span>
            `).join('');
        }
    }
    renderComparePicker();
}

async function loadCompareCandidates() {
    const el = $('compare-picker');
    if (!el) return;
    el.innerHTML = `<span class="spinner"></span>`;
    try {
        const res = await fetch('/ls?category=results&path=simulatePSS/tables');
        const data = await res.json();
        state.compareCandidates = (data.items || []).filter(i => i.type === 'file' && /\.(csv|tsv|xlsx?)$/i.test(i.name));
        renderComparePicker();
    } catch(e) {
        el.innerHTML = `<span class="browser-empty" style="color:var(--danger)">Error: ${escapeHtml(String(e))}</span>`;
    }
}

function renderComparePicker() {
    const el = $('compare-picker');
    if (!el) return;
    if (!state.compareCandidates.length) {
        el.innerHTML = '<span class="browser-empty">No simulation tables found. Run the pipeline first.</span>';
        return;
    }
    el.innerHTML = state.compareCandidates.map(i => {
        const sel = state.compareSelection.some(x => x.path === i.path);
        return `<div class="quality-file-item ${sel ? 'selected' : ''}"
                     onclick="toggleCompareSelection('${escapeAttr(i.path)}','${escapeAttr(i.name)}')">
                    <input type="checkbox" ${sel ? 'checked' : ''} style="accent-color:var(--accent2);pointer-events:none">
                    ${escapeHtml(i.name)}
                </div>`;
    }).join('');
}

function updateBaselineOptions() {
    const sel = $('compare-baseline');
    if (!sel) return;

    const current = sel.value;
    sel.innerHTML = state.compareSelection.map((item, i) => `
        <option value="${item.path}" ${i === 0 ? 'selected' : ''}>${escapeHtml(item.name)}</option>
    `).join('');

    if (current && state.compareSelection.some(x => x.path === current)) sel.value = current;
}

function populateFocusSpecies(speciesList, selected) {
    state.compareSpeciesAll = Array.isArray(speciesList) ? speciesList.slice() : [];
    state.compareFocusSpecies = selected && state.compareSpeciesAll.includes(selected)
        ? selected
        : (state.compareSpeciesAll[0] || null);

    const input = $('compare-focus-input');
    if (input) input.value = state.compareFocusSpecies || '';
    const lbl = $('compare-focus-label');
    if (lbl) lbl.textContent = state.compareFocusSpecies || '—';
}

function showFocusDropdown() {
    renderFocusDropdown($('compare-focus-input')?.value || '');
    const dd = $('compare-focus-dropdown');
    if (dd) dd.style.display = 'block';
}

function hideFocusDropdown() {
    const dd = $('compare-focus-dropdown');
    if (dd) dd.style.display = 'none';
}

function renderFocusDropdown(query) {
    const dd = $('compare-focus-dropdown');
    if (!dd) return;
    const q = query.trim().toLowerCase();
    const matches = (q
        ? state.compareSpeciesAll.filter(sp => sp.toLowerCase().includes(q))
        : state.compareSpeciesAll
    ).slice(0, 80);

    if (!matches.length) {
        dd.innerHTML = `<div class="focus-drop-empty">No matches</div>`;
    } else {
        dd.innerHTML = matches.map(sp =>
            `<div class="focus-drop-item${sp === state.compareFocusSpecies ? ' active' : ''}"
                  onmousedown="pickFocusSpecies('${escapeAttr(sp)}')">${escapeHtml(sp)}</div>`
        ).join('');
    }
}

function pickFocusSpecies(species) {
    const input = $('compare-focus-input');
    if (input) input.value = species;
    hideFocusDropdown();
    selectFocusSpecies(species);
}

function onFocusSpeciesInput(val) {
    renderFocusDropdown(val);
    const dd = $('compare-focus-dropdown');
    if (dd) dd.style.display = 'block';
}

function onFocusSpeciesKeydown(e) {
    if (e.key === 'Escape') { hideFocusDropdown(); return; }
    if (e.key === 'Enter') {
        const q = e.target.value.trim().toLowerCase();
        const match = state.compareSpeciesAll.find(sp => sp.toLowerCase().includes(q));
        if (match) pickFocusSpecies(match);
        hideFocusDropdown();
    }
}

// Close dropdown when clicking outside
document.addEventListener('mousedown', e => {
    if (!e.target.closest('.compare-focus-picker')) hideFocusDropdown();
});

function formatMetric(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    return typeof v === 'number' ? v.toFixed(4) : escapeHtml(String(v));
}

function renderCompareKpis(payload) {
    const el = $('compare-kpis');
    if (!el) return;

    if (!payload || !payload.focused_metrics || !payload.focused_metrics.length) {
        el.innerHTML = `<span class="browser-empty">Run a comparison to see summary metrics.</span>`;
        return;
    }

    const first = payload.focused_metrics[0];
    el.innerHTML = `
        <div class="compare-kpi"><div class="compare-kpi-label">Focus species</div><div class="compare-kpi-value">${escapeHtml(payload.focus_species || '—')}</div></div>
        <div class="compare-kpi"><div class="compare-kpi-label">Baseline</div><div class="compare-kpi-value">${escapeHtml(payload.baseline || '—')}</div></div>
        <div class="compare-kpi"><div class="compare-kpi-label">Strongest steady-state % diff</div><div class="compare-kpi-value">${formatMetric(first.max_steady_state_pct_diff)}</div></div>
        <div class="compare-kpi"><div class="compare-kpi-label">Strongest timepoint % diff</div><div class="compare-kpi-value">${formatMetric(first.max_timepoint_pct_diff)}</div></div>
        <div class="compare-kpi"><div class="compare-kpi-label">Largest cumulative diff</div><div class="compare-kpi-value">${formatMetric(first.max_cumulative_difference)}</div></div>
        <div class="compare-kpi"><div class="compare-kpi-label">Largest AUC diff</div><div class="compare-kpi-value">${formatMetric(first.max_auc_difference)}</div></div>
        <div class="compare-kpi"><div class="compare-kpi-label">Largest final change</div><div class="compare-kpi-value">${formatMetric(first.max_abs_final_change)}</div></div>
        <div class="compare-kpi"><div class="compare-kpi-label">Largest peak shift</div><div class="compare-kpi-value">${formatMetric(first.max_peak_shift)}</div></div>
    `;
}

function renderSpeciesRanking(payload) {
    const el = $('compare-ranking');
    if (!el) return;

    if (!payload || !payload.species_rankings || !payload.species_rankings.length) {
        el.innerHTML = `<span class="browser-empty">Select at least 2 simulation result tables to compare.</span>`;
        return;
    }

    const metric = $('compare-rank-metric')?.value || 'max_abs_steady_state_pct_diff';
    const rows = [...payload.species_rankings].sort((a, b) => (b[metric] || 0) - (a[metric] || 0)).slice(0, 12);

    let html = `<table><thead><tr><th>Species</th><th>Steady-state %</th><th>Timepoint %</th><th>Cumulative</th><th>AUC diff</th><th>Final Δ</th><th>Peak shift</th></tr></thead><tbody>`;
    rows.forEach(row => {
        const active = row.species === state.compareFocusSpecies ? 'active' : '';
        html += `<tr class="${active}" onclick="selectFocusSpecies('${escapeAttr(row.species)}')"><td>${escapeHtml(row.species)}</td><td>${formatMetric(row.max_abs_steady_state_pct_diff)}</td><td>${formatMetric(row.max_abs_timepoint_pct_diff)}</td><td>${formatMetric(row.max_cumulative_difference)}</td><td>${formatMetric(row.max_auc_difference)}</td><td>${formatMetric(row.max_abs_final_change)}</td><td>${formatMetric(row.max_peak_shift)}</td></tr>`;
    });
    html += `</tbody></table>`;
    el.innerHTML = html;
}

function selectFocusSpecies(species) {
    state.compareFocusSpecies = species;
    const lbl = $('compare-focus-label');
    if (lbl) lbl.textContent = species || '—';
    const input = $('compare-focus-input');
    if (input && document.activeElement !== input) input.value = species || '';

    if (state.comparePayload) {
        renderFocusedCompareCharts(state.comparePayload);
        renderCompareKpis(state.comparePayload);
        renderCompareSummary(state.comparePayload);
        renderSpeciesRanking(state.comparePayload);
    }
}


function renderCompareSummary(payload) {
    const el = $('compare-summary');
    if (!el) return;

    if (!payload || !payload.focused_metrics || !payload.focused_metrics.length) {
        el.innerHTML = `<span class="browser-empty">Detailed metrics will appear here.</span>`;
        return;
    }

    let html = `<table><thead><tr><th>Simulation</th><th>Parameter</th><th>Perturb.</th><th>% diff @ steady state</th><th>% diff @ t</th><th>Cumulative diff</th><th>AUC diff</th><th>Final Δ</th><th>Peak time</th><th>Peak value</th><th>Peak shift</th></tr></thead><tbody>`;
    payload.focused_metrics.forEach(row => {
        html += `<tr><td>${escapeHtml(row.simulation)}</td><td>${escapeHtml(row.parameter || '—')}</td><td>${escapeHtml(row.perturbation || '—')}</td><td>${formatMetric(row.steady_state_pct_diff)}</td><td>${formatMetric(row.timepoint_pct_diff)}</td><td>${formatMetric(row.cumulative_difference)}</td><td>${formatMetric(row.auc_difference)}</td><td>${formatMetric(row.final_concentration_change)}</td><td>${formatMetric(row.peak_time)}</td><td>${formatMetric(row.peak_value)}</td><td>${formatMetric(row.peak_shift)}</td></tr>`;
    });
    html += `</tbody></table>`;
    el.innerHTML = html;
}



function renderTsnePlot(payload) {
    const snapNote = $('compare-tsne-snap-note');
    const fullNote = $('compare-tsne-full-note');
    const frames   = payload?.embedding_species_frames;
    const tsne     = payload?.embedding_species_tsne;

    // ── Merged t-SNE (full trajectory) ───────────────────────
    if (!tsne?.points?.length) {
        if (state.compareTsneFullChart) { state.compareTsneFullChart.destroy(); state.compareTsneFullChart = null; }
        if (fullNote) fullNote.textContent = 'Run comparison to generate species embedding.';
    } else {
        if (fullNote) fullNote.textContent = '';
        state.compareTsneFullChart = renderSpeciesEmbeddingChart(
            $('compare-tsne-full-chart'), tsne, state.compareTsneFullChart
        );
    }

    // ── Per-timepoint dot plot ────────────────────────────────
    if (!frames?.length) {
        if (state.compareTsneSnapChart) { state.compareTsneSnapChart.destroy(); state.compareTsneSnapChart = null; }
        if (snapNote) snapNote.textContent = 'Run comparison to generate species embedding.';
        return;
    }

    state.compareSpeciesFrames = frames;

    const slider = $('embed-timepoint');
    if (slider) {
        slider.min   = 0;
        slider.max   = frames.length - 1;
        slider.step  = 1;
        slider.value = frames.length - 1;
    }

    if (snapNote) {
        const f = frames[0];
        snapNote.textContent =
            `${f.n_species} species × ${f.n_sims} simulations · consistent axes across all time points · drag slider to animate`;
    }

    renderSpeciesFrameAt(frames.length - 1);
}

function renderSpeciesFrameAt(idx) {
    const frames = state.compareSpeciesFrames;
    if (!frames?.length) return;
    const frame = frames[Math.max(0, Math.min(Math.round(idx), frames.length - 1))];
    const lbl = $('embed-timepoint-val');
    if (lbl) lbl.textContent = formatMetric(frame.time_point);
    state.compareTsneSnapChart = renderSpeciesEmbeddingChart(
        $('compare-tsne-snap-chart'), frame, state.compareTsneSnapChart
    );
}

function renderSpeciesEmbeddingChart(canvas, embedding, existing) {
    if (!canvas || !embedding?.points?.length) {
        if (existing) existing.destroy();
        return null;
    }

    // One dataset per simulation — each species in that simulation is one point
    const simOrder = [];
    const simGroups = {};
    for (const p of embedding.points) {
        if (!simGroups[p.sim]) { simGroups[p.sim] = []; simOrder.push(p.sim); }
        simGroups[p.sim].push({ x: p.x, y: p.y, species: p.species });
    }

    const datasets = simOrder.map((name, i) => ({
        label: name,
        data: simGroups[name],
        parsing: false,
        backgroundColor: simRgba(i, 0.5),
        borderColor:     simRgba(i, 0.75),
        borderWidth: 0.5,
        pointRadius: 3,
        pointHoverRadius: 7
    }));

    const tp  = embedding.time_point != null ? ` — t=${formatMetric(embedding.time_point)}` : '';
    const ttl = `Species space${tp}  ·  ${embedding.method}  ·  ${embedding.n_species} species × ${embedding.n_sims} sims`;

    const options = {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        plugins: {
            legend: { display: true, position: 'bottom' },
            tooltip: {
                callbacks: {
                    label: ctx => `${ctx.raw.species}  [${ctx.dataset.label}]`
                }
            },
            title: { display: true, text: ttl, font: { size: 11 } }
        },
        scales: {
            x: { type: 'linear', title: { display: true, text: 'Dim 1' } },
            y: { type: 'linear', title: { display: true, text: 'Dim 2' } }
        },
        elements: { point: { radius: 3, hoverRadius: 7 } }
    };

    if (existing) {
        existing.data.datasets = datasets;
        existing.options = options;
        existing.update('none');
        return existing;
    }
    return new Chart(canvas.getContext('2d'), { type: 'scatter', data: { datasets }, options });
}

function buildChartOptions(yTitle) {
    return {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        normalized: true,
        parsing: false,
        interaction: { mode: 'nearest', intersect: false },
        plugins: {
            legend: { display: true, position: 'bottom' },
            decimation: { enabled: true, algorithm: 'min-max' }
        },
        scales: {
            x: { type: 'linear', title: { display: true, text: 'Time' } },
            y: { title: { display: true, text: yTitle } }
        },
        elements: {
            line: { tension: 0.15, borderWidth: 2 },
            point: { radius: 0, hoverRadius: 3 }
        }
    };
}

function updateOrCreateChart(existingChart, canvas, datasets, yTitle) {
    if (!canvas) return existingChart;
    if (existingChart) {
        existingChart.data.datasets = datasets;
        existingChart.options = buildChartOptions(yTitle);
        existingChart.update('none');
        return existingChart;
    }
    return new Chart(canvas.getContext('2d'), { type: 'line', data: { datasets }, options: buildChartOptions(yTitle) });
}

function renderFocusedCompareCharts(payload) {
    if (!payload || !payload.focused_plot) return;
    const trajCanvas = $('compare-chart');
    const deltaCanvas = $('compare-delta-chart');
    if (!trajCanvas || !deltaCanvas) return;

    const trajDatasets = (payload.focused_plot.simulations || []).map(sim => ({
        label: sim.name,
        data: sim.points.map(p => ({ x: p.t, y: p.y })),
        parsing: false,
        pointRadius: 0,
        borderWidth: 2,
        spanGaps: true
    }));

    const deltaDatasets = (payload.focused_plot.delta_from_baseline || []).map(sim => ({
        label: `${sim.name} Δ`,
        data: sim.points.map(p => ({ x: p.t, y: p.y })),
        parsing: false,
        pointRadius: 0,
        borderWidth: 2,
        spanGaps: true
    }));

    state.compareChart = updateOrCreateChart(state.compareChart, trajCanvas, trajDatasets, payload.focus_species || 'Value');
    state.compareDeltaChart = updateOrCreateChart(state.compareDeltaChart, deltaCanvas, deltaDatasets, 'Δ vs baseline');
}

async function runSimulationCompare() {
    if (state.compareSelection.length < 2) {
        showToast('Select at least 2 simulation result tables first.', 'error');
        return;
    }

    setBtnLoading('btn-run-compare', true);
    setStatus('compare-status', 'running', 'Analysing simulations…');

    const timePoint  = parseFloat($('embed-timepoint')?.value ?? '1000');
    const baseline   = $('compare-baseline')?.value || state.compareSelection[0].path;
    const focusSpecies = state.compareFocusSpecies || null;
    const files      = state.compareSelection.map(x => x.path);

    try {
        const res = await fetch('/compare_simulations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ files, baseline, time_point: timePoint, focus_species: focusSpecies })
        });

        const contentType = res.headers.get('content-type') || '';
        let data;
        if (contentType.includes('application/json')) {
            data = await res.json();
        } else {
            const text = await res.text();
            throw new Error(`Server returned ${res.status}. Response: ${text.slice(0, 120)}`);
        }
        if (!res.ok) throw new Error(data.error || `Comparison failed (${res.status})`);

        state.comparePayload = data;
        populateFocusSpecies(data.available_species || [], state.compareFocusSpecies || data.focus_species);
        renderFocusedCompareCharts(data);
        renderCompareKpis(data);
        renderSpeciesRanking(data);
        renderCompareSummary(data);
        setEmbeddingLoading(true);
        setStatus('compare-status', 'running', 'Charts ready — computing embeddings…');
    } catch (err) {
        console.error(err);
        setStatus('compare-status', 'error', err.message || 'Comparison failed');
        showToast(`Comparison failed: ${err.message || err}`, 'error', 5000);
        setBtnLoading('btn-run-compare', false);
        return;
    }

    setBtnLoading('btn-run-compare', false);

    // Fire the slow embedding fetch in the background — UI is already interactive
    fetchCompareEmbeddings(files);
}

function setStatus(id, phase, msg) {
    const el = $(id);
    if (!el) return;
    if (!phase) { el.style.display = 'none'; return; }
    el.style.display = 'flex';
    el.className = `compare-status ${phase}`;
    const icon = phase === 'running'
        ? '<span class="spinner" style="width:12px;height:12px;border-width:2px;margin:0"></span>'
        : phase === 'ok' ? '✓' : '✕';
    el.innerHTML = `${icon} ${escapeHtml(msg)}`;
}

function setEmbeddingLoading(loading) {
    const fullNote = $('compare-tsne-full-note');
    const snapNote = $('compare-tsne-snap-note');
    if (loading) {
        const html = '<span class="embed-computing"><span class="spinner embed-spinner"></span>Computing embeddings…</span>';
        if (fullNote) fullNote.innerHTML = html;
        if (snapNote) snapNote.innerHTML = html;
    } else {
        if (fullNote) fullNote.textContent = '';
        if (snapNote) snapNote.textContent = '';
    }
}

async function fetchCompareEmbeddings(files) {
    try {
        const res = await fetch('/compare_embeddings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ files })
        });
        const data = await res.json();
        if (data.error) {
            setStatus('compare-status', 'error', 'Embedding failed: ' + data.error);
            showToast('Embedding failed: ' + data.error, 'error');
            return;
        }

        if (state.comparePayload) {
            state.comparePayload.embedding_trajectory     = data.embedding_trajectory;
            state.comparePayload.embedding_species_frames = data.embedding_species_frames;
            state.comparePayload.embedding_species_tsne   = data.embedding_species_tsne;
        }
        setEmbeddingLoading(false);
        renderTsnePlot(data);
        setStatus('compare-status', 'ok', 'Comparison complete');
    } catch (err) {
        setStatus('compare-status', 'error', 'Embedding compute failed: ' + err.message);
        showToast('Embedding compute failed: ' + err.message, 'error', 4000);
        setEmbeddingLoading(false);
    }
}

// ── TOAST ─────────────────────────────────────────────────────
function showToast(msg, type = 'info', duration = 3000) {
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();

    const t = document.createElement('div');
    t.className = `toast ${type}`;
    const icon = type === 'ok' ? '✓' : type === 'error' ? '✕' : 'ℹ';
    const color = type === 'ok' ? 'var(--accent)' : type === 'error' ? 'var(--danger)' : 'var(--text-dim)';
    t.innerHTML = `<span style="color:${color}">${icon}</span> ${escapeHtml(msg)}`;
    document.body.appendChild(t);
    setTimeout(() => {
        t.style.transition = 'opacity .2s, transform .2s';
        t.style.opacity = '0';
        t.style.transform = 'translateY(8px)';
        setTimeout(() => t.remove(), 220);
    }, duration);
}

// ── LOG HELPERS ───────────────────────────────────────────────
function addLogLine(msg, type = 'info') {
    const body = $('log-body');
    const now  = new Date().toLocaleTimeString('en-GB', { hour12: false });
    const line = document.createElement('div');
    line.className = 'log-line';
    line.innerHTML = `<span class="log-time">${now}</span><span class="log-msg ${type}">${escapeHtml(msg)}</span>`;
    body.appendChild(line);
    body.scrollTop = body.scrollHeight;
    updateLogCount();
}

function clearLogs() {
    $('log-body').innerHTML = '';
    updateLogCount();
}

function updateLogCount() {
    const el = $('log-count');
    if (!el) return;
    const count = state.logSource === 'clean' ? state.cleanLogs.length : state.rawLogs.length;
    el.textContent = `${count} lines`;
}

function renderLogsFromSource() {
    const logs = state.logSource === 'clean' ? state.cleanLogs : state.rawLogs;
    clearLogs();
    logs.forEach(({ msg, type }) => addLogLine(msg, type));
}

function setLogSource(src) {
    state.logSource = src;
    $('btn-log-clean').classList.toggle('active', src === 'clean');
    $('btn-log-raw').classList.toggle('active',   src === 'raw');
    renderLogsFromSource();
}

// ── UI RESET ──────────────────────────────────────────────────
function resetUI() {
    [1,2,3,4].forEach(i => {
        const el = $(`stage-status-${i}`);
        if (el) { el.textContent = 'PENDING'; el.className = 'stage-status'; }
        const card = document.querySelector(`.stage-card[data-stage="${i}"]`);
        if (card) card.classList.remove('stage-done', 'stage-running');
        _setLogStageBadge(i, 'pending');
    });

    Object.keys(state.stage2Details).forEach(k => state.stage2Details[k] = false);
    renderStage2Details();

    $('progress-bar').style.width = '0%';
    $('progress-pct').textContent = '0 / 4 stages complete';
    updateRibbon({ status: 'running', stages: 0 });

    const errorsContainer = $('errors-container');
    if (errorsContainer) errorsContainer.style.display = 'none';

    state.cleanLogs = [];
    state.rawLogs   = [];
    clearLogs();
}

// ── RIBBON UPDATES ────────────────────────────────────────────
function updateRibbon({ status, stages }) {
    const statusEl = $('ribbon-status');
    const progressEl = $('ribbon-progress');
    if (statusEl) statusEl.textContent = status || 'Idle';
    if (progressEl) progressEl.textContent = stages !== undefined ? `${stages} / 4 stages` : '—';

    const condEl = $('ribbon-conditions');
    if (condEl) condEl.textContent = state.simConditions.filter(s => s.enabled).length;

    const execEl = $('ribbon-executor');
    const profileEl = $('cfg-profile');
    if (execEl && profileEl) execEl.textContent = profileEl.value || 'local';
}

// ── STAGE 2 SUB-PROCESS DETAILS ───────────────────────────────
function renderStage2Details() {
    const map = {
        decayRates:           'decay_rates.py',
        initialConcentrations:'concentrations.py',
        rateLaws:             'rate_laws.py',
        kineticParameters:    'kinetic_parameters.py'
    };
    const container = $('stage2-subprocs');
    if (!container) return;
    container.innerHTML = Object.entries(map).map(([key, label]) => `
        <div class="sub-process-item ${state.stage2Details[key] ? 'done' : ''}" id="sub-${key}">
            <span class="sub-process-dot"></span>${label}
        </div>
    `).join('');
}

function updateStage2Sub(name) {
    if (name in state.stage2Details) {
        state.stage2Details[name] = true;
        const el = $(`sub-${name}`);
        if (el) el.classList.add('done');
    }
}

// ── STAGE PROGRESS ────────────────────────────────────────────
function _setLogStageBadge(n, state_) {
    const item  = $(`log-stage-${n}`);
    const badge = $(`log-stage-badge-${n}`);
    if (!item || !badge) return;
    item.classList.remove('lsi-running', 'lsi-done', 'lsi-error');
    if (state_ === 'running') { item.classList.add('lsi-running'); badge.textContent = 'RUNNING'; }
    else if (state_ === 'done')  { item.classList.add('lsi-done');    badge.textContent = 'DONE'; }
    else if (state_ === 'error') { item.classList.add('lsi-error');   badge.textContent = 'ERROR'; }
    else                          { badge.textContent = 'PENDING'; }
}

function markStageRunning(n) {
    const el   = $(`stage-status-${n}`);
    const card = document.querySelector(`.stage-card[data-stage="${n}"]`);
    if (el && !el.classList.contains('done')) {
        el.textContent = 'RUNNING'; el.className = 'stage-status running';
    }
    if (card && !card.classList.contains('stage-done')) card.classList.add('stage-running');
    const badge = $(`log-stage-badge-${n}`);
    if (badge && badge.textContent !== 'DONE') _setLogStageBadge(n, 'running');
}

function markStageDone(n) {
    const el   = $(`stage-status-${n}`);
    const card = document.querySelector(`.stage-card[data-stage="${n}"]`);
    if (el && !el.classList.contains('done')) {
        el.textContent = 'DONE'; el.className = 'stage-status done';
    }
    if (card) { card.classList.remove('stage-running'); card.classList.add('stage-done'); }
    _setLogStageBadge(n, 'done');

    const completed = [1,2,3,4].filter(i => {
        const s = $(`stage-status-${i}`);
        return s && s.classList.contains('done');
    }).length;

    $('progress-bar').style.width = `${(completed / 4) * 100}%`;
    $('progress-pct').textContent = `${completed} / 4 stages complete`;
    updateRibbon({ status: state.running ? 'Running' : 'Done', stages: completed });
}

function updateStagesFromStatus(data) {
    const completedStages = new Set(data.completed_stages || []);
    const runningStages   = new Set(data.running_stages   || []);

    [1, 2, 3, 4].forEach(n => {
        if (completedStages.has(n))     markStageDone(n);
        else if (runningStages.has(n))  markStageRunning(n);
    });

    if (data.stage_2_details) {
        Object.entries(data.stage_2_details).forEach(([key, done]) => { if (done) updateStage2Sub(key); });
    }
}

// ── PIPELINE EXECUTION ────────────────────────────────────────
// Toggle the Run ↔ Stop buttons in the top bar.
function setRunningUI(running) {
    const run = $('btn-run'), stop = $('btn-stop');
    if (run)  { run.style.display  = running ? 'none' : '';   run.disabled = false; }
    if (stop) { stop.style.display = running ? '' : 'none';   stop.disabled = false; }
}

async function stopPipeline() {
    if (!state.running || !state.currentJobId) return;
    const stop = $('btn-stop');
    if (stop) stop.disabled = true;
    addLogLine('Stop requested…', 'warn');
    try {
        const res = await fetch(`/stop/${state.currentJobId}`, { method: 'POST' });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.error || `Server error ${res.status}`);
        }
        // The status poller will observe 'stopped' and finalize the UI. If polling
        // already stopped, finalize directly so the user isn't left hanging.
        if (!state.pollingInterval) pipelineFinished('stopped');
    } catch (err) {
        addLogLine(`Failed to stop pipeline: ${err}`, 'error');
        showToast(`Failed to stop: ${err}`, 'error');
        if (stop) stop.disabled = false;
    }
}

async function startPipeline() {
    if (state.running) return;

    if (state.eventSource)    { state.eventSource.close();            state.eventSource    = null; }
    if (state.pollingInterval){ clearInterval(state.pollingInterval); state.pollingInterval = null; }

    resetUI();

    state.running = true;
    const statusEl = $('globalStatus');
    statusEl.querySelector('.status-text').textContent = 'RUNNING';
    statusEl.className = 'run-status running';
    setRunningUI(true);

    updateBadgeLogs('run');
    showPanel('logs');
    addLogLine('Pipeline initialising…', 'dim');

    const payload = {
        data_path:      $('path-data').value,
        script_path:    $('path-scripts').value,
        events_path:    $('path-events').value,
        subset:         $('s3-subset').value,
        decay_form:     $('s3-decay-form').value,
        profile:        $('cfg-profile').value,
        resume:         $('cfg-resume').checked,
        rel_tol:        $('cfg-rel-tol').value.trim(),
        abs_tol:        $('cfg-abs-tol').value.trim(),
        max_steps:      $('cfg-max-steps').value.trim(),
        sim_conditions: state.simConditions.filter(s => s.enabled)
    };

    try {
        const res = await fetch('/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) {
            const msg = data.error || `Server error ${res.status}`;
            pipelineFinished('error');
            addLogLine(`Pipeline validation failed: ${msg}`, 'error');
            showToast(msg, 'error');
            return;
        }
        state.currentJobId = data.job_id;
        pollLogs(data.job_id);
    } catch (err) {
        pipelineFinished('error');
        addLogLine(`Failed to start pipeline: ${err}`, 'error');
        showToast(`Failed to start: ${err}`, 'error');
    }
}

function pollLogs(job_id) {
    state.eventSource = new EventSource(`/logs/${job_id}`);
    state.pollingInterval = setInterval(() => fetchStatus(job_id), 2000);

    state.eventSource.onmessage = (e) => {
        try {
            const log = JSON.parse(e.data);
            if (log.source === 'clean') state.cleanLogs.push({ msg: log.msg, type: log.type });
            else if (log.source === 'raw') state.rawLogs.push({ msg: log.msg, type: log.type });
            if (log.source === state.logSource) addLogLine(log.msg, log.type);
        } catch (err) {
            console.error('SSE message parse error:', err, e.data);
        }
    };

    state.eventSource.onerror = () => {
        if (state.eventSource) { state.eventSource.close(); state.eventSource = null; }
        // Do one immediate status check; the polling interval continues independently
        // so we don't lose updates if SSE drops mid-run.
        fetchStatus(job_id);
    };
}

async function fetchStatus(job_id) {
    try {
        const res  = await fetch(`/status/${job_id}`);
        const data = await res.json();

        if (data.errors && data.errors.length) {
            const errorsContainer = $('errors-container');
            $('errors-list').innerHTML = data.errors.map(e => `<div>${escapeHtml(e)}</div>`).join('');
            errorsContainer.style.display = 'block';
        } else {
            if ($('errors-container')) $('errors-container').style.display = 'none';
        }

        updateStagesFromStatus(data);

        if (data.status === 'done' || data.status === 'error' || data.status === 'stopped') {
            pipelineFinished(data.status);
            if (state.pollingInterval) { clearInterval(state.pollingInterval); state.pollingInterval = null; }
            if (state.eventSource)     { state.eventSource.close();            state.eventSource    = null; }
        }
    } catch (err) {
        console.error('Failed to fetch status', err);
    }
}

function pipelineFinished(status) {
    state.running = false;
    state.currentJobId = null;
    setRunningUI(false);

    [1,2,3,4].forEach(i => {
        const el = $(`stage-status-${i}`);
        if (status === 'done') {
            // Pipeline succeeded — any stage not yet marked done should be done now.
            if (el && !el.classList.contains('done')) markStageDone(i);
        } else {
            // Pipeline errored or was stopped — any stage still running is finalized.
            const card = document.querySelector(`.stage-card[data-stage="${i}"]`);
            if (card) card.classList.remove('stage-running');
            if (el && el.classList.contains('running')) {
                if (status === 'stopped') {
                    el.textContent = 'STOPPED'; el.className = 'stage-status';
                } else {
                    el.textContent = 'ERROR'; el.className = 'stage-status error';
                    _setLogStageBadge(i, 'error');
                }
            }
        }
    });

    const statusEl = $('globalStatus');
    if (status === 'done') {
        statusEl.querySelector('.status-text').textContent = 'DONE';
        statusEl.className = 'run-status done';
        addLogLine('Pipeline finished successfully.', 'ok');
        updateBadgeLogs('ok');
        showToast('Pipeline completed successfully', 'ok');
        updateRibbon({ status: 'Done', stages: 4 });
    } else if (status === 'stopped') {
        statusEl.querySelector('.status-text').textContent = 'STOPPED';
        statusEl.className = 'run-status';
        addLogLine('Pipeline stopped by user.', 'warn');
        updateBadgeLogs('idle');
        showToast('Pipeline stopped — you can run it again', 'warn');
        updateRibbon({ status: 'Stopped' });
    } else {
        statusEl.querySelector('.status-text').textContent = 'ERROR';
        statusEl.className = 'run-status error';
        addLogLine('Pipeline exited with errors.', 'error');
        updateBadgeLogs('err');
        showToast('Pipeline exited with errors — check the logs', 'error', 5000);
        updateRibbon({ status: 'Error' });
    }
}

// ── BADGE HELPERS ─────────────────────────────────────────────
function updateBadgeLogs(cls) {
    const b = $('badge-logs');
    if (!b) return;
    b.className = `nav-badge ${cls}`;
    b.textContent = cls === 'run' ? 'live' : cls === 'ok' ? 'done' : cls === 'err' ? 'err' : '—';
}

// ── EVENTS FILE AUTOCOMPLETE ──────────────────────────────────
async function loadEventsDatalist() {
    const dl = $('events-datalist');
    if (!dl) return;
    const paths = [];
    async function collect(subPath) {
        try {
            const res = await fetch(`/ls?category=data&path=${encodeURIComponent(subPath)}`);
            const data = await res.json();
            for (const item of (data.items || [])) {
                if (item.type === 'dir') {
                    await collect(item.path);
                } else if (item.name.endsWith('.json')) {
                    paths.push(item.path);
                }
            }
        } catch (_) {}
    }
    await collect('events');
    // /ls returns paths relative to the data root ("events/condition/x.json"), but
    // main.nf resolves cond.events with file(), i.e. relative to the launch directory.
    // Prefix the configured data path so a picked value is actually resolvable.
    const base = ($('path-data')?.value || 'data/').replace(/\/?$/, '/');
    dl.innerHTML = paths.map(p => `<option value="${escapeAttr(base + p)}">`).join('');
}

// ── SIM CONDITIONS ────────────────────────────────────────────
function renderSimConditions() {
    $('sim-conditions').innerHTML = state.simConditions.map((s, i) => `
        <div class="sim-row">
            <input type="checkbox" class="sim-enabled" ${s.enabled ? 'checked' : ''}
                   onchange="state.simConditions[${i}].enabled=this.checked;renderSimBadge()">
            <input type="text"   value="${escapeAttr(s.suffix)}"
                   placeholder="suffix"
                   oninput="state.simConditions[${i}].suffix=this.value;updateCmdPreview()">
            <input type="number" value="${s.start}"
                   oninput="state.simConditions[${i}].start=+this.value">
            <input type="number" value="${s.end}"
                   oninput="state.simConditions[${i}].end=+this.value">
            <input type="number" value="${s.points}"
                   oninput="state.simConditions[${i}].points=+this.value">
            <input type="text"   value="${escapeAttr(s.events)}"
                   placeholder="path/to/events.json or paste JSON"
                   list="events-datalist"
                   oninput="state.simConditions[${i}].events=this.value">
            <button class="btn-icon" title="Remove condition"
                    onclick="state.simConditions.splice(${i},1);renderSimConditions()">✕</button>
        </div>
    `).join('');

    renderSimBadge();
}

function renderSimBadge() {
    const count = state.simConditions.filter(s => s.enabled).length;
    if ($('badge-sim')) $('badge-sim').textContent = count;
    const labelEl = $('sim-count-label');
    if (labelEl) labelEl.textContent = `${count} condition${count !== 1 ? 's' : ''} active`;
    updateRibbon({});
}

function addSimRow() {
    const existing = new Set(state.simConditions.map(s => s.suffix));
    let suffix = 'condition';
    let n = 1;
    while (existing.has(suffix)) suffix = `condition_${n++}`;
    state.simConditions.push({ enabled: true, start: 0, end: 1000, points: 1000, suffix, events: '' });
    renderSimConditions();
}

// ── COMMAND PREVIEW ───────────────────────────────────────────
function updateCmdPreview() {
    const profile    = $('cfg-profile')?.value  || 'local';
    const dataPath   = $('path-data')?.value     || 'data/';
    const scriptPath = $('path-scripts')?.value  || 'scripts/modeling';
    const eventsPath = $('path-events')?.value   || 'data/events';
    const subset     = $('s3-subset')?.value     || '';
    const decayForm  = $('s3-decay-form')?.value  || '';
    const resume     = $('cfg-resume')?.checked;
    const relTol     = $('cfg-rel-tol')?.value.trim();
    const absTol     = $('cfg-abs-tol')?.value.trim();
    const maxSteps   = $('cfg-max-steps')?.value.trim();

    let cmd = `nextflow run main.nf`;
    cmd += ` -profile <span class="cmd-flag">${escapeHtml(profile)}</span>`;
    cmd += ` --data_path <span class="cmd-value">${escapeHtml(dataPath)}</span>`;
    cmd += ` --script_path <span class="cmd-value">${escapeHtml(scriptPath)}</span>`;
    cmd += ` --events_path <span class="cmd-value">${escapeHtml(eventsPath)}</span>`;
    cmd += ` --subset <span class="cmd-value">${escapeHtml(subset)}</span>`;
    if (decayForm) cmd += ` --decay_form <span class="cmd-value">${escapeHtml(decayForm)}</span>`;
    if (relTol)   cmd += ` --rel_tol <span class="cmd-value">${escapeHtml(relTol)}</span>`;
    if (absTol)   cmd += ` --abs_tol <span class="cmd-value">${escapeHtml(absTol)}</span>`;
    if (maxSteps) cmd += ` --max_steps <span class="cmd-value">${escapeHtml(maxSteps)}</span>`;
    if (resume) cmd += ` <span class="cmd-flag">-resume</span>`;

    const el = $('cmd-preview');
    if (el) el.innerHTML = cmd;

    updateRibbon({});
}

// ── CLEAN WORKSPACE ───────────────────────────────────────────
async function cleanWorkspace() {
    if (!confirm('⚠️ This will delete results/, logs/, tmp/, work/ (except conda), and all Nextflow cache files. Continue?')) return;

    const btn = $('btn-clean');
    const originalHTML = btn.innerHTML;
    btn.textContent = 'Cleaning…';
    btn.disabled = true;

    try {
        const res  = await fetch('/clean', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
            showToast('Workspace cleaned successfully', 'ok');
        } else {
            showToast(`Clean failed: ${data.message}`, 'error');
        }
    } catch (err) {
        showToast(`Clean failed: ${err}`, 'error');
    } finally {
        btn.innerHTML = originalHTML;
        btn.disabled = false;
    }
}

// ── KEYBOARD SHORTCUTS ────────────────────────────────────────
document.addEventListener('keydown', e => {
    const tag = document.activeElement?.tagName;
    const inInput = tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA';
    if (e.ctrlKey || e.metaKey || inInput) return;

    if (e.key === 'r' || e.key === 'R') {
        if (!state.running) startPipeline();
    }
    const panelMap = { '1':'overview', '2':'config', '3':'sim', '4':'logs', '5':'data-browser', '6':'results-browser', '7':'compare', '8':'network', '9':'about' };
    if (panelMap[e.key]) showPanel(panelMap[e.key]);
});

// ══════════════════════════════════════════════════════════════
//  TASK INSPECTOR
// ══════════════════════════════════════════════════════════════
const taskState = { tasks: [], activeHash: null, activeFile: null };

const FILE_LABELS = {
    '.command.out': 'stdout',
    '.command.err': 'stderr',
    '.command.sh':  'script',
    '.command.log': 'log',
    '.command.run': 'run'
};
const FILE_PRIORITY = ['.command.out', '.command.err', '.command.log', '.command.sh', '.command.run'];

async function loadWorkTasks() {
    const list = $('task-list');
    list.innerHTML = `<div class="task-empty"><span class="spinner"></span> Loading tasks…</div>`;

    try {
        const res  = await fetch('/work_tasks');
        const data = await res.json();
        taskState.tasks = data.tasks || [];

        const badge = $('badge-tasks');
        if (badge) {
            const errCount = taskState.tasks.filter(t => t.exit_code !== 0 && t.exit_code !== null).length;
            badge.textContent = taskState.tasks.length;
            badge.className = `nav-badge ${errCount > 0 ? 'err' : taskState.tasks.length > 0 ? 'ok' : ''}`;
        }
        renderTaskList();
    } catch (err) {
        list.innerHTML = `<div class="task-empty" style="color:var(--danger)">Failed to load: ${escapeHtml(String(err))}</div>`;
    }
}

function renderTaskList() {
    const list = $('task-list');
    if (!taskState.tasks.length) {
        list.innerHTML = `<div class="task-empty">No work tasks found. Run the pipeline first.</div>`;
        return;
    }
    list.innerHTML = taskState.tasks.map(task => {
        const isActive  = task.hash === taskState.activeHash;
        const exitOk    = task.exit_code === 0;
        const exitNull  = task.exit_code === null;
        const exitCls   = exitNull ? 'unk' : exitOk ? 'ok' : 'err';
        const exitLabel = exitNull ? 'running' : `exit ${task.exit_code}`;
        const name      = task.process_name ? task.process_name.replace(/_/g,' ') : 'unknown process';

        const fileChips = FILE_PRIORITY
            .filter(f => ['.command.sh','.command.out','.command.err','.command.log'].includes(f))
            .map(f => {
                const has = task.available_files.includes(f);
                return `<span class="task-file-chip ${has ? 'has-content' : ''}">${FILE_LABELS[f] || f}</span>`;
            }).join('');

        return `
            <div class="task-card exit-${exitCls} ${isActive ? 'active' : ''}"
                 onclick="selectTask('${escapeAttr(task.hash)}')">
                <div class="task-card-header">
                    <span class="task-card-name">${escapeHtml(name)}</span>
                    <span class="task-exit-badge ${exitCls}">${escapeHtml(exitLabel)}</span>
                </div>
                <div class="task-card-hash">[${escapeHtml(task.short_hash)}]</div>
                <div class="task-card-files">${fileChips}</div>
            </div>`;
    }).join('');
}

async function selectTask(hash) {
    taskState.activeHash = hash;
    const task = taskState.tasks.find(t => t.hash === hash);
    if (!task) return;
    renderTaskList();

    const tabs = FILE_PRIORITY.filter(f => task.available_files.includes(f));
    if (!tabs.length) {
        $('task-file-tabs').style.display = 'none';
        $('task-file-body').innerHTML = `<span class="browser-empty">No output files for this task.</span>`;
        return;
    }
    const autoTab = (task.exit_code !== 0 && task.exit_code !== null && tabs.includes('.command.err'))
        ? '.command.err' : tabs[0];

    renderTaskTabs(task, tabs, autoTab);
    await loadTaskFile(hash, autoTab);
}

function renderTaskTabs(task, tabs, activeFile) {
    taskState.activeFile = activeFile;
    const container = $('task-file-tabs');
    container.style.display = 'flex';
    container.innerHTML = tabs.map(f => {
        const isActive = f === activeFile;
        const isErr    = f === '.command.err' && task.exit_code !== 0 && task.exit_code !== null;
        return `<button class="task-tab ${isActive ? 'active' : ''} ${isErr ? 'err-tab' : ''}"
                        onclick="switchTaskTab('${escapeAttr(task.hash)}','${f}')">${FILE_LABELS[f] || f}</button>`;
    }).join('');
}

async function switchTaskTab(hash, file) {
    const task = taskState.tasks.find(t => t.hash === hash);
    if (!task) return;
    const tabs = FILE_PRIORITY.filter(f => task.available_files.includes(f));
    renderTaskTabs(task, tabs, file);
    await loadTaskFile(hash, file);
}

async function loadTaskFile(hash, file) {
    const body = $('task-file-body');
    body.innerHTML = `<div class="task-file-loading"><span class="spinner"></span> Loading…</div>`;

    try {
        const res  = await fetch(`/work_file?hash=${encodeURIComponent(hash)}&file=${encodeURIComponent(file)}`);
        const data = await res.json();

        if (data.error) { body.textContent = `Error: ${data.error}`; return; }

        const content = data.content || '(empty)';
        const sizeKb  = data.size ? `${(data.size / 1024).toFixed(1)} KB` : '';

        body.innerHTML = renderFileContent(content, file, sizeKb);
    } catch (err) {
        body.textContent = `Failed to load: ${err}`;
    }
}

function renderFileContent(raw, file, sizeInfo) {
    const stripped = raw.replace(/\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '');
    const escaped  = escapeHtml(stripped);

    const colored = escaped.split('\n').map(line => {
        if (line.match(/^\s*#/))                    return `<span class="ansi-dim">${line}</span>`;
        if (line.match(/\b(error|Error|ERROR)\b/))  return `<span class="ansi-red">${line}</span>`;
        if (line.match(/\b(warn|Warn|WARNING)\b/))  return `<span class="ansi-yellow">${line}</span>`;
        if (line.match(/\b(success|done|complete)\b/i)) return `<span class="ansi-green">${line}</span>`;
        if (line.match(/^Traceback|^\s+File "|^[A-Z][a-zA-Z]+Error:/)) return `<span class="ansi-red">${line}</span>`;
        return line;
    }).join('\n');

    const sizeSpan = sizeInfo
        ? `<span style="color:var(--text-dim);font-size:9px;float:right;margin-bottom:8px;display:block">${sizeInfo}</span>`
        : '';
    return sizeSpan + colored;
}

// ── INIT ──────────────────────────────────────────────────────
window.onload = () => {
    renderSimConditions();
    renderStage2Details();
    updateCmdPreview();
    updateRibbon({ status: 'Idle', stages: 0 });

    ['path-data','path-scripts','path-events','s3-subset','s3-decay-form','cfg-profile','cfg-resume',
     'cfg-rel-tol','cfg-abs-tol','cfg-max-steps'].forEach(id => {
        const el = $(id);
        if (el) {
            el.addEventListener('change', updateCmdPreview);
            el.addEventListener('input', updateCmdPreview);
        }
    });

    loadEventsDatalist();
    browse('', 'data-tree', 'data');

    const networkPanel = $('panel-network');
    if (networkPanel && networkPanel.classList.contains('active')) {
        ensureNetworkViewerLoaded();
    }
};


/* ══════════════════════════════════════════════════════════════
   QUALITY CHECKS
   ══════════════════════════════════════════════════════════════ */
const qualityState = { selectedFiles: new Set() };

async function loadQualityFiles() {
    const container = $('quality-file-list');
    container.innerHTML = `<span class="spinner"></span>`;
    try {
        const res  = await fetch('/ls?category=results&path=simulatePSS/tables');
        const data = await res.json();
        if (!data.items || !data.items.length) {
            container.innerHTML = '<span class="browser-empty">No simulation tables found. Run the pipeline first.</span>';
            return;
        }
        container.innerHTML = data.items
            .filter(i => i.type === 'file' && /^simulation_.*\.csv$/i.test(i.name))
            .map(i => {
                const sel = qualityState.selectedFiles.has(i.path);
                return `<div class="quality-file-item ${sel ? 'selected' : ''}" onclick="toggleQualityFile('${escapeAttr(i.path)}','${escapeAttr(i.name)}',this)">
                    <input type="checkbox" ${sel ? 'checked' : ''} style="accent-color:var(--accent2);pointer-events:none">
                    ${escapeHtml(i.name)}
                </div>`;
            }).join('') || '<span class="browser-empty">No CSV files found.</span>';
    } catch(e) {
        container.innerHTML = `<span class="browser-empty" style="color:var(--danger)">Error: ${escapeHtml(String(e))}</span>`;
    }
}

function toggleQualityFile(path, name, el) {
    if (qualityState.selectedFiles.has(path)) {
        qualityState.selectedFiles.delete(path);
        el.classList.remove('selected');
        el.querySelector('input').checked = false;
    } else {
        qualityState.selectedFiles.add(path);
        el.classList.add('selected');
        el.querySelector('input').checked = true;
    }
}

async function runQualityCheck() {
    if (!qualityState.selectedFiles.size) {
        showToast('Select at least one simulation file first.', 'error');
        return;
    }
    setBtnLoading('btn-run-quality', true);
    setStatus('quality-status', 'running', 'Analysing simulations…');
    try {
        const res  = await fetch('/quality_check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ files: [...qualityState.selectedFiles] })
        });
        const data = await res.json();
        setStatus('quality-status', 'ok', 'Analysis complete');
        renderQualityResults(data);
    } catch(e) {
        setStatus('quality-status', 'error', String(e));
        showToast(`Quality check failed: ${e}`, 'error', 5000);
    }
    setBtnLoading('btn-run-quality', false);
}

// ── REACTION ACTIVITY ─────────────────────────────────────────
let reactionChart = null;

async function runReactionActivity() {
    if (!qualityState.selectedFiles.size) {
        showToast('Select at least one simulation file above first.', 'error');
        return;
    }
    setBtnLoading('btn-run-reactions', true);
    setStatus('reaction-status', 'running', 'Computing reaction activity…');
    try {
        const res  = await fetch('/reaction_activity', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ files: [...qualityState.selectedFiles] })
        });
        const data = await res.json();
        renderReactionActivity(data.results);
        const missing = data.results.filter(r => r.missing).length;
        if (missing) {
            setStatus('reaction-status', 'error',
                `${missing} file(s) missing flux data — re-run the pipeline first`);
        } else {
            setStatus('reaction-status', 'ok', 'Done');
        }
    } catch(e) {
        setStatus('reaction-status', 'error', String(e));
        showToast(`Reaction activity failed: ${e}`, 'error', 5000);
    }
    setBtnLoading('btn-run-reactions', false);
}

function renderReactionActivity(results) {
    const wrap  = $('reaction-chart-wrap');
    const empty = $('reaction-empty');
    if (!results || !results.length) {
        wrap.style.display  = 'none';
        empty.style.display = '';
        empty.textContent   = 'No results returned.';
        return;
    }

    const valid = results.filter(r => r.totals && r.totals.length);
    if (!valid.length) {
        wrap.style.display  = 'none';
        empty.style.display = '';
        const msgs = results.map(r => r.error || (r.missing ? 'missing flux data (re-run pipeline)' : '?'));
        empty.textContent = 'No flux data: ' + msgs.join('; ');
        return;
    }

    const topN = parseInt($('reaction-top-n')?.value || '20', 10);

    // Union of top-N reactions across all files, ordered by max total across files
    const totalsMap = {};
    valid.forEach(sim => {
        sim.totals.forEach(({ reaction, total }) => {
            totalsMap[reaction] = Math.max(totalsMap[reaction] || 0, total || 0);
        });
    });
    const labels = Object.entries(totalsMap)
        .sort((a, b) => b[1] - a[1])
        .slice(0, topN)
        .map(([k]) => k);

    const palette = [
        'rgba(43,143,255,0.75)', 'rgba(80,220,140,0.75)', 'rgba(255,160,50,0.75)',
        'rgba(220,80,100,0.75)', 'rgba(160,100,255,0.75)', 'rgba(50,200,220,0.75)'
    ];
    const datasets = valid.map((sim, i) => {
        const lookup = Object.fromEntries((sim.totals || []).map(t => [t.reaction, t.total]));
        return {
            label: sim.name.replace(/^simulation_/, '').replace(/\.csv$/, ''),
            data:  labels.map(rxn => lookup[rxn] ?? 0),
            backgroundColor: palette[i % palette.length],
            borderRadius: 3,
        };
    });

    if (reactionChart) { reactionChart.destroy(); reactionChart = null; }

    const canvas = $('reaction-chart');
    canvas.height = Math.max(300, labels.length * (valid.length > 1 ? 22 : 14));
    wrap.style.display  = 'block';
    empty.style.display = 'none';

    reactionChart = new Chart(canvas, {
        type: 'bar',
        data: { labels, datasets },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: valid.length > 1, labels: { color: 'var(--text)', font: { size: 11 } } },
                tooltip: {
                    callbacks: {
                        label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.x.toExponential(3)}`
                    }
                }
            },
            scales: {
                x: {
                    ticks: { color: 'var(--text-dim)', font: { size: 10 } },
                    grid:  { color: 'rgba(255,255,255,0.05)' },
                    title: { display: true, text: 'Cumulative flux (∫|J| dt)', color: 'var(--text-dim)', font: { size: 10 } }
                },
                y: {
                    ticks: { color: 'var(--text)', font: { size: 10, family: 'var(--font-mono)' } },
                    grid:  { display: false }
                }
            }
        }
    });
}

function renderQualityResults(data) {
    const wrap = $('quality-results');
    wrap.style.display = 'block';

    // Summary table
    const summaryEl = $('quality-summary');
    const reports   = data.file_reports || [];
    if (reports.length) {
        let html = '<table class="quality-summary-table"><thead><tr><th>File</th><th>Negative</th><th>Non-stable</th><th>Extreme</th><th>Total species</th></tr></thead><tbody>';
        reports.forEach(r => {
            if (r.error) {
                html += `<tr><td>${escapeHtml(r.file)}</td><td colspan="4" style="color:var(--danger)">${escapeHtml(r.error)}</td></tr>`;
            } else {
                html += `<tr><td style="font-family:var(--font-mono);font-size:10px">${escapeHtml(r.file)}</td>
                    <td style="color:${r.negative_count > 0 ? 'var(--danger)' : 'var(--ok)'}">${r.negative_count}</td>
                    <td style="color:${r.unstable_count > 0 ? 'var(--warn)' : 'var(--ok)'}">${r.unstable_count}</td>
                    <td style="color:${r.extreme_count > 0 ? 'var(--warn)' : 'var(--ok)'}">${r.extreme_count}</td>
                    <td>${r.total_species}</td></tr>`;
            }
        });
        html += '</tbody></table>';
        summaryEl.innerHTML = html;
    }

    // Aggregate across all files
    const allNeg      = new Map();
    const allUnstable = new Set();
    reports.forEach(r => {
        if (!r.error) {
            (r.negative_species || []).forEach(sp => {
                const t = r.first_neg_times?.[sp];
                if (!allNeg.has(sp) || t < allNeg.get(sp)) allNeg.set(sp, t);
            });
            (r.unstable_species || []).forEach(sp => allUnstable.add(sp));
        }
    });

    const negCount      = $('quality-neg-count');
    const unstableCount = $('quality-unstable-count');
    const deadCount     = $('quality-deadend-count');
    if (negCount)      negCount.textContent      = allNeg.size;
    if (unstableCount) unstableCount.textContent = allUnstable.size;
    if (deadCount)     deadCount.textContent     = data.dead_end_count || 0;

    const badge = $('badge-quality');
    if (badge) {
        badge.textContent  = allNeg.size > 0 ? allNeg.size : '✓';
        badge.className    = `nav-badge ${allNeg.size > 0 ? 'err' : 'ok'}`;
    }

    renderSpeciesChips('quality-negative', [...allNeg.entries()].sort((a,b)=>(a[1]||0)-(b[1]||0)).map(([sp,t])=>({sp,t})), 'danger');
    renderSpeciesChips('quality-unstable', [...allUnstable].map(sp=>({sp})), 'warn');
    renderSpeciesChips('quality-deadend',  (data.dead_end_species||[]).map(sp=>({sp})), '');
}

function renderSpeciesChips(containerId, items, cls) {
    const el = $(containerId);
    if (!el) return;
    if (!items.length) {
        el.innerHTML = `<span style="font-size:11px;color:var(--ok)">None detected ✓</span>`;
        return;
    }
    el.innerHTML = items.slice(0, 200).map(({sp, t}) =>
        `<span class="quality-species-chip ${cls}" title="${t !== undefined ? 'First at t=' + (t?.toFixed(1) ?? '?') : ''}">${escapeHtml(sp)}${t !== undefined ? ` <span style="opacity:.5">@${t?.toFixed(0)}</span>` : ''}</span>`
    ).join('') + (items.length > 200 ? `<span class="quality-species-chip" style="color:var(--text-dim)">+${items.length-200} more</span>` : '');
}

/* ══════════════════════════════════════════════════════════════
   PARAMETER SCAN
   ══════════════════════════════════════════════════════════════ */
const scanState = {
    jobId: null, pollTimer: null, result: null,
    heatmapMode: 'abs', allParams: [],
    nominalValues: {},    // param name → nominal value from Antimony file
    lastFoldValues: null, // fold-change values used in last scan (for display)
    lastNominal: null,    // nominal value used to convert fold → absolute
    lastThreshold: 5      // significance threshold % for last scan
};

async function loadAntimonyFiles() {
    try {
        const res  = await fetch('/list_antimony');
        const data = await res.json();
        const sel  = $('scan-model-file');
        if (!sel || !data.files?.length) return;
        sel.innerHTML = data.files.map(f => `<option value="${escapeAttr(f)}">${escapeHtml(f)}</option>`).join('');
    } catch(e) {}
}

async function loadModelParams() {
    const modelFile = $('scan-model-file')?.value || 'Antimony.txt';
    const listEl    = $('scan-param-list');
    listEl.innerHTML = `<div class="browser-empty"><span class="spinner"></span> Loading model…</div>`;

    try {
        // We can't run tellurium client-side; use a trick: read the Antimony text and parse parameter names
        const res  = await fetch(`/get_file?category=results&path=${encodeURIComponent('buildAntimony/' + modelFile)}`);
        const data = await res.json();
        if (data.type !== 'text') { listEl.innerHTML = '<div class="browser-empty" style="color:var(--danger)">Could not read model file.</div>'; return; }

        // Parse parameter assignments from Antimony text (lines are tab-indented)
        const text    = data.content;
        const params  = new Set();
        const nominal = {};
        for (const m of text.matchAll(/^\s*(\w+)\s*=\s*([\d.e+\-]+)\s*;/gm)) {
            const name = m[1];
            const val  = parseFloat(m[2]);
            if (name.startsWith('c_')) continue;
            if (name === 'n')          continue;
            if (name.startsWith('s_')) {
                const key = '[' + name + ']';
                params.add(key);
                nominal[key] = val;
            } else {
                params.add(name);
                nominal[name] = val;
            }
        }

        scanState.allParams     = [...params].sort();
        scanState.nominalValues = nominal;
        renderParamList(scanState.allParams);
    } catch(e) {
        listEl.innerHTML = `<div class="browser-empty" style="color:var(--danger)">Error: ${escapeHtml(String(e))}</div>`;
    }
}

function renderParamList(params) {
    const listEl = $('scan-param-list');
    if (!params.length) { listEl.innerHTML = '<div class="browser-empty">No parameters found.</div>'; return; }
    listEl.innerHTML = params.map(p =>
        `<div class="compare-species-item" onclick="selectScanParam('${escapeAttr(p)}')">${escapeHtml(p)}</div>`
    ).join('');
}

function filterParamList() {
    const q = ($('scan-param-search')?.value || '').toLowerCase();
    document.querySelectorAll('#scan-param-list .compare-species-item').forEach(el => {
        el.classList.toggle('hidden', q && !el.textContent.toLowerCase().includes(q));
    });
}

function onScanTypeChange() {
    const type = $('scan-type')?.value || 'fold';
    const minLbl = $('scan-min-label');
    const maxLbl = $('scan-max-label');
    const minInp = $('scan-min');
    const maxInp = $('scan-max');
    if (type === 'fold') {
        if (minLbl) minLbl.textContent = 'Min fold-change';
        if (maxLbl) maxLbl.textContent = 'Max fold-change';
        if (minInp && parseFloat(minInp.value) > 10) minInp.value = '0.01';
        if (maxInp && parseFloat(maxInp.value) < 2)  maxInp.value = '100';
    } else {
        if (minLbl) minLbl.textContent = 'Min value';
        if (maxLbl) maxLbl.textContent = 'Max value';
    }
    updateScanPreview();
}

function selectScanParam(p) {
    const inp = $('scan-param');
    if (inp) inp.value = p;
    document.querySelectorAll('#scan-param-list .compare-species-item').forEach(el =>
        el.classList.toggle('active', el.textContent.trim() === p)
    );
    // Show nominal value hint
    const nominal = scanState.nominalValues?.[p];
    const hint = $('scan-param-nominal-hint');
    if (hint) hint.textContent = nominal !== undefined ? `Nominal value: ${nominal}` : '';
    updateScanPreview();
}

function updateScanPreview() {
    const param = $('scan-param')?.value || '—';
    const min   = parseFloat($('scan-min')?.value) || 0.01;
    const max   = parseFloat($('scan-max')?.value) || 100;
    const steps = parseInt($('scan-steps')?.value) || 12;
    const type  = $('scan-type')?.value || 'fold';
    const thresh = parseFloat($('scan-threshold')?.value) || 5;
    const preview = $('scan-preview');
    if (!preview) return;
    const vals = generateScanValues(min, max, steps, type);
    const nominal = scanState.nominalValues?.[param];
    let valStr;
    if (type === 'fold' && nominal !== undefined) {
        valStr = vals.map(v => `${v.toPrecision(2)}× (= ${(v * nominal).toPrecision(3)})`).join(', ');
    } else {
        valStr = vals.map(v => v.toPrecision(3)).join(', ');
    }
    const modeLabel = type === 'fold' ? 'fold-changes' : 'values';
    preview.innerHTML = `param = <span class="cmd-flag">${escapeHtml(param)}</span><br>${modeLabel} = [<span class="cmd-value">${valStr}</span>]<br>threshold = <span class="cmd-flag">${thresh}%</span> output change`;
}

function generateScanValues(min, max, steps, type) {
    const vals = [];
    for (let i = 0; i < steps; i++) {
        const t = steps > 1 ? i / (steps - 1) : 0;
        if (type === 'log') {
            const logMin = Math.log10(Math.max(min, 1e-30));
            const logMax = Math.log10(Math.max(max, 1e-30));
            vals.push(Math.pow(10, logMin + (logMax - logMin) * t));
        } else if (type === 'fold') {
            // Log-spaced fold-changes so we cover 0.01x … 100x evenly
            const logMin = Math.log10(Math.max(min, 1e-6));
            const logMax = Math.log10(Math.max(max, 1e-6));
            vals.push(Math.pow(10, logMin + (logMax - logMin) * t));
        } else {
            vals.push(min + (max - min) * t);
        }
    }
    return vals;
}

async function startParamScan() {
    const param  = $('scan-param')?.value?.trim();
    const min    = parseFloat($('scan-min')?.value);
    const max    = parseFloat($('scan-max')?.value);
    const steps  = parseInt($('scan-steps')?.value);
    const type   = $('scan-type')?.value || 'fold';
    const end    = parseFloat($('scan-end')?.value) || 1000;
    const model  = $('scan-model-file')?.value || 'Antimony.txt';

    if (!param) { showToast('Enter a parameter name.', 'error'); return; }
    if (isNaN(min) || isNaN(max) || min >= max) { showToast('Invalid range.', 'error'); return; }

    const foldValues = generateScanValues(min, max, steps, type);
    const nominal = scanState.nominalValues?.[param];

    // Convert fold-changes to absolute values using the nominal
    let values;
    if (type === 'fold') {
        if (nominal === undefined || nominal === null) {
            showToast('Cannot use fold-change mode: nominal value not found. Load model parameters first, or switch to Absolute mode.', 'error', 6000);
            return;
        }
        // Nominal (1.0×) is always first — it is the baseline the rest are compared against
        const allFolds  = [1.0, ...foldValues.filter(f => Math.abs(f - 1.0) > 0.001)];
        values = allFolds.map(fc => fc * nominal);
        scanState.lastFoldValues = allFolds;
        scanState.lastNominal    = nominal;
    } else {
        values = foldValues;
        scanState.lastFoldValues = null;
        scanState.lastNominal    = null;
    }

    scanState.lastThreshold = parseFloat($('scan-threshold')?.value) || 5;

    setBtnLoading('btn-run-scan', true);
    setStatus('scan-status', 'running', 'Starting…');

    try {
        const res  = await fetch('/param_scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ param_name: param, values, start: 0, end, points: 500, model_file: model })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to start scan');
        scanState.jobId = data.job_id;
        pollParamScan();
    } catch(e) {
        setStatus('scan-status', 'error', String(e));
        showToast(`Scan failed: ${e}`, 'error', 5000);
        setBtnLoading('btn-run-scan', false);
    }
}

function pollParamScan() {
    if (scanState.pollTimer) clearInterval(scanState.pollTimer);
    scanState.pollTimer = setInterval(async () => {
        try {
            const res  = await fetch(`/param_scan_result/${scanState.jobId}`);
            const data = await res.json();
            if (data.status === 'running') {
                setStatus('scan-status', 'running', `Running… ${data.progress}%`);
            } else if (data.status === 'done') {
                clearInterval(scanState.pollTimer);
                setBtnLoading('btn-run-scan', false);
                setStatus('scan-status', 'ok', 'Scan complete');
                scanState.result = data.result;
                renderScanResults(data.result);
                showToast('Parameter scan complete.', 'ok');
            } else if (data.status === 'error') {
                clearInterval(scanState.pollTimer);
                setBtnLoading('btn-run-scan', false);
                setStatus('scan-status', 'error', data.error || 'Scan failed');
                showToast(`Scan error: ${data.error}`, 'error', 8000);
            }
        } catch(e) {
            clearInterval(scanState.pollTimer);
            setBtnLoading('btn-run-scan', false);
            setStatus('scan-status', 'error', 'Poll failed');
        }
    }, 1500);
}

function renderScanResults(result) {
    $('scan-results-wrap').style.display = 'block';
    renderThresholdSummary(result);
    renderScanRanking(result);
    renderScanHeatmap(result, scanState.heatmapMode);
}

function setHeatmapMode(mode) {
    scanState.heatmapMode = mode;
    $('heatmap-abs-btn').classList.toggle('active', mode === 'abs');
    $('heatmap-rel-btn').classList.toggle('active', mode === 'rel');
    if (scanState.result) renderScanHeatmap(scanState.result, mode);
}

function rerenderScanThreshold() {
    if (scanState.result) {
        scanState.lastThreshold = parseFloat($('scan-threshold')?.value) || 5;
        renderThresholdSummary(scanState.result);
        renderScanRanking(scanState.result);
    }
}

function renderThresholdSummary(result) {
    const el       = $('scan-threshold-summary');
    if (!el) return;
    const thresh   = scanState.lastThreshold;
    const folds    = scanState.lastFoldValues;
    const nominal  = scanState.lastNominal;
    const scans    = result.scan_results || [];
    const paramName = result.param_name;

    // For each species, find the minimum scan value (or fold-change) where % change exceeds threshold
    const baseline = scans[0]?.concentrations || {};
    let firstExceedFold  = null;
    let firstExceedLabel = null;
    let nExceed          = 0;

    for (let i = 1; i < scans.length; i++) {
        const concs = scans[i].concentrations;
        if (!concs) continue;
        let anyExceed = false;
        for (const sp of Object.keys(baseline)) {
            const base = baseline[sp] || 0;
            const curr = concs[sp] || 0;
            const pct  = Math.abs(base) > 1e-15 ? Math.abs((curr - base) / base) * 100 : Math.abs(curr) * 1000;
            if (pct >= thresh) { anyExceed = true; break; }
        }
        if (anyExceed) {
            nExceed++;
            if (firstExceedFold === null) {
                firstExceedFold  = folds ? folds[i] : scans[i].param_value;
                firstExceedLabel = folds
                    ? `${firstExceedFold.toPrecision(3)}× the nominal value`
                    : firstExceedFold.toPrecision(4);
            }
        }
    }

    if (firstExceedFold === null) {
        el.innerHTML = `<div class="scan-threshold-box ok">
            <b>Robust parameter.</b> No scan value caused ≥${thresh}% change in any species within the tested range.
            The model simulation is insensitive to changes in <code>${escapeHtml(paramName)}</code> here.
        </div>`;
    } else {
        const totalSteps = scans.length - 1;
        el.innerHTML = `<div class="scan-threshold-box warn">
            <b>Simulation first diverges at ${escapeHtml(firstExceedLabel)}.</b>
            ${nExceed} of ${totalSteps} scan steps caused ≥${thresh}% change in at least one species.
            Below that level, <code>${escapeHtml(paramName)}</code> can be changed without noticeably affecting the simulation.
        </div>`;
    }
}

function renderScanRanking(result) {
    const el     = $('scan-sensitivity-ranking');
    const top    = (result.top_sensitive_species || []).slice(0, 20);
    if (!top.length) { el.innerHTML = '<span class="browser-empty">No results.</span>'; return; }
    const thresh  = scanState.lastThreshold;
    const folds   = scanState.lastFoldValues;
    const scans   = result.scan_results || [];
    const maxPct  = Math.max(...top.map(r => r.max_pct_change || 0), 1);
    const baseline = scans[0]?.concentrations || {};

    const hdrFold = folds ? '<th>Min fold-change to trigger</th>' : '';
    let html = `<table><thead><tr><th>Species</th><th>Max % change</th><th>Bar</th>${hdrFold}</tr></thead><tbody>`;
    top.forEach(({species, max_pct_change}) => {
        const pct   = max_pct_change || 0;
        const bar   = Math.round((pct / maxPct) * 100);
        const color = pct > 50 ? 'var(--danger)' : pct > 10 ? 'var(--warn)' : 'var(--accent)';

        let minFoldCell = '';
        if (folds) {
            const base = baseline[species] || 0;
            let minFold = null;
            for (let i = 1; i < scans.length; i++) {
                const curr = scans[i].concentrations?.[species] || 0;
                const p = Math.abs(base) > 1e-15 ? Math.abs((curr - base) / base) * 100 : Math.abs(curr) * 1000;
                if (p >= thresh) { minFold = folds[i]; break; }
            }
            minFoldCell = minFold !== null
                ? `<td style="font-family:var(--font-mono);color:${color}">${minFold.toPrecision(3)}×</td>`
                : `<td style="color:var(--text-dim)">not reached</td>`;
        }

        html += `<tr onclick="selectScanSpecies('${escapeAttr(species)}')" style="cursor:pointer">
            <td style="font-family:var(--font-mono);font-size:10px">${escapeHtml(species)}</td>
            <td style="font-family:var(--font-mono);color:${color}">${pct.toFixed(2)}%</td>
            <td style="min-width:80px"><div style="height:6px;background:${color};width:${bar}%;border-radius:2px"></div></td>
            ${minFoldCell}
        </tr>`;
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

function selectScanSpecies(sp) {
    showToast(`Species: ${sp}`, 'info');
}

function renderScanHeatmap(result, mode) {
    const el  = $('scan-heatmap');
    const top = (result.top_sensitive_species || []).slice(0, 20);
    if (!top.length) { el.innerHTML = '<span class="browser-empty">No data.</span>'; return; }
    const vals  = result.values || [];
    const scans = result.scan_results || [];

    const folds = scanState.lastFoldValues;
    let html = '<table><thead><tr><th>Species ↓ / Scan →</th>';
    vals.forEach((v, i) => {
        const lbl = folds
            ? `${folds[i].toPrecision(2)}×`
            : (v < 0.001 || v >= 1000 ? v.toExponential(1) : v.toPrecision(3));
        const title = folds ? `${folds[i].toPrecision(3)}× nominal = ${v.toPrecision(4)}` : String(v);
        html += `<th title="${escapeAttr(title)}">${lbl}</th>`;
    });
    html += '</tr></thead><tbody>';

    top.forEach(({species}) => {
        const concs  = scans.map(sr => sr.concentrations?.[species] ?? null);
        const base   = concs.find(v => v !== null);
        const finite = concs.filter(v => v !== null && isFinite(v));
        const vmin   = finite.length ? Math.min(...finite) : 0;
        const vmax   = finite.length ? Math.max(...finite) : 1;

        html += `<tr><td class="heatmap-label" title="${escapeAttr(species)}">${escapeHtml(species)}</td>`;
        concs.forEach(v => {
            let bg = '#0e1620';
            let txt = '—';
            if (v !== null && isFinite(v)) {
                txt = v === 0 ? '0' : v.toExponential(1);
                if (mode === 'rel' && base !== null && Math.abs(base) > 1e-30) {
                    const fc = (v - base) / Math.abs(base);
                    bg = foldChangeToColor(fc);
                } else {
                    bg = concToHeatmapColor(v, vmin, vmax);
                }
            }
            html += `<td style="background:${bg}" title="${escapeAttr(species)}: ${txt}">${txt}</td>`;
        });
        html += '</tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

function concToHeatmapColor(val, vmin, vmax) {
    if (val === null || !isFinite(val)) return '#111920';
    const logV   = Math.log10(Math.max(val, 1e-30));
    const logMin = Math.log10(Math.max(vmin, 1e-30));
    const logMax = Math.log10(Math.max(vmax, 1e-30));
    let t = (logMax > logMin) ? (logV - logMin) / (logMax - logMin) : 0.5;
    t = Math.max(0, Math.min(1, t));
    if (t < 0.5) {
        const s = t * 2;
        return `rgb(${Math.round(41 + s*(255-41))},${Math.round(128+s*(255-128))},255)`;
    }
    const s = (t - 0.5) * 2;
    return `rgb(255,${Math.round(255 - s*195)},${Math.round(255 - s*255)})`;
}

function foldChangeToColor(fc) {
    const clamped = Math.max(-3, Math.min(3, fc));
    if (clamped >= 0) {
        const t = clamped / 3;
        return `rgb(255,${Math.round(255*(1-t))},${Math.round(255*(1-t))})`;
    }
    const t = -clamped / 3;
    return `rgb(${Math.round(255*(1-t))},${Math.round(255*(1-t))},255)`;
}

/* ══════════════════════════════════════════════════════════════
   SIMULATION FLOW — CYTOSCAPE NETWORK ANIMATION
   ══════════════════════════════════════════════════════════════ */
const sfState = {
    cy:           null,
    network:      null,   // {species, reactions, compartments, types}
    simData:      null,   // {time, species, n_points, n_species}
    timeIdx:      0,
    playTimer:    null,
    nodeChart:    null,
    selectedNode: null,
    initialized:  false,
    // Pre-built per-frame lookup for fast animation: array of {node, key, base0} in cy node order
    nodeIndex:    null,   // rebuilt in buildCytoscape and when simData changes
    loadedSimPath: null,  // path of the currently loaded sim file
    loadedSpecies: null,  // set of species keys requested in last sim fetch
};

const TYPE_COLORS = {
    protein:        '#2b8fff',
    protein_active: '#00c896',
    metabolite:     '#f4a118',
    gene:           '#9b5de5',
    mrna:           '#e879f9',
    complex:        '#38bdf8',
    complex_active: '#34d399',
    mirna:          '#fb923c',
    abiotic:        '#94a3b8',
    foreign_entity: '#f87171',
    process:        '#a3e635',
    process_active: '#86efac',
    ncrna:          '#c084fc',
    unknown:        '#475569',
};

async function loadSimflowSimList() {
    const sel = $('simflow-sim-select');
    if (!sel) return;
    try {
        const res  = await fetch('/ls?category=results&path=simulatePSS/tables');
        const data = await res.json();
        if (!data.items) return;
        const csvs = data.items.filter(i => i.type === 'file' && /\.csv$/i.test(i.name));
        sel.innerHTML = '<option value="">— none —</option>' +
            csvs.map(i => `<option value="${escapeAttr(i.path)}">${escapeHtml(i.name)}</option>`).join('');
    } catch(e) {}
}

async function initSimflow() {
    // Always refresh the sim file list; only load network once
    await loadSimflowSimList();
    if (sfState.network) return;

    const overlay = $('simflow-overlay');
    const showOverlay = (msg) => { if (overlay) { overlay.classList.remove('hidden'); overlay.textContent = msg; } };
    const hideOverlay = ()    => { if (overlay) overlay.classList.add('hidden'); };

    showOverlay('Loading network data…');

    try {
        const res = await fetch('/network_data');
        if (!res.ok) {
            const d = await res.json();
            showOverlay(d.error || 'Network data not found. Run the pipeline first.');
            return;
        }
        sfState.network = await res.json();
        populateSimflowFilters();
        // Hide overlay before building the graph — layout runs async and does not block
        hideOverlay();
        buildCytoscape();
    } catch(e) {
        showOverlay(`Error loading network: ${e}`);
    }
}

function populateSimflowFilters() {
    const net = sfState.network;
    if (!net) return;

    const cmpSel  = $('simflow-compartment');
    const typeSel = $('simflow-type');
    if (cmpSel) {
        // Default to c_chl (35 species) — c_cyt has 800+ nodes and is slow to layout
        cmpSel.innerHTML = '<option value="">All compartments</option>' +
            net.compartments.map(c => `<option value="${escapeAttr(c)}" ${c==='c_chl'?'selected':''}>${escapeHtml(c)}</option>`).join('');
    }
    if (typeSel) {
        typeSel.innerHTML = '<option value="">All types</option>' +
            net.types.map(t => `<option value="${escapeAttr(t)}">${escapeHtml(t)}</option>`).join('');
    }
}

function buildCytoscape() {
    const net     = sfState.network;
    const cmp     = $('simflow-compartment')?.value || '';
    const typeFlt = $('simflow-type')?.value || '';

    const filteredSpecies = Object.entries(net.species).filter(([,v]) =>
        (!cmp || v.compartment === cmp) && (!typeFlt || v.type === typeFlt)
    );
    const nodeIds = new Set(filteredSpecies.map(([id]) => id));

    const nodes = filteredSpecies.map(([id, v]) => ({
        data: { id, label: v.name || id, type: v.type, compartment: v.compartment },
        style: { 'background-color': TYPE_COLORS[v.type] || '#475569' }
    }));

    const edges = [];
    Object.entries(net.reactions).forEach(([rxId, rx]) => {
        const reactants = rx.reactants.filter(r => nodeIds.has(r));
        const products  = rx.products.filter(p => nodeIds.has(p));
        reactants.forEach(r => products.forEach(p => {
            edges.push({ data: { id: `${rxId}_${r}_${p}`, source: r, target: p } });
        }));
    });

    // Store the visible species set for filtered sim-data fetching.
    // Bare ids: simulate.py writes bare column names, so a bracketed key matched
    // nothing and the overlay silently loaded 0 species.
    sfState.visibleSpeciesKeys = new Set(filteredSpecies.map(([id]) => id));

    const cyEl = $('simflow-cy');
    if (!cyEl || typeof cytoscape === 'undefined') return;

    if (sfState.cy) { sfState.cy.destroy(); sfState.cy = null; }
    sfState.nodeIndex = null;  // will be rebuilt after layout

    const nodeCount = nodes.length;
    // For large graphs cose is too slow — use grid (instant) and let user trigger force layout manually
    const useCose  = nodeCount <= 150;
    const layout   = useCose
        ? { name: 'cose', animate: true, randomize: true, idealEdgeLength: 60,
            nodeRepulsion: 8000, numIter: 600, animationDuration: 800 }
        : { name: 'grid', condense: true, avoidOverlap: true };

    const nodeCountEl = $('simflow-node-count');
    if (nodeCountEl) nodeCountEl.textContent = `${nodeCount} nodes · ${edges.length} edges${useCose ? '' : ' (grid layout — click ⟳ for force layout)'}`;

    sfState.cy = cytoscape({
        container: cyEl,
        elements:  { nodes, edges },
        style: [
            { selector: 'node', style: {
                'label':            'data(label)',
                'font-size':        '7px',
                'color':            '#c5d4e8',
                'text-valign':      'bottom',
                'text-margin-y':    '4px',
                'text-outline-width': 0,
                'width':            16,
                'height':           16,
                'border-width':     1,
                'border-color':     'rgba(255,255,255,0.15)',
                'text-wrap':        'none',
            }},
            { selector: 'node:selected', style: {
                'border-width': 2,
                'border-color': '#00c896',
            }},
            { selector: 'edge', style: {
                'width':                1,
                'line-color':           'rgba(31,46,64,0.8)',
                'target-arrow-color':   'rgba(31,46,64,0.8)',
                'target-arrow-shape':   'triangle',
                'curve-style':          'bezier',
                'arrow-scale':          0.6,
            }},
        ],
        layout,
        wheelSensitivity: 0.3,
    });

    sfState.cy.on('layoutstop', () => {
        buildNodeIndex();
        if (sfState.simData) refreshSimflowColors();
    });

    sfState.cy.on('tap', 'node', e => {
        const node = e.target;
        onSimflowNodeClick(node.id(), node.data('label'), node.data('type'), node.data('compartment'));
    });

    sfState.cy.on('tap', e => {
        if (e.target === sfState.cy) {
            sfState.selectedNode = null;
            const info = $('simflow-node-info');
            if (info) info.style.display = 'none';
        }
    });
}

function onSimflowNodeClick(id, label, type, compartment) {
    sfState.selectedNode = id;
    const infoEl   = $('simflow-node-info');
    const detailEl = $('simflow-node-detail');
    if (!infoEl || !detailEl) return;
    infoEl.style.display = 'block';

    const simKey = '[' + id + ']';
    let concText = '—';
    if (sfState.simData?.species?.[simKey]) {
        const val = sfState.simData.species[simKey][sfState.timeIdx];
        concText  = val !== null ? val.toExponential(3) : '—';
    }

    detailEl.innerHTML = `
        <div><b>ID:</b> ${escapeHtml(id)}</div>
        <div><b>Name:</b> ${escapeHtml(label)}</div>
        <div><b>Type:</b> ${escapeHtml(type)}</div>
        <div><b>Compartment:</b> ${escapeHtml(compartment)}</div>
        <div><b>Conc @ t:</b> ${concText}</div>
    `;
    renderNodeTrajectory(id);
}

function renderNodeTrajectory(id) {
    const canvas = $('simflow-node-chart');
    if (!canvas || !sfState.simData) return;

    const simKey = '[' + id + ']';
    const concs  = sfState.simData.species?.[simKey];
    if (!concs) { canvas.style.display = 'none'; return; }

    canvas.style.display = 'block';
    if (sfState.nodeChart) { sfState.nodeChart.destroy(); sfState.nodeChart = null; }

    sfState.nodeChart = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            datasets: [{
                label: id,
                data:  sfState.simData.time.map((t,i) => ({ x: t, y: concs[i] })),
                borderColor: '#00c896', borderWidth: 1.5,
                pointRadius: 0, spanGaps: true, parsing: false,
            }]
        },
        options: {
            responsive: false, maintainAspectRatio: false,
            animation: false, parsing: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { type: 'linear', title: { display: false }, ticks: { color: '#3d5468', maxTicksLimit: 4, font: { size: 8 } } },
                y: { title: { display: false }, ticks: { color: '#3d5468', maxTicksLimit: 4, font: { size: 8 } } }
            }
        }
    });
}

async function loadSimflowData() {
    const simPath = $('simflow-sim-select')?.value;
    if (!simPath) { showToast('Select a simulation file first.', 'error'); return; }

    // Only fetch data for currently visible species — avoids loading 5 MB when only 35 are shown
    const visible = sfState.visibleSpeciesKeys;
    const speciesParam = visible?.size
        ? '&species=' + encodeURIComponent([...visible].join(','))
        : '';

    showToast('Loading simulation data…', 'info', 10000);
    try {
        const res  = await fetch(`/get_sim_data?path=${encodeURIComponent(simPath)}${speciesParam}`);
        const data = await res.json();
        if (!res.ok || data.error) throw new Error(data.error || 'Failed to load');
        document.querySelectorAll('.toast').forEach(t => t.remove());

        sfState.simData      = data;
        sfState.loadedSimPath   = simPath;
        sfState.loadedSpecies   = visible ? new Set(visible) : null;

        buildNodeIndex();

        const slider = $('simflow-time-slider');
        if (slider) { slider.max = data.n_points - 1; slider.value = 0; }
        sfState.timeIdx = 0;

        const timeBlock = $('simflow-time-block');
        if (timeBlock) timeBlock.style.display = 'block';
        updateSimflowTimeDisplay(0);
        refreshSimflowColors();
        showLegend();
        const total = data.total_species ?? data.n_species;
        showToast(`Loaded ${data.n_species} of ${total} species × ${data.n_points} time points.`, 'ok');
    } catch(e) {
        document.querySelectorAll('.toast').forEach(t => t.remove());
        showToast(`Load failed: ${e}`, 'error', 5000);
    }
}

function buildNodeIndex() {
    // Pre-build a flat array of {cyNode, simKey, type} so refreshSimflowColors
    // doesn't pay the cost of cy.nodes() iteration + property lookups every frame
    if (!sfState.cy || !sfState.simData) { sfState.nodeIndex = null; return; }
    const sim = sfState.simData;
    const idx = [];
    sfState.cy.nodes().forEach(node => {
        // Accept bare or bracketed column names — older tables used [s_x], simulate.py writes s_x
        const id   = node.id();
        const key  = sim.species?.[id] !== undefined ? id : '[' + id + ']';
        const data = sim.species?.[key];
        idx.push({ node, key, data, base0: data?.[0] ?? 0, type: node.data('type') });
    });
    sfState.nodeIndex = idx;
}

function onSimflowTimeChange() {
    const slider = $('simflow-time-slider');
    sfState.timeIdx = parseInt(slider?.value || 0);
    updateSimflowTimeDisplay(sfState.timeIdx);
    refreshSimflowColors();
    if (sfState.selectedNode) renderNodeTrajectory(sfState.selectedNode);
}

function updateSimflowTimeDisplay(idx) {
    const timeVal = $('simflow-time-val');
    if (!timeVal) return;
    const t = sfState.simData?.time?.[idx];
    timeVal.textContent = t !== undefined ? t.toFixed(1) : idx;
}

function toggleSimflowPlay() {
    const btn = $('simflow-play-btn');
    if (sfState.playTimer) {
        clearInterval(sfState.playTimer);
        sfState.playTimer = null;
        if (btn) btn.textContent = '▶ Play';
    } else {
        if (btn) btn.textContent = '⏸ Pause';
        const speed  = parseInt($('simflow-speed')?.value || 100);
        const slider = $('simflow-time-slider');
        sfState.playTimer = setInterval(() => {
            const maxIdx = parseInt(slider?.max || 999);
            if (sfState.timeIdx >= maxIdx) {
                sfState.timeIdx = 0;
            } else {
                sfState.timeIdx++;
            }
            if (slider) slider.value = sfState.timeIdx;
            updateSimflowTimeDisplay(sfState.timeIdx);
            refreshSimflowColors();
        }, speed);
    }
}

function applySimflowFilter() {
    if (!sfState.network) return;
    if (sfState.playTimer) { clearInterval(sfState.playTimer); sfState.playTimer = null; const btn = $('simflow-play-btn'); if(btn) btn.textContent='▶ Play'; }
    buildCytoscape();
    // Re-fetch sim data filtered to new visible species if a file was already loaded
    if (sfState.loadedSimPath) {
        loadSimflowData();
    }
}

function runForceLayout() {
    if (!sfState.cy) return;
    const n = sfState.cy.nodes().length;
    const nodeCountEl = $('simflow-node-count');
    if (nodeCountEl) nodeCountEl.textContent = `Computing force layout for ${n} nodes… (may take a minute)`;
    sfState.cy.layout({
        name: 'cose', animate: true, randomize: false,
        idealEdgeLength: 60, nodeRepulsion: 8000, numIter: 600, animationDuration: 1200
    }).run();
}

function refreshSimflowColors() {
    const cy        = sfState.cy;
    const colorMode = $('simflow-color-mode')?.value || 'abs';
    if (!cy) return;

    // Type-colour mode: no sim data needed
    if (colorMode === 'type') {
        cy.batch(() => {
            cy.nodes().forEach(node => {
                node.style({ 'background-color': TYPE_COLORS[node.data('type')] || '#475569', width: 16, height: 16 });
            });
        });
        updateLegend(null, null, 'type');
        return;
    }

    const idx  = sfState.nodeIndex;
    const tidx = sfState.timeIdx;
    if (!idx) return;

    const threshold = Math.pow(10, parseFloat($('simflow-threshold')?.value || -8));

    // Single pass: collect values and compute log range
    const vals = new Float64Array(idx.length);
    let logMin = Infinity, logMax = -Infinity;
    for (let i = 0; i < idx.length; i++) {
        const v = idx[i].data?.[tidx] ?? 0;
        vals[i] = v;
        if (v > 0) { const l = Math.log10(v); if (l < logMin) logMin = l; if (l > logMax) logMax = l; }
    }
    if (!isFinite(logMin)) logMin = -15;
    if (!isFinite(logMax)) logMax = 0;
    const logRange = logMax - logMin || 1;

    // Apply styles in a single batch
    cy.batch(() => {
        for (let i = 0; i < idx.length; i++) {
            const { node, base0 } = idx[i];
            const v = vals[i];

            if (v < threshold) { node.style('display', 'none'); continue; }
            node.style('display', 'element');

            if (v <= 0 || !isFinite(v)) {
                node.style({ 'background-color': '#182030', width: 10, height: 10 });
                continue;
            }

            let color;
            if (colorMode === 'rel') {
                const fc = base0 > 1e-30 ? (v - base0) / base0 : 0;
                color = foldChangeToColor(fc);
            } else {
                color = lerpColor((Math.log10(v) - logMin) / logRange);
            }
            const size = 8 + Math.max(0, Math.min(1, (Math.log10(v) - logMin) / logRange)) * 28;
            node.style({ 'background-color': color, width: size, height: size });
        }
    });

    updateLegend(logMin, logMax, colorMode);
}

function lerpColor(t) {
    t = Math.max(0, Math.min(1, t));
    if (t < 0.5) {
        const s = t * 2;
        return `rgb(${Math.round(41+s*214)},${Math.round(128+s*127)},255)`;
    }
    const s = (t - 0.5) * 2;
    return `rgb(255,${Math.round(255-s*195)},${Math.round(255-s*255)})`;
}

function showLegend() {
    const legend = $('simflow-legend');
    if (legend) legend.classList.add('visible');
}

function updateLegend(logMin, logMax, mode) {
    const legend = $('simflow-legend');
    if (!legend) return;
    legend.classList.add('visible');
    if (mode === 'type') {
        legend.innerHTML = Object.entries(TYPE_COLORS).map(([type, color]) =>
            `<div style="display:flex;align-items:center;gap:5px"><span style="width:9px;height:9px;border-radius:50%;background:${color};display:inline-block"></span>${type}</div>`
        ).join('');
        return;
    }
    const lowLabel  = mode === 'rel' ? '3× decrease' : `10^${logMin.toFixed(0)}`;
    const highLabel = mode === 'rel' ? '3× increase' : `10^${logMax.toFixed(0)}`;
    const grad      = mode === 'rel'
        ? 'linear-gradient(to right,rgb(0,0,255),white,rgb(255,0,0))'
        : 'linear-gradient(to right,rgb(41,128,185),rgb(255,255,255),rgb(255,60,60))';
    legend.innerHTML = `
        <div style="font-size:9px;color:var(--text-dim);margin-bottom:4px">${mode === 'rel' ? 'Fold-change vs t=0' : 'Log₁₀ concentration'}</div>
        <div style="width:140px;height:8px;border-radius:2px;background:${grad}"></div>
        <div style="display:flex;justify-content:space-between;font-size:8px;color:var(--text-dim);margin-top:2px">
            <span>${lowLabel}</span><span>${highLabel}</span>
        </div>`;
}