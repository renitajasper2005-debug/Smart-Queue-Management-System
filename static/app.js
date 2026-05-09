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
  const predictedStart = formatPredictedTime(nextUp.predicted_start);
  const hasAdminFields = Boolean(nextUp.phone || nextUp.notification_state || nextUp.last_notification_channel);
  if (!hasAdminFields) {
    el.innerHTML = `
      <strong>${nextUp.name}</strong> is next for ${nextUp.service || 'General'}.
      ETA: <strong>${eta} min</strong>.
      Predicted start: <strong>${predictedStart}</strong>.
    `;
    return;
  }

  const phone = nextUp.phone || 'No phone';
  const channel = nextUp.last_notification_channel || 'sms';
  const state = nextUp.notification_state || 'pending';
  el.innerHTML = `
    <strong>${nextUp.name}</strong> is next for ${nextUp.service || 'General'}.
    ETA: <strong>${eta} min</strong>.
    Predicted start: <strong>${predictedStart}</strong>.
    Contact: <code>${phone}</code>.
    Notification: <strong>${state}</strong> via ${channel}.
  `;
}

function describePatientStatus(record, waitMetrics) {
  const status = record.status || 'waiting';
  if (status === 'accepted') {
    return {
      message: 'Accepted. Please go to the doctor.',
      chipClass: 'chip-accepted',
      meta: record.accepted_at ? `Accepted at ${formatPredictedTime(record.accepted_at)}` : 'Accepted by admin scan.',
    };
  }
  if (status === 'completed') {
    return {
      message: 'Appointment completed.',
      chipClass: 'chip-completed',
      meta: record.completed_at ? `Completed at ${formatPredictedTime(record.completed_at)}` : 'Completed.',
    };
  }
  if (status === 'missed') {
    return {
      message: 'Marked missed. Please contact reception.',
      chipClass: 'chip-missed',
      meta: 'This slot was marked missed.',
    };
  }
  return {
    message: 'Waiting for admin acceptance.',
    chipClass: 'chip-waiting',
    meta: waitMetrics ? `Queue position ${waitMetrics.position} | ETA ${waitMetrics.estimated_wait_minutes} min` : 'Still in queue.',
  };
}

function describeNotification(record) {
  if (!record?.last_notified_at || !record?.last_notification_detail) {
    return null;
  }

  const typeMap = {
    eta: 'ETA update',
    checkin_reminder: '10 min reminder',
    next_turn: 'Your turn is next',
    auto_30_min: '30 min reminder',
    auto_10_min: '10 min reminder',
    auto_now: 'Turn now',
  };

  const channel = record.last_notification_channel || 'notification';
  const type = typeMap[record.last_notification_type] || 'Notification';
  return {
    title: `${type} sent`,
    meta: `${formatPredictedTime(record.last_notified_at)} via ${channel}`,
    detail: record.last_notification_detail,
  };
}

function renderNotificationBlock(record) {
  const info = describeNotification(record);
  if (!info) return '';

  return `
    <div class="notification-inline">
      <div class="notification-inline-title">${info.title}</div>
      <div class="notification-inline-meta">${info.meta}</div>
      <div class="notification-inline-detail">${info.detail}</div>
    </div>
  `;
}

const seenPatientNotifications = new Map();

function announceNewPatientNotifications(bookings) {
  if (!Array.isArray(bookings)) return;

  bookings.forEach((record) => {
    const key = record.id;
    if (!key || !record.last_notified_at || !record.last_notification_detail) return;

    const signature = `${record.last_notified_at}|${record.last_notification_type || ''}|${record.last_notification_detail}`;
    const previous = seenPatientNotifications.get(key);
    if (previous && previous !== signature) {
      toast(`Update for ${record.name || key}: ${record.last_notification_detail}`, 'info', 5000);
    }
    seenPatientNotifications.set(key, signature);
  });
}

function normalizeRecordId(value) {
  return (value || '').trim().toUpperCase();
}

