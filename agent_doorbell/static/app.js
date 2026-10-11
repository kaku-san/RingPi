"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = { status: null, events: [], profiles: [], proposals: [], view: "home", filter: "all", preview: false, snapshotBusy: false, currentEvent: null, frame: 0, audio: null, sound: false, zone: null, offset: 0, more: false };
let toastTimer, previewTimer, playbackTimer, summaryTimer;
const timeFormat = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const dayFormat = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const fullFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" });

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...options.headers } });
  if (response.status === 204) return null;
  const body = await response.json();
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : "Please check the entered values.";
    const error = new Error(detail); error.body = body; throw error;
  }
  return body;
}

function toast(message, alert = false) {
  clearTimeout(toastTimer);
  const target = $("#toast"); target.textContent = message; target.className = `toast${alert ? " alert" : ""}`; target.hidden = false;
  toastTimer = setTimeout(() => { target.hidden = true; }, alert ? 7000 : 4000);
}

function reportError(error) { toast(error.message || "The device is not responding. Please try again."); }
function formatDate(raw) { const date = new Date(raw); return Number.isNaN(date.valueOf()) ? "Time unavailable" : fullFormat.format(date); }
function eventTitle(event) { return event.kind === "test" ? "Test doorbell ring" : event.kind === "ring" ? "Someone rang the bell" : "Motion at your door"; }
function personLabel(event) {
  if (event.person_status === "probable_match" && event.person_name) {
    const score = typeof event.face_similarity === "number" ? ` (${Number(event.face_similarity).toFixed(2)})` : "";
    return `Probably ${event.person_name}${score}`;
  }
  if (event.person_status === "manual" && event.person_name) return event.person_name;
  if (["unknown", "uncertain"].includes(event.person_status) || ["unknown", "uncertain"].includes(event.face_status)) return "unknown";
  if (event.face_status && !["unavailable", "not_enrolled", "pending"].includes(event.face_status)) return ({ no_face: "No clear face", multiple_faces: "Multiple faces", low_quality: "Face too small or blurry", enrollment_deleted: "Enrollment deleted" })[event.face_status] || "unknown";
  return ({ detected: "Person detected", not_detected: "No person detected", unavailable: "Person detection unavailable" })[event.person_status] || "unknown";
}

