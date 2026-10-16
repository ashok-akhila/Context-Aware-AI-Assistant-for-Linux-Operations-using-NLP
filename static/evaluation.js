// ---------------- tab switching ----------------

const tabs = document.querySelectorAll('.eval-tab');
const panels = {
  dashboard: document.getElementById('tab-dashboard'),
  testcases: document.getElementById('tab-testcases'),
  review: document.getElementById('tab-review'),
};

tabs.forEach(tab => {
  tab.addEventListener('click', () => {
    tabs.forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    Object.values(panels).forEach(p => p.classList.add('hidden'));
    panels[tab.dataset.tab].classList.remove('hidden');

    if (tab.dataset.tab === 'dashboard') loadDashboard();
    if (tab.dataset.tab === 'testcases') loadTestCases();
    if (tab.dataset.tab === 'review') loadReviewQueue();
  });
});

// ---------------- shared state ----------------

let CONFIG = null;

async function loadConfig() {
  if (CONFIG) return CONFIG;
  const res = await fresh('/api/evaluation/config');
  CONFIG = await res.json();
  return CONFIG;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// Always bypass the browser cache for evaluation data — it changes after
// every run, and a cached GET is exactly how the Review Queue ends up
// showing an old list.
function fresh(url, opts) {
  return fetch(url, Object.assign({ cache: 'no-store' }, opts || {}));
}

// Results created during this page session — highlighted with a NEW badge
// in the Review Queue so the latest run is easy to spot.
const newResultIds = new Set();

function showToast(message, actionLabel, actionFn) {
  let toast = document.getElementById('eval-toast');
  if (!toast) {
    toast = el('div', 'eval-toast');
    toast.id = 'eval-toast';
    document.body.appendChild(toast);
  }
  toast.innerHTML = '';
  toast.appendChild(el('span', 'eval-toast-msg', message));
  if (actionLabel) {
    const btn = el('button', 'eval-toast-btn', actionLabel);
    btn.addEventListener('click', () => { toast.classList.remove('show'); actionFn(); });
    toast.appendChild(btn);
  }
  const close = el('button', 'eval-toast-close', '×');
  close.addEventListener('click', () => toast.classList.remove('show'));
  toast.appendChild(close);
  toast.classList.add('show');
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toast.classList.remove('show'), 15000);
}

function openReviewTab() {
  document.querySelector('.eval-tab[data-tab="review"]').click();
}

// Called after EVERY finished run (saved test case or ad-hoc query), whether
// it was scored automatically or not: remember it as new, reload the Review
// Queue right away so it is already up to date, then tell the person.
async function onRunFinished(data) {
  if (data && data.result_id) newResultIds.add(data.result_id);
  openReviewTab();                       // tab click reloads the queue
  try { await loadReviewQueue(); } catch (_) { /* polling retries shortly */ }
  window.scrollTo({ top: 0, behavior: 'smooth' });
  showToast(summarizeAutoEvalResult(data));
}

function classToCss(classification) {
  return 'cls-' + (classification || 'pending').toLowerCase().replace(/\s+/g, '-');
}

function pctText(value) {
  return (value !== null && value !== undefined) ? value + '%' : '—';
}

// ---------------- DASHBOARD ----------------
// Field names here match evaluation/metrics.py's full_dashboard_payload()
// exactly: total_evaluations_run, total_scored_evaluations,
// overall_system_evaluation_score, fully_successful, partially_successful,
// failed. The overall score is CUMULATIVE (sum of points across every
// scored result, divided once) — never an average of per-result percentages
// — and it's recomputed fresh from evaluation/results.json on every load,
// so it survives a Flask/WSL/browser restart with no special handling here.