function extractRecordIdFromQr(rawValue) {
  const value = (rawValue || '').trim();
  if (!value) return '';

  const idPattern = /([A-Z0-9]{8})$/i;
  const directMatch = value.match(idPattern);
  if (directMatch && directMatch[1]) {
    return directMatch[1].toUpperCase();
  }

  try {
    const url = new URL(value);
    const parts = url.pathname.split('/').filter(Boolean);
    const lastPart = parts[parts.length - 1] || '';
    if (/^[A-Z0-9]{8}$/i.test(lastPart)) {
      return lastPart.toUpperCase();
    }
  } catch (_) {
    // ignore invalid URL and fall through
  }

  return '';
}

function openAdminRecord(id, sourceLabel = 'manual entry') {
  const recordId = normalizeRecordId(id);
  if (!recordId) {
    toast('Could not read a valid appointment ID from the QR.', 'error');
    return false;
  }

  const statusEl = document.getElementById('scanner-status');
  if (statusEl) {
    statusEl.textContent = `QR scanned from ${sourceLabel}. Loading appointment ${recordId} for review.`;
  }
  window.location.href = `/admin/scan/${recordId}`;
  return true;
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

function renderSlotOptions(selectEl, slots, selectedValue = '', placeholder = 'Select an available slot') {
  if (!selectEl) return;
  if (!slots.length) {
    selectEl.innerHTML = '<option value="">No slots available</option>';
    selectEl.value = '';
    selectEl.disabled = true;
    return;
  }

  const options = [`<option value="">${placeholder}</option>`];
  slots.forEach((slot) => {
    const selected = slot === selectedValue ? ' selected' : '';
    options.push(`<option value="${slot}"${selected}>${slot}</option>`);
  });
  selectEl.innerHTML = options.join('');
  selectEl.disabled = false;
  if (selectedValue && slots.includes(selectedValue)) {
    selectEl.value = selectedValue;
  }
}

function filterPastSlotsForToday(slots, dateValue) {
  if (!Array.isArray(slots) || !dateValue) return slots || [];

  const now = new Date();
  const today = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
  ].join('-');

  if (dateValue !== today) {
    return slots;
  }

  const currentMinutes = (now.getHours() * 60) + now.getMinutes();
  return slots.filter((slot) => {
    const [hours, minutes] = String(slot).split(':').map((value) => Number.parseInt(value, 10));
    if (Number.isNaN(hours) || Number.isNaN(minutes)) return false;
    return ((hours * 60) + minutes) > currentMinutes;
  });
}

async function loadAvailableSlots({ dateId, serviceId, timeId, excludeId = '', selectedValue = '' }) {
  const dateEl = document.getElementById(dateId);
  const serviceEl = document.getElementById(serviceId);
  const timeEl = document.getElementById(timeId);
  if (!dateEl || !serviceEl || !timeEl) return;

  const date = dateEl.value;
  const service = serviceEl.value;
  if (!date || !service) {
    timeEl.innerHTML = '<option value="">Select date and service first</option>';
    timeEl.disabled = true;
    return;
  }

  timeEl.innerHTML = '<option value="">Loading available slots...</option>';
  timeEl.disabled = true;

  try {
    const query = new URLSearchParams({ date, service });
    if (excludeId) query.set('exclude_id', excludeId);
    const data = await API.get(`/api/slots?${query.toString()}`);
    const slots = dateId === 'date'
      ? filterPastSlotsForToday(data.slots || [], date)
      : (data.slots || []);
    renderSlotOptions(timeEl, slots, selectedValue);
  } catch (_) {
    timeEl.innerHTML = '<option value="">Could not load slots</option>';
    timeEl.disabled = true;
    toast('Could not load available slots.', 'error');
  }
}