function switchView(view) {
  if (!["home", "history", "people", "agent"].includes(view)) view = "home";
  state.view = view;
  $$(".view").forEach((element) => { element.hidden = element.id !== `view-${view}`; element.classList.toggle("active", !element.hidden); });
  $$(".nav-item").forEach((button) => { button.classList.toggle("active", button.dataset.view === view); button.setAttribute("aria-current", button.dataset.view === view ? "page" : "false"); });
  $("#page-crumb").textContent = ({ home: "Overview", history: "Activity", people: "Familiar people", agent: "Guest approval" })[view];
  if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
  if (view !== "home") stopPreview();
  if (view === "people") loadProfiles().catch(reportError);
  if (view === "agent") { loadProposals().catch(reportError); loadBots().catch(reportError); loadAccess().catch(reportError); }
  window.scrollTo({ top: 0, behavior: "instant" });
}
$$("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
$(".brand").addEventListener("click", () => switchView("home"));
window.addEventListener("hashchange", () => switchView(location.hash.slice(1)));
$$("[data-close]").forEach((button) => button.addEventListener("click", () => $(`#${button.dataset.close}`).close()));
$$("dialog").forEach((dialog) => dialog.addEventListener("close", () => { if (dialog.id === "event-dialog") clearInterval(playbackTimer); }));

function renderStatus(status) {
  state.status = status;
  const camera = status.camera;
  $("#simulated-banner").hidden = !camera.simulated;
  const cameraNames = { online: "Camera ready", offline: "Camera offline", busy: "Camera in use", unverified: "Ready to check", ready: "Test camera ready" };
  $("#camera-state").textContent = cameraNames[camera.state] || camera.state;
  $("#camera-state").className = `status-badge ${camera.state === "online" ? "good" : "warning"}`;
  $("#camera-dot").className = `tiny-dot ${camera.state === "online" ? "online" : "offline"}`;
  $("#glance-camera").textContent = ({ online: "Ready", offline: "Unavailable", busy: "In use", unverified: "Not checked", ready: "Test fixture" })[camera.state] || camera.state;
  $("#glance-motion").textContent = status.settings.motion_enabled ? "Watching your zone" : "Paused";
  $("#glance-presence").textContent = status.settings.person_alerts_enabled ? `After ${status.settings.person_alert_seconds}s` : "Off";
  $("#glance-quiet").textContent = status.settings.quiet_hours_enabled ? `${status.settings.quiet_hours_start}–${status.settings.quiet_hours_end}` : "Off";
  $("#glance-person").textContent = status.local_vision?.objects_available ? "Object detector ready" : status.detector.available ? "Pedestrian detector ready" : "Not configured";
  const captions = status.local_captions;
  $("#glance-caption").textContent = !captions?.enabled ? "Off" : !captions.provider?.available ? "Needs installation" : captions.active ? "Describing locally" : "Ready to try";
  $("#retention-badge").textContent = `${status.settings.retention_days}-day retention`;
  if (camera.has_frame) {
    const image = $("#camera-image");
    const next = `/api/camera/latest.jpg?v=${camera.sequence}`;
    if (image.getAttribute("src") !== next) image.src = next;
    image.hidden = false;
    $("#camera-placeholder").hidden = true;
    $("#frame-overlay").hidden = false;
    $("#frame-time").textContent = formatDate(camera.captured_at);
    $("#camera-caption").textContent = camera.state === "online" ? `Updated ${timeFormat.format(new Date(camera.captured_at))}` : `Last image · ${camera.detail}`;
  } else {
    $("#camera-empty-title").textContent = camera.state === "offline" ? "Your camera isn’t connected yet" : camera.state === "busy" ? "Giving your camera some space" : "A window to your doorstep";
    $("#camera-empty-detail").textContent = camera.detail;
    $("#camera-caption").textContent = "Ring alerts work without the camera";
  }
  const zone = status.settings.zone;
  $("#motion-overlay").hidden = !camera.has_frame || !status.settings.motion_enabled;
  ["x", "y", "width", "height"].forEach((key) => $("#motion-rect").setAttribute(key, zone[key] * 100));
}

function emptyEvents(title = "A quiet moment at home.", message = "Your rings and motion events will appear here. Try the test doorbell to begin.") {
  const element = node("div", "empty-events"); element.append(node("div", "empty-icon", "◷"));
  const copy = node("div"); copy.append(node("strong", null, title), node("p", null, message)); element.append(copy); return element;
}

function eventRow(event) {
  const row = node("button", "event-row"); row.type = "button"; row.dataset.eventId = event.id;
  row.setAttribute("aria-label", `${eventTitle(event)}, ${formatDate(event.occurred_at)}, ${event.capture_status}`);
  const thumb = node("div", "event-thumb");
  if (event.frames) { const image = node("img"); image.src = event.media[0]; image.alt = "Event snapshot"; image.loading = "lazy"; thumb.append(image); }
  else thumb.textContent = event.kind === "motion" ? "◎" : "♧";
  const copy = node("div", "event-copy");
  const heading = node("div"); heading.append(node("strong", null, eventTitle(event)));
  if (event.kind === "test" || event.simulated) heading.append(node("span", "event-tag test", event.simulated ? "SIMULATED" : "TEST"));
  else if (!event.acknowledged) heading.append(node("span", "event-tag", "NEW"));
  const sub = event.capture_status === "pending" ? "Saving a short capture…" : event.frames ? `${event.frames} snapshot${event.frames === 1 ? "" : "s"} · ${personLabel(event)}` : event.camera_error || "No camera image available";
  copy.append(heading, node("p", null, sub));
  if (event.description) copy.append(node("p", "local-observation", `Local observations: ${event.description}`));
  if (event.caption) copy.append(node("p", "local-observation", `Local model (unverified): ${event.caption}`));
  const date = new Date(event.occurred_at); const time = node("div", "event-time", timeFormat.format(date)); time.append(node("small", null, dayFormat.format(date)));
  row.append(thumb, copy, time, node("span", "event-arrow", "↗"));
  row.addEventListener("click", () => openEvent(event.id));
  return row;
}

function ringStateLabel(event) {
  if (event.capture_status === "pending") return "Capturing";
  if (event.capture_status !== "complete") return event.capture_status;
  return event.kind === "test" || event.simulated ? "Test ring" : "Ring";
}

function renderLatestRing() {
  const event = state.events.find((item) => item.kind !== "motion");
  const badge = $("#latest-ring-state");
  const body = $("#latest-ring-body");
  if (!badge || !body) return;
  if (!event) {
    badge.textContent = "No ring yet";
    badge.className = "status-badge";
    body.replaceChildren(node("p", "latest-ring-meta", "A ring shows its photo, caption, and state here."));
    return;
  }
  badge.textContent = ringStateLabel(event);
  badge.className = `status-badge ${event.capture_status === "complete" ? "good" : "warning"}`;
  const photo = node("div", "latest-ring-photo");
  if (event.frames) {
    const image = node("img");
    image.src = event.media[0];
    image.alt = "Latest ring snapshot";
    photo.append(image);
  } else photo.append(node("p", null, event.camera_error || "No camera image for this ring."));
  const copy = node("div", "latest-ring-copy");
  copy.append(node("h3", null, eventTitle(event)));
  if (event.caption) copy.append(node("p", "latest-ring-caption", `Local model (unverified): ${event.caption}`));
  else if (["queued", "running"].includes(event.caption_status)) copy.append(node("p", "latest-ring-caption", "A local model caption is processing separately."));
  else if (event.caption_error) copy.append(node("p", "latest-ring-caption", event.caption_error));
  else copy.append(node("p", "latest-ring-caption", "No caption yet."));
  if (event.description) copy.append(node("p", "latest-ring-meta", `Local observations: ${event.description}`));
  copy.append(node("p", "latest-ring-meta", `${personLabel(event)}. Capture: ${event.capture_status}.`));
  body.replaceChildren(photo, copy);
}

function renderEvents() {
  state.events.sort((a, b) => b.occurred_at.localeCompare(a.occurred_at));
  renderLatestRing();
  $("#recent-events").replaceChildren(...(state.events.length ? state.events.slice(0, 4).map(eventRow) : [emptyEvents()]));
  const filtered = state.events.filter((event) => state.filter === "all" || (state.filter === "ring" ? event.kind !== "motion" : event.kind === "motion"));
  $("#all-events").replaceChildren(...(filtered.length ? filtered.map(eventRow) : [emptyEvents("Nothing here just yet.", "New activity matching this filter will appear here.")]));
  clearTimeout(summaryTimer);
  summaryTimer = setTimeout(() => loadSummary().catch(() => {}), 100);
  $("#load-more").hidden = !state.more;
}

async function loadSummary() {
  const midnight = new Date(); midnight.setHours(0, 0, 0, 0);
  const summary = await api(`/api/events/summary?since=${encodeURIComponent(midnight.toISOString())}`);
  $("#today-count").textContent = summary.total;
  $("#ring-count").textContent = `${summary.rings} rings & tests`;
  $("#motion-count").textContent = `${summary.motion} motion events`;
  $("#nav-event-count").textContent = summary.unread;
}

function upsertEvent(event) {
  const index = state.events.findIndex((item) => item.id === event.id);
  if (index === -1) state.events.unshift(event); else state.events[index] = event;
  renderEvents();
  if (state.currentEvent?.id === event.id && $("#event-dialog").open) { state.currentEvent = event; renderEventMedia(); }
}

async function loadEvents(more = false) {
  const offset = more ? state.offset : 0;
  const body = await api(`/api/events?limit=100&offset=${offset}`);
  state.more = body.events.length === 100;
  state.offset = offset + body.events.length;
  if (!more) state.events = body.events;
  else body.events.forEach((event) => { if (!state.events.some((old) => old.id === event.id)) state.events.push(event); });
  renderEvents();
}
$("#load-more").addEventListener("click", () => loadEvents(true).catch(reportError));
$$("[data-filter]").forEach((button) => button.addEventListener("click", () => { state.filter = button.dataset.filter; $$("[data-filter]").forEach((item) => item.classList.toggle("active", item === button)); renderEvents(); }));

async function armSound() {
  try {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) throw new Error("This browser doesn’t support the chime.");
    state.audio ||= new Audio(); await state.audio.resume(); state.sound = true;
    $("#sound-toggle").classList.add("enabled"); $("#sound-toggle").setAttribute("aria-label", "Mute chime");
  } catch (error) { reportError(error); }
}
function chime() {
  if (!state.sound || !state.audio || state.audio.state !== "running") return;
  [659.25, 523.25].forEach((frequency, index) => {
    const oscillator = state.audio.createOscillator(); const gain = state.audio.createGain();
    const at = state.audio.currentTime + index * 0.27;
    oscillator.type = "sine"; oscillator.frequency.value = frequency; oscillator.connect(gain); gain.connect(state.audio.destination);
    gain.gain.setValueAtTime(0, at); gain.gain.linearRampToValueAtTime(0.18, at + 0.015); gain.gain.exponentialRampToValueAtTime(0.001, at + 0.7);
    oscillator.start(at); oscillator.stop(at + 0.75);
  });
}
$("#sound-toggle").addEventListener("click", async () => {
  if (state.sound) { state.sound = false; $("#sound-toggle").classList.remove("enabled"); $("#sound-toggle").setAttribute("aria-label", "Enable chime"); toast("Chime muted on this browser."); }
  else { await armSound(); chime(); toast("Chime enabled while this browser stays connected."); }
});
$("#test-ring").addEventListener("click", async () => {
  const button = $("#test-ring"); button.disabled = true;
  try { const result = await api("/api/ring", { method: "POST", body: JSON.stringify({ source: "browser" }) }); upsertEvent(result.event); if (result.duplicate) toast("That ring is already being captured."); }
  catch (error) { reportError(error); }
  finally { button.disabled = false; }
});

async function snapshot(quiet = false) {
  if (state.snapshotBusy) return;
  if (!state.status) { if (!quiet) toast("Connecting to your device. Please try again shortly."); return; }
  state.snapshotBusy = true; $("#snapshot").disabled = true;
  try { const result = await api("/api/camera/snapshot", { method: "POST", body: "{}" }); renderStatus({ ...state.status, camera: result.camera }); if (!quiet) toast("Fresh snapshot. Stored only on this device."); }
  catch (error) { if (error.body?.camera) renderStatus({ ...state.status, camera: error.body.camera }); if (!quiet) reportError(error); }
  finally { state.snapshotBusy = false; $("#snapshot").disabled = false; }
}
$("#snapshot").addEventListener("click", () => snapshot());
function stopPreview() { state.preview = false; clearTimeout(previewTimer); $("#preview-toggle").textContent = "▶ Start preview"; }
async function previewLoop() {
  if (!state.preview || document.hidden) return;
  await snapshot(true);
  if (state.preview) previewTimer = setTimeout(previewLoop, 3000);
}
$("#preview-toggle").addEventListener("click", () => { if (state.preview) stopPreview(); else { state.preview = true; $("#preview-toggle").textContent = "Ⅱ Pause preview"; previewLoop(); } });
document.addEventListener("visibilitychange", () => { clearTimeout(previewTimer); if (!document.hidden && state.preview) previewLoop(); });

