<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useTelemetry } from './composables/useTelemetry.js';
import { acquireLease, renewLease, releaseLease } from './api/lease.js';
import { connectCamera, disconnectCamera } from './api/camera.js';
import { updateCameraConfig } from './api/config.js';
import { startExposure, abortExposure } from './api/exposure.js';
import { setThermalTarget, toggleCooler } from './api/thermal.js';
import { configureSession } from './api/session.js';

// ─── TELEMETRY ────────────────────────────────────────────────────────────────
const { telemetryData, lastCaptureData, lastEvent, isConnected, connect, disconnect } = useTelemetry();

// ─── AUTH & LEASE ─────────────────────────────────────────────────────────────
const apiKey         = ref('');
const ownerInput     = ref('');
const leaseTtlRaw    = ref('120');  // string to support 'null' sentinel
const leasePriority  = ref(10);
const leaseTtl       = computed(() => leaseTtlRaw.value === 'null' ? null : parseInt(leaseTtlRaw.value));
const currentOwner   = ref(null);
const currentLeaseId = ref(null);

function clearLeaseState() {
  currentOwner.value   = null;
  currentLeaseId.value = null;
}

const leaseExpiresIn = computed(() => telemetryData.value?.lease?.expires_in_s ?? null);
const leaseProgress  = computed(() => {
  const lease = telemetryData.value?.lease;
  if (!lease?.active || !lease.acquired_at || !lease.expires_at) return 0;
  const total     = new Date(lease.expires_at) - new Date(lease.acquired_at);
  const remaining = new Date(lease.expires_at) - Date.now();
  return Math.max(0, Math.min(100, (remaining / total) * 100));
});

// ─── LOADING FLAGS ────────────────────────────────────────────────────────────
const busyLease    = ref(false);
const busyCamera   = ref(false);
const busyConfig   = ref(false);
const busyExposure = ref(false);
const busyCooler   = ref(false);

// ─── CAMERA CONFIG ────────────────────────────────────────────────────────────
const cfgBinning = ref('1');
const cfgGain    = ref(26);
const cfgOffset  = ref(10);
const cfgRoiX    = ref(0);
const cfgRoiY    = ref(0);
const cfgRoiW    = ref(9600);
const cfgRoiH    = ref(6422);

// ─── THERMAL ──────────────────────────────────────────────────────────────────
const thermalTarget = ref(-10);

// ─── EXPOSURE ─────────────────────────────────────────────────────────────────
const expTime  = ref(3.0);
const expType  = ref('LIGHT');
const expJobId = ref('');

// ─── STORAGE ──────────────────────────────────────────────────────────────────
const sessionPath = ref('/data/');
const filePrefix  = ref('cavex');

// ─── LOG ──────────────────────────────────────────────────────────────────────
const logEntries = ref([]);
const logEl      = ref(null);
function addLog(level, msg) {
  const ts = new Date().toISOString().slice(11, 23);
  logEntries.value.unshift({ ts, level, msg });
  if (logEntries.value.length > 120) logEntries.value.pop();
}

// ── Real-time log from WS events ──────────────────────────────────────────
watch(lastEvent, (ev) => {
  if (!ev) return;
  const d = ev.data ?? {};
  switch (ev.type) {
    case 'ws_connected':
      addLog('ok', 'WebSocket connected — streaming telemetry at 2 Hz');
      break;
    case 'ws_disconnected':
      addLog('warn', 'WebSocket disconnected — reconnecting…');
      break;
    case 'capture_event':
      if (d.event === 'capture_started')
        addLog('info', `Exposure started  id:${(d.exposure_id||'').slice(0,8)}  t:${d.exptime_s}s`);
      else if (d.event === 'capture_done')
        addLog('ok', `Capture complete  ${d.image_valid ? '✓ FITS valid' : '✗ FITS invalid'}  ${(d.image_path||'').split('/').pop()}`);
      else if (d.event === 'capture_failed')
        addLog('err', `Capture failed: ${d.message}`);
      else if (d.event === 'capture_aborted')
        addLog('warn', `Exposure aborted  id:${(d.exposure_id||'').slice(0,8)}`);
      break;
    case 'lease_event':
      if (d.event === 'lease_acquired')
        addLog('ok', `Lease acquired  owner:${d.owner}  priority:${d.priority}`);
      else if (d.event === 'lease_released')
        addLog('info', `Lease released  owner:${d.owner ?? '?'}`);
      else if (d.event === 'lease_preempted')
        addLog('warn', `Lease preempted  ${d.old_owner} → ${d.new_owner}`);
      else if (d.event === 'lease_expired')
        addLog('warn', `Lease expired  owner:${d.owner ?? '?'}`);
      break;
    case 'alarm_event':
      addLog('err', `ALARM [${d.code ?? '?'}] ${d.message ?? ''}`);
      break;
    default:
      break;
  }
});

// Camera state changes
watch(() => telemetryData.value?.status?.state, (val, old) => {
  if (val && old && val !== old)
    addLog('info', `Camera state  ${old} → ${val}`);
});

// Connection state
watch(() => telemetryData.value?.device?.connected, (val, old) => {
  if (old === undefined) return;
  addLog(val ? 'ok' : 'warn', val ? 'Camera hardware connected' : 'Camera hardware disconnected');
});

// Detect lease expiration via telemetry and clear local state
watch(() => telemetryData.value?.lease?.active, (active) => {
  if (active === false && currentLeaseId.value) {
    addLog('warn', `Lease expired — control released`);
    clearLeaseState();
  }
});

// Restore lease state if page was refreshed while a lease was active
// Runs once on first telemetry arrival that shows an active lease
watch(() => telemetryData.value?.lease, (lease) => {
  if (!lease?.active || currentLeaseId.value) return;
  // A lease is active on the server but we lost the frontend state (e.g. page refresh)
  currentLeaseId.value = lease.lease_id;
  currentOwner.value   = lease.owner;
  addLog('ok', `Lease restored from server — owner: ${lease.owner} | id: ${lease.lease_id?.slice(0,8)}…`);
}, { once: true });

// Sync sensor config inputs from telemetry on first arrival
watch(() => telemetryData.value?.config, (cfg) => {
  if (!cfg) return;
  if (cfg.binning?.[0]) cfgBinning.value = String(cfg.binning[0]);
  if (cfg.gain  != null) cfgGain.value   = cfg.gain;
  if (cfg.offset != null) cfgOffset.value = cfg.offset;
  if (cfg.roi?.length === 4) {
    cfgRoiX.value = cfg.roi[0]; cfgRoiY.value = cfg.roi[1];
    cfgRoiW.value = cfg.roi[2]; cfgRoiH.value = cfg.roi[3];
  }
}, { once: true });

