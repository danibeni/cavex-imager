/**
 * Camera Exposure API Service
 * Handles HTTP requests to start and abort camera exposures.
 */

const API_BASE_URL = '/api/v1';

/**
 * Starts a new exposure (HTTP 202 Accepted — operation is asynchronous).
 * Monitor completion via WebSocket telemetry: capture_event { event: 'capture_done' }.
 *
 * @param {string} leaseId - The current active lease token.
 * @param {string} apiKey - The API authentication key.
 * @param {Object} payload - Exposure configuration.
 * @param {number} payload.exptime_s - Exposure time in seconds.
 * @param {string} [payload.frame_type='LIGHT'] - Frame type: LIGHT, DARK, BIAS, or FLAT.
 * @param {string} [payload.job_id] - Optional job identifier.
 * @returns {Promise<Object>} exposure_id, started_at, estimated_done_at.
 */
export async function startExposure(leaseId, apiKey, payload) {
  const response = await fetch(`${API_BASE_URL}/camera/exposure/start`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Lease-ID': leaseId || '',
      'X-API-Key': apiKey || '',
    },
    body: JSON.stringify({
      exptime_s:  payload.exptime_s,
      frame_type: payload.frame_type ?? 'LIGHT',
      job_id:     payload.job_id ?? undefined,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const msg = errorData?.error?.message || errorData?.detail;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg) || 'Failed to start exposure.');
  }

  return response.json();
}

/**
 * Aborts the currently running exposure.
 * @param {string} leaseId - The current active lease token.
 * @param {string} apiKey - The API authentication key.
 * @returns {Promise<Object>} Confirmation with aborted_at timestamp.
 */
export async function abortExposure(leaseId, apiKey) {
  const response = await fetch(`${API_BASE_URL}/camera/exposure/abort`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Lease-ID': leaseId || '',
      'X-API-Key': apiKey || '',
    },
    body: JSON.stringify({}),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const msg = errorData?.error?.message || errorData?.detail;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg) || 'Failed to abort exposure.');
  }

  return response.json();
}