async function loadDashboard() {
  const res = await fresh('/api/evaluation/metrics');
  const data = await res.json();
  const o = data.overall;

  document.getElementById('stat-total-run').textContent = o.total_evaluations_run;
  document.getElementById('stat-total-scored').textContent = o.total_scored_evaluations;
  document.getElementById('stat-overall').textContent = pctText(o.overall_system_evaluation_score);
  document.getElementById('stat-overall-sub').textContent =
    o.total_scored_evaluations > 0
      ? `Based on ${o.total_scored_evaluations} scored evaluation${o.total_scored_evaluations === 1 ? '' : 's'} (cumulative)`
      : '';
  document.getElementById('stat-fully').textContent = o.fully_successful;
  document.getElementById('stat-partial').textContent = o.partially_successful;
  document.getElementById('stat-failed').textContent = o.failed;
  document.getElementById('stat-safety').textContent = pctText(data.safety_handling_score);
  document.getElementById('stat-context').textContent = pctText(data.context_correctness_score);

  const emptyNote = document.getElementById('dashboard-empty-note');
  emptyNote.textContent = 'No scored evaluations yet. Create or run a query below, then score it in the Review Queue to populate this dashboard.';
  emptyNote.style.display = o.total_scored_evaluations === 0 ? 'block' : 'none';

  renderPieChart(o);
  renderBarChart('bar-chart-wrap', data.by_category, key => key, 'No categories scored yet.');
  renderBarChart('criterion-chart-wrap', data.by_criterion, val => val.label, 'No criteria scored yet.', true);
}

function renderPieChart(overall) {
  const wrap = document.getElementById('pie-chart-wrap');
  const legend = document.getElementById('pie-legend');
  wrap.innerHTML = '';
  legend.innerHTML = '';

  const total = overall.fully_successful + overall.partially_successful + overall.failed;
  const segments = [
    { label: 'Fully successful', value: overall.fully_successful, color: '#4fd18a' },
    { label: 'Partially successful', value: overall.partially_successful, color: '#e0a83c' },
    { label: 'Failed', value: overall.failed, color: '#e0567a' },
  ];

  if (total === 0) {
    wrap.innerHTML = '<div class="empty-note" style="padding:20px 0;">No scored evaluations yet.</div>';
    return;
  }

  let cumulative = 0;
  const stops = segments.map(s => {
    const start = (cumulative / total) * 360;
    cumulative += s.value;
    const end = (cumulative / total) * 360;
    return `${s.color} ${start}deg ${end}deg`;
  }).join(', ');

  const circle = el('div');
  circle.style.width = '170px';
  circle.style.height = '170px';
  circle.style.borderRadius = '50%';
  circle.style.margin = '0 auto';
  circle.style.background = `conic-gradient(${stops})`;
  wrap.appendChild(circle);

  segments.forEach(s => {
    const item = el('span');
    const swatch = el('span', 'legend-swatch');
    swatch.style.background = s.color;
    item.appendChild(swatch);
    item.appendChild(document.createTextNode(`${s.label} (${s.value})`));
    legend.appendChild(item);
  });
}

function renderBarChart(containerId, dataObj, labelFn, emptyText, isCriterion) {
  const wrap = document.getElementById(containerId);
  wrap.innerHTML = '';
  const entries = Object.entries(dataObj || {});

  if (entries.length === 0) {
    wrap.innerHTML = `<div class="empty-note" style="padding:12px 0;">${emptyText}</div>`;
    return;
  }

  entries.forEach(([key, val]) => {
    const label = labelFn(isCriterion ? val : key);
    const pct = val.score_percent;
    const row = el('div', 'bar-row');
    row.appendChild(el('div', 'bar-row-label', label));
    const track = el('div', 'bar-row-track');
    const fill = el('div', 'bar-row-fill');
    fill.style.width = (pct || 0) + '%';
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el('div', 'bar-row-value', pct !== null && pct !== undefined ? pct + '%' : 'N/A'));
    wrap.appendChild(row);
  });
}

// ---------------- TEST CASES + ARBITRARY QUERY EVALUATION ----------------

async function populateCategoryDropdowns() {
  const config = await loadConfig();
  [document.getElementById('new-tc-category'), document.getElementById('quick-eval-category')].forEach(select => {
    if (!select) return;
    select.innerHTML = '';
    config.categories.forEach(cat => {
      const opt = el('option');
      opt.value = cat;
      opt.textContent = cat;
      select.appendChild(opt);
    });
  });
}

