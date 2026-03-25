const API_BASE = window.location.origin;
let useAI = false;
let history = JSON.parse(localStorage.getItem('bp_history') || '[]');
let accessKey = localStorage.getItem('bp_access_key') || '';

// --- Init ---
if (accessKey) {
  showApp();
} else {
  document.getElementById('access-gate').style.display = '';
  document.getElementById('access-key-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') submitAccessKey();
  });
}

function submitAccessKey() {
  const key = document.getElementById('access-key-input').value.trim();
  if (!key) return;
  accessKey = key;
  localStorage.setItem('bp_access_key', key);
  showApp();
}

function showApp() {
  document.getElementById('access-gate').style.display = 'none';
  document.getElementById('main-container').style.display = '';
  checkHealth();
  renderHistory();
  detectTab();
  document.getElementById('url-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') runParse();
  });
}

// --- Health check ---
async function checkHealth() {
  try {
    const r = await fetch(`${API_BASE}/health`);
    const ok = r.ok;
    document.getElementById('status-dot').className = 'dot ' + (ok ? 'online' : 'offline');
    document.getElementById('status-text').textContent = ok ? 'API online' : 'API offline';
  } catch {
    document.getElementById('status-dot').className = 'dot offline';
    document.getElementById('status-text').textContent = 'API offline';
  }
}

// --- Toggle AI ---
function toggleAI() {
  useAI = !useAI;
  document.getElementById('ai-toggle').className = 'toggle ' + (useAI ? 'on' : '');
}

// --- Parse ---
async function runParse() {
  const url = document.getElementById('url-input').value.trim();
  if (!url) return;

  const btn = document.getElementById('parse-btn');
  btn.disabled = true;
  btn.textContent = '...';

  document.getElementById('result-section').innerHTML = `
    <div class="loading-box">
      <div class="spinner"></div>
      <span>Загружаем <span style="color:var(--accent);font-family:'DM Mono',monospace">${esc(url)}</span></span>
    </div>`;

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120000);
    const resp = await fetch(`${API_BASE}/parse`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, use_ai: useAI, access_key: accessKey }),
      signal: controller.signal,
    });
    clearTimeout(timeout);

    const data = await resp.json();

    if (!resp.ok) {
      if (resp.status === 403 && (data.detail || '').includes('ключ')) {
        localStorage.removeItem('bp_access_key');
        accessKey = '';
        document.getElementById('main-container').style.display = 'none';
        document.getElementById('access-gate').style.display = '';
        document.getElementById('access-error').textContent = 'Неверный ключ доступа';
        return;
      }
      showError(data.detail || 'Неизвестная ошибка');
      return;
    }

    renderResult(data);
    addToHistory(data);

  } catch (e) {
    showError('Не удалось подключиться к API. Запущен ли сервер?');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Парсить';
  }
}

// --- Render result ---
function renderResult(data) {
  const isAI = data.method.includes('ai');
  const socialsHTML = data.socials.length > 0
    ? data.socials.map(s => `
        <div class="social-row">
          <span class="social-name">${esc(s.platform)}</span>
          ${s.is_bot === true ? '<span class="bot-badge">bot</span>' : ''}
          <a class="social-link" href="${esc(s.url)}" target="_blank">${esc(s.url)}</a>
          ${s.followers != null ? `<span class="social-followers">${formatFollowers(s.followers)}</span>` : ''}
        </div>`).join('')
    : '<div class="no-data">Соцсети не найдены</div>';

  document.getElementById('result-section').innerHTML = `
    <div class="result-card">
      <div class="result-header">
        <a class="result-url" href="${esc(data.url)}" target="_blank">${esc(data.url)}</a>
        <span class="method-badge ${isAI ? 'ai' : ''}">${esc(data.method)}</span>
      </div>
      <div class="result-body">
        <div>
          <div class="section-label">Описание бренда</div>
          ${data.description
            ? `<div class="description-text">${esc(data.description)}</div>`
            : '<div class="no-data">Описание не найдено</div>'}
        </div>
        <div>
          <div class="section-label">Соцсети · ${data.socials.length}</div>
          <div class="socials-grid">${socialsHTML}</div>
        </div>
      </div>
      <div class="result-export-bar">
        <button class="export-btn" onclick="exportResultCSV()">↓ CSV</button>
        <button class="export-btn" onclick="exportResultJSON()">↓ JSON</button>
      </div>
    </div>`;

  window._lastResult = data;
}

function showError(msg) {
  document.getElementById('result-section').innerHTML =
    `<div class="error-box">✗ ${esc(msg)}</div>`;
}

