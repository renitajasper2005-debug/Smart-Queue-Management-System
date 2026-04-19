const API = {
  async post(url, body) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return response.json();
  },

  async get(url) {
    const response = await fetch(url);
    return response.json();
  },
};

async function loadServiceLabels() {
  const selects = ['service', 'manage-service', 'wk-service'];
  try {
    const data = await API.get('/api/services');
    const services = data.services || [];
    selects.forEach((id) => {
      const select = document.getElementById(id);
      if (!select) return;
      const currentValue = select.value;
      [...select.options].forEach((option) => {
        const match = services.find((item) => item.name === option.value);
        if (match) {
          option.textContent = `${match.name} (${match.duration_minutes} min)`;
        }
      });
      select.value = currentValue;
    });
  } catch (_) {
    // ignore metadata load errors
  }
}



function renderServiceEditor(services) {
  const container = document.getElementById('service-editor');
  if (!container) return;
  container.innerHTML = services.map((service, index) => `
    <div class="service-row">
      <div>
        <label for="service-name-${index}">Service</label>
        <input type="text" id="service-name-${index}" data-service-name value="${service.name}" readonly />
      </div>
      <div>
        <label for="service-duration-${index}">Duration (min)</label>
        <input type="number" id="service-duration-${index}" data-service-duration min="1" value="${service.duration_minutes}" />
      </div>
    </div>
  `).join('');
}

async function loadServiceEditor() {
  const container = document.getElementById('service-editor');
  if (!container) return;
  try {
    const data = await API.get('/api/services');
    renderServiceEditor(data.services || []);
  } catch (_) {
    container.innerHTML = '<div style="color:var(--red)">Could not load service durations.</div>';
  }
}

async function saveServiceDurations() {
  const container = document.getElementById('service-editor');
  const button = document.getElementById('save-services-btn');
  if (!container || !button) return;

  const names = [...container.querySelectorAll('[data-service-name]')];
  const durations = [...container.querySelectorAll('[data-service-duration]')];
  const services = names.map((nameEl, index) => ({
    name: nameEl.value,
    duration_minutes: Number(durations[index]?.value || 0),
  }));

  setButtonBusy(button, '<span class="spinner"></span> Saving...', 'Save Durations');
  try {
    const data = await API.post('/api/services', { services });
    if (data.error) {
      toast(data.error, 'error');
      return;
    }
    renderServiceEditor(data.services || []);
    loadServiceLabels();
    loadAdminQueue();
    loadQueue();
    toast(data.message || 'Service durations updated.', 'success');
  } catch (_) {
    toast('Could not save service durations.', 'error');
  } finally {
    setButtonBusy(button, '', 'Save Durations');
  }
}

function getToastContainer() {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  return container;
}

function toast(message, type = 'info', duration = 3500) {
  const colors = {
    success: { bg: 'rgba(34,197,94,0.15)', border: 'rgba(34,197,94,0.3)', color: '#22c55e' },
    error: { bg: 'rgba(239,68,68,0.15)', border: 'rgba(239,68,68,0.3)', color: '#ef4444' },
    info: { bg: 'rgba(108,71,255,0.15)', border: 'rgba(108,71,255,0.3)', color: '#a78bfa' },
  };

  const palette = colors[type] || colors.info;
  const el = document.createElement('div');
  el.className = 'toast';
  el.style.cssText = `background:${palette.bg};border:1px solid ${palette.border};color:${palette.color}`;
  el.textContent = message;
  getToastContainer().appendChild(el);

  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity .4s';
    setTimeout(() => el.remove(), 400);
  }, duration);
}

function setButtonBusy(button, busyText, idleText) {
  if (!button) return;
  if (busyText) {
    button.dataset.idleText = idleText || button.innerHTML;
    button.disabled = true;
    button.innerHTML = busyText;
    return;
  }
  button.disabled = false;
  button.innerHTML = button.dataset.idleText || idleText || button.innerHTML;
}

