// ── RES SYSTEM — OPTIMIZED MAIN JS ──

// ── Toast ──
function showToast(message, type = 'success') {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(60px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

// ── Mobile sidebar ──
function initSidebar() {
  const hamburger = document.querySelector('.hamburger');
  const sidebar   = document.querySelector('.sidebar');
  const overlay   = document.querySelector('.sidebar-overlay');
  if (!hamburger) return;
  hamburger.addEventListener('click', () => {
    sidebar.classList.toggle('mobile-open');
    overlay.classList.toggle('show');
  });
  overlay.addEventListener('click', () => {
    sidebar.classList.remove('mobile-open');
    overlay.classList.remove('show');
  });
}

// ── Modal ──
function openModal(id)  { document.getElementById(id).classList.add('show'); }
function closeModal(id) { document.getElementById(id).classList.remove('show'); }

// ── Request cache (client-side) — avoid duplicate requests ──
const _reqCache = {};
async function apiGet(url, clientTTL = 0) {
  if (clientTTL > 0) {
    const hit = _reqCache[url];
    if (hit && (Date.now() - hit.ts) < clientTTL * 1000) {
      return hit.data;
    }
  }
  const res  = await fetch(url);
  const data = await res.json();
  if (clientTTL > 0) _reqCache[url] = { data, ts: Date.now() };
  return data;
}

async function apiPost(url, data) {
  const res = await fetch(url, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(data)
  });
  return res.json();
}

// ── Debounce — prevent rapid calls ──
function debounce(fn, ms = 300) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

// ── Formatting ──
function formatCurrency(amount) {
  return parseFloat(amount || 0).toFixed(2) + ' $';
}

function formatDate(dateStr) {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  } catch { return dateStr; }
}

function statusBadge(status) {
  const map = {
    'pending':            ['pending',   '⏳', 'Pending'],
    'processed':          ['processed', '✅', 'Processed'],
    'on the way':         ['on-way',    '🚚', 'On the Way'],
    'delivery confirmed': ['confirmed', '🎉', 'Delivered'],
    'rejected':           ['rejected',  '❌', 'Rejected']
  };
  const key = (status || '').toLowerCase();
  const [cls, icon, label] = map[key] || ['pending', '⏳', status || 'Unknown'];
  return `<span class="badge ${cls}">${icon} ${label}</span>`;
}

function parseItems(itemsStr) {
  if (!itemsStr) return '—';
  let str = String(itemsStr).trim();
  // Clean Google Sheets escaping
  while ((str.startsWith('"') && str.endsWith('"')) ||
         (str.startsWith("'") && str.endsWith("'"))) {
    str = str.slice(1, -1);
  }
  str = str.replace(/\\"/g, '"').replace(/\\/g, '');
  // Try JSON parse
  if (str.indexOf('[') !== -1 || str.indexOf('{') !== -1) {
    const start = str.indexOf('[') !== -1 ? str.indexOf('[') : str.indexOf('{');
    try {
      const parsed = JSON.parse(str.substring(start));
      const arr    = Array.isArray(parsed) ? parsed : [parsed];
      return arr.map(i => `${i.name||'?'} x${i.qty||1}`).join(' • ');
    } catch(e) {
      try {
        const parsed = JSON.parse(str.substring(start).replace(/'/g,'"'));
        const arr    = Array.isArray(parsed) ? parsed : [parsed];
        return arr.map(i => `${i.name||'?'} x${i.qty||1}`).join(' • ');
      } catch(e2) {}
    }
  }
  return str.length > 60 ? str.substring(0,60) + '...' : (str || '—');
}

document.addEventListener('DOMContentLoaded', initSidebar);