// ─── DERIVED STATE ────────────────────────────────────────────────────────────
const device   = computed(() => telemetryData.value?.device ?? {});
const status   = computed(() => telemetryData.value?.status ?? {});
const thermal  = computed(() => status.value?.thermal ?? {});
const exposure = computed(() => status.value?.exposure ?? {});
const config   = computed(() => telemetryData.value?.config ?? {});
const storage  = computed(() => telemetryData.value?.storage ?? {});
// lastCap comes from the discrete capture_event (WS), NOT from the telemetry_snapshot,
// because broadcast_snapshot() never includes last_capture in its payload.
const lastCap = computed(() => lastCaptureData.value ?? null);

// imageUrl is set ONCE per new capture arrival via a watcher.
// DO NOT use Date.now() inside a :src binding — it re-evaluates on every
// telemetry tick (0.5 s) and forces the browser to reload the image continuously.
const imageUrl = ref('');
const exposureHistory = ref([]); // last 20 completed captures
watch(lastCaptureData, (cap) => {
  if (!cap) return;
  if (cap?.file_path) {
    const filename = cap.file_path.split('/').pop();
    imageUrl.value = `/api/v1/preview/${filename}?t=${Date.now()}`;
  }
  // Prepend to history, keep last 20
  exposureHistory.value.unshift({ ...cap });
  if (exposureHistory.value.length > 20) exposureHistory.value.pop();
});

const coolerClass = computed(() => {
  if (!thermal.value.cooler_on) return 'badge-off';
  const cs = thermal.value.cooler_state;
  if (cs === 'STABLE')  return 'badge-stable';
  if (cs === 'COOLING') return 'badge-cooling';
  return 'badge-warn';
});

const diskPct = computed(() => {
  const free  = storage.value.disk_free_gb ?? 450;
  const total = 500;
  return Math.round(((total - free) / total) * 100);
});

// ─── HANDLERS ─────────────────────────────────────────────────────────────────
async function handleAcquire() {
  if (!ownerInput.value.trim()) return addLog('warn', 'Enter a username first');
  busyLease.value = true;
  try {
    const r = await acquireLease(ownerInput.value.trim(), apiKey.value, leasePriority.value, leaseTtl.value);
    currentOwner.value   = r.owner;
    currentLeaseId.value = r.lease_id;
    const ttlLabel = leaseTtl.value === null ? 'PERMANENT' : `${leaseTtl.value}s`;
    addLog('ok', `Lease acquired → owner: ${r.owner} | ttl: ${ttlLabel} | id: ${r.lease_id.slice(0,8)}…`);
  } catch (e) {
    addLog('err', `Acquire failed: ${e.message}`);
  } finally { busyLease.value = false; }
}

async function handleRenew() {
  if (!currentLeaseId.value) return;
  busyLease.value = true;
  try {
    const r = await renewLease(currentLeaseId.value, apiKey.value, leaseTtl.value);
    const ttlLabel = leaseTtl.value === null ? 'PERMANENT' : `+${leaseTtl.value}s`;
    addLog('ok', `Lease renewed → ${ttlLabel} | id: ${r.lease_id?.slice(0,8)}…`);
  } catch (e) {
    addLog('err', `Renew failed: ${e.message}`);
    // Lease is gone (expired or not found) — unlock the UI so user can re-acquire
    clearLeaseState();
  } finally { busyLease.value = false; }
}

async function handleRelease() {
  if (!currentLeaseId.value) return;
  busyLease.value = true;
  try {
    await releaseLease(currentLeaseId.value, apiKey.value);
    addLog('ok', `Lease released by ${currentOwner.value}`);
  } catch (e) {
    addLog('err', `Release failed: ${e.message}`);
  } finally {
    // Always clear local state on release attempt — even if server returns error,
    // the lease is effectively gone from this client's perspective
    clearLeaseState();
    busyLease.value = false;
  }
}

async function handleConnect() {
  if (!currentLeaseId.value) return addLog('warn', 'Acquire lease first');
  busyCamera.value = true;
  try {
    await connectCamera(currentLeaseId.value, apiKey.value);
    addLog('ok', 'Camera connect command sent');
  } catch (e) {
    addLog('err', `Connect failed: ${e.message}`);
  } finally { busyCamera.value = false; }
}

async function handleDisconnect() {
  if (!currentLeaseId.value) return;
  busyCamera.value = true;
  try {
    await disconnectCamera(currentLeaseId.value, apiKey.value);
    addLog('ok', 'Camera disconnect command sent');
  } catch (e) {
    addLog('err', `Disconnect failed: ${e.message}`);
  } finally { busyCamera.value = false; }
}

async function handleApplyConfig() {
  if (!currentLeaseId.value) return addLog('warn', 'Acquire lease first');
  const bin = parseInt(cfgBinning.value);
  const payload = {
    binning: { x: bin, y: bin },
    gain: cfgGain.value,
    offset: cfgOffset.value,
    roi: { x: cfgRoiX.value, y: cfgRoiY.value, width: cfgRoiW.value, height: cfgRoiH.value },
  };
  busyConfig.value = true;
  try {
    await updateCameraConfig(currentLeaseId.value, apiKey.value, payload);
    addLog('ok', `Config applied — bin:${bin}x${bin} gain:${cfgGain.value}`);
  } catch (e) {
    addLog('err', `Config failed: ${e.message}`);
  } finally { busyConfig.value = false; }
}

async function handleApplySession() {
  if (!currentLeaseId.value) return addLog('warn', 'Acquire lease first');
  busyConfig.value = true;
  try {
    const r = await configureSession(currentLeaseId.value, apiKey.value, sessionPath.value, filePrefix.value);
    addLog('ok', `Session configured — path: ${r.storage_path} | free: ${r.disk_free_gb?.toFixed(1)} GB`);
  } catch (e) {
    addLog('err', `Session config failed: ${e.message}`);
  } finally { busyConfig.value = false; }
}

async function handleSetTemp() {
  if (!currentLeaseId.value) return addLog('warn', 'Acquire lease first');
  busyCooler.value = true;
  try {
    await setThermalTarget(currentLeaseId.value, apiKey.value, thermalTarget.value);
    addLog('ok', `Thermal target set → ${thermalTarget.value} °C`);
  } catch (e) {
    addLog('err', `Thermal failed: ${e.message}`);
  } finally { busyCooler.value = false; }
}

