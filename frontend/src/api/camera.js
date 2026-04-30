/**
 * Camera API Service
 * Handles HTTP requests for hardware connection and disconnection.
 */

const API_BASE_URL = '/api/v1';

/**
 * Sends a connect command to the INDIGO camera hardware.
 * @param {string} leaseId - The current active lease token.
 * @param {string} apiKey - The API authentication key.
 * @returns {Promise<Object>} Success confirmation with device name and connected_at.
 */
export async function connectCamera(leaseId, apiKey) {
  const response = await fetch(`${API_BASE_URL}/camera/connect`, {
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
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg) || 'Failed to connect the camera.');
  }

  return response.json();
}

/**
 * Sends a disconnect command to the INDIGO camera hardware.
 * @param {string} leaseId - The current active lease token.
 * @param {string} apiKey - The API authentication key.
 * @returns {Promise<Object>} Success confirmation with disconnected_at.
 */
export async function disconnectCamera(leaseId, apiKey) {
  const response = await fetch(`${API_BASE_URL}/camera/disconnect`, {
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
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg) || 'Failed to disconnect the camera.');
  }

  return response.json();
}