function renderMyBookings(bookings) {
  const listEl = document.getElementById('my-bookings-list');
  if (!listEl) return;

  if (!bookings.length) {
    listEl.innerHTML = `
      <div class="empty-state">
        <div class="emoji">...</div>
        <p>No saved bookings yet. Book an appointment and it will appear here.</p>
      </div>
    `;
    return;
  }

  listEl.innerHTML = bookings.map((record) => {
    const status = record.status || 'waiting';
    const statusLabel = status === 'accepted' ? 'accepted' : status;
    const when = `${record.date || '-'} ${record.time || ''}`.trim();
    const duration = record.expected_duration_minutes ? `${record.expected_duration_minutes} min service` : 'Service time pending';
    const notificationBlock = renderNotificationBlock(record);
    return `
      <div class="booking-card">
        <div class="booking-card-header">
          <div>
            <div class="booking-card-title">${record.name || 'Patient'}</div>
            <div class="booking-card-meta">${record.service || 'General'} | ID: <code>${record.id}</code></div>
          </div>
          <span class="chip chip-${status}">${statusLabel}</span>
        </div>
        <div class="booking-card-meta">Time: ${when}</div>
        <div class="booking-card-meta">${duration}</div>
        ${notificationBlock}
        <img class="booking-card-qr" src="${record.qr_url}" alt="QR for ${record.id}" />
        <div class="booking-card-actions">
          <a href="${record.qr_url}" class="btn btn-secondary btn-sm" target="_blank" rel="noreferrer">Open QR</a>
          <a href="/status" class="btn btn-secondary btn-sm">Check Status</a>
        </div>
      </div>
    `;
  }).join('');
}

async function loadMyBookings() {
  try {
    const data = await API.get('/api/my-bookings');
    if (data.error) {
      toast(data.error, 'error');
      return;
    }
    const bookings = data.bookings || [];
    announceNewPatientNotifications(bookings);
    renderMyBookings(bookings);
  } catch (_) {
    const listEl = document.getElementById('my-bookings-list');
    if (listEl) {
      listEl.innerHTML = `
        <div class="empty-state">
          <div class="emoji">...</div>
          <p>Could not load saved bookings right now.</p>
        </div>
      `;
    }
  }
}

const bookingForm = document.getElementById('booking-form');
if (bookingForm) {
  const bookingDateEl = document.getElementById('date');
  const bookingServiceEl = document.getElementById('service');
  if (bookingDateEl) {
    bookingDateEl.addEventListener('change', () => loadAvailableSlots({ dateId: 'date', serviceId: 'service', timeId: 'time' }));
  }
  if (bookingServiceEl) {
    bookingServiceEl.addEventListener('change', () => loadAvailableSlots({ dateId: 'date', serviceId: 'service', timeId: 'time' }));
  }

  bookingForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = bookingForm.querySelector('button[type=submit]');
    setButtonBusy(button, '<span class="spinner"></span> Booking...', 'Book Appointment');

    const payload = {
      name: document.getElementById('name').value.trim(),
      email: document.getElementById('email').value.trim(),
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
      if (data.notification?.detail) {
        toast(data.notification.detail, data.notification.ok ? 'info' : 'error', 4500);
      }
      showQR(data);
      if (document.getElementById('my-bookings-list')) {
        loadMyBookings();
      }
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
  document.getElementById('manage-email').value = record.email || '';
  document.getElementById('manage-date').value = record.date || '';
  document.getElementById('manage-service').value = record.service || '';
  document.getElementById('manage-phone').value = record.phone || '';
  loadAvailableSlots({
    dateId: 'manage-date',
    serviceId: 'manage-service',
    timeId: 'manage-time',
    excludeId: record.id || '',
    selectedValue: record.time || '',
  });

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
  const manageDateEl = document.getElementById('manage-date');
  const manageServiceEl = document.getElementById('manage-service');
  if (manageDateEl) {
    manageDateEl.addEventListener('change', () => loadAvailableSlots({ dateId: 'manage-date', serviceId: 'manage-service', timeId: 'manage-time', excludeId: currentManageId || '' }));
  }
  if (manageServiceEl) {
    manageServiceEl.addEventListener('change', () => loadAvailableSlots({ dateId: 'manage-date', serviceId: 'manage-service', timeId: 'manage-time', excludeId: currentManageId || '' }));
  }

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
      email: document.getElementById('manage-email').value.trim(),
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
      email: document.getElementById('wk-email').value.trim(),
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
  checkinForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const id = normalizeRecordId(document.getElementById('checkin-id').value);
    openAdminRecord(id);
  });
}