function formatPredictedTime(value) {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';
  return parsed.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function renderNextUp(targetId, nextUp) {
  const el = document.getElementById(targetId);
  if (!el) return;

  if (!nextUp) {
    el.textContent = 'No waiting patients right now.';
    return;
  }

  const eta = nextUp.estimated_wait_minutes || 0;
  const phone = nextUp.phone || 'No phone';
  const channel = nextUp.last_notification_channel || 'sms';
  const state = nextUp.notification_state || 'pending';
  const predictedStart = formatPredictedTime(nextUp.predicted_start);
  el.innerHTML = `
    <strong>${nextUp.name}</strong> is next for ${nextUp.service || 'General'}.
    ETA: <strong>${eta} min</strong>.
    Predicted start: <strong>${predictedStart}</strong>.
    Contact: <code>${phone}</code>.
    Notification: <strong>${state}</strong> via ${channel}.
  `;
}

function showQR(data) {
  const box = document.getElementById('qr-result');
  if (!box) return;

  document.getElementById('qr-id').textContent = data.id;
  document.getElementById('qr-img').src = data.qr_url;
  document.getElementById('qr-name').textContent = data.record.name;
  document.getElementById('qr-service').textContent = data.record.service || 'General';
  document.getElementById('qr-datetime').textContent = `${data.record.date || 'Walk-in'} ${data.record.time || ''}`.trim();
  box.style.display = 'block';
  box.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

const bookingForm = document.getElementById('booking-form');
if (bookingForm) {
  bookingForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = bookingForm.querySelector('button[type=submit]');
    setButtonBusy(button, '<span class="spinner"></span> Booking...', 'Book Appointment');

    const payload = {
      name: document.getElementById('name').value.trim(),
      date: document.getElementById('date').value,
      time: document.getElementById('time').value,
      service: document.getElementById('service').value,
      phone: document.getElementById('phone')?.value.trim() || '',
    };

    try {
      const data = await API.post('/appointment', payload);
      if (data.error) {
        toast(data.error, 'error');
        return;
      }
      toast('Appointment booked successfully.', 'success');
      showQR(data);
      bookingForm.reset();
    } catch (_) {
      toast('Network error. Try again.', 'error');
    } finally {
      setButtonBusy(button, '', 'Book Appointment');
    }
  });
}

const manageLookupForm = document.getElementById('manage-lookup-form');
const manageForm = document.getElementById('manage-form');
let currentManageId = null;

function fillManageForm(record, waitMetrics) {
  if (!manageForm) return;
  currentManageId = record.id;
  document.getElementById('manage-name').value = record.name || '';
  document.getElementById('manage-date').value = record.date || '';
  document.getElementById('manage-time').value = record.time || '';
  document.getElementById('manage-service').value = record.service || '';
  document.getElementById('manage-phone').value = record.phone || '';

  const summary = document.getElementById('manage-summary');
  if (summary) {
    const bits = [`Status: ${record.status || 'waiting'}`];
    if (record.expected_duration_minutes) {
      bits.push(`Service time: ${record.expected_duration_minutes} min`);
    }
    if (waitMetrics) {
      bits.push(`Position: ${waitMetrics.position}`);
      bits.push(`ETA: ${waitMetrics.estimated_wait_minutes} min`);
    }
    summary.textContent = bits.join(' | ');
    summary.style.display = 'block';
  }

  manageForm.style.display = 'block';
}

if (manageLookupForm) {
  manageLookupForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = manageLookupForm.querySelector('button[type=submit]');
    const id = document.getElementById('manage-id').value.trim().toUpperCase();
    setButtonBusy(button, '<span class="spinner"></span> Loading...', 'Load Booking');

    try {
      const data = await API.get(`/record/${id}`);
      if (data.error) {
        toast(data.error, 'error');
        return;
      }
      fillManageForm(data.record, data.wait_metrics);
      toast('Booking loaded.', 'success');
    } catch (_) {
      toast('Could not load booking.', 'error');
    } finally {
      setButtonBusy(button, '', 'Load Booking');
    }
  });
}

if (manageForm) {
  manageForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!currentManageId) {
      toast('Load a booking first.', 'error');
      return;
    }

    const button = manageForm.querySelector('button[type=submit]');
    setButtonBusy(button, '<span class="spinner"></span> Saving...', 'Save Changes');

    const payload = {
      name: document.getElementById('manage-name').value.trim(),
      date: document.getElementById('manage-date').value,
      time: document.getElementById('manage-time').value,
      service: document.getElementById('manage-service').value,
      phone: document.getElementById('manage-phone').value.trim(),
    };

    try {
      const data = await API.post(`/record/${currentManageId}`, payload);
      if (data.error) {
        toast(data.error, 'error');
        return;
      }
      fillManageForm(data.record);
      toast(data.message || 'Booking updated.', 'success');
    } catch (_) {
      toast('Could not update booking.', 'error');
    } finally {
      setButtonBusy(button, '', 'Save Changes');
    }
  });
}

