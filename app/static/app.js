// === SSE ===

function initSSE() {
  const source = new EventSource('/api/notifications/stream');

  source.addEventListener('notification', (e) => {
    const data = JSON.parse(e.data);
    prependNotification(data);
  });

  source.addEventListener('status_change', (e) => {
    const data = JSON.parse(e.data);
    updateNotificationStatus(data.id, data.status);
  });

  source.addEventListener('ping', () => {});

  source.onerror = () => {
    console.log('SSE connection lost, reconnecting...');
  };
}

function prependNotification(n) {
  const list = document.getElementById('notification-list');
  if (!list) return;

  // Remove empty state if present
  const empty = list.querySelector('.empty-state');
  if (empty) empty.remove();

  const el = document.createElement('div');
  el.className = `notification ${n.priority} ${n.status} notification-new`;
  el.dataset.id = n.id;

  const created = new Date(n.created_at);
  const timeStr = created.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    + ', ' + created.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });

  el.innerHTML = `
    <div class="notification-header">
      <span class="priority-badge ${n.priority}">${n.priority}</span>
      <span class="notification-title">${escapeHtml(n.title)}</span>
      <span class="notification-meta">
        ${n.source ? `<span class="source">${escapeHtml(n.source)}</span>` : ''}
        <time>${timeStr}</time>
      </span>
    </div>
    ${n.message ? `<div class="notification-body">${escapeHtml(n.message)}</div>` : ''}
    <div class="notification-actions">
      <button class="btn btn-small" onclick="markAs('${n.id}', 'read')">Mark read</button>
      <button class="btn btn-small btn-danger" onclick="deleteNotification('${n.id}')">Delete</button>
    </div>
  `;

  list.prepend(el);
}

function updateNotificationStatus(id, status) {
  const el = document.querySelector(`.notification[data-id="${id}"]`);
  if (!el) return;
  el.classList.remove('unread', 'read', 'archived');
  el.classList.add(status);
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// === Add notification modal ===

function openAddNotification() {
  document.getElementById('add-notification-modal').style.display = 'flex';
}

function closeAddNotification() {
  document.getElementById('add-notification-modal').style.display = 'none';
}

async function submitNotification(e) {
  e.preventDefault();
  const title = document.getElementById('notif-title').value;
  const message = document.getElementById('notif-message').value || null;
  const priority = document.getElementById('notif-priority').value;
  const source = document.getElementById('notif-source').value || null;

  const resp = await fetch('/api/notifications/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, message, priority, source }),
  });

  if (resp.ok) {
    closeAddNotification();
    document.getElementById('notif-title').value = '';
    document.getElementById('notif-message').value = '';
    document.getElementById('notif-priority').value = 'medium';
    document.getElementById('notif-source').value = '';
  } else {
    alert('Failed to create notification');
  }

  return false;
}

// === Notification actions ===

async function markAs(id, status) {
  const resp = await fetch(`/api/notifications/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
  if (resp.ok) {
    updateNotificationStatus(id, status);
  }
}

async function deleteNotification(id) {
  if (!confirm('Delete this notification?')) return;
  const resp = await fetch(`/api/notifications/${id}`, { method: 'DELETE' });
  if (resp.ok) {
    const el = document.querySelector(`.notification[data-id="${id}"]`);
    if (el) el.remove();
  }
}

// === Token management ===

async function createToken(e) {
  e.preventDefault();
  const name = document.getElementById('token-name').value;
  const device_type = document.getElementById('token-type').value;

  const resp = await fetch('/api/tokens/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, device_type }),
  });

  if (!resp.ok) {
    alert('Failed to create token');
    return false;
  }

  const data = await resp.json();
  showNewToken(data.token);
  document.getElementById('create-token-form').reset();
  return false;
}

function showNewToken(token) {
  const display = document.getElementById('new-token-display');
  const value = document.getElementById('new-token-value');
  const qr = document.getElementById('qr-container');

  value.textContent = token;
  display.style.display = 'block';
  display.scrollIntoView({ behavior: 'smooth' });

  // Generate QR code as simple SVG
  generateQR(token, qr);
}

function dismissToken() {
  document.getElementById('new-token-display').style.display = 'none';
  location.reload();
}

function copyToken() {
  const value = document.getElementById('new-token-value').textContent;
  navigator.clipboard.writeText(value).then(() => {
    const btn = event.target;
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy', 2000);
  });
}

async function revokeToken(id, name) {
  if (!confirm(`Revoke token "${name}"? Devices using it will lose access.`)) return;
  const resp = await fetch(`/api/tokens/${id}`, { method: 'DELETE' });
  if (resp.ok) {
    const row = document.querySelector(`tr[data-id="${id}"]`);
    if (row) row.remove();
  }
}

// === QR Code (simple approach: use an img pointing to a data URL) ===

function generateQR(text, container) {
  // Use a public QR API or generate client-side
  // For simplicity, we'll encode as a Google Charts-style URL
  // In production, consider a small JS QR lib or server-side generation
  container.innerHTML = `<img src="https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(text)}" alt="QR Code" width="180" height="180">`;
}

// === Filter ===

function applyFilters() {
  const status = document.getElementById('filter-status').value;
  const priority = document.getElementById('filter-priority').value;
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (priority) params.set('priority', priority);
  window.location.href = '/?' + params.toString();
}