async function handleToggleCooler() {
  if (!currentLeaseId.value) return addLog('warn', 'Acquire lease first');
  const enable = !thermal.value.cooler_on;
  busyCooler.value = true;
  try {
    await toggleCooler(currentLeaseId.value, apiKey.value, enable, thermalTarget.value);
    addLog('ok', `Cooler ${enable ? 'enabled' : 'disabled'}`);
  } catch (e) {
    addLog('err', `Cooler toggle failed: ${e.message}`);
  } finally { busyCooler.value = false; }
}

async function handleStartExposure() {
  if (!currentLeaseId.value) return addLog('warn', 'Acquire lease first');
  busyExposure.value = true;
  try {
    const r = await startExposure(currentLeaseId.value, apiKey.value, {
      exptime_s:  expTime.value,
      frame_type: expType.value,
      job_id:     expJobId.value || undefined
    });
    addLog('ok', `Exposure started → id:${r.exposure_id?.slice(0,8)}… type:${expType.value} t:${expTime.value}s`);
  } catch (e) {
    addLog('err', `Exposure failed: ${e.message}`);
  } finally { busyExposure.value = false; }
}

async function handleAbort() {
  if (!currentLeaseId.value) return;
  busyExposure.value = true;
  try {
    await abortExposure(currentLeaseId.value, apiKey.value);
    addLog('warn', 'Exposure aborted by operator');
  } catch (e) {
    addLog('err', `Abort failed: ${e.message}`);
  } finally { busyExposure.value = false; }
}

onMounted(connect);
onUnmounted(disconnect);
</script>