async function openEvent(eventId) {
  try {
    const [event] = await Promise.all([api(`/api/events/${eventId}`), loadProfiles()]); state.currentEvent = event; state.frame = 0;
    $("#event-title").textContent = eventTitle(event); $("#event-note").value = event.note;
    const options = [new Option("Unknown", "unknown"), new Option("Uncertain", "uncertain"), ...state.profiles.map((profile) => new Option(`${profile.name} · manual label`, profile.id))];
    $("#event-person").replaceChildren(...options); $("#event-person").value = event.person_id || (event.person_status === "uncertain" ? "uncertain" : "unknown");
    renderEventMedia(); $("#event-dialog").showModal();
  } catch (error) { reportError(error); }
}
function renderEventMedia() {
  const event = state.currentEvent; if (!event) return;
  const media = $("#event-media"); media.replaceChildren(); state.frame = Math.min(state.frame, Math.max(0, event.frames - 1));
  if (event.frames) {
    const image = node("img"); image.alt = `${event.simulated ? "Synthetic test" : "Local event"} capture, frame ${state.frame + 1}`; image.src = event.media[state.frame]; media.append(image);
    const strip = node("div", "capture-strip");
    const previous = node("button", null, "← Previous"); previous.type = "button"; previous.addEventListener("click", () => { state.frame = (state.frame - 1 + event.frames) % event.frames; renderEventMedia(); });
    const next = node("button", null, "Next →"); next.type = "button"; next.addEventListener("click", () => { state.frame = (state.frame + 1) % event.frames; renderEventMedia(); });
    strip.append(previous, node("span", null, `Frame ${state.frame + 1} / ${event.frames} · snapshot sequence`), next); media.append(strip);
  } else media.append(emptyEvents(event.capture_status === "pending" ? "Capturing this moment…" : "No camera image for this event.", event.camera_error || "The event itself is safely recorded."));
  $("#event-meta").replaceChildren(node("strong", null, formatDate(event.occurred_at)), node("br"), document.createTextNode(`${personLabel(event)}. Capture: ${event.capture_status}.${event.simulated ? " Synthetic test camera." : ""}`));
  if (event.description) $("#event-meta").append(node("p", null, `Local observations: ${event.description}`));
  else if (event.description_status === "no_confident_observations") $("#event-meta").append(node("p", null, "No confident local observations. This does not establish that the scene is empty."));
  if (event.caption) $("#event-meta").append(node("p", null, `Local model caption (unverified): ${event.caption}`));
  else if (["queued", "running"].includes(event.caption_status)) $("#event-meta").append(node("p", null, "A local model caption is processing separately. Quick observations are already available."));
  else if (event.caption_error) $("#event-meta").append(node("p", null, event.caption_error));
  if (event.local_analysis) {
    const analysis = event.local_analysis;
    const details = node("details", "setup-guide");
    details.id = "event-recognition";
    details.append(node("summary", null, "Recognition details"));
    const objects = [...new Set((analysis.objects || []).map(o => o.label))].join(", ");
    details.append(node("p", "muted small", `${objects ? `Detected: ${objects}. ` : ""}Local analysis: ${analysis.timings_ms?.analysis_total ?? "—"} ms.`));
    if (Array.isArray(analysis.frames) && analysis.frames.length) {
      const labels = {probable_match: "Probable enrolled match", uncertain: "Uncertain face match", unknown: "Unknown enrolled identity", not_enrolled: "No compatible references", no_face: "No clear face", low_quality: "Face too small or blurry", multiple_faces: "Multiple faces", unavailable: "Analysis unavailable", enrollment_deleted: "Enrollment deleted"};
      const results = analysis.frames.slice(0, 5).map(frame => `Frame ${frame.index + 1}: ${frame.error === "analysis_busy" ? "Analysis busy" : labels[frame.status] || "Analysis unavailable"}`);
      details.append(node("p", "muted small", `Recognition checked ${analysis.frames.length} of ${analysis.expected_frames} frames. ${results.join("; ")}.`));
      const reasons = {identity_disagreement: "Different people matched across frames. No single identity was assigned.", conflicting_face_evidence: "The clear faces did not agree. No single identity was assigned.", multiple_people: "Several people were detected. No single identity was assigned.", analysis_incomplete: "Some frames could not be checked. No single identity was assigned.", burst_in_progress: "Checking the remaining frames before assigning an identity.", enrollment_deleted: "Enrollment was deleted; prior automatic identity evidence was cleared."};
      if (reasons[analysis.aggregation?.reason]) details.append(node("p", null, reasons[analysis.aggregation.reason]));
    }
    if (analysis.status === "probable_match") {
      const score = typeof analysis.similarity === "number" ? analysis.similarity : event.face_similarity;
      const line = event.person_name && typeof score === "number" ? `Probably ${event.person_name} (${Number(score).toFixed(2)})` : "unknown";
      $("#event-meta").append(node("p", null, line));
    }
    const admission = analysis.admission;
    if (admission && admission.device_label === "DONE" && admission.server_confirmed === true && admission.real_actuation === false && admission.hardware_confirmed === false && admission.identity_proven === false) {
      $("#event-meta").append(node("p", null, "Simulated open."));
    } else if (admission && admission.text) {
      $("#event-meta").append(node("p", null, admission.text));
    }
    $("#event-meta").append(details);
  }
}
$("#event-form").addEventListener("submit", async (event) => {
  event.preventDefault(); if (!state.currentEvent) return;
  const selection = $("#event-person").value;
  const payload = { acknowledged: true, note: $("#event-note").value, person_id: ["unknown", "uncertain"].includes(selection) ? null : selection, person_status: ["unknown", "uncertain"].includes(selection) ? selection : "manual" };
  try { upsertEvent(await api(`/api/events/${state.currentEvent.id}`, { method: "PATCH", body: JSON.stringify(payload) })); $("#event-dialog").close(); toast("Your note and correction are saved locally."); } catch (error) { reportError(error); }
});
$("#delete-event").addEventListener("click", async () => {
  if (!state.currentEvent || !confirm("Delete this event and its local captures?")) return;
  try { const id = state.currentEvent.id; await api(`/api/events/${id}`, { method: "DELETE" }); state.events = state.events.filter((event) => event.id !== id); renderEvents(); $("#event-dialog").close(); toast("Event and captures deleted."); } catch (error) { reportError(error); }
});

function openSettings() {
  if (!state.status) return toast("Waiting for the device to connect.");
  const settings = state.status.settings; state.zone = { ...settings.zone };
  $("#motion-enabled").checked = settings.motion_enabled;
  $("#motion-threshold").value = Math.max(1, Math.min(100, Math.round(101 - settings.motion_threshold * 500)));
  $("#cooldown").value = settings.cooldown_seconds; $("#retention").value = settings.retention_days;
  $("#person-alerts-enabled").checked = Boolean(settings.person_alerts_enabled);
  $("#person-alert-seconds").value = settings.person_alert_seconds;
  $("#person-alert-cooldown").value = Math.max(1, Math.round(settings.person_alert_cooldown_seconds / 60));
  $("#quiet-hours-enabled").checked = Boolean(settings.quiet_hours_enabled);
  $("#quiet-start").value = settings.quiet_hours_start;
  $("#quiet-end").value = settings.quiet_hours_end;
  $("#summary-enabled").checked = settings.summary_enabled !== false;
  $("#summary-time").value = settings.summary_time;
  $("#timezone").value = settings.timezone;
  const image = $("#zone-image"); image.hidden = !state.status.camera.has_frame;
  if (!image.hidden) image.src = `/api/camera/latest.jpg?v=${state.status.camera.sequence}`;
  $("#settings-error").textContent = ""; renderZone(); $("#settings-dialog").showModal();
}
$$(".settings-open").forEach((button) => button.addEventListener("click", openSettings));
function renderZone() {
  ["x", "y", "width", "height"].forEach((key) => { $("#zone-rect").setAttribute(key, state.zone[key] * 100); $(`#zone-${key}`).value = Math.round(state.zone[key] * 100); });
}
["x", "y", "width", "height"].forEach((key) => $(`#zone-${key}`).addEventListener("input", () => {
  state.zone[key] = Number($(`#zone-${key}`).value) / 100;
  ["x", "y", "width", "height"].forEach((field) => $("#zone-rect").setAttribute(field, state.zone[field] * 100));
}));
let zoneStart;
function zonePoint(event) { const rect = $("#zone-editor").getBoundingClientRect(); return { x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) }; }
$("#zone-editor").addEventListener("pointerdown", (event) => { zoneStart = zonePoint(event); $("#zone-editor").setPointerCapture(event.pointerId); });
$("#zone-editor").addEventListener("pointermove", (event) => {
  if (!zoneStart) return;
  const point = zonePoint(event); state.zone = { x: Math.min(zoneStart.x, point.x), y: Math.min(zoneStart.y, point.y), width: Math.abs(point.x - zoneStart.x), height: Math.abs(point.y - zoneStart.y) }; renderZone();
});
$("#zone-editor").addEventListener("pointerup", () => { zoneStart = null; });
$("#zone-editor").addEventListener("pointercancel", () => { zoneStart = null; });
$("#settings-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const payload = { motion_enabled: $("#motion-enabled").checked, motion_threshold: Math.max(0.005, (101 - Number($("#motion-threshold").value)) / 500), cooldown_seconds: Number($("#cooldown").value), retention_days: Number($("#retention").value), zone: state.zone, person_alerts_enabled: $("#person-alerts-enabled").checked, person_alert_seconds: Number($("#person-alert-seconds").value), person_alert_cooldown_seconds: Number($("#person-alert-cooldown").value) * 60, quiet_hours_enabled: $("#quiet-hours-enabled").checked, quiet_hours_start: $("#quiet-start").value, quiet_hours_end: $("#quiet-end").value, timezone: $("#timezone").value.trim(), summary_enabled: $("#summary-enabled").checked, summary_time: $("#summary-time").value };
  try { const settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) }); renderStatus({ ...state.status, settings }); $("#settings-dialog").close(); await loadEvents(); toast("Settings saved on your device."); }
  catch (error) { $("#settings-error").textContent = error.message; }
});