// --- History ---
function addToHistory(data) {
  history.unshift({
    url: data.url,
    description: data.description,
    socials: data.socials,
    method: data.method,
    ts: Date.now(),
  });
  if (history.length > 50) history = history.slice(0, 50);
  saveHistory();
  renderHistory();
}

function addCompareToHistory(results) {
  history.unshift({
    type: 'compare',
    results: results.map(r => ({ url: r.url, socials: r.socials, method: r.method })),
    ts: Date.now(),
  });
  if (history.length > 50) history = history.slice(0, 50);
  saveHistory();
  renderHistory();
}

function renderHistory() {
  const list = document.getElementById('history-list');
  const exportCSV = document.getElementById('export-csv-btn');
  const exportJSON = document.getElementById('export-json-btn');

  if (history.length === 0) {
    list.innerHTML = '<div class="empty-history">// история пуста</div>';
    exportCSV.disabled = true;
    exportJSON.disabled = true;
    return;
  }

  exportCSV.disabled = false;
  exportJSON.disabled = false;

  list.innerHTML = history.map((item, i) => {
    const date = new Date(item.ts).toLocaleString('ru', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
    });
    if (item.type === 'compare') {
      const domains = item.results.map(r => {
        try { return new URL(r.url).hostname.replace('www.', ''); } catch { return r.url; }
      });
      return `
        <div class="history-item" onclick="loadFromHistory(${i})">
          <div class="history-dot compare-dot"></div>
          <span class="history-url">${esc(domains.join(' vs '))}</span>
          <span class="history-socials-count">сравнение</span>
          <span class="history-meta">${esc(date)}</span>
        </div>`;
    }
    return `
      <div class="history-item" onclick="loadFromHistory(${i})">
        <div class="history-dot"></div>
        <span class="history-url">${esc(item.url)}</span>
        <span class="history-socials-count">${item.socials.length} соцс.</span>
        <span class="history-meta">${esc(date)}</span>
      </div>`;
  }).join('');
}