<template>
  <div class="shell">

    <!-- ══ TOP BAR ═══════════════════════════════════════════════════════════ -->
    <header class="topbar">
      <div class="tb-left">
        <span class="logo-mark">⬡</span>
        <span class="logo-text">CAVEX<span class="logo-sub">IMAGER</span></span>
        <span class="version-badge">v1.3</span>
      </div>

      <nav class="tb-indicators">
        <div class="ind-pill" :class="isConnected ? 'ind-ok' : 'ind-err'">
          <span class="ind-dot"></span>
          <span>WS</span>
        </div>
        <div class="ind-pill" :class="device.connected ? 'ind-ok' : 'ind-err'">
          <span class="ind-dot"></span>
          <span>CAM</span>
        </div>
        <div class="ind-pill" :class="currentLeaseId ? 'ind-lease' : 'ind-off'">
          <span class="ind-dot"></span>
          <span>LEASE</span>
        </div>
        <div class="state-chip" :class="`state-${(status.state||'UNKNOWN').toLowerCase()}`">
          {{ status.state ?? 'UNKNOWN' }}
        </div>
      </nav>

      <div class="tb-right">
        <span class="tb-label">API KEY</span>
        <input v-model="apiKey" type="password" placeholder="••••••••••••" class="tb-input" spellcheck="false" />
      </div>
    </header>

    <!-- ══ MAIN WORKSPACE ════════════════════════════════════════════════════ -->
    <div class="workspace">

      <!-- ── LEFT PANEL ──────────────────────────────────────────────────── -->
      <aside class="panel panel-left">

        <!-- Access Control -->
        <section class="card">
          <h3 class="card-title"><span class="card-icon">⬡</span> ACCESS CONTROL</h3>
          <div class="form-row">
            <label class="lbl">OPERATOR</label>
            <input v-model="ownerInput" type="text" placeholder="username" class="inp"
              :disabled="!!currentLeaseId" />
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px">
            <div class="form-row">
              <label class="lbl">TTL</label>
              <select v-model="leaseTtlRaw" class="inp" :disabled="!!currentLeaseId">
                <option value="60">60 s</option>
                <option value="120">120 s</option>
                <option value="300">300 s</option>
                <option value="600">600 s</option>
                <option value="null">PERMANENT</option>
              </select>
            </div>
            <div class="form-row">
              <label class="lbl">PRIORITY</label>
              <select v-model.number="leasePriority" class="inp" :disabled="!!currentLeaseId">
                <option :value="1">1 — low</option>
                <option :value="10">10 — normal</option>
                <option :value="50">50 — high</option>
                <option :value="100">100 — urgent</option>
              </select>
            </div>
          </div>
          <div class="btn-row">
            <button class="btn btn-acquire" @click="handleAcquire"
              :disabled="busyLease || !!currentLeaseId">
              <span v-if="busyLease && !currentLeaseId" class="spin">◌</span>
              <span v-else>ACQUIRE</span>
            </button>
            <button class="btn btn-primary" @click="handleRenew"
              :disabled="busyLease || !currentLeaseId"
              :title="leaseTtl === null ? 'Keep permanent' : `Extend lease by ${leaseTtl}s`">
              RENEW
            </button>
            <button class="btn btn-release" @click="handleRelease"
              :disabled="busyLease || !currentLeaseId">
              RELEASE
            </button>
          </div>
          <div v-if="currentLeaseId" class="lease-info">
            <div class="di-row">
              <span class="lbl">OWNER</span>
              <span class="val val-green mono">{{ currentOwner }}</span>
            </div>
            <div class="di-row">
              <span class="lbl">ID</span>
              <span class="val mono small">{{ currentLeaseId.slice(0,14) }}…</span>
            </div>
            <div class="mt-1">
              <div class="di-row mb-1">
                <span class="lbl">TTL</span>
                <span v-if="leaseExpiresIn === null" class="val mono val-blue">PERMANENT</span>
                <span v-else class="val mono" :class="leaseExpiresIn < 30 ? 'val-red' : 'val-yellow'">
                  {{ leaseExpiresIn }}s
                </span>
              </div>
              <div v-if="leaseExpiresIn !== null" class="mini-bar-bg">
                <div class="mini-bar-fill"
                  :style="{width: leaseProgress+'%'}"
                  :class="leaseProgress < 25 ? 'mini-bar-red' : ''"></div>
              </div>
            </div>
          </div>
        </section>

        <!-- Camera Hardware -->
        <section class="card">
          <h3 class="card-title"><span class="card-icon">◎</span> CAMERA HARDWARE</h3>
          <div class="device-block">
            <div class="di-row">
              <span class="lbl">DEVICE</span>
              <span class="val mono">{{ device.name ?? '—' }}</span>
            </div>
            <div class="di-row">
              <span class="lbl">MODEL</span>
              <span class="val mono">{{ device.model ?? '—' }}</span>
            </div>
            <div class="di-row">
              <span class="lbl">STATE</span>
              <span class="val" :class="device.connected ? 'val-green' : 'val-red'">
                {{ device.connected ? 'CONNECTED' : 'DISCONNECTED' }}
              </span>
            </div>
          </div>
          <div class="btn-row mt-2">
            <button class="btn btn-connect" @click="handleConnect"
              :disabled="device.connected || !currentLeaseId || busyCamera">
              <span v-if="busyCamera && !device.connected" class="spin">◌</span>
              <span v-else>CONNECT</span>
            </button>
            <button class="btn btn-release" @click="handleDisconnect"
              :disabled="!device.connected || !currentLeaseId || busyCamera">
              DISCONNECT
            </button>
          </div>
        </section>

        <!-- Storage -->
        <section class="card">
          <h3 class="card-title"><span class="card-icon">▤</span> STORAGE</h3>
          <div class="form-row">
            <label class="lbl">SESSION PATH</label>
            <input v-model="sessionPath" type="text" class="inp mono" />
          </div>
          <div class="form-row">
            <label class="lbl">FILE PREFIX</label>
            <input v-model="filePrefix" type="text" class="inp mono" />
          </div>
          <button class="btn btn-primary w-full mt-1" @click="handleApplySession"
            :disabled="!currentLeaseId || busyConfig">
            <span v-if="busyConfig" class="spin">◌</span>
            <span v-else>APPLY SESSION</span>
          </button>
          <div class="mt-2">
            <div class="di-row mb-1">
              <span class="lbl">DISK USED</span>
              <span class="val" :class="diskPct > 85 ? 'val-red' : diskPct > 60 ? 'val-yellow' : 'val-green'">
                {{ diskPct }}%
              </span>
            </div>
            <div class="bar-bg">
              <div class="bar-fill"
                :style="{width: diskPct+'%'}"
                :class="diskPct > 85 ? 'bar-red' : diskPct > 60 ? 'bar-yellow' : 'bar-blue'">
              </div>
            </div>
            <div class="disk-detail mt-1">
              <span class="lbl">FREE</span>
              <span class="val mono small">{{ storage.disk_free_gb?.toFixed(1) ?? '—' }} GB</span>
              <span class="lbl" style="margin-left:auto">LAST</span>
              <span class="val mono small truncate">{{ storage.last_file ?? '—' }}</span>
            </div>
          </div>
        </section>

      </aside>

      <!-- ── CENTER ──────────────────────────────────────────────────────── -->
      <main class="center-panel">

        <!-- Viewport -->
        <div class="viewport">
          <div class="vp-corner vp-tl"></div>
          <div class="vp-corner vp-tr"></div>
          <div class="vp-corner vp-bl"></div>
          <div class="vp-corner vp-br"></div>

          <img
            v-if="lastCap?.file_path"
            :src="imageUrl"
            class="vp-image"
            alt="last capture"
          />
          <div v-else class="vp-empty">
            <div class="reticle">
              <div class="ret-h"></div>
              <div class="ret-v"></div>
              <div class="ret-circle"></div>
            </div>
            <span class="vp-label">AWAITING FIRST CAPTURE</span>
          </div>

          <div v-if="lastCap" class="vp-meta">
            <span>{{ lastCap.file_path?.split('/').pop() }}</span>
            <span :class="lastCap.image_valid ? 'val-green' : 'val-red'">
              {{ lastCap.image_valid ? '✓ VALID FITS' : '✗ INVALID FITS' }}
            </span>
            <span>{{ lastCap.completed_at?.slice(0,19).replace('T',' ') ?? '' }}</span>
          </div>
        </div>

        <!-- Exposure Control -->
        <section class="card exposure-card">
          <div class="exp-params">
            <div class="exp-field">
              <label class="lbl">EXPOSURE (s)</label>
              <input v-model.number="expTime" type="number" min="0.001" step="0.5"
                class="inp inp-large mono text-center" />
            </div>
            <div class="exp-field">
              <label class="lbl">FRAME TYPE</label>
              <select v-model="expType" class="inp inp-large">
                <option>LIGHT</option>
                <option>DARK</option>
                <option>BIAS</option>
                <option>FLAT</option>
              </select>
            </div>
            <div class="exp-field exp-field-wide">
              <label class="lbl">JOB ID <span class="lbl-opt">(optional)</span></label>
              <input v-model="expJobId" type="text" placeholder="e.g. night_001" class="inp mono" />
            </div>
          </div>

          <div class="exp-actions">
            <button
              class="btn btn-expose"
              :class="{'btn-expose-pulse': !exposure.is_exposing && currentLeaseId}"
              @click="handleStartExposure"
              :disabled="!currentLeaseId || busyExposure || exposure.is_exposing">
              <span v-if="busyExposure && !exposure.is_exposing" class="spin">◌</span>
              <span v-else>▶ START EXPOSURE</span>
            </button>
            <button class="btn btn-abort" @click="handleAbort"
              :disabled="!exposure.is_exposing || !currentLeaseId || busyExposure">
              ■ ABORT
            </button>
          </div>

          <div class="progress-section">
            <div class="prog-meta">
              <span>
                <span class="lbl">ELAPSED</span>
                <span class="val mono"> {{ exposure.elapsed_s?.toFixed(1) ?? '0.0' }}s</span>
                <span class="lbl" style="margin-left:12px">TOTAL</span>
                <span class="val mono"> {{ exposure.total_s?.toFixed(1) ?? '0.0' }}s</span>
              </span>
              <span class="val mono">{{ (exposure.progress_pct ?? 0).toFixed(1) }}%</span>
            </div>
            <div class="bar-bg bar-large mt-1">
              <div class="bar-fill bar-exp" :style="{width: (exposure.progress_pct ?? 0) + '%'}">
                <div v-if="exposure.is_exposing" class="bar-shimmer"></div>
              </div>
            </div>
          </div>
        </section>

      </main>

      <!-- ── RIGHT PANEL ─────────────────────────────────────────────────── -->
      <aside class="panel panel-right">

        <!-- Thermal Control -->
        <section class="card">
          <h3 class="card-title"><span class="card-icon">❄</span> THERMAL CONTROL</h3>
          <div class="thermal-display">
            <div class="temp-readout">
              <span class="temp-value"
                :class="Math.abs((thermal.ccd_temp_c??99)-(thermal.target_temp_c??-10)) < 1 ? 'temp-stable' : 'temp-drift'">
                {{ thermal.ccd_temp_c?.toFixed(1) ?? '—' }}
              </span>
              <span class="temp-unit">°C</span>
            </div>
            <div class="di-row">
              <span class="lbl">TARGET</span>
              <span class="val mono">{{ thermal.target_temp_c ?? '—' }} °C</span>
              <span style="margin-left:auto">
                <span :class="['cooler-badge', coolerClass]">
                  {{ thermal.cooler_state ?? (thermal.cooler_on ? 'ON' : 'OFF') }}
                </span>
              </span>
            </div>
          </div>
          <div class="mt-2">
            <div class="di-row mb-1">
              <span class="lbl">COOLER POWER</span>
              <span class="val mono" :class="(thermal.cooler_power_pct??0) > 90 ? 'val-red' : 'val-green'">
                {{ thermal.cooler_power_pct?.toFixed(0) ?? '0' }}%
              </span>
            </div>
            <div class="bar-bg">
              <div class="bar-fill"
                :style="{width: (thermal.cooler_power_pct??0)+'%'}"
                :class="(thermal.cooler_power_pct??0) > 90 ? 'bar-red' : 'bar-blue'">
              </div>
            </div>
          </div>
          <div class="btn-row mt-2" style="align-items:flex-end; gap:6px">
            <div style="display:flex; flex-direction:column; gap:4px">
              <label class="lbl">SET TARGET</label>
              <input v-model.number="thermalTarget" type="number" step="1"
                class="inp mono text-center" style="width:72px" />
            </div>
            <span class="lbl" style="padding-bottom:8px">°C</span>
            <button class="btn btn-primary" style="flex:1; align-self:flex-end"
              @click="handleSetTemp" :disabled="!currentLeaseId || busyCooler">
              SET
            </button>
          </div>
          <button
            :class="['btn', 'w-full', 'mt-1', thermal.cooler_on ? 'btn-release' : 'btn-connect']"
            @click="handleToggleCooler"
            :disabled="!currentLeaseId || busyCooler">
            {{ thermal.cooler_on ? 'DISABLE COOLER' : 'ENABLE COOLER' }}
          </button>
        </section>

        <!-- Sensor Config -->
        <section class="card">
          <h3 class="card-title"><span class="card-icon">◈</span> SENSOR CONFIG</h3>
          <div class="form-row">
            <label class="lbl">BINNING</label>
            <select v-model="cfgBinning" class="inp">
              <option value="1">1×1</option>
              <option value="2">2×2</option>
              <option value="3">3×3</option>
            </select>
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px">
            <div class="form-row">
              <label class="lbl">GAIN</label>
              <input v-model.number="cfgGain" type="number" class="inp mono" />
            </div>
            <div class="form-row">
              <label class="lbl">OFFSET</label>
              <input v-model.number="cfgOffset" type="number" class="inp mono" />
            </div>
          </div>
          <label class="lbl mt-2 block">ROI &nbsp;(x · y · w · h)</label>
          <div class="roi-grid mt-1">
            <input v-model.number="cfgRoiX" type="number" class="inp mono text-center" title="X" />
            <input v-model.number="cfgRoiY" type="number" class="inp mono text-center" title="Y" />
            <input v-model.number="cfgRoiW" type="number" class="inp mono text-center" title="Width" />
            <input v-model.number="cfgRoiH" type="number" class="inp mono text-center" title="Height" />
          </div>
          <div class="current-config mt-2">
            <span class="lbl">ACTIVE →</span>
            <span class="val mono small">
              bin:{{ config.binning?.[0] ?? '—' }}  gain:{{ config.gain ?? '—' }}  off:{{ config.offset ?? '—' }}
            </span>
          </div>
          <button class="btn btn-primary w-full mt-2" @click="handleApplyConfig"
            :disabled="!currentLeaseId || busyConfig">
            <span v-if="busyConfig" class="spin">◌</span>
            <span v-else>APPLY SETTINGS</span>
          </button>
        </section>

        <!-- Exposure History -->
        <section class="card hist-card">
          <h3 class="card-title">
            <span class="card-icon">◉</span> EXPOSURE HISTORY
            <span class="hist-count">{{ exposureHistory.length }}</span>
          </h3>
          <div v-if="!exposureHistory.length" class="hist-empty">
            No exposures yet
          </div>
          <div v-else class="hist-list">
            <div
              v-for="(cap, i) in exposureHistory"
              :key="i"
              class="hist-row"
              :class="i === 0 ? 'hist-row-latest' : ''"
            >
              <span class="hist-ts mono">{{ cap.completed_at?.slice(11,19) ?? '—' }}</span>
              <span class="hist-file mono truncate" :title="cap.file_path">
                {{ cap.file_path?.split('/').pop() ?? '—' }}
              </span>
              <span class="hist-exp mono">{{ cap.exptime_s }}s</span>
              <span :class="cap.image_valid ? 'val-green' : 'val-red'" class="hist-valid">
                {{ cap.image_valid ? '✓' : '✗' }}
              </span>
            </div>
          </div>
        </section>

      </aside>
    </div>

    <!-- ══ TERMINAL LOG ═══════════════════════════════════════════════════════ -->
    <footer class="terminal">
      <div class="term-header">
        <span class="term-title">▸ SYSTEM LOG</span>
        <span class="term-ws" :class="isConnected ? 'val-green' : 'val-red'">
          {{ isConnected ? '● WS ACTIVE @ 2 Hz' : '○ WS OFFLINE' }}
        </span>
        <button class="term-clear" @click="logEntries = []">CLR</button>
      </div>
      <div class="term-body">
        <div v-for="(e, i) in logEntries" :key="i" :class="['log-line', `log-${e.level}`]">
          <span class="log-ts">{{ e.ts }}</span>
          <span class="log-msg">{{ e.msg }}</span>
        </div>
        <div v-if="!logEntries.length" class="log-line log-info">
          <span class="log-ts">──────</span>
          <span class="log-msg">Waiting for activity…</span>
        </div>
      </div>
    </footer>

  </div>