async function loadProfiles() {
  const body = await api("/api/profiles"); state.profiles = body.profiles; state.profileVision = body.vision;
  $("#face-model-state").textContent = body.vision?.available ? "YuNet + SFace ready locally. Conservative matching thresholds need evaluation at your door." : body.vision?.detail || "Local face models unavailable";
  const enrolled = state.profiles.filter(person => person.face_samples > 0).length;
  $("#people-setup-count").textContent = `${state.profiles.length} local ${state.profiles.length === 1 ? "person" : "people"} · ${enrolled} with reference photos`;
  $("#setup-add-people").textContent = state.profiles.length ? "Manage people →" : "Add people →";
  $("#recognition-open").disabled = !body.vision?.available || !enrolled;
  const cards = state.profiles.map((person) => {
    const card = node("article", "card person-card");
    const remove = node("button", "remove-person", "×"); remove.setAttribute("aria-label", `Remove ${person.name}`); remove.addEventListener("click", async () => {
      if (!confirm(`Remove ${person.name}’s local profile? Events will be marked unknown.`)) return;
      try { await api(`/api/profiles/${person.id}`, { method: "DELETE" }); await Promise.all([loadProfiles(), loadEvents()]); toast("Profile removed."); } catch (error) { reportError(error); }
    });
    const initials = person.name.split(/\s+/).slice(0, 2).map((word) => word[0]).join("").toUpperCase();
    const edit = node("button", "secondary-button", "Edit name"); edit.addEventListener("click", () => openPerson(person));
    const enroll = node("button", "secondary-button", "Enroll photo"); enroll.disabled = !body.vision?.available || person.face_samples >= 5;
    enroll.addEventListener("click", () => openEnrollment(person));
    const actions = node("div", "person-actions"); actions.append(edit, enroll);
    if (person.face_samples) {
      const test = node("button", "secondary-button", "Test recognition"); test.disabled = !body.vision?.available;
      test.addEventListener("click", () => openRecognition(person)); actions.append(test);
      const clear = node("button", "text-button", "Delete enrolled photos");
      clear.addEventListener("click", async () => { if (!confirm(`Delete ${person.name}'s enrolled photos and embeddings?`)) return; try { await api(`/api/profiles/${person.id}/enrollment`, { method: "DELETE" }); await Promise.all([loadProfiles(), loadEvents()]); toast("Enrollment deleted from this device."); } catch (error) { reportError(error); } }); actions.append(clear);
    }
    const windowNote = person.visit_window ? ` · WINDOW ${person.visit_window}` : "";
    card.append(remove, node("div", "person-initials", initials), node("h3", null, person.name), node("p", null, person.note || "A familiar person at your door."), node("small", null, `${person.face_samples || 0} LOCAL PHOTOS · PROBABLE MATCHING${windowNote}`), actions); return card;
  });
  $("#people-list").replaceChildren(...(cards.length ? cards : [emptyEvents("Make room for familiar people.", "Add a name, enroll chosen photos with permission, then test with another photo.")]));
}

let personSaving = false, enrollmentBusy = false, recognitionBusy = false;
const photoURLs = new Map();
function clearPhoto(prefix) {
  if (photoURLs.has(prefix)) { URL.revokeObjectURL(photoURLs.get(prefix)); photoURLs.delete(prefix); }
  $(`#${prefix}-photo`).value = ""; $(`#${prefix}-preview`).hidden = true; $(`#${prefix}-preview-image`).removeAttribute("src");
  $(`#${prefix}-preview-name`).textContent = "";
}
function chosenPhoto(prefix) {
  const file = $(`#${prefix}-photo`).files[0];
  if (!file) return null;
  if (file.size > 8_000_000) throw new Error("Choose a photo under 8 MB. Export a smaller JPEG/PNG or use a cropped screenshot.");
  if (!["image/jpeg", "image/png"].includes(file.type) && !(!file.type && /\.(jpe?g|png)$/i.test(file.name))) {
    throw new Error("Choose a JPEG or PNG. For an iPhone HEIC photo, export as JPEG/PNG or use a cropped screenshot.");
  }
  return file;
}
function busyDialog(id, busy) { $$(`#${id} button, #${id} input`).forEach(element => { element.disabled = busy; }); }
for (const prefix of ["enroll", "recognition"]) {
  $(`#${prefix}-photo`).addEventListener("change", () => {
    $(`#${prefix}-error`).textContent = "";
    const feedback = $(`#${prefix === "enroll" ? "enroll-feedback" : "recognition-result"}`); feedback.hidden = true;
    if (photoURLs.has(prefix)) { URL.revokeObjectURL(photoURLs.get(prefix)); photoURLs.delete(prefix); }
    $(`#${prefix}-preview`).hidden = true;
    try {
      const file = chosenPhoto(prefix); if (!file) return;
      const url = URL.createObjectURL(file); photoURLs.set(prefix, url);
      $(`#${prefix}-preview-image`).src = url; $(`#${prefix}-preview-name`).textContent = file.name; $(`#${prefix}-preview`).hidden = false;
    } catch (error) { $(`#${prefix}-error`).textContent = error.message; }
  });
  $(`#${prefix}-dialog`).addEventListener("close", () => { clearPhoto(prefix); $(`#${prefix}-error`).textContent = ""; });
}
for (const [id, isBusy] of [["person-dialog", () => personSaving], ["enroll-dialog", () => enrollmentBusy], ["recognition-dialog", () => recognitionBusy]]) {
  $(`#${id}`).addEventListener("cancel", event => { if (isBusy()) event.preventDefault(); });
}
function openPerson(person = null) {
  state.peopleFlow = person;
  $("#person-form").reset(); $("#person-error").textContent = "";
  $("#person-name").value = person?.name || ""; $("#person-note").value = person?.note || "";
  $("#person-title").textContent = person ? "Edit a person" : "Add a person";
  $("#person-dialog").showModal();
}
function renderEnrollmentState() {
  const person = state.peopleFlow;
  const count = person?.face_samples || 0, ready = !!state.profileVision?.available;
  $("#enroll-count").textContent = `${count} of 5 reference photos saved locally.${!ready ? " Local recognition models are unavailable; your named profile is saved." : count >= 5 ? " Reference photo limit reached. You can test or manage this person's enrollment." : " Choose a photo, then enroll it."}`;
  $("#enroll-save").disabled = enrollmentBusy || !ready || count >= 5;
  $("#enroll-photo").disabled = enrollmentBusy || !ready || count >= 5;
  $("#enroll-next").disabled = enrollmentBusy || !ready || !count;
}
function openEnrollment(person) {
  state.peopleFlow = state.profiles.find(item => item.id === person.id) || person;
  $("#enroll-form").reset(); clearPhoto("enroll"); $("#enroll-error").textContent = ""; $("#enroll-feedback").hidden = true;
  $("#enroll-title").textContent = `Photos for ${state.peopleFlow.name}`;
  renderEnrollmentState(); $("#enroll-dialog").showModal();
}
function openRecognition(person = null) {
  state.recognitionTarget = person;
  $("#recognition-form").reset(); clearPhoto("recognition"); $("#recognition-error").textContent = ""; $("#recognition-result").hidden = true;
  $("#recognition-target").textContent = person ? `Testing against your local references for ${person.name}. A different enrolled person may match.` : "Compare your chosen photo against all enrolled people on this Pi.";
  $("#recognition-back").hidden = !person; $("#recognition-dialog").showModal();
}
$("#setup-add-people").addEventListener("click", () => { switchView("people"); if (!state.profiles.length) openPerson(); });
$("#add-person").addEventListener("click", () => openPerson());
$("#recognition-open").addEventListener("click", () => openRecognition());
$("#enroll-back").addEventListener("click", () => { const person = state.peopleFlow; $("#enroll-dialog").close(); openPerson(person); });
$("#enroll-next").addEventListener("click", () => { const person = state.peopleFlow; $("#enroll-dialog").close(); openRecognition(person); });
$("#recognition-back").addEventListener("click", () => { const person = state.recognitionTarget; $("#recognition-dialog").close(); openEnrollment(person); });
$("#person-form").addEventListener("submit", async (event) => {
  event.preventDefault(); if (personSaving) return;
  personSaving = true; busyDialog("person-dialog", true); $("#person-error").textContent = "";
  const current = state.peopleFlow;
  try {
    const person = await api(current ? `/api/profiles/${current.id}` : "/api/profiles", { method: current ? "PUT" : "POST", body: JSON.stringify({ name: $("#person-name").value, note: $("#person-note").value }) });
    state.peopleFlow = person;
    await loadProfiles(); $("#person-dialog").close(); openEnrollment(person);
    toast("Name saved locally. Now choose reference photos.");
  } catch (error) { $("#person-error").textContent = error.message; }
  finally { personSaving = false; busyDialog("person-dialog", false); }
});

