/* SubstanceMapper — frontend logic */

// ── Navigation ──────────────────────────────────────────────────────────────
function showSection(name) {
  document.querySelectorAll('.section').forEach(s => {
    s.style.display = 'none';
    s.classList.remove('active');
  });
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));

  const sec = document.getElementById(name);
  if (sec) { sec.style.display = 'block'; sec.classList.add('active'); }

  const link = document.querySelector(`.nav-link[data-section="${name}"]`);
  if (link) link.classList.add('active');
}

document.querySelectorAll('.nav-link').forEach(link => {
  link.addEventListener('click', e => {
    e.preventDefault();
    showSection(link.dataset.section);
  });
});

document.getElementById('newJobBtn').addEventListener('click', () => {
  showSection('upload');
  document.getElementById('progressCard').style.display = 'none';
  document.getElementById('submitBtn').disabled = false;
  document.getElementById('submitBtn').textContent = '🚀 Start Matching';
});

// ── File drop zones ──────────────────────────────────────────────────────────
function initDropZone(zoneId, inputId, labelId) {
  const zone = document.getElementById(zoneId);
  const input = document.getElementById(inputId);
  const label = document.getElementById(labelId);

  zone.addEventListener('click', () => input.click());

  input.addEventListener('change', () => {
    if (input.files.length) {
      label.textContent = `✔ ${input.files[0].name}`;
      zone.classList.add('has-file');
    }
  });

  zone.addEventListener('dragover', e => { e.preventDefault(); zone.style.borderColor = 'var(--accent)'; });
  zone.addEventListener('dragleave', () => { zone.style.borderColor = ''; });
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.style.borderColor = '';
    const file = e.dataTransfer.files[0];
    if (file) {
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      label.textContent = `✔ ${file.name}`;
      zone.classList.add('has-file');
    }
  });
}

initDropZone('dataDropZone',   'data_file',   'data_file_label');
initDropZone('refDropZone',    'ref_file',    'ref_file_label');
initDropZone('lookupDropZone', 'lookup_file', 'lookup_file_label');

// ── Collapsible lookup section ───────────────────────────────────────────────
document.getElementById('lookupToggle').addEventListener('click', () => {
  const body = document.getElementById('lookupBody');
  const chev = document.querySelector('.chevron');
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  chev.textContent = open ? '▸' : '▾';
});

// ── Form submission ──────────────────────────────────────────────────────────
const form = document.getElementById('jobForm');
const errBox = document.getElementById('formError');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  errBox.style.display = 'none';

  // Basic validation
  const dataFile   = document.getElementById('data_file').files[0];
  const refFile    = document.getElementById('ref_file').files[0];
  const substCol   = document.getElementById('substance_col').value.trim();
  const refCol     = document.getElementById('ref_col').value.trim();

  if (!dataFile)   return showError('Please upload a data file.');
  if (!refFile)    return showError('Please upload a reference list.');
  if (!substCol)   return showError('Please specify the substance column name.');
  if (!refCol)     return showError('Please specify the reference column name.');

  const fd = new FormData(form);

  // Checkbox quirk — FormData only includes checked checkboxes
  if (!document.getElementById('only_first_match').checked) {
    fd.set('only_first_match', 'false');
  } else {
    fd.set('only_first_match', 'true');
  }

  const submitBtn = document.getElementById('submitBtn');
  submitBtn.disabled = true;
  submitBtn.textContent = '⏳ Submitting…';

  let jobId;
  try {
    const resp = await fetch('/submit', { method: 'POST', body: fd });
    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(txt || `Server error ${resp.status}`);
    }
    const data = await resp.json();
    jobId = data.job_id;
  } catch (err) {
    submitBtn.disabled = false;
    submitBtn.textContent = '🚀 Start Matching';
    return showError(`Submission failed: ${err.message}`);
  }

  // Show progress card
  const card = document.getElementById('progressCard');
  card.style.display = 'block';
  card.scrollIntoView({ behavior: 'smooth' });

  pollJob(jobId);
});

function showError(msg) {
  errBox.textContent = msg;
  errBox.style.display = 'block';
}