const scannerVideo = document.getElementById('scanner-video');
const scannerPlaceholder = document.getElementById('scanner-placeholder');
const scannerStatus = document.getElementById('scanner-status');
const startScanBtn = document.getElementById('start-scan-btn');
const stopScanBtn = document.getElementById('stop-scan-btn');
const scanImageInput = document.getElementById('scan-image-input');

let scannerStream = null;
let scannerIntervalId = null;
let scannerBusy = false;
let scannerCanvas = null;
let scannerContext = null;

function setScannerStatus(message) {
  if (scannerStatus) {
    scannerStatus.textContent = message;
  }
}

function stopScanner() {
  if (scannerIntervalId) {
    window.clearInterval(scannerIntervalId);
    scannerIntervalId = null;
  }

  if (scannerStream) {
    scannerStream.getTracks().forEach((track) => track.stop());
    scannerStream = null;
  }

  if (scannerVideo) {
    scannerVideo.pause();
    scannerVideo.srcObject = null;
    scannerVideo.style.display = 'none';
  }

  if (scannerPlaceholder) {
    scannerPlaceholder.style.display = 'grid';
  }

  if (startScanBtn) startScanBtn.disabled = false;
  if (stopScanBtn) stopScanBtn.disabled = true;
  scannerBusy = false;
}

function hasBarcodeDetectorSupport() {
  return 'BarcodeDetector' in window;
}

function hasJsQrSupport() {
  return typeof window.jsQR === 'function';
}

function ensureScannerCanvas(width, height) {
  const safeWidth = Math.max(1, width || 1);
  const safeHeight = Math.max(1, height || 1);
  if (!scannerCanvas) {
    scannerCanvas = document.createElement('canvas');
    scannerContext = scannerCanvas.getContext('2d', { willReadFrequently: true });
  }
  if (scannerCanvas.width !== safeWidth || scannerCanvas.height !== safeHeight) {
    scannerCanvas.width = safeWidth;
    scannerCanvas.height = safeHeight;
  }
  return { canvas: scannerCanvas, context: scannerContext };
}

async function decodeQrFromSource(source) {
  if (hasBarcodeDetectorSupport()) {
    const detector = new window.BarcodeDetector({ formats: ['qr_code'] });
    const barcodes = await detector.detect(source);
    if (barcodes.length) {
      return barcodes[0].rawValue || '';
    }
  }

  if (!hasJsQrSupport()) {
    return '';
  }

  const width = source.videoWidth || source.naturalWidth || source.width || 0;
  const height = source.videoHeight || source.naturalHeight || source.height || 0;
  if (!width || !height) {
    return '';
  }

  const { canvas, context } = ensureScannerCanvas(width, height);
  context.drawImage(source, 0, 0, width, height);
  const imageData = context.getImageData(0, 0, width, height);
  const decoded = window.jsQR(imageData.data, imageData.width, imageData.height, {
    inversionAttempts: 'attemptBoth',
  });
  return decoded?.data || '';
}

async function detectQrCode(source, sourceLabel) {
  if (scannerBusy) return;
  scannerBusy = true;

  try {
    const rawValue = await decodeQrFromSource(source);
    if (!rawValue) return;
    const recordId = extractRecordIdFromQr(rawValue);
    if (!recordId) {
      setScannerStatus(`QR detected from ${sourceLabel}, but the appointment ID could not be read.`);
      return;
    }

    stopScanner();
    openAdminRecord(recordId, sourceLabel);
  } catch (_) {
    setScannerStatus('QR scanning is available, but the camera frame could not be read right now.');
  } finally {
    scannerBusy = false;
  }
}