$("#enroll-form").addEventListener("submit", async event => {
  event.preventDefault(); if (enrollmentBusy) return;
  $("#enroll-error").textContent = "";
  let file;
  try { file = chosenPhoto("enroll"); } catch (error) { $("#enroll-error").textContent = error.message; return; }
  if (!file || !state.peopleFlow) return;
  enrollmentBusy = true; busyDialog("enroll-dialog", true);
  const person = state.peopleFlow;
  try {
    const response = await fetch(`/api/profiles/${person.id}/enroll`, { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file });
    const body = await response.json(); if (!response.ok) throw new Error(body.detail || "Enrollment failed");
    await loadProfiles(); state.peopleFlow = state.profiles.find(item => item.id === person.id) || { ...person, face_samples: body.face_samples || (person.face_samples || 0) + 1 };
    clearPhoto("enroll");
    $("#enroll-feedback").textContent = "Photo accepted: one face passed the size and sharpness checks. Choose another reference photo or continue to a local test.";
    $("#enroll-feedback").hidden = false; toast("Photo and face embedding enrolled locally.");
  } catch (error) { $("#enroll-error").textContent = error.message; }
  finally { enrollmentBusy = false; busyDialog("enroll-dialog", false); renderEnrollmentState(); }
});
$("#recognition-form").addEventListener("submit", async event => {
  event.preventDefault(); if (recognitionBusy) return;
  $("#recognition-error").textContent = ""; $("#recognition-result").hidden = true;
  let file;
  try { file = chosenPhoto("recognition"); } catch (error) { $("#recognition-error").textContent = error.message; return; }
  if (!file) return;
  recognitionBusy = true; busyDialog("recognition-dialog", true);
  try {
    const response = await fetch("/api/recognition/test", { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file });
    const result = await response.json(); if (!response.ok) throw new Error(result.detail || "Local test failed");
    const messages = { unknown: "unknown", uncertain: "unknown", no_face: "No clear face found. Try a well-lit, front-facing photo.", multiple_faces: "Multiple faces found. Choose a photo of just one person.", low_quality: "The face is too small or blurry. Try a sharper, closer photo.", not_enrolled: "No references match the current model version. Enroll a new reference photo.", enrollment_deleted: "Enrollment changed during the test. Choose an enrolled person and try again.", unavailable: "Local recognition is unavailable." };
    const panel = $("#recognition-result");
    const score = typeof result.similarity === "number" ? result.similarity.toFixed(2) : null;
    const headline = result.status === "probable_match" ? `Probably ${result.person_name || "unknown"}${score ? ` (${score})` : ""}` : (messages[result.status] || "unknown");
    panel.replaceChildren(node("strong", null, headline));
    if (result.status === "probable_match" && state.recognitionTarget && result.profile_id !== state.recognitionTarget.id) panel.append(node("p", null, `A different enrolled person matched.`));
    panel.append(node("p", null, "Test photo not saved. No camera capture or Bot message.")); panel.hidden = false; clearPhoto("recognition");
  } catch (error) { $("#recognition-error").textContent = error.message; }
  finally { recognitionBusy = false; busyDialog("recognition-dialog", false); }
});

