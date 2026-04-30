/**
 * Camera Configuration API Service
 * Handles HTTP requests to update sensor parameters (Binning, Gain, Offset, ROI).
 */

const API_BASE_URL = '/api/v1';

/**
 * Sends a configuration payload to the INDIGO camera hardware.
 * All fields are optional — only provided fields are updated.
 *
 * Expected payload shape (all fields optional):
 * {
 *   binning: { x: number, y: number },
 *   roi:     { x: number, y: number, width: number, height: number },
 *   gain:    number,
 *   offset:  number,
 *   cooler:  { enabled: boolean, target_c: number },
 * }
 *
 * @param {string} leaseId - The current active lease token.
 * @param {string} apiKey - The API authentication key.
 * @param {Object} configPayload - Configuration settings to apply.
 * @returns {Promise<Object>} Applied configuration confirmation.
 */
export async function updateCameraConfig(leaseId, apiKey, configPayload) {
  const response = await fetch(`${API_BASE_URL}/camera/config`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Lease-ID': leaseId || '',
      'X-API-Key': apiKey || '',
    },
    body: JSON.stringify(configPayload),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const msg = errorData?.error?.message || errorData?.detail;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg) || 'Failed to update camera configuration.');
  }

  return response.json();
}