function loadFromHistory(i) {
  const item = history[i];
  if (item.type === 'compare') {
    switchTab('compare');
    item.results.forEach((r, idx) => {
      document.getElementById(`compare-url-${idx + 1}`).value = r.url;
    });
    for (let j = item.results.length + 1; j <= 4; j++) {
      document.getElementById(`compare-url-${j}`).value = '';
    }
    renderCompareTable(item.results);
  } else {
    switchTab('parse');
    document.getElementById('url-input').value = item.url;
    renderResult(item);
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function clearHistory() {
  history = [];
  saveHistory();
  renderHistory();
  document.getElementById('result-section').innerHTML = '';
  document.getElementById('compare-result').innerHTML = '';
}

function saveHistory() {
  localStorage.setItem('bp_history', JSON.stringify(history));
}

// --- Export single result ---
function exportResultCSV() {
  const item = window._lastResult;
  if (!item) return;
  const rows = [['URL', 'Описание', 'Платформа', 'Ссылка', 'Подписчики', 'Бот', 'Метод']];
  if (item.socials.length === 0) {
    rows.push([item.url, item.description || '', '', '', '', '', item.method]);
  } else {
    item.socials.forEach(s => {
      const botLabel = s.is_bot === true ? 'да' : s.is_bot === false ? 'нет' : '';
      rows.push([item.url, item.description || '', s.platform, s.url, s.followers ?? '', botLabel, item.method]);
    });
  }
  const csv = rows.map(r =>
    r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')
  ).join('\n');
  const domain = new URL(item.url).hostname.replace('www.', '');
  download('\uFEFF' + csv, `${domain}.csv`, 'text/csv');
}

function exportResultJSON() {
  const item = window._lastResult;
  if (!item) return;
  const domain = new URL(item.url).hostname.replace('www.', '');
  download(JSON.stringify(item, null, 2), `${domain}.json`, 'application/json');
}

// --- Export history ---
function exportCSV() {
  const rows = [['URL', 'Описание', 'Платформа', 'Ссылка', 'Подписчики', 'Бот', 'Метод', 'Дата']];
  history.forEach(item => {
    const date = new Date(item.ts).toLocaleString('ru');
    if (item.socials.length === 0) {
      rows.push([item.url, item.description || '', '', '', '', '', item.method, date]);
    } else {
      item.socials.forEach(s => {
        const botLabel = s.is_bot === true ? 'да' : s.is_bot === false ? 'нет' : '';
        rows.push([item.url, item.description || '', s.platform, s.url, s.followers ?? '', botLabel, item.method, date]);
      });
    }
  });

  const csv = rows.map(r =>
    r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')
  ).join('\n');

  download('\uFEFF' + csv, 'brand-parser-export.csv', 'text/csv');
}

function exportJSON() {
  download(JSON.stringify(history, null, 2), 'brand-parser-export.json', 'application/json');
}

function formatFollowers(n) {
  return n.toLocaleString('ru-RU');
}

function esc(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function download(content, filename, type) {
  const blob = new Blob([content], { type });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}

// --- Tabs ---
function switchTab(tab, updateHash) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-parse').style.display = tab === 'parse' ? '' : 'none';
  document.getElementById('tab-compare').style.display = tab === 'compare' ? '' : 'none';
  document.querySelectorAll('.tab')[tab === 'parse' ? 0 : 1].classList.add('active');
  // Sync AI toggle visual
  document.getElementById('ai-toggle-compare').className = 'toggle ' + (useAI ? 'on' : '');
  if (updateHash !== false) {
    window.history.replaceState(null, '', tab === 'compare' ? '/compare' : '/');
  }
}

// Detect initial tab from URL
function detectTab() {
  const path = window.location.pathname;
  if (path === '/compare') switchTab('compare', false);
}

// Handle browser back/forward
window.addEventListener('popstate', () => {
  detectTab();
});

// --- Toggle AI (updated for compare tab) ---
const _origToggleAI = toggleAI;
toggleAI = function() {
  useAI = !useAI;
  document.getElementById('ai-toggle').className = 'toggle ' + (useAI ? 'on' : '');
  document.getElementById('ai-toggle-compare').className = 'toggle ' + (useAI ? 'on' : '');
};

// --- Compare ---
async function runCompare() {
  const urls = [];
  for (let i = 1; i <= 4; i++) {
    const v = document.getElementById(`compare-url-${i}`).value.trim();
    if (v) urls.push(v);
  }
  if (urls.length < 2) {
    document.getElementById('compare-result').innerHTML =
      '<div class="error-box">Введите минимум 2 URL для сравнения</div>';
    return;
  }

  // Check for duplicates by domain
  const domains = urls.map(u => {
    try { return new URL(u.startsWith('http') ? u : 'https://' + u).hostname.replace('www.', ''); } catch { return u; }
  });
  const seen = new Set();
  for (const d of domains) {
    if (seen.has(d)) {
      document.getElementById('compare-result').innerHTML =
        `<div class="error-box">Домен ${esc(d)} указан дважды</div>`;
      return;
    }
    seen.add(d);
  }

  const btn = document.getElementById('compare-btn');
  btn.disabled = true;
  btn.textContent = '...';

  document.getElementById('compare-result').innerHTML = `
    <div class="loading-box">
      <div class="spinner"></div>
      <span>Сравниваем ${urls.length} сайта...</span>
    </div>`;

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 180000);
    const resp = await fetch(`${API_BASE}/parse/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls, use_ai: useAI, access_key: accessKey }),
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!resp.ok) {
      const data = await resp.json();
      if (resp.status === 403 && (data.detail || '').includes('ключ')) {
        localStorage.removeItem('bp_access_key');
        accessKey = '';
        document.getElementById('main-container').style.display = 'none';
        document.getElementById('access-gate').style.display = '';
        document.getElementById('access-error').textContent = 'Неверный ключ доступа';
        return;
      }
      document.getElementById('compare-result').innerHTML =
        `<div class="error-box">${esc(data.detail || 'Ошибка')}</div>`;
      return;
    }

    const results = await resp.json();
    renderCompareTable(results);
    addCompareToHistory(results);

  } catch (e) {
    document.getElementById('compare-result').innerHTML =
      '<div class="error-box">Не удалось подключиться к API</div>';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Сравнить';
  }
}

function renderCompareTable(results) {
  // Group socials by platform for each result
  const grouped = results.map(r => {
    const map = {};
    r.socials.forEach(s => {
      if (!map[s.platform]) map[s.platform] = [];
      map[s.platform].push(s);
    });
    return map;
  });

  // Collect all platforms in order, determine max entries per platform
  const platformOrder = [];
  const platformMax = {};
  results.forEach(r => {
    r.socials.forEach(s => {
      if (!platformMax[s.platform]) {
        platformOrder.push(s.platform);
        platformMax[s.platform] = 0;
      }
    });
  });
  // dedupe platformOrder
  const platforms = [...new Set(platformOrder)];
  platforms.forEach(p => {
    grouped.forEach(g => {
      platformMax[p] = Math.max(platformMax[p], (g[p] || []).length);
    });
  });

  // Build brand labels from URL domains
  const brands = results.map(r => {
    try { return new URL(r.url).hostname.replace('www.', ''); } catch { return r.url; }
  });

  const colCount = brands.length;

  // Header row
  let html = '<div class="compare-table-wrap"><table class="compare-table"><thead><tr><th>Соцсеть</th>';
  brands.forEach(b => { html += `<th>${esc(b)}</th>`; });
  html += '</tr></thead><tbody>';

  // Platform rows — one row per entry (multiple rows if any brand has duplicates)
  const sums = results.map(() => 0);
  platforms.forEach(p => {
    const maxCount = platformMax[p];
    for (let idx = 0; idx < maxCount; idx++) {
      html += '<tr>';
      if (idx === 0 && maxCount > 1) {
        html += `<td rowspan="${maxCount}">${esc(p)}</td>`;
      } else if (idx === 0) {
        html += `<td>${esc(p)}</td>`;
      }
      grouped.forEach((g, i) => {
        const entries = g[p] || [];
        const s = entries[idx];
        if (s) {
          const f = s.followers;
          if (f != null) sums[i] += f;
          const bot = s.is_bot === true ? ' <span class="bot-badge">bot</span>' : '';
          const label = f != null ? formatFollowers(f) : 'да';
          html += `<td><a class="compare-cell-link" href="${esc(s.url)}" target="_blank" title="${esc(s.url)}">${label}</a>${bot}</td>`;
        } else if (results[i].method === 'error' || results[i].socials.length === 0) {
          html += '<td style="color:var(--danger)">Блокировка данных</td>';
        } else {
          html += '<td style="color:var(--text3)">нет</td>';
        }
      });
      html += '</tr>';
    }
  });

  // Count row
  html += '<tr class="compare-summary-row"><td>Кол-во соцсетей</td>';
  results.forEach(r => {
    html += (r.method === 'error' || r.socials.length === 0)
      ? '<td style="color:var(--danger)">—</td>'
      : `<td>${r.socials.length}</td>`;
  });
  html += '</tr>';

  // Sum row
  html += '<tr class="compare-summary-row"><td>Всего подписчиков</td>';
  results.forEach((r, i) => {
    html += (r.method === 'error' || r.socials.length === 0)
      ? '<td style="color:var(--danger)">—</td>'
      : `<td>${formatFollowers(sums[i])}</td>`;
  });
  html += '</tr>';

  html += '</tbody></table></div>';

  html += `<div class="export-bar" style="margin-top:16px;">
    <button class="export-btn" onclick="exportCompareCSV()">↓ CSV</button>
    <button class="export-btn" onclick="exportCompareJSON()">↓ JSON</button>
  </div>`;

  document.getElementById('compare-result').innerHTML = html;
  window._lastCompare = results;
}

function exportCompareCSV() {
  const results = window._lastCompare;
  if (!results) return;
  const brands = results.map(r => {
    try { return new URL(r.url).hostname.replace('www.', ''); } catch { return r.url; }
  });

  // Header
  const header = ['Платформа'];
  brands.forEach(b => { header.push(b + ' — Ссылка', b + ' — Подписчики'); });
  const rows = [header];

  // All platforms
  const allPlatforms = [];
  results.forEach(r => r.socials.forEach(s => {
    if (!allPlatforms.includes(s.platform)) allPlatforms.push(s.platform);
  }));

  // Group
  const grouped = results.map(r => {
    const map = {};
    r.socials.forEach(s => {
      if (!map[s.platform]) map[s.platform] = [];
      map[s.platform].push(s);
    });
    return map;
  });

  allPlatforms.forEach(p => {
    const maxCount = Math.max(...grouped.map(g => (g[p] || []).length));
    for (let idx = 0; idx < maxCount; idx++) {
      const row = [p + (idx > 0 ? ` (${idx + 1})` : '')];
      grouped.forEach(g => {
        const s = (g[p] || [])[idx];
        row.push(s ? s.url : '', s && s.followers != null ? s.followers : '');
      });
      rows.push(row);
    }
  });

  // Summary
  const countRow = ['Кол-во соцсетей'];
  const sumRow = ['Всего подписчиков'];
  results.forEach(r => {
    countRow.push(r.socials.length, '');
    const sum = r.socials.reduce((a, s) => a + (s.followers || 0), 0);
    sumRow.push(sum, '');
  });
  rows.push(countRow, sumRow);

  const csv = rows.map(r =>
    r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')
  ).join('\n');
  download('\uFEFF' + csv, 'compare-export.csv', 'text/csv');
}

function exportCompareJSON() {
  const results = window._lastCompare;
  if (!results) return;
  download(JSON.stringify(results, null, 2), 'compare-export.json', 'application/json');
}