</template>

<style>
/* ── RESET & FONTS ─────────────────────────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Barlow+Condensed:wght@300;400;600;700&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:        #080c10;
  --bg-card:   #0d1318;
  --bg-card2:  #111820;
  --border:    #1e2d3a;
  --border-hi: #2a4255;
  --text:      #c8d8e4;
  --text-dim:  #4a6070;
  --text-hi:   #e8f4ff;
  --green:     #00e5a0;
  --green-dim: #007a54;
  --blue:      #38b4ff;
  --blue-dim:  #1a5a80;
  --yellow:    #f5c842;
  --red:       #ff4a4a;
  --lease:     #a78bfa;
  --mono:      'Space Mono', monospace;
  --sans:      'Barlow Condensed', sans-serif;
  --r:         4px;
  --r2:        2px;
}

html, body, #app {
  height: 100%;
  overflow: hidden;
}

body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  font-size: 14px;
  line-height: 1.4;
}

/* ── SHELL ─────────────────────────────────────────────────────────────────── */
.shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg);
  background-image: radial-gradient(ellipse 80% 40% at 50% -10%, rgba(56,180,255,.06) 0%, transparent 70%);
}

/* ── TOPBAR ────────────────────────────────────────────────────────────────── */
.topbar {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 0 16px;
  height: 48px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border-hi);
  flex-shrink: 0;
}