const walkinForm = document.getElementById('walkin-form');
if (walkinForm) {
  walkinForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = walkinForm.querySelector('button[type=submit]');
    setButtonBusy(button, '<span class="spinner"></span> Registering...', 'Register Walk-in');

    const payload = {
      name: document.getElementById('wk-name').value.trim(),
      service: document.getElementById('wk-service').value,
      phone: document.getElementById('wk-phone')?.value.trim() || '',
    };

    try {
      const data = await API.post('/walkin', payload);
      if (data.error) {
        toast(data.error, 'error');
        return;
      }
      toast('Walk-in registered successfully.', 'success');
      const box = document.getElementById('walkin-result');
      if (box) {
        document.getElementById('wk-id').textContent = data.id;
        document.getElementById('wk-rname').textContent = data.record.name;
        box.style.display = 'block';
        box.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      walkinForm.reset();
    } catch (_) {
      toast('Network error. Try again.', 'error');
    } finally {
      setButtonBusy(button, '', 'Register Walk-in');
    }
  });
}

const checkinForm = document.getElementById('checkin-form');
if (checkinForm) {
  checkinForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const id = document.getElementById('checkin-id').value.trim().toUpperCase();
    const button = checkinForm.querySelector('button[type=submit]');
    setButtonBusy(button, '<span class="spinner"></span> Checking in...', 'Check In');

    try {
      const data = await API.post(`/checkin/${id}`, {});
      if (data.error) {
        toast(data.error, 'error');
        return;
      }
      toast(data.message, 'success');
      const box = document.getElementById('checkin-result');
      if (box) {
        document.getElementById('ci-name').textContent = data.record?.name || id;
        document.getElementById('ci-status').textContent = data.record?.status || 'arrived';
        document.getElementById('ci-service').textContent = data.record?.service || '-';
        box.style.display = 'block';
        box.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    } catch (_) {
      toast('Network error. Try again.', 'error');
    } finally {
      setButtonBusy(button, '', 'Check In');
    }
  });
}

function renderQueue(data) {
  const setEl = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };

  setEl('stat-total', data.total || 0);
  setEl('stat-waiting', (data.waiting || []).length);
  setEl('stat-arrived', (data.arrived || []).length);
  setEl('stat-completed', (data.completed || []).length);
  setEl('stat-service-mins', data.average_service_minutes || 0);
  renderNextUp('next-up-banner', data.next_up);
  renderNextUp('next-up-card', data.next_up);

  const listEl = document.getElementById('queue-list');
  if (!listEl) return;

  const all = [
    ...(data.waiting || []),
    ...(data.arrived || []),
    ...(data.completed || []),
    ...(data.missed || []),
  ];

  if (all.length === 0) {
    listEl.innerHTML = '<div class="empty-state"><div class="emoji">...</div><p>No entries yet</p></div>';
    return;
  }

  listEl.innerHTML = all.map((record, index) => {
    const pos = data.position_map?.[record.id] || '-';
    const typeChip = record.type === 'walkin'
      ? '<span class="chip chip-walkin">Walk-in</span>'
      : '<span class="chip chip-appt">Appointment</span>';
    const statusChip = `<span class="chip chip-${record.status}">${record.status}</span>`;
    const eta = record.status === 'waiting' ? ` | ETA: ${record.estimated_wait_minutes || 0} min` : '';
    const predictedStart = record.predicted_start ? ` | Starts: ${formatPredictedTime(record.predicted_start)}` : '';
    const duration = record.service_duration_minutes || record.expected_duration_minutes;
    return `
      <div class="queue-item fade-in" style="animation-delay:${index * 0.04}s">
        <div class="queue-item-left">
          <div class="queue-position">${record.status === 'waiting' ? pos : 'OK'}</div>
          <div>
            <div class="queue-name">${record.name}</div>
            <div class="queue-meta">${record.service || 'General'}${duration ? ` (${duration} min)` : ''} | ${record.date || ''} ${record.time || ''} | ID: <code>${record.id}</code>${eta}${predictedStart}</div>
          </div>
        </div>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          ${typeChip} ${statusChip}
        </div>
      </div>`;
  }).join('');
}

async function loadQueue() {
  try {
    const data = await API.get('/api/queue');
    renderQueue(data);
  } catch (_) {
    // keep quiet during auto-refresh
  }
}

if (document.getElementById('queue-list')) {
  loadQueue();
  setInterval(loadQueue, 5000);
}

async function editRecordAdmin(recordId) {
  const existing = await API.get(`/record/${recordId}`);
  if (existing.error) {
    toast(existing.error, 'error');
    return;
  }

  const record = existing.record;
  const name = window.prompt('Name', record.name || '');
  if (name === null) return;
  const date = window.prompt('Date (YYYY-MM-DD)', record.date || '');
  if (date === null) return;
  const time = window.prompt('Time (HH:MM)', record.time || '');
  if (time === null) return;
  const service = window.prompt('Service', record.service || '');
  if (service === null) return;
  const phone = window.prompt('Phone', record.phone || '');
  if (phone === null) return;

  const updated = await API.post(`/record/${recordId}`, { name, date, time, service, phone });
  if (updated.error) {
    toast(updated.error, 'error');
    return;
  }

  toast(updated.message || 'Record updated.', 'success');
  loadAdminQueue();
}