let botItems = [], botFormDirty = false, botRingBusy = false;
function updateBotRingButton() {
  const item = botItems.find(b => b.provider === $("#bot-provider").value);
  $("#bot-ring-test").disabled = botRingBusy || botFormDirty || !item?.enabled || !item?.revision;
}
function renderBotSetup() {
  const item = botItems.find(b => b.provider === $("#bot-provider").value);
  if (!item) return;
  const names = { not_connected: "Not connected", configured: "Configured · not tested", test_accepted: "Test accepted · confirm destination", test_verified: "Test verified · sharing paused", enabled: "Ring sharing enabled" };
  $("#bot-state").textContent = names[item.state] || item.state;
  $("#bot-detail").textContent = item.detail;
  $("#bot-fields").hidden = !item.supported;
  const hermes = item.provider === "hermes", c = item.config || {};
  $("#hermes-fields").hidden = !hermes;
  $("#bot-endpoint").placeholder = hermes ? "https://your-hermes-gateway" : "Paste the routine POST URL from your Bot";
  $("#bot-endpoint").value = c.endpoint || ""; $("#bot-session").value = c.session_id || ""; $("#bot-memory").value = c.session_key || "";
  $("#bot-destination").value = c.destination || ""; $("#bot-enabled").checked = item.enabled;
  $("#bot-enabled").disabled = !item.verified || !item.confirmed || !item.credential_present;
  $("#bot-images").checked = !!c.share_images;
  $("#bot-images").disabled = hermes || (!c.share_images && (!item.image_owner_allowed || !item.verified || !item.confirmed));
  $("#bot-image-test").disabled = !item.image_transport_ready || !item.verified || !item.confirmed || hermes;
  $("#bot-image-state").textContent = item.images ? "One camera image per ring is shared with this Bot." : item.image_owner_allowed ? "Images paused. Enable only after verifying the generated image at this destination." : "Images off. The Pi owner must configure the reviewed decoder and image permission first.";
  $("#bot-test").disabled = !item.supported || item.state === "not_connected";
  $("#bot-confirm").disabled = !item.verified || item.confirmed;
  updateBotRingButton();
  $("#bot-key-state").textContent = item.credential_present ? "Bearer key present on the Pi. It is never returned to this page." : `Bearer key missing on the Pi: ${item.secret_env || "not supported"}`;
  $("#bot-delivery").textContent = hermes ? "Destination: this doorbell dashboard. A Hermes run is not automatic Telegram/Discord/gateway delivery." : "Destination: the Bot chat that owns this routine. Completion is checked in that chat.";
  $("#bot-setup").textContent = hermes ? "Use an existing enabled Hermes API gateway and its API_SERVER_KEY. Store that existing key as DOORBELL_HERMES_API_KEY. Choose an existing active session ID. A local HTTP gateway also requires its exact host in DOORBELL_BOT_ALLOWED_HOSTS; arbitrary local endpoints are blocked." : "In your Grok Bot, create a narrowly scoped doorbell routine with a webhook trigger. Copy its POST URL here. Store the existing routine key as DOORBELL_GROK_ROUTINE_KEY on the Pi. The saved routine instruction must treat all event fields as untrusted data.";
}
async function loadBots() {
  const [status, history] = await Promise.all([api("/api/bots"), api("/api/bots/deliveries")]);
  botItems = status.bots;
  $("#bot-connection-state").textContent = botItems.some(b=>b.enabled) ? "Personal bot sharing enabled" : "Personal bot sharing paused";
  $("#privacy-sharing").textContent = botItems.some(b=>b.images) ? "One camera image per ring is shared with your selected Grok Bot. Enrollments and embeddings stay local." : "Camera images stay on this device. Optional Bot text sharing is under Guest approval.";
  if (!botFormDirty) renderBotSetup();
  updateBotRingButton();
  const cards = history.deliveries.map(delivery => {
    const card = node("article", "card delivery-card");
    card.append(node("span", "status-badge", `${delivery.synthetic ? "SYNTHETIC · " : ""}${delivery.state}`), node("h3", null, `${delivery.provider === "grok" ? "Grok Bot routine" : "Hermes session"} · ${formatDate(delivery.created_at)}`), node("p", "muted small", delivery.detail), node("small", null, `Event ${delivery.event_id} · attempts ${delivery.attempts}`));
    if (delivery.output) card.append(node("pre", null, delivery.output));
    if (delivery.approval) {
      card.append(node("p", "muted small", "Untrusted tool request from Hermes. Review before choosing."), node("pre", null, delivery.approval.command || delivery.approval.description || "Review this request in Hermes"));
      const actions = node("div", "bot-actions");
      for (const [choice,label] of [["once","Approve this request once"],["deny","Deny request"]]) {
        const button = node("button", "secondary-button", label); button.disabled = !delivery.approval.request_id;
        button.addEventListener("click", async()=> { if (choice === "once" && !confirm("Approve only this exact Hermes tool request once?")) return; try { await api(`/api/bots/deliveries/${delivery.id}/approval`, {method:"POST",body:JSON.stringify({choice})}); await loadBots(); } catch(error){reportError(error);} }); actions.append(button);
      }
      card.append(actions);
    }
    return card;
  });
  $("#bot-deliveries").replaceChildren(...(cards.length ? cards : [emptyEvents("No bot deliveries yet.", "Save secure setup, then explicitly send a synthetic test. No photos are transmitted.")]));
}
$("#bot-provider").addEventListener("change", ()=>{botFormDirty=false;renderBotSetup();});
$("#bot-form").addEventListener("input", ()=>{botFormDirty=true;updateBotRingButton();});
$("#bot-form").addEventListener("submit", async event=> {
  event.preventDefault(); const provider=$("#bot-provider").value;
  try {
    const shareImages=$("#bot-images").checked;
    await api("/api/bots", {method:"PUT", body:JSON.stringify({provider,endpoint:$("#bot-endpoint").value,session_id:$("#bot-session").value,session_key:$("#bot-memory").value,destination:$("#bot-destination").value,delivery:provider==="hermes"?"dashboard":"owning_bot_chat",share_images:shareImages,enabled:$("#bot-enabled").checked})});
    botFormDirty=false;await loadBots();toast("Setup saved locally. Saving never probes or sends to your bot.");
  }catch(error){reportError(error);}
});
$("#bot-image-test").addEventListener("click", async()=> {
  if(botFormDirty) return toast("Save setup changes before testing.");
  if(!confirm("Send one small generated geometry image to this Grok Bot? This tests transfer and image reading; camera-scene quality still needs verification. No camera or visitor data is used. It may consume a Bot run.")) return;
  const requestId=Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,"0")).join("");
  try{const result=await api("/api/bots/grok/test-image",{method:"POST",body:JSON.stringify({request_id:requestId})});await loadBots();toast(`Generated image queued · ${result.image_bytes} bytes. Check the Bot chat for an actual image description.`);}catch(error){reportError(error);}
});
$("#bot-test").addEventListener("click", async()=> {
  if (botFormDirty) return toast("Save the setup changes before testing.");
  if (!confirm("Send one synthetic text event to your bot? It may use its configured tools. No visitor data or photo will be sent.")) return;
  try{await api(`/api/bots/${$("#bot-provider").value}/test`,{method:"POST",body:"{}"});await loadBots();toast("Synthetic test queued. Check the delivery history and chosen destination.");}catch(error){reportError(error);}
});
$("#bot-confirm").addEventListener("click", async()=> {
  if (!confirm("Have you checked the synthetic test at the chosen Bot chat or dashboard destination?")) return;
  try{await api(`/api/bots/${$("#bot-provider").value}/confirm`,{method:"POST",body:"{}"});await loadBots();toast("Destination confirmed. You can now explicitly enable future physical rings.");}catch(error){reportError(error);}
});
$("#bot-ring-test").addEventListener("click", async()=> {
  if (botFormDirty || botRingBusy) return;
  botRingBusy=true; updateBotRingButton();
  try {
    await loadBots();
    const item=botItems.find(b=>b.provider===$("#bot-provider").value);
    if (!item?.enabled || !item.revision) return toast("Pair and enable this bot before testing a ring.");
    const target={provider:item.provider,revision:item.revision,confirmed:true};
    const media=item.images ? "One current-camera photo will leave the Pi for your Grok cloud workspace. Face embeddings stay on the Pi." : "Photos and face embeddings stay on the Pi.";
    if (!confirm(`Send one TEST ring message to ${item.name} · ${item.config.destination}?\n\nIt includes the name line or unknown, the short caption, and a photo when images are on. ${media} No physical button is pressed.`)) return;
    const result=await api("/api/ring/test-with-bot",{method:"POST",body:JSON.stringify(target)});
    upsertEvent(result.event); await loadBots();
    toast(result.duplicate ? "That paired test ring is already being captured." : "Test ring saved. One message is queued.");
  } catch(error) {reportError(error);}
  finally {botRingBusy=false;updateBotRingButton();}
});

async function loadProposals() {
  state.proposals = (await api("/api/agent/proposals")).proposals;
  const cards = state.proposals.map((proposal) => {
    const card = node("article", "card proposal"); card.append(node("span", "status-badge", proposal.status), node("small", null, `${proposal.source} · ${formatDate(proposal.created_at)}`), node("p", null, proposal.text));
    if (proposal.status === "pending") {
      const actions = node("div", "proposal-actions"); const speak = node("button", "primary-button", "Approve & speak here"); const dismiss = node("button", "secondary-button", "Dismiss");
      speak.disabled = !("speechSynthesis" in window); if (speak.disabled) speak.title = "Speech playback is unavailable in this browser";
      speak.addEventListener("click", () => decideProposal(proposal, "approved")); dismiss.addEventListener("click", () => decideProposal(proposal, "dismissed")); actions.append(speak, dismiss); card.append(actions);
    } else card.append(node("small", null, proposal.status === "approved" ? "Approved for this browser. Playback is not confirmation of delivery to a visitor." : "Dismissed without playback."));
    return card;
  });
  $("#proposal-list").replaceChildren(...(cards.length ? cards : [emptyEvents("Nothing waiting for your approval.", "Draft a local reply or connect an adapter to propose one.")]));
}
async function decideProposal(proposal, decision) {
  try {
    await api(`/api/agent/proposals/${proposal.id}/decision`, { method: "POST", body: JSON.stringify({ decision }) });
    if (decision === "approved" && "speechSynthesis" in window) { const reply = new SpeechSynthesisUtterance(proposal.text); reply.onerror = () => toast("Reply approved, but browser speech playback failed."); speechSynthesis.speak(reply); toast("Approved. Playback requested on this browser only."); }
    else toast("Reply dismissed.");
    await loadProposals();
  } catch (error) { reportError(error); }
}
$("#reply-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try { await api("/api/agent/proposals", { method: "POST", body: JSON.stringify({ text: $("#reply-text").value, source: "Dashboard draft" }) }); $("#reply-form").reset(); await loadProposals(); toast("Draft ready. It won’t speak until you approve."); } catch (error) { reportError(error); }
});
$("#adapter-context").addEventListener("click", async () => { try { $("#context-output").textContent = JSON.stringify(await api("/api/agent/context"), null, 2); $("#context-dialog").showModal(); } catch (error) { reportError(error); } });