async function loadTestCases() {
  await populateCategoryDropdowns();
  const res = await fresh('/api/evaluation/test-cases');
  const testCases = await res.json();
  const list = document.getElementById('test-case-list');
  list.innerHTML = '';

  if (testCases.length === 0) {
    list.innerHTML = '<div class="empty-list-note">No test cases yet — add one above.</div>';
    return;
  }

  const tpl = document.getElementById('tpl-tc-row');
  testCases.slice().reverse().forEach(tc => {
    const node = tpl.content.cloneNode(true);
    node.querySelector('.tc-category').textContent = tc.category;
    node.querySelector('.tc-query').textContent = tc.user_query;

    node.querySelector('.tc-run-btn').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.textContent = 'Running…';
      let finalLabel = 'Retry';

      // Never wait forever: abort the request after 3 minutes.
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 180000);

      try {
        const res = await fetch(`/api/evaluation/run/${tc.test_id}`, {
          method: 'POST',
          signal: controller.signal,
        });

        // The server can answer with a non-JSON body (e.g. an HTML 500 page).
        // Don't let that throw silently — treat it as a normal error.
        let data = null;
        try { data = await res.json(); } catch (_) { /* not JSON */ }

        if (!res.ok || !data) {
          alert((data && data.error) ||
            `The server failed while running this test case (HTTP ${res.status}). ` +
            `Check the terminal running app.py for the error details.`);
        } else {
          finalLabel = 'Run again';
          await onRunFinished(data);
        }
      } catch (err) {
        if (err.name === 'AbortError') {
          alert('This test case did not finish within 3 minutes, so the request was cancelled. ' +
                'Check the terminal running app.py, then try again.');
        } else {
          alert('Could not reach the server: ' + err.message);
        }
      } finally {
        clearTimeout(timer);
        btn.disabled = false;
        btn.textContent = finalLabel;
      }
    });

    node.querySelector('.tc-delete-btn').addEventListener('click', async () => {
      if (!confirm('Delete this test case?')) return;
      await fetch(`/api/evaluation/test-cases/${tc.test_id}`, { method: 'DELETE' });
      loadTestCases();
    });

    list.appendChild(node);
  });
}

document.getElementById('new-tc-submit').addEventListener('click', async () => {
  const category = document.getElementById('new-tc-category').value;
  const query = document.getElementById('new-tc-query').value.trim();
  if (!query) return;

  const res = await fetch('/api/evaluation/test-cases', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category, user_query: query }),
  });
  const data = await res.json();
  if (res.ok) {
    document.getElementById('new-tc-query').value = '';
    if (data.duplicate) {
      alert(data.message || 'Test case already exists.');
    }
    loadTestCases();
  } else {
    alert(data.error || 'Failed to create test case.');
  }
});

// Arbitrary-query evaluation — POST /api/evaluation/run-query. Runs the
// query through the exact same assistant pipeline, no test case required,
// then automatically evaluates it — see summarizeAutoEvalResult() below.
function summarizeAutoEvalResult(data) {
  if (data.evaluation_error) {
    return `Query run recorded, but automatic evaluation could not complete: ${data.evaluation_error}\nYou can retry it from the Review Queue tab.`;
  }
  if (data.scored) {
    return `Automatically evaluated: ${data.total_score}/${data.applicable_points} applicable points ` +
           `(${data.percentage}%) · ${data.classification}`;
  }
  return 'Query evaluated.';
}

const quickEvalBtn = document.getElementById('quick-eval-submit');
if (quickEvalBtn) {
  quickEvalBtn.addEventListener('click', async () => {
    const category = document.getElementById('quick-eval-category').value;
    const query = document.getElementById('quick-eval-query').value.trim();
    if (!query) return;

    quickEvalBtn.disabled = true;
    quickEvalBtn.textContent = 'Running…';
    try {
      const res = await fetch('/api/evaluation/run-query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category, user_query: query }),
      });
      let data = null;
      try { data = await res.json(); } catch (_) { /* non-JSON error page */ }
      if (!data) {
        alert(`The server failed while running this query (HTTP ${res.status}). Check the terminal running app.py.`);
      } else if (res.ok) {
        document.getElementById('quick-eval-query').value = '';
        await onRunFinished(data);
      } else {
        alert(data.error || 'Failed to evaluate query.');
      }
    } finally {
      quickEvalBtn.disabled = false;
      quickEvalBtn.textContent = 'Run Evaluation';
    }
  });
}