async function adminAction(id, action) {
  const routeMap = {
    checkin: `/checkin/${id}`,
    complete: `/complete/${id}`,
    miss: `/miss/${id}`,
    notify_eta: `/notify/eta/${id}`,
    notify_checkin: `/notify/checkin-soon/${id}`,
  };

  try {
    const data = await API.post(routeMap[action], {});
    if (data.error) {
      toast(data.error, 'error');
      return;
    }
    toast(data.message, 'success');
    loadAdminQueue();
  } catch (_) {
    toast('Action failed.', 'error');
  }
}

function renderAdminTable(data) {
  const tbody = document.getElementById('admin-tbody');
  if (!tbody) return;

  const all = [
    ...(data.waiting || []),
    ...(data.arrived || []),
    ...(data.completed || []),
    ...(data.missed || []),
  ];

  const setEl = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };

  setEl('stat-total', data.total || 0);
  setEl('stat-waiting', (data.waiting || []).length);
  setEl('stat-arrived', (data.arrived || []).length);
  setEl('stat-completed', (data.completed || []).length);
  setEl('stat-service-mins', data.average_service_minutes || 0);
  renderNextUp('next-up-card', data.next_up);

  if (all.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:2rem">No records yet</td></tr>';
    return;
  }

  tbody.innerHTML = all.map((record) => {
    const note = record.last_notification_detail
      ? `<div style="margin-top:.35rem;color:var(--muted);font-size:.75rem">${record.last_notification_detail}</div>`
      : '';
    const eta = record.estimated_wait_minutes !== undefined ? `<div style="margin-top:.35rem;color:var(--muted);font-size:.75rem">ETA: ${record.estimated_wait_minutes} min</div>` : '';
    const predictedStart = record.predicted_start ? `<div style="margin-top:.35rem;color:var(--muted);font-size:.75rem">Predicted start: ${formatPredictedTime(record.predicted_start)}</div>` : '';
    const duration = record.service_duration_minutes || record.expected_duration_minutes;

    return `
      <tr>
        <td><code style="color:var(--accent2)">${record.id}</code></td>
        <td>${record.name}</td>
        <td><span class="chip chip-${record.type === 'walkin' ? 'walkin' : 'appt'}">${record.type}</span></td>
        <td>${record.service || 'General'}${duration ? `<div style="margin-top:.35rem;color:var(--muted);font-size:.75rem">${duration} min service</div>` : ''}</td>
        <td>${record.date || '-'} ${record.time || ''}${eta}${predictedStart}</td>
        <td><span class="chip chip-${record.status}">${record.status}</span>${note}</td>
        <td>
          <div style="display:flex;gap:4px;flex-wrap:wrap">
            <button class="btn btn-sm btn-secondary" onclick="editRecordAdmin('${record.id}')">Edit</button>
            ${record.status !== 'arrived' && record.status !== 'completed' && record.status !== 'missed'
              ? `<button class="btn btn-sm" style="background:rgba(56,189,248,0.15);color:var(--accent3);border:1px solid rgba(56,189,248,0.25)" onclick="adminAction('${record.id}','checkin')">Check-in</button>`
              : ''}
            ${record.status !== 'completed' && record.status !== 'missed'
              ? `<button class="btn btn-sm btn-success" onclick="adminAction('${record.id}','complete')">Complete</button>`
              : ''}
            ${record.status === 'waiting'
              ? `<button class="btn btn-sm btn-secondary" onclick="adminAction('${record.id}','notify_eta')">Send ETA</button>`
              : ''}
            ${record.status === 'waiting' || record.status === 'arrived'
              ? `<button class="btn btn-sm btn-secondary" onclick="adminAction('${record.id}','notify_checkin')">Remind 10 min</button>`
              : ''}
            ${record.status === 'waiting'
              ? `<button class="btn btn-sm btn-danger" onclick="adminAction('${record.id}','miss')">Miss</button>`
              : ''}
          </div>
        </td>
      </tr>`;
  }).join('');
}

async function loadAdminQueue() {
  try {
    const data = await API.get('/api/queue');
    renderAdminTable(data);
  } catch (_) {
    toast('Could not load admin queue.', 'error');
  }
}

if (document.getElementById('admin-tbody')) {
  loadAdminQueue();
  setInterval(loadAdminQueue, 5000);
}

loadServiceLabels();

const saveServicesButton = document.getElementById('save-services-btn');
if (saveServicesButton) {
  loadServiceEditor();
  saveServicesButton.addEventListener('click', saveServiceDurations);
}