.tb-left { display: flex; align-items: center; gap: 8px; }

.logo-mark {
  color: var(--blue);
  font-size: 22px;
  line-height: 1;
  filter: drop-shadow(0 0 6px var(--blue));
}

.logo-text {
  font-family: var(--sans);
  font-weight: 700;
  font-size: 18px;
  letter-spacing: .12em;
  color: var(--text-hi);
}

.logo-sub {
  font-weight: 300;
  color: var(--blue);
  margin-left: 4px;
  letter-spacing: .06em;
}

.version-badge {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--text-dim);
  border: 1px solid var(--border);
  border-radius: 99px;
  padding: 1px 6px;
  letter-spacing: .06em;
}

.tb-indicators {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}

.ind-pill {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border-radius: 99px;
  border: 1px solid var(--border);
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: .08em;
  transition: all .3s;
}

.ind-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  animation: pulse-dot 2s ease-in-out infinite;
}

.ind-ok    { color: var(--green);    border-color: var(--green-dim); background: rgba(0,229,160,.07); }
.ind-err   { color: var(--red);      border-color: #5a1a1a;          background: rgba(255,74,74,.07); }
.ind-lease { color: var(--lease);    border-color: #4a3060;          background: rgba(167,139,250,.07); }
.ind-off   { color: var(--text-dim); }

.state-chip {
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: .1em;
  padding: 3px 10px;
  border-radius: var(--r2);
  border: 1px solid var(--border-hi);
  background: var(--bg);
}
.state-idle    { color: var(--blue);     border-color: var(--blue-dim); }
.state-busy    { color: var(--yellow);   border-color: #5a4810; animation: state-blink .8s step-end infinite; }
.state-error   { color: var(--red);      border-color: #5a1a1a; }
.state-unknown { color: var(--text-dim); }

.tb-right { display: flex; align-items: center; gap: 8px; margin-left: auto; }

.tb-label { font-size: 11px; color: var(--text-dim); letter-spacing: .08em; }

.tb-input {
  background: var(--bg);
  border: 1px solid var(--border-hi);
  border-radius: var(--r2);
  color: var(--text);
  font-family: var(--mono);
  font-size: 11px;
  padding: 4px 8px;
  width: 160px;
  outline: none;
  transition: border-color .2s;
}
.tb-input:focus { border-color: var(--blue); }

/* ── WORKSPACE ─────────────────────────────────────────────────────────────── */
.workspace {
  display: grid;
  grid-template-columns: 275px 1fr 275px;
  flex: 1;
  overflow: hidden;
  border-top: 1px solid var(--border);
  min-height: 0;
}

/* ── PANELS ────────────────────────────────────────────────────────────────── */
.panel {
  overflow-y: auto;
  overflow-x: hidden;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}

.panel-left  { border-right: 1px solid var(--border); }
.panel-right { border-left:  1px solid var(--border); }

.center-panel {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
}

/* ── CARDS ─────────────────────────────────────────────────────────────────── */
.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 12px;
  position: relative;
}

.card::before {
  content: '';
  position: absolute;
  top: 0; left: 12px; right: 12px; height: 1px;
  background: linear-gradient(90deg, transparent, var(--border-hi), transparent);
}

.card-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-family: var(--sans);
  font-weight: 600;
  font-size: 11px;
  letter-spacing: .14em;
  color: var(--text-dim);
  text-transform: uppercase;
  margin-bottom: 12px;
}

.card-icon { color: var(--blue); font-size: 13px; line-height: 1; }

/* ── FORMS & INPUTS ────────────────────────────────────────────────────────── */
.form-row { display: flex; flex-direction: column; gap: 4px; margin-bottom: 8px; }

.lbl {
  font-size: 10px;
  letter-spacing: .1em;
  color: var(--text-dim);
  text-transform: uppercase;
}
.lbl-opt { opacity: .5; }

.val { font-family: var(--sans); font-size: 13px; }

.inp {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  color: var(--text-hi);
  font-family: var(--sans);
  font-size: 13px;
  padding: 6px 8px;
  outline: none;
  width: 100%;
  transition: border-color .2s, box-shadow .2s;
  -webkit-appearance: none;
  appearance: none;
}
.inp:focus    { border-color: var(--blue); box-shadow: 0 0 0 2px rgba(56,180,255,.15); }
.inp:disabled { opacity: .4; cursor: not-allowed; }
.inp.mono     { font-family: var(--mono); font-size: 12px; }
.inp.inp-large { font-size: 15px; padding: 8px 10px; font-weight: 600; }
.inp.text-center { text-align: center; }

select.inp { cursor: pointer; }

/* ── BUTTONS ───────────────────────────────────────────────────────────────── */
.btn {
  font-family: var(--sans);
  font-weight: 700;
  font-size: 12px;
  letter-spacing: .1em;
  padding: 7px 14px;
  border: 1px solid transparent;
  border-radius: var(--r2);
  cursor: pointer;
  transition: all .15s;
  outline: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  white-space: nowrap;
}
.btn:disabled { opacity: .35; cursor: not-allowed; filter: grayscale(.4); }

.btn-acquire { background: rgba(0,229,160,.1);  border-color: var(--green-dim); color: var(--green); flex: 1; }
.btn-acquire:not(:disabled):hover { background: rgba(0,229,160,.2); border-color: var(--green); }

.btn-release { background: rgba(255,74,74,.1);  border-color: #5a1a1a;          color: var(--red);   flex: 1; }
.btn-release:not(:disabled):hover { background: rgba(255,74,74,.2); border-color: var(--red); }

.btn-connect { background: rgba(56,180,255,.1); border-color: var(--blue-dim);  color: var(--blue);  flex: 1; }
.btn-connect:not(:disabled):hover { background: rgba(56,180,255,.2); border-color: var(--blue); }

.btn-primary { background: rgba(56,180,255,.12); border-color: var(--blue-dim); color: var(--blue); }
.btn-primary:not(:disabled):hover { background: rgba(56,180,255,.22); border-color: var(--blue); }

.btn-abort {
  background: rgba(255,74,74,.1);
  border-color: #5a1a1a;
  color: var(--red);
  font-size: 13px;
  padding: 10px 20px;
}
.btn-abort:not(:disabled):hover { background: rgba(255,74,74,.2); border-color: var(--red); }

.btn-expose {
  flex: 1;
  background: rgba(0,229,160,.1);
  border-color: var(--green-dim);
  color: var(--green);
  font-size: 14px;
  letter-spacing: .15em;
  padding: 11px 20px;
}
.btn-expose:not(:disabled):hover { background: rgba(0,229,160,.18); border-color: var(--green); }
.btn-expose-pulse:not(:disabled) { animation: pulse-green 2.5s ease-in-out infinite; }

.btn-row { display: flex; gap: 6px; }
.w-full  { width: 100%; }

/* ── BARS ──────────────────────────────────────────────────────────────────── */
.bar-bg {
  background: rgba(255,255,255,.04);
  border-radius: 2px;
  height: 6px;
  overflow: hidden;
  border: 1px solid var(--border);
}
.bar-large { height: 10px; }
.bar-fill {
  height: 100%;
  border-radius: 2px;
  transition: width .5s ease;
  position: relative;
}
.bar-blue   { background: linear-gradient(90deg, var(--blue-dim), var(--blue)); }
.bar-yellow { background: linear-gradient(90deg, #7a5c00, var(--yellow)); }
.bar-red    { background: linear-gradient(90deg, #7a0000, var(--red)); }
.bar-exp    { background: linear-gradient(90deg, var(--green-dim), var(--green)); }

.bar-shimmer {
  position: absolute; inset: 0;
  background: linear-gradient(90deg, transparent 30%, rgba(255,255,255,.25) 50%, transparent 70%);
  animation: shimmer 1.5s linear infinite;
}

.mini-bar-bg   { background: rgba(255,255,255,.04); border-radius: 2px; height: 3px; overflow: hidden; }
.mini-bar-fill { height: 100%; transition: width .5s; background: linear-gradient(90deg, var(--lease), #c4b5fd); }
.mini-bar-red  { background: linear-gradient(90deg, #7a0000, var(--red)) !important; }

/* ── VIEWPORT ──────────────────────────────────────────────────────────────── */
.viewport {
  position: relative;
  flex: 1;
  background: #020508;
  border-bottom: 1px solid var(--border);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
}

.vp-corner {
  position: absolute;
  width: 16px; height: 16px;
  border-color: var(--blue-dim);
  border-style: solid;
  z-index: 2;
  pointer-events: none;
}
.vp-tl { top: 8px;    left: 8px;   border-width: 1px 0 0 1px; }
.vp-tr { top: 8px;    right: 8px;  border-width: 1px 1px 0 0; }
.vp-bl { bottom: 8px; left: 8px;   border-width: 0 0 1px 1px; }
.vp-br { bottom: 8px; right: 8px;  border-width: 0 1px 1px 0; }

.vp-image {
  max-width: 100%; max-height: 100%;
  object-fit: contain;
  z-index: 1;
  image-rendering: pixelated;
}

.vp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 18px;
}

.reticle { position: relative; width: 80px; height: 80px; }
.ret-h {
  position: absolute;
  top: 50%; left: 0; right: 0; height: 1px;
  background: rgba(56,180,255,.25);
  transform: translateY(-50%);
}
.ret-v {
  position: absolute;
  left: 50%; top: 0; bottom: 0; width: 1px;
  background: rgba(56,180,255,.25);
  transform: translateX(-50%);
}
.ret-circle {
  position: absolute; inset: 10px;
  border-radius: 50%;
  border: 1px solid rgba(56,180,255,.18);
  animation: ret-spin 12s linear infinite;
}

.vp-label {
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: .2em;
  color: var(--text-dim);
}

.vp-meta {
  position: absolute; bottom: 0; left: 0; right: 0;
  display: flex; gap: 16px; padding: 5px 12px;
  background: rgba(8,12,16,.85);
  border-top: 1px solid var(--border);
  font-family: var(--mono); font-size: 10px;
  color: var(--text-dim);
  z-index: 3;
}

/* ── EXPOSURE CARD ─────────────────────────────────────────────────────────── */
.exposure-card {
  border-radius: 0;
  border-left: none; border-right: none; border-bottom: none;
  flex-shrink: 0;
}

.exp-params {
  display: grid;
  grid-template-columns: 1fr 1fr 1.4fr;
  gap: 10px;
  margin-bottom: 10px;
}
.exp-field { display: flex; flex-direction: column; gap: 4px; }

.exp-actions { display: flex; gap: 8px; margin-bottom: 10px; }

.prog-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
}

/* ── THERMAL ───────────────────────────────────────────────────────────────── */
.thermal-display { margin-bottom: 10px; }

.temp-readout {
  display: flex; align-items: baseline; gap: 4px; margin-bottom: 8px;
}
.temp-value {
  font-family: var(--mono); font-size: 36px; font-weight: 700; line-height: 1;
  transition: color .5s;
}
.temp-stable { color: var(--green); text-shadow: 0 0 18px rgba(0,229,160,.4); }
.temp-drift  { color: var(--yellow); text-shadow: 0 0 18px rgba(245,200,66,.3); }
.temp-unit   { font-size: 16px; color: var(--text-dim); }

.cooler-badge {
  font-family: var(--mono); font-size: 9px; letter-spacing: .1em;
  padding: 2px 8px; border-radius: 99px; border: 1px solid;
}
.badge-off     { color: var(--text-dim); border-color: var(--border); background: transparent; }
.badge-stable  { color: var(--green);  border-color: var(--green-dim); background: rgba(0,229,160,.08); }
.badge-cooling { color: var(--blue);   border-color: var(--blue-dim);  background: rgba(56,180,255,.08); animation: pulse-dot 1s ease-in-out infinite; }
.badge-warn    { color: var(--red);    border-color: #5a1a1a;          background: rgba(255,74,74,.08); }

/* ── SENSOR CONFIG ─────────────────────────────────────────────────────────── */
.roi-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 1fr;
  gap: 5px;
}

.current-config {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 8px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  font-size: 11px;
}

/* ── UTILITY ───────────────────────────────────────────────────────────────── */
.di-row     { display: flex; align-items: center; gap: 8px; font-size: 12px; margin-bottom: 4px; }
.device-block { margin-bottom: 6px; }
.cap-grid   { display: flex; flex-direction: column; gap: 4px; }
.lease-info { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); }
.disk-detail { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.truncate   { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 90px; }
.mt-1  { margin-top: 6px; }
.mt-2  { margin-top: 10px; }
.mb-1  { margin-bottom: 4px; }
.block { display: block; }
.small { font-size: 10px; }

/* COLOR VALS */
.val-green  { color: var(--green); }
.val-blue   { color: var(--blue); }
.val-yellow { color: var(--yellow); }
.val-red    { color: var(--red); }
.mono       { font-family: var(--mono); font-size: 12px; }

/* ── TERMINAL ──────────────────────────────────────────────────────────────── */
.terminal {
  flex-shrink: 0;
  height: 116px;
  background: #060a0d;
  border-top: 1px solid var(--border-hi);
  display: flex;
  flex-direction: column;
  font-family: var(--mono);
  font-size: 11px;
}
.term-header {
  display: flex; align-items: center; gap: 12px;
  padding: 5px 14px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.term-title { color: var(--text-dim); letter-spacing: .1em; font-size: 10px; }
.term-ws    { font-size: 10px; letter-spacing: .08em; margin-left: auto; }
.term-clear {
  background: none; border: 1px solid var(--border); color: var(--text-dim);
  font-family: var(--mono); font-size: 10px; padding: 2px 8px;
  border-radius: var(--r2); cursor: pointer; letter-spacing: .06em;
}
.term-clear:hover { color: var(--text); border-color: var(--border-hi); }

.term-body {
  flex: 1; overflow-y: auto; padding: 6px 14px;
  display: flex; flex-direction: column; gap: 2px;
  scrollbar-width: thin; scrollbar-color: var(--border) transparent;
}

.log-line { display: flex; gap: 12px; align-items: baseline; }
.log-ts   { color: var(--text-dim); font-size: 10px; flex-shrink: 0; letter-spacing: .04em; }
.log-msg  { color: var(--text); }
.log-ok   .log-msg { color: var(--green); }
.log-err  .log-msg { color: var(--red); }
.log-warn .log-msg { color: var(--yellow); }
.log-info .log-msg { color: var(--text-dim); }

/* ── SPINNER ───────────────────────────────────────────────────────────────── */
.spin { animation: spin .8s linear infinite; display: inline-block; }

/* ── KEYFRAMES ─────────────────────────────────────────────────────────────── */
@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50%       { opacity: .3; }
}
@keyframes pulse-green {
  0%, 100% { box-shadow: 0 0 0 0 rgba(0,229,160,0); }
  50%       { box-shadow: 0 0 0 4px rgba(0,229,160,.15); }
}
@keyframes state-blink {
  0%, 100% { opacity: 1; }
  50%       { opacity: .5; }
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
@keyframes shimmer {
  from { transform: translateX(-100%); }
  to   { transform: translateX(200%); }
}
@keyframes ret-spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

/* ── SCROLLBARS ────────────────────────────────────────────────────────────── */
::-webkit-scrollbar       { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

/* ── EXPOSURE HISTORY ──────────────────────────────────────────────────────── */
.hist-card { display: flex; flex-direction: column; }

.hist-count {
  margin-left: auto;
  font-family: var(--mono);
  font-size: 10px;
  color: var(--blue);
  background: rgba(56,180,255,.1);
  border: 1px solid var(--blue-dim);
  border-radius: 99px;
  padding: 1px 7px;
}

.hist-empty {
  font-size: 11px;
  color: var(--text-dim);
  text-align: center;
  padding: 14px 0;
  font-family: var(--mono);
  letter-spacing: .06em;
}

.hist-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 260px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}

.hist-row {
  display: grid;
  grid-template-columns: 52px 1fr 30px 14px;
  gap: 6px;
  align-items: center;
  padding: 4px 6px;
  border-radius: var(--r2);
  background: rgba(255,255,255,.02);
  border: 1px solid transparent;
  transition: background .15s;
}
.hist-row:hover { background: rgba(255,255,255,.05); }
.hist-row-latest { border-color: var(--border); background: rgba(56,180,255,.04); }

.hist-ts    { font-size: 10px; color: var(--text-dim); }
.hist-file  { font-size: 10px; color: var(--text); }
.hist-exp   { font-size: 10px; color: var(--text-dim); text-align: right; }
.hist-valid { font-size: 11px; text-align: center; font-weight: 700; }

/* ── RESPONSIVE LAYOUT ─────────────────────────────────────────────────────── */

/* Tablet: ≤ 1024px — panels shrink, no right panel overflow */
@media (max-width: 1024px) {
  .workspace {
    grid-template-columns: 240px 1fr 240px;
  }
}

/* Tablet portrait / small laptop: ≤ 860px — stack panels below center */
@media (max-width: 860px) {
  html, body, #app { overflow: auto; }

  .shell {
    height: auto;
    min-height: 100vh;
    overflow: auto;
  }

  .workspace {
    grid-template-columns: 1fr;
    grid-template-rows: auto;
    overflow: visible;
  }

  .panel-left, .panel-right {
    border: none;
    border-top: 1px solid var(--border);
    max-height: none;
    overflow: visible;
  }

  .center-panel {
    min-height: 480px;
    overflow: visible;
  }

  .viewport { min-height: 300px; }

  .terminal { height: auto; min-height: 140px; }
}

/* Mobile: ≤ 540px — compact topbar and smaller inputs */
@media (max-width: 540px) {
  .topbar {
    flex-wrap: wrap;
    height: auto;
    padding: 8px 10px;
    gap: 8px;
  }

  .tb-right { width: 100%; }
  .tb-input { width: 100%; }

  .tb-indicators { margin-left: 0; flex-wrap: wrap; }

  .exp-params {
    grid-template-columns: 1fr 1fr;
  }
  .exp-field-wide { grid-column: 1 / -1; }

  .roi-grid { grid-template-columns: 1fr 1fr; }

  .workspace { grid-template-columns: 1fr; }

  .version-badge { display: none; }
}
</style>