// ---------------- REVIEW QUEUE ----------------

const SCORED_PAGE_SIZE = 15;
let scoredVisible = SCORED_PAGE_SIZE;
let lastSignature = null;
let reviewLoadCounter = 0;

async function loadReviewQueue() {
  const myLoad = ++reviewLoadCounter;
  const config = await loadConfig();
  const [pendingRes, scoredRes, statusRes] = await Promise.all([
    fresh('/api/evaluation/results?scored=false'),
    fresh('/api/evaluation/results?scored=true'),
    fresh('/api/evaluation/results-status'),
  ]);
  const pendingAll = await pendingRes.json();
  const scoredAll = await scoredRes.json();
  const status = await statusRes.json();

  // A newer load started while this one was in flight — drop this stale one.
  if (myLoad !== reviewLoadCounter) return;

  lastSignature = status.signature;

  // The newest result overall goes in its own "Latest run" card at the very
  // top, so a fresh run is never buried under older stuck/scored items.
  const everything = pendingAll.concat(scoredAll).sort((a, b) => (b.run_at || 0) - (a.run_at || 0));
  const latest = everything.length ? everything[0] : null;
  if (latest) {
    renderResultList('latest-list', [latest], config, !!latest.scored, { expanded: true });
  } else {
    document.getElementById('latest-list').innerHTML = '<div class="empty-list-note">No runs yet — run a test case or an arbitrary query.</div>';
  }
  const pending = pendingAll.filter(r => !latest || r.result_id !== latest.result_id);
  const scored = scoredAll.filter(r => !latest || r.result_id !== latest.result_id);

  renderResultList('pending-list', pending, config, false);

  // Only render the newest scored results; rendering all of them (each with
  // a large context dump) made the page heavy and slow to update.
  renderResultList('scored-list', scored.slice(0, scoredVisible), config, true);
  if (scored.length > scoredVisible) {
    const more = el('button', 'show-more-btn',
      `Show ${Math.min(SCORED_PAGE_SIZE, scored.length - scoredVisible)} more (${scored.length - scoredVisible} older)`);
    more.addEventListener('click', () => { scoredVisible += SCORED_PAGE_SIZE; loadReviewQueue(); });
    document.getElementById('scored-list').appendChild(more);
  }

  const failedCount = pendingAll.filter(r => r.evaluation_error).length;
  const retryAll = document.getElementById('retry-all-btn');
  if (retryAll) {
    retryAll.style.display = failedCount > 0 ? 'inline-block' : 'none';
    retryAll.textContent = `Retry ${failedCount} failed evaluation${failedCount === 1 ? '' : 's'}`;
  }
  const updated = document.getElementById('review-updated');
  if (updated) updated.textContent = `Updated ${new Date().toLocaleTimeString()} · ${status.total} results, ${status.pending} need attention`;
}

// True while the person is typing a note / changing a dropdown inside the
// Review Queue — we must not wipe their unsaved override by re-rendering.
function isEditingReview() {
  const a = document.activeElement;
  return !!(a && panels.review.contains(a) && /^(TEXTAREA|SELECT|INPUT)$/.test(a.tagName));
}

// Auto-refresh: while the Review Queue is open, check a tiny status
// endpoint every few seconds and reload only if something actually changed
// (new run finished, a score saved, a retry completed — including runs
// started from another browser tab).
async function pollReviewStatus() {
  if (document.hidden || panels.review.classList.contains('hidden')) return;
  try {
    const res = await fresh('/api/evaluation/results-status');
    if (!res.ok) return;
    const status = await res.json();
    if (status.signature === lastSignature) return;
    if (isEditingReview()) {
      const updated = document.getElementById('review-updated');
      if (updated) updated.textContent = 'New results available — click Refresh when you finish editing.';
      return;
    }
    await loadReviewQueue();
  } catch (_) { /* transient network error — try again on the next tick */ }
}
setInterval(pollReviewStatus, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) pollReviewStatus(); });

