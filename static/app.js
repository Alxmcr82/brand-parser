const API_BASE = window.location.origin;
let useAI = false;
let history = JSON.parse(localStorage.getItem('bp_history') || '[]');

// --- Init ---
checkHealth();
renderHistory();

document.getElementById('url-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') runParse();
});

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
      <span>Загружаем <span style="color:var(--accent);font-family:'DM Mono',monospace">${url}</span></span>
    </div>`;

  try {
    const resp = await fetch(`${API_BASE}/parse`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, use_ai: useAI }),
    });

    const data = await resp.json();

    if (!resp.ok) {
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
          <span class="social-name">${s.platform}</span>
          ${s.is_bot === true ? '<span class="bot-badge">bot</span>' : ''}
          <a class="social-link" href="${s.url}" target="_blank">${s.url}</a>
          ${s.followers != null ? `<span class="social-followers">${formatFollowers(s.followers)}</span>` : ''}
        </div>`).join('')
    : '<div class="no-data">Соцсети не найдены</div>';

  document.getElementById('result-section').innerHTML = `
    <div class="result-card">
      <div class="result-header">
        <span class="result-url">${data.url}</span>
        <span class="method-badge ${isAI ? 'ai' : ''}">${data.method}</span>
      </div>
      <div class="result-body">
        <div>
          <div class="section-label">Описание бренда</div>
          ${data.description
            ? `<div class="description-text">${data.description}</div>`
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
    `<div class="error-box">✗ ${msg}</div>`;
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
    return `
      <div class="history-item" onclick="loadFromHistory(${i})">
        <div class="history-dot"></div>
        <span class="history-url">${item.url}</span>
        <span class="history-socials-count">${item.socials.length} соцс.</span>
        <span class="history-meta">${date}</span>
      </div>`;
  }).join('');
}

function loadFromHistory(i) {
  const item = history[i];
  document.getElementById('url-input').value = item.url;
  renderResult(item);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function clearHistory() {
  history = [];
  saveHistory();
  renderHistory();
  document.getElementById('result-section').innerHTML = '';
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

function download(content, filename, type) {
  const blob = new Blob([content], { type });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}