async function startCameraScanner() {
  if (!scannerVideo || !startScanBtn || !stopScanBtn) return;

  if (!navigator.mediaDevices?.getUserMedia) {
    setScannerStatus('This browser cannot access the camera here. Use Scan from Photo or enter the appointment ID manually.');
    toast('Camera access is not supported in this browser.', 'error');
    return;
  }

  if (!hasBarcodeDetectorSupport() && !hasJsQrSupport()) {
    setScannerStatus('QR scanning support could not be loaded here. Use manual ID entry for now.');
    toast('QR scanning is not available in this browser right now.', 'error');
    return;
  }

  stopScanner();

  try {
    scannerStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' } },
      audio: false,
    });

    scannerVideo.srcObject = scannerStream;
    await scannerVideo.play();
    scannerVideo.style.display = 'block';
    if (scannerPlaceholder) {
      scannerPlaceholder.style.display = 'none';
    }

    startScanBtn.disabled = true;
    stopScanBtn.disabled = false;
    setScannerStatus('Camera started. Hold the patient QR steady in front of the camera.');

    scannerIntervalId = window.setInterval(() => {
      if (!scannerVideo.videoWidth || !scannerVideo.videoHeight) return;
      detectQrCode(scannerVideo, 'camera');
    }, 700);
  } catch (_) {
    stopScanner();
    setScannerStatus('Camera access was blocked or is unavailable. Use Scan from Photo or enter the appointment ID manually.');
    toast('Could not access the camera for QR scanning.', 'error');
  }
}

async function scanQrFromImage(file) {
  if (!file) return;

  if (!hasBarcodeDetectorSupport() && !hasJsQrSupport()) {
    setScannerStatus('QR reading support could not be loaded here. Enter the appointment ID manually.');
    toast('Photo QR scanning is not available in this browser right now.', 'error');
    return;
  }

  try {
    const bitmap = await createImageBitmap(file);
    setScannerStatus('Scanning the uploaded QR photo...');
    await detectQrCode(bitmap, 'uploaded photo');
  } catch (_) {
    setScannerStatus('The uploaded image could not be scanned. Try a clearer QR image or enter the ID manually.');
    toast('Could not scan the uploaded QR image.', 'error');
  } finally {
    if (scanImageInput) {
      scanImageInput.value = '';
    }
  }
}

if (startScanBtn) {
  startScanBtn.addEventListener('click', startCameraScanner);
}

if (stopScanBtn) {
  stopScanBtn.addEventListener('click', () => {
    stopScanner();
    setScannerStatus('Camera stopped. You can start it again, scan from a photo, or enter the appointment ID manually.');
  });
}

if (scanImageInput) {
  scanImageInput.addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    await scanQrFromImage(file);
  });
}

window.addEventListener('beforeunload', stopScanner);

function renderStatusList(bookings) {
  const listEl = document.getElementById('status-list');
  if (!listEl) return;

  if (!bookings.length) {
    listEl.innerHTML = `
      <div class="empty-state">
        <div class="emoji">...</div>
        <p>No saved bookings yet. Book an appointment or register a walk-in to track status here.</p>
      </div>
    `;
    return;
  }

  listEl.innerHTML = bookings.map((record) => {
    const statusInfo = describePatientStatus(record, record.wait_metrics);
    const when = `${record.date || '-'} ${record.time || ''}`.trim();
    const notificationBlock = renderNotificationBlock(record);
    return `
      <div class="booking-card">
        <div class="booking-card-header">
          <div>
            <div class="booking-card-title">${record.name || 'Patient'}</div>
            <div class="booking-card-meta">${record.service || 'General'} | ID: <code>${record.id}</code></div>
          </div>
          <span class="chip ${statusInfo.chipClass}">${record.status || 'waiting'}</span>
        </div>
        <div class="booking-card-meta">Time: ${when}</div>
        <div class="booking-card-meta">${statusInfo.meta}</div>
        <div style="font-weight:600">${statusInfo.message}</div>
        ${notificationBlock}
      </div>
    `;
  }).join('');
}

async function loadMyStatuses() {
  try {
    const data = await API.get('/api/my-bookings');
    if (data.error) {
      toast(data.error, 'error');
      return;
    }
    const bookings = data.bookings || [];
    announceNewPatientNotifications(bookings);
    renderStatusList(bookings);
  } catch (_) {
    const listEl = document.getElementById('status-list');
    if (listEl) {
      listEl.innerHTML = `
        <div class="empty-state">
          <div class="emoji">...</div>
          <p>Could not load your statuses right now.</p>
        </div>
      `;
    }
  }
}