const refreshBtn = document.getElementById('review-refresh-btn');
if (refreshBtn) refreshBtn.addEventListener('click', () => loadReviewQueue());

const retryAllBtn = document.getElementById('retry-all-btn');
if (retryAllBtn) {
  retryAllBtn.addEventListener('click', async () => {
    retryAllBtn.disabled = true;
    try {
      const res = await fresh('/api/evaluation/results?scored=false');
      const pending = (await res.json()).filter(r => r.evaluation_error);
      for (let i = 0; i < pending.length; i++) {
        retryAllBtn.textContent = `Retrying ${i + 1} of ${pending.length}…`;
        try {
          await fetch(`/api/evaluation/results/${pending[i].result_id}/auto-evaluate`, { method: 'POST' });
        } catch (_) { /* leave it in the queue; keep going */ }
      }
    } finally {
      retryAllBtn.disabled = false;
      await loadReviewQueue();
    }
  });
}

function evalModeLabel(mode) {
  if (mode === 'automatic') return 'Automatic';
  if (mode === 'manual_override') return 'Manual override';
  if (mode === 'manual') return 'Manual';
  return 'Unscored';
}

function renderResultList(containerId, results, config, alreadyScored, opts) {
  opts = opts || {};
  const container = document.getElementById(containerId);
  container.innerHTML = '';

  if (results.length === 0) {
    container.innerHTML = `<div class="empty-list-note">${alreadyScored ? 'No scored results yet.' : 'Nothing needs attention right now.'}</div>`;
    return;
  }

  const tpl = document.getElementById('tpl-result-card');
  results.forEach(result => {
    const node = tpl.content.cloneNode(true);

    node.querySelector('.result-category').textContent = result.category;

    // Collapsed by default (except the Latest card) so old results don't push
    // the newest one off-screen.
    const cardEl = node.querySelector('.result-card');
    const toggleBtn = node.querySelector('.toggle-details-btn');
    if (!opts.expanded) cardEl.classList.add('collapsed');
    toggleBtn.textContent = opts.expanded ? 'Hide details' : 'Show details';
    toggleBtn.addEventListener('click', () => {
      const nowCollapsed = cardEl.classList.toggle('collapsed');
      toggleBtn.textContent = nowCollapsed ? 'Show details' : 'Hide details';
    });

    node.querySelector('.delete-result-btn').addEventListener('click', async () => {
      if (!confirm('Delete this result permanently? It will no longer count toward any score.')) return;
      try {
        const r = await fetch(`/api/evaluation/results/${result.result_id}`, { method: 'DELETE' });
        if (!r.ok) { const d = await r.json().catch(() => ({})); alert(d.error || 'Delete failed.'); return; }
        newResultIds.delete(result.result_id);
        await loadReviewQueue();
      } catch (err) { alert('Delete failed: ' + err); }
    });

    if (newResultIds.has(result.result_id)) {
      node.querySelector('.result-card').classList.add('result-new');
      node.querySelector('.result-card-head').appendChild(el('span', 'new-badge', 'NEW'));
    }

    const modeBadge = node.querySelector('.eval-mode-badge');
    modeBadge.textContent = evalModeLabel(result.evaluation_mode);
    modeBadge.classList.add('mode-' + (result.evaluation_mode || 'unscored').replace(/_/g, '-'));

    const clsBadge = node.querySelector('.result-classification');
    clsBadge.textContent = result.classification || 'pending review';
    clsBadge.classList.add(classToCss(result.classification));

    node.querySelector('.result-query').textContent = result.user_query;
    node.querySelector('.result-context').textContent =
      JSON.stringify(result.context_collected, null, 2);

    const responseText = result.llm_result
      ? JSON.stringify(result.llm_result, null, 2)
      : '(no LLM response — see error below)';
    node.querySelector('.result-response').textContent = responseText;

    if (result.backend_validation) {
      const section = node.querySelector('.result-validation-section');
      section.style.display = 'block';
      node.querySelector('.result-validation').textContent =
        JSON.stringify(result.backend_validation, null, 2);
    }

    if (result.pipeline_error) {
      const section = node.querySelector('.result-error-section');
      section.style.display = 'block';
      node.querySelector('.result-error').textContent = result.pipeline_error;
    }

    if (result.evaluation_error) {
      const section = node.querySelector('.result-eval-error-section');
      section.style.display = 'block';
      node.querySelector('.result-eval-error').textContent = result.evaluation_error;
      node.querySelector('.retry-eval-btn').addEventListener('click', async (e) => {
        e.target.disabled = true;
        e.target.textContent = 'Retrying…';
        try {
          const res = await fetch(`/api/evaluation/results/${result.result_id}/auto-evaluate`, { method: 'POST' });
          const data = await res.json();
          if (res.ok) {
            loadReviewQueue();
          } else {
            alert(data.error || 'Retry failed.');
            e.target.disabled = false;
            e.target.textContent = 'Retry automatic evaluation';
          }
        } catch (err) {
          e.target.disabled = false;
          e.target.textContent = 'Retry automatic evaluation';
        }
      });
    }

    const criteriaGrid = node.querySelector('.criteria-grid');
    const criterionTpl = document.getElementById('tpl-criterion-row');
    const selects = {};
    const evidenceByKey = (result.criteria || {});

    Object.entries(config.criteria).forEach(([key, meta]) => {
      const critNode = criterionTpl.content.cloneNode(true);
      critNode.querySelector('.criterion-label').textContent = meta.label;
      const select = critNode.querySelector('.criterion-select');
      select.dataset.key = key;
      if (result.criterion_statuses && result.criterion_statuses[key]) {
        select.value = result.criterion_statuses[key];
      }
      const evidenceEl = critNode.querySelector('.criterion-evidence');
      const evidenceText = evidenceByKey[key] && evidenceByKey[key].evidence;
      if (evidenceText) {
        evidenceEl.textContent = evidenceText;
      } else {
        evidenceEl.remove();
      }
      selects[key] = select;
      criteriaGrid.appendChild(critNode);
    });

    const notesInput = node.querySelector('.notes-input');
    notesInput.value = result.evaluator_notes || '';

    const saveBtn = node.querySelector('.save-eval-btn');
    if (result.evaluation_mode === 'automatic') {
      saveBtn.textContent = 'Override & Save';
    }

    const scoreReadout = node.querySelector('.score-readout');
    if (result.scored) {
      scoreReadout.textContent = `${result.total_score}/${result.applicable_points} applicable points (${result.percentage}%) · ${result.classification}`;
    }

    saveBtn.addEventListener('click', async (e) => {
      const criterion_statuses = {};
      Object.entries(selects).forEach(([key, sel]) => { criterion_statuses[key] = sel.value; });

      e.target.disabled = true;
      const res = await fetch(`/api/evaluation/results/${result.result_id}/score`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ criterion_statuses, evaluator_notes: notesInput.value }),
      });
      const updated = await res.json();
      e.target.disabled = false;

      if (res.ok) {
        scoreReadout.textContent = `${updated.total_score}/${updated.applicable_points} applicable points (${updated.percentage}%) · ${updated.classification}`;
        clsBadge.className = 'result-classification ' + classToCss(updated.classification);
        clsBadge.textContent = updated.classification;
        modeBadge.textContent = evalModeLabel(updated.evaluation_mode);
        modeBadge.className = 'eval-mode-badge mode-' + (updated.evaluation_mode || 'unscored').replace(/_/g, '-');
        loadReviewQueue(); // moves it from pending to scored list, and the next
                            // Dashboard visit will reflect the new cumulative total
      } else {
        alert(updated.error || 'Failed to save evaluation.');
      }
    });

    container.appendChild(node);
  });
}

// ---------------- init ----------------

loadDashboard();