// ── Polling ──────────────────────────────────────────────────────────────────
async function pollJob(jobId) {
  const fill  = document.getElementById('progressFill');
  const pct   = document.getElementById('progressPct');
  const lbl   = document.getElementById('progressLabel');

  while (true) {
    await sleep(1200);
    let job;
    try {
      const r = await fetch(`/status/${jobId}`);
      job = await r.json();
    } catch { continue; }

    const { status, progress, total, error } = job;

    if (status === 'running' && total > 0) {
      const p = Math.round((progress / total) * 100);
      fill.style.width = p + '%';
      pct.textContent = p + '%';
      lbl.textContent = `Processing unique entries: ${progress} / ${total}`;
    } else if (status === 'queued') {
      lbl.textContent = 'Queued — waiting to start…';
    } else if (status === 'error') {
      lbl.textContent = '❌ Error: ' + error;
      pct.textContent = '';
      fill.style.background = 'var(--red)';
      document.getElementById('submitBtn').disabled = false;
      document.getElementById('submitBtn').textContent = '🚀 Start Matching';
      return;
    } else if (status === 'done') {
      fill.style.width = '100%';
      pct.textContent = '100%';
      lbl.textContent = '✅ Done!';
      await sleep(600);
      await showResults(jobId, job.stats);
      return;
    }
  }
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Results ──────────────────────────────────────────────────────────────────
async function showResults(jobId, stats) {
  // Download link
  document.getElementById('downloadLink').href = `/download/${jobId}`;

  // Stats cards
  renderStats(stats);

  // Similarity distribution chart
  if (stats.sim_distribution) {
    renderSimChart(stats.sim_distribution);
  }

  // Preview table
  try {
    const r = await fetch(`/preview/${jobId}?n=10`);
    const data = await r.json();
    renderPreview(data.columns, data.rows);
  } catch {}

  showSection('results');
}

function renderStats(s) {
  const grid = document.getElementById('statsGrid');
  const items = [
    { label: 'Total rows',         value: s.n_rows },
    { label: 'Unique inputs',      value: s.n_unique_input },
    { label: 'Extracted',          value: s.n_extracted },
    { label: 'Not extracted',      value: s.n_not_extracted },
    { label: '% extracted',        value: (s.pct_extracted ?? '—') + (s.pct_extracted != null ? '%' : '') },
    { label: 'Unique substances',  value: s.n_unique_extracted },
    { label: 'Mean similarity',    value: s.sim_mean ?? '—' },
    { label: 'Median similarity',  value: s.sim_median ?? '—' },
  ];
  grid.innerHTML = items.map(i =>
    `<div class="stat-card"><div class="stat-value">${i.value ?? '—'}</div><div class="stat-label">${i.label}</div></div>`
  ).join('');
}

function renderSimChart(dist) {
  const card = document.getElementById('simChartCard');
  const chart = document.getElementById('simBarChart');
  card.style.display = 'block';

  const total = Object.values(dist).reduce((a, b) => a + b, 0) || 1;
  chart.innerHTML = Object.entries(dist).map(([label, count]) => {
    const pct = Math.round((count / total) * 100);
    return `
      <div class="bar-row">
        <span class="bar-label">${label}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>
        <span class="bar-count">${count}</span>
      </div>`;
  }).join('');
}

function renderPreview(columns, rows) {
  const head = document.getElementById('previewHead');
  const body = document.getElementById('previewBody');

  head.innerHTML = '<tr>' + columns.map(c => `<th>${c}</th>`).join('') + '</tr>';

  // Find similarity column index for colour coding
  const simIdx = columns.findIndex(c => /similarity/i.test(c));

  body.innerHTML = rows.map(row => {
    const cells = row.map((cell, i) => {
      if (i === simIdx && cell !== '') {
        const v = parseFloat(cell);
        const cls = v >= 0.95 ? 'sim-high' : v >= 0.85 ? 'sim-mid' : 'sim-low';
        return `<td class="${cls}">${isNaN(v) ? cell : v.toFixed(3)}</td>`;
      }
      return `<td>${cell}</td>`;
    });
    return `<tr>${cells.join('')}</tr>`;
  }).join('');
}
