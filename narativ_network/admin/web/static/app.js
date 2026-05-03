// Narrative Network — admin UI client.
// Vanilla JS. No build. Polls /api/now_playing for live "on air" badge.

(function () {
  // ── On-air badge poll ────────────────────────────────────────────
  const onair = document.getElementById('onair');
  async function refreshNow() {
    if (!onair) return;
    try {
      const r = await fetch('/api/now_playing');
      const j = await r.json();
      if (j.current) {
        const showname = j.current.show_title || '(slate)';
        onair.textContent = `▶ ON AIR — ${showname}`;
        onair.style.color = '#ff3a3a';
      } else {
        onair.textContent = '— no plan —';
      }
    } catch (e) { /* offline */ }
  }
  refreshNow();
  setInterval(refreshNow, 15000);

  // ── Pipeline action buttons (dashboard only) ─────────────────────
  function actionBtn(id, url, label) {
    const btn = document.getElementById(id);
    const status = document.getElementById('action-status');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      if (status) status.textContent = label + '…';
      try {
        const r = await fetch(url, { method: 'POST', headers: { Authorization: 'Bearer ' + getToken() }});
        const j = await r.json().catch(() => ({}));
        if (status) status.textContent = label + ' done. ' + (j.detail || JSON.stringify(j));
      } catch (e) {
        if (status) status.textContent = 'Error: ' + e;
      } finally {
        btn.disabled = false;
      }
    });
  }
  actionBtn('btn-poll',    '/api/poll_now',   'Polling inbox');
  actionBtn('btn-process', '/api/process_now','Processing episodes');
  actionBtn('btn-regen',   '/api/regenerate', 'Regenerating playlist');

  // ── BREAK IN / RETURN TO AIR (OBS scene control) ─────────────────
  const btnBreak = document.getElementById('btn-breakin');
  const btnReturn = document.getElementById('btn-return');
  if (btnBreak) {
    btnBreak.addEventListener('click', async () => {
      if (!confirm('Cut the scheduled program and go LIVE?')) return;
      const reason = prompt('Reason (optional, logged):', 'breaking news') || '';
      const fd = new FormData(); fd.append('reason', reason);
      const r = await fetch('/api/break_in', { method: 'POST', body: fd,
        headers: { Authorization: 'Bearer ' + getToken() } });
      if (!r.ok) { alert('Break-in failed: ' + r.status + ' ' + (await r.text())); }
      else { document.body.style.outline = '4px solid #ff3a3a'; setTimeout(()=>{document.body.style.outline='';}, 1200); }
    });
  }
  if (btnReturn) {
    btnReturn.addEventListener('click', async () => {
      const r = await fetch('/api/return_to_air', { method: 'POST',
        headers: { Authorization: 'Bearer ' + getToken() } });
      if (!r.ok) { alert('Return failed: ' + r.status + ' ' + (await r.text())); }
    });
  }

  // ── Schedule grid ────────────────────────────────────────────────
  const grid = document.getElementById('grid');
  if (!grid) return;

  const SLOT_MIN = parseInt(grid.dataset.slotMinutes, 10) || 30;
  const SLOTS = JSON.parse(grid.dataset.slots || '[]');
  const DAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const SLOTS_PER_DAY = 24 * 60 / SLOT_MIN;

  // Build header row
  const corner = document.createElement('div'); corner.className = 'h'; corner.textContent = '';
  grid.appendChild(corner);
  for (const d of DAYS) {
    const h = document.createElement('div'); h.className = 'h'; h.textContent = d;
    grid.appendChild(h);
  }
  for (let i = 0; i < SLOTS_PER_DAY; i++) {
    const startMin = i * SLOT_MIN;
    const hh = String(Math.floor(startMin/60)).padStart(2,'0');
    const mm = String(startMin%60).padStart(2,'0');
    const label = document.createElement('div'); label.className = 'l'; label.textContent = `${hh}:${mm}`;
    grid.appendChild(label);
    for (let dow = 0; dow < 7; dow++) {
      const cell = document.createElement('div');
      cell.className = 'cell';
      cell.dataset.dow = dow;
      cell.dataset.startMinute = startMin;
      grid.appendChild(cell);
    }
  }

  // Paint existing slots
  for (const s of SLOTS) {
    const cell = grid.querySelector(`.cell[data-dow="${s.day_of_week}"][data-start-minute="${s.start_minute}"]`);
    if (cell) {
      cell.classList.add('has-slot');
      cell.dataset.slotId = s.id;
      cell.title = `${s.label || s.rule_type} · ${s.length_min}m · ${s.recurrence}`;
      cell.textContent = (s.label || ruleAbbrev(s.rule_type)).slice(0, 18);
    }
  }

  function ruleAbbrev(t) {
    return ({fixed_episode:'FIX',show_rotation:'ROT',category_pool:'POOL',stunt_block:'STUNT'})[t] || t;
  }

  // ── Drag from palette → cell ────────────────────────────────────
  let dragData = null;
  document.querySelectorAll('.show-tile').forEach(tile => {
    tile.addEventListener('dragstart', ev => {
      dragData = {
        showId: tile.dataset.showId ? parseInt(tile.dataset.showId, 10) : null,
        rule: tile.dataset.rule || null,
        rulePayload: tile.dataset.rulePayload || null,
        label: tile.textContent.trim(),
      };
      ev.dataTransfer.effectAllowed = 'copy';
    });
  });
  grid.addEventListener('dragover', ev => {
    const cell = ev.target.closest('.cell');
    if (!cell) return;
    ev.preventDefault();
    cell.classList.add('drag-over');
  });
  grid.addEventListener('dragleave', ev => {
    const cell = ev.target.closest('.cell');
    if (cell) cell.classList.remove('drag-over');
  });
  grid.addEventListener('drop', async ev => {
    const cell = ev.target.closest('.cell');
    if (!cell || !dragData) return;
    ev.preventDefault();
    cell.classList.remove('drag-over');
    const dow = parseInt(cell.dataset.dow, 10);
    const startMin = parseInt(cell.dataset.startMinute, 10);

    let rule_type = 'show_rotation';
    let rule_payload = '{}';
    if (dragData.showId != null) {
      rule_type = 'show_rotation';
      rule_payload = JSON.stringify({ show_id: dragData.showId, policy: 'newest_unaired' });
    } else if (dragData.rule) {
      rule_type = dragData.rule;
      rule_payload = dragData.rulePayload || '{}';
    }
    await postSlot({
      slot_id: cell.dataset.slotId || '',
      label: dragData.label,
      day_of_week: dow,
      start_minute: startMin,
      length_min: SLOT_MIN,
      rule_type,
      rule_payload,
      recurrence: 'weekly',
    });
    location.reload();
  });

  // ── Click cell → edit dialog ────────────────────────────────────
  const dlg = document.getElementById('slot-editor');
  grid.addEventListener('click', ev => {
    const cell = ev.target.closest('.cell');
    if (!cell || !dlg) return;
    document.getElementById('f-slot-id').value = cell.dataset.slotId || '';
    document.getElementById('f-label').value = cell.textContent.trim();
    document.getElementById('f-dow').value = cell.dataset.dow;
    document.getElementById('f-start').value = cell.dataset.startMinute;
    document.getElementById('f-length').value = SLOT_MIN;
    dlg.showModal();
  });
  if (dlg) {
    dlg.addEventListener('close', async () => {
      if (dlg.returnValue === 'save') {
        const fd = new FormData(document.getElementById('slot-form'));
        await postSlot(Object.fromEntries(fd.entries()));
        location.reload();
      } else if (dlg.returnValue === 'delete') {
        const id = document.getElementById('f-slot-id').value;
        if (id) {
          await postForm('/api/slot/delete', { slot_id: id });
          location.reload();
        }
      }
    });
  }

  function getToken() {
    let t = localStorage.getItem('nn_admin_token');
    if (!t) { t = prompt('Admin token?') || ''; localStorage.setItem('nn_admin_token', t); }
    return t;
  }
  async function postSlot(data) { return postForm('/api/slot', data); }
  async function postForm(url, data) {
    const fd = new FormData();
    for (const [k,v] of Object.entries(data)) fd.append(k, v);
    const r = await fetch(url, { method: 'POST', body: fd, headers: { Authorization: 'Bearer ' + getToken() }});
    if (!r.ok) {
      if (r.status === 401) { localStorage.removeItem('nn_admin_token'); }
      alert('Request failed: ' + r.status + ' ' + (await r.text()));
    }
    return r;
  }
})();