let accessArrival = null, accessBusy = false;
const simulationBody = body => JSON.stringify({simulation_only: true, ...body});
// Request IDs provide deduplication, not credentials; getRandomValues also works on a private HTTP LAN.
const simulationId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, "0")).join("");
const accessMessages = {
  visitor_second_factor_required: "Visitor credential missing. No simulated entry allowed.",
  visitor_face_not_accepted: "This face result did not match the visitor. No simulated entry allowed.",
  pass_not_active: "This pass is pending, expired, revoked, used or uncertain. No simulated entry allowed.",
  pass_not_yet_valid: "The visit window has not started. No simulated entry allowed.",
  invalid_pass_window: "Choose a window ending in the future, lasting at most 24 hours and starting within seven days.",
  unsupported_capability: "Opening a door is a separate capability and is unavailable in this simulator.",
  lock_state_unknown: "The simulated lock state is unknown. No action was confirmed.",
  simulation_confirmation_missing: "Simulation confirmation is missing. State is uncertain; no physical action occurred.",
  simulation_failed: "Simulation failed. No physical action occurred.",
  simulation_only_confirmation: "Simulation completed. No physical lock action occurred.",
  proposal_expired: "The command review expired. Create a new proposal.",
  mock_owner_confirmation_required: "Confirm the exact simulated owner decision before continuing.",
  replay_payload_changed: "This request ID belongs to a different request. No action was repeated.",
  ambiguous_guest_pass: "More than one active pass matches this guest. No simulated entry allowed.",
  guest_approval_required: "Owner approval is still required."
};
function accessMessage(error) { return accessMessages[error.message || error.code] || error.message || error.code || "Simulation could not complete."; }
function localDateInput(date) { return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
async function loadAccess() {
  const snapshot = await api("/api/access/simulation");
  $("#access-lock-state").textContent = `Simulated lock: ${snapshot.status.lock.simulated_state}. Lock plugin: simulation. Actual lock: unknown. Physical door position: unknown.`;
  const commandCards = snapshot.commands.map(item => {
    const card = node("article", "card delivery-card");
    card.append(node("strong", null, `Simulated ${item.payload.action} · ${item.status.replaceAll("_", " ")}`));
    if (item.result) card.append(node("p", null, accessMessage(item.result)));
    if (item.status === "pending") {
      const button = node("button", "secondary-button", "Approve simulated command");
      button.addEventListener("click", () => accessDecision("commands", item, "approve"));
      const dismiss = node("button", "text-button", "Dismiss"); dismiss.addEventListener("click", () => accessDecision("commands", item, "dismiss"));
      const actions = node("div", "bot-actions"); actions.append(button, dismiss); card.append(actions);
    }
    return card;
  });
  $("#access-command-list").replaceChildren(...commandCards);
  const passCards = snapshot.passes.map(item => {
    const card = node("article", "card delivery-card"); card.dataset.passId = item.id;
    card.append(node("strong", null, `${item.payload.visitor_name} · simulated pass · ${item.status}`),
      node("p", "muted small", `${formatDate(item.valid_from)} to ${formatDate(item.valid_until)} · one use · ${item.payload.mock_reference_count} pretend references`));
    if (item.result?.used_at) card.append(node("p", null, "Used in simulation. No physical lock action occurred."));
    const actions = node("div", "bot-actions");
    if (item.status === "pending") {
      const approve = node("button", "secondary-button", "Approve simulated pass"); approve.addEventListener("click", () => accessDecision("passes", item, "approve")); actions.append(approve);
    }
    if (["pending", "approved"].includes(item.status)) {
      const revoke = node("button", "text-button", "Revoke simulated pass"); revoke.addEventListener("click", () => accessDecision("passes", item, "revoke")); actions.append(revoke);
    }
    if (item.status === "approved") {
      const arrive = node("button", "secondary-button", "Simulate arrival"); arrive.addEventListener("click", () => {
        accessArrival = item; $("#access-arrival-form").reset(); $("#access-arrival-result").hidden = true;
        $("#access-arrival-run").disabled = false;
        $("#access-arrival-person").textContent = `Temporary visitor: ${item.payload.visitor_name}. One use within the approved window.`;
        $("#access-arrival-dialog").showModal();
      }); actions.append(arrive);
    }
    card.append(actions); return card;
  });
  $("#access-pass-list").replaceChildren(...passCards);
  const guestCards = (snapshot.guest_approvals || []).map(item => {
    const card = node("article", "card delivery-card");
    const done = item.device_label === "DONE";
    card.append(node("strong", null, `Guest approval · ${item.status.replaceAll("_", " ")}`),
      node("p", null, done ? "Simulated open." : "Waiting for the simulated open."));
    if (item.status === "pending_approval") {
      const factor = node("input"); factor.type = "checkbox"; factor.className = "guest-factor";
      const factorLabel = node("label", "muted small"); factorLabel.append(factor, document.createTextNode(" Pretend visitor credential verified"));
      const approve = node("button", "secondary-button", "Approve guest simulation");
      approve.addEventListener("click", () => guestDecision(item, "approve", factor.checked));
      const dismiss = node("button", "text-button", "Dismiss guest simulation");
      dismiss.addEventListener("click", () => guestDecision(item, "dismiss", false));
      const actions = node("div", "bot-actions"); actions.append(factorLabel, approve, dismiss); card.append(actions);
    }
    return card;
  });
  $("#guest-approval-list").replaceChildren(...guestCards);
  const guestWindowCards = (snapshot.named_guests || []).map(item => {
    const card = node("article", "card delivery-card");
    card.append(node("strong", null, item.name), node("p", "muted small", item.window
      ? `Allowed window: ${item.window}.`
      : "No visit window."));
    return card;
  });
  $("#guest-window-list").replaceChildren(...(guestWindowCards.length ? guestWindowCards : [node("p", "muted small", "No enrolled guests.")]));
  const pinCards = (snapshot.guest_pins || []).filter(item => !item.pin && !item.pin_hash && !item.salt).map(item => {
    const card = node("article", "card delivery-card");
    card.append(node("strong", null, item.label), node("p", "muted small", `Expires ${formatDate(item.expires_at)}`));
    return card;
  });
  $("#guest-pin-list").replaceChildren(...(pinCards.length ? pinCards : [node("p", "muted small", "No active guest PINs.")]));
  const bridge = await api("/api/enrollment/bridge");
  $("#bridge-status").textContent = bridge.enabled ? "SSH photo proposal bridge configured. Door control remains simulation only." : "Real photo bridge awaits owner setup. Five-photo demo is available. Recovery planning does not change the Pi.";
  const imports = await api("/api/enrollment/simulation/proposals");
  $("#bridge-proposal-list").replaceChildren(...imports.proposals.map(item => {
    const card = node("article", "card delivery-card"); card.dataset.proposalId = item.id;
    card.append(node("strong", null, `${item.payload.visitor_name} · five-photo demo · ${item.status}`),
      node("p", "muted small", `${formatDate(item.payload.valid_from)} to ${formatDate(item.payload.valid_until)} · five generated images · one use`));
    if (item.status === "pending") {
      const actions = node("div", "bot-actions");
      for (const [decision, label] of [["approve", "Approve demo references and pass"], ["dismiss", "Dismiss demo proposal"]]) {
        const button = node("button", "secondary-button", label);
        button.addEventListener("click", async () => {
          if (accessBusy) return;
          if (!confirm(`${decision === "approve" ? "Approve" : "Dismiss"} ${item.payload.visitor_name}, five generated demo images, ${formatDate(item.payload.valid_from)} to ${formatDate(item.payload.valid_until)}, one simulated use? This is a mock owner decision.`)) return;
          accessBusy = true;
          try {
            await api(`/api/enrollment/simulation/proposals/${item.id}/decision`, {method: "POST", body: simulationBody({
              payload_digest: item.digest, confirm_mock_owner: true, decision})});
            await loadAccess();
          } catch (error) { $("#access-error").textContent = accessMessage(error); }
          finally { accessBusy = false; }
        }); actions.append(button);
      }
      card.append(actions);
    }
    if (item.status === "approved") card.append(node("p", null, "Temporary references approved. Use Simulate arrival on the visitor pass below."));
    return card;
  }));
  $("#access-audit").replaceChildren(...snapshot.audit.map(item => node("p", null, `${formatDate(item.occurred_at)} · ${item.kind} · ${item.status} · ${item.code}`)));
  if (!$("#access-valid-from").value) {
    $("#access-valid-from").value = localDateInput(new Date());
    $("#access-valid-until").value = localDateInput(new Date(Date.now() + 3600000));
  }
}
$("#bridge-fixture-create").addEventListener("click", async () => {
  if (accessBusy) return; accessBusy = true; $("#access-error").textContent = "";
  try {
    await api("/api/enrollment/simulation/proposals", {method: "POST", body: simulationBody({request_id: simulationId(),
      visitor_name: $("#access-visitor-name").value, valid_from: new Date($("#access-valid-from").value).toISOString(),
      valid_until: new Date($("#access-valid-until").value).toISOString()})});
    await loadAccess();
  } catch (error) { $("#access-error").textContent = accessMessage(error); }
  finally { accessBusy = false; }
});
async function guestDecision(item, decision, factorChecked) {
  if (accessBusy) return;
  if (!confirm(`${decision === "approve" ? "Approve" : "Dismiss"} this guest simulation only? A probable match is not identity. No physical lock is connected.`)) return;
  accessBusy = true;
  try {
    const result = await api(`/api/access/simulation/guest-approvals/${item.id}/decision`, {method: "POST", body: simulationBody({
      payload_digest: item.digest, confirm_mock_owner: true, decision, second_factor: factorChecked ? "verified_mock" : "missing"})});
    $("#access-error").textContent = result.device_label === "DONE"
      ? "Simulated open."
      : accessMessage(result.attempt || result.result || result);
    await loadAccess();
  } catch (error) { $("#access-error").textContent = accessMessage(error); }
  finally { accessBusy = false; }
}
async function accessDecision(kind, item, decision) {
  if (accessBusy) return;
  const detail = kind === "commands" ? `Simulated ${item.payload.action}` : `${item.payload.visitor_name}, ${formatDate(item.valid_from)} to ${formatDate(item.valid_until)}, one use`;
  if (!confirm(`${decision === "approve" ? "Approve" : decision === "revoke" ? "Revoke" : "Dismiss"} this simulation only? ${detail}. No physical lock or real owner authentication is connected.`)) return;
  accessBusy = true;
  try {
    await api(`/api/access/simulation/${kind}/${item.id}/decision`, {method: "POST", body: simulationBody({payload_digest: item.digest, confirm_mock_owner: true, decision})});
    await loadAccess();
  } catch (error) { $("#access-error").textContent = accessMessage(error); }
  finally { accessBusy = false; }
}
$("#access-command-form").addEventListener("submit", async event => {
  event.preventDefault(); if (accessBusy) return; accessBusy = true; $("#access-error").textContent = "";
  try { await api("/api/access/simulation/commands", {method: "POST", body: simulationBody({request_id: simulationId(), action: $("#access-command").value})}); await loadAccess(); }
  catch (error) { $("#access-error").textContent = accessMessage(error); }
  finally { accessBusy = false; }
});
$("#access-pass-form").addEventListener("submit", async event => {
  event.preventDefault(); if (accessBusy) return; accessBusy = true; $("#access-error").textContent = "";
  try {
    await api("/api/access/simulation/passes", {method: "POST", body: simulationBody({request_id: simulationId(), visitor_name: $("#access-visitor-name").value,
      mock_reference_count: Number($("#access-reference-count").value), valid_from: new Date($("#access-valid-from").value).toISOString(), valid_until: new Date($("#access-valid-until").value).toISOString()})});
    $("#access-visitor-name").value = ""; await loadAccess();
  } catch (error) { $("#access-error").textContent = accessMessage(error); }
  finally { accessBusy = false; }
});
$("#access-arrival-form").addEventListener("submit", async event => {
  event.preventDefault(); if (accessBusy || !accessArrival) return; accessBusy = true; $("#access-arrival-run").disabled = true;
  try {
    const face = $("#access-arrival-face").value;
    const result = await api(`/api/access/simulation/passes/${accessArrival.id}/arrival`, {method: "POST", body: simulationBody({request_id: simulationId(), face_status: face,
      face_visitor_id: face === "probable_match" ? accessArrival.visitor_id : null, second_factor: $("#access-arrival-factor").checked ? "verified_mock" : "missing"})});
    $("#access-arrival-result").textContent = accessMessage(result); $("#access-arrival-result").hidden = false; await loadAccess();
    if (result.status !== "denied") accessArrival = null;
  } catch (error) { $("#access-arrival-result").textContent = accessMessage(error); $("#access-arrival-result").hidden = false; }
  finally { accessBusy = false; $("#access-arrival-run").disabled = !accessArrival; }
});

const stream = new EventSource("/api/stream");
stream.addEventListener("connected", () => { $("#connection").classList.add("online"); $("#connection").lastChild.textContent = " Live updates connected"; $("#fatal-banner").hidden = true; loadEvents().catch(reportError); api("/api/status").then(renderStatus).catch(reportError); });
stream.onerror = () => { $("#connection").classList.remove("online"); $("#connection").lastChild.textContent = " Reconnecting…"; };
stream.addEventListener("ring", (message) => { const event = JSON.parse(message.data); upsertEvent(event); chime(); toast(event.kind === "test" ? "Ding dong. Your test ring arrived." : "Ding dong. Someone is at your door.", true); });
stream.addEventListener("motion", (message) => { upsertEvent(JSON.parse(message.data)); toast("Movement in your front-door zone."); });
stream.addEventListener("event_updated", (message) => upsertEvent(JSON.parse(message.data)));
stream.addEventListener("event_deleted", (message) => { const { id } = JSON.parse(message.data); state.events = state.events.filter((event) => event.id !== id); renderEvents(); });
stream.addEventListener("status", (message) => renderStatus(JSON.parse(message.data)));
stream.addEventListener("proposal", () => { if (state.view === "agent") loadProposals().catch(reportError); });
stream.addEventListener("bots", () => { if (state.view === "agent") loadBots().catch(reportError); });

$("#today-label").textContent = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(new Date()).toUpperCase();
switchView(location.hash.slice(1) || "home");
Promise.all([api("/api/status").then(renderStatus), loadEvents(), loadProfiles()]).catch((error) => { $("#fatal-banner").textContent = "Can’t reach your doorbell device. The page will reconnect automatically."; $("#fatal-banner").hidden = false; console.error(error); });
setInterval(() => { api("/api/status").then(renderStatus).catch(() => {}); loadSummary().catch(() => {}); if (state.view === "agent") loadAccess().catch(() => {}); }, 20000);