function renderQueue(data) {
  const isAdminView = data.viewer_role === 'admin';
  const setEl = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };

  setEl('stat-total', data.total || 0);
  setEl('stat-waiting', (data.waiting || []).length);
  setEl('stat-arrived', (data.accepted || data.arrived || []).length);
  setEl('stat-completed', (data.completed || []).length);
  setEl('stat-service-mins', data.average_service_minutes || 0);
  renderNextUp('next-up-banner', data.next_up);
  renderNextUp('next-up-card', data.next_up);

  const listEl = document.getElementById('queue-list');
  if (!listEl) return;

  const all = [
    ...(data.waiting || []),
    ...(data.accepted || data.arrived || []),
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
    const statusLabel = record.status === 'accepted' ? 'accepted' : record.status;
    const statusChip = `<span class="chip chip-${record.status}">${statusLabel}</span>`;
    const eta = record.status === 'waiting' ? ` | ETA: ${record.estimated_wait_minutes || 0} min` : '';
    const predictedStart = record.predicted_start ? ` | Starts: ${formatPredictedTime(record.predicted_start)}` : '';
    const duration = record.service_duration_minutes || record.expected_duration_minutes;
    const recordMeta = isAdminView && record.id ? ` | ID: <code>${record.id}</code>` : '';
    return `
      <div class="queue-item fade-in" style="animation-delay:${index * 0.04}s">
        <div class="queue-item-left">
          <div class="queue-position">${record.status === 'waiting' ? pos : 'OK'}</div>
          <div>
            <div class="queue-name">${record.name}</div>
            <div class="queue-meta">${record.service || 'General'}${duration ? ` (${duration} min)` : ''} | ${record.date || ''} ${record.time || ''}${recordMeta}${eta}${predictedStart}</div>
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
    if (document.getElementById('checkin-result')) {
      window.location.href = `/admin/scan/${id}`;
    }
  } catch (_) {
    toast('Action failed.', 'error');
  }
}

function renderAdminTable(data) {
  const tbody = document.getElementById('admin-tbody');
  if (!tbody) return;

  const all = [
    ...(data.waiting || []),
    ...(data.accepted || data.arrived || []),
    ...(data.completed || []),
    ...(data.missed || []),
  ];

  const setEl = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };

  setEl('stat-total', data.total || 0);
  setEl('stat-waiting', (data.waiting || []).length);
  setEl('stat-arrived', (data.accepted || data.arrived || []).length);
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
        <td><span class="chip chip-${record.status}">${record.status === 'accepted' ? 'accepted' : record.status}</span>${note}</td>
        <td>
          <div style="display:flex;gap:4px;flex-wrap:wrap">
            <button class="btn btn-sm btn-secondary" onclick="editRecordAdmin('${record.id}')">Edit</button>
            ${record.status !== 'accepted' && record.status !== 'arrived' && record.status !== 'completed' && record.status !== 'missed'
              ? `<button class="btn btn-sm" style="background:rgba(56,189,248,0.15);color:var(--accent3);border:1px solid rgba(56,189,248,0.25)" onclick="adminAction('${record.id}','checkin')">Accept</button>`
              : ''}
            ${record.status !== 'completed' && record.status !== 'missed'
              ? `<button class="btn btn-sm btn-success" onclick="adminAction('${record.id}','complete')">Complete</button>`
              : ''}
            ${record.status === 'waiting'
              ? `<button class="btn btn-sm btn-secondary" onclick="adminAction('${record.id}','notify_eta')">Send ETA</button>`
              : ''}
            ${record.status === 'waiting' || record.status === 'accepted' || record.status === 'arrived'
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

if (document.getElementById('my-bookings-list')) {
  loadMyBookings();
  setInterval(loadMyBookings, 10000);
}

if (document.getElementById('status-list')) {
  loadMyStatuses();
  setInterval(loadMyStatuses, 10000);
}
