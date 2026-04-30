/**
 * Camera Thermal API Service
 * Handles HTTP requests to control CCD cooling via /camera/config.
 */

const API_BASE_URL = '/api/v1';

/**
 * Sets the cooler target temperature (enables cooler if not already on).
 * @param {string} leaseId - The current active lease token.
 * @param {string} apiKey - The API authentication key.
 * @param {number} targetTempC - Target CCD temperature in Celsius.
 * @returns {Promise<Object>} Applied configuration response.
 */
export async function setThermalTarget(leaseId, apiKey, targetTempC) {
  const response = await fetch(`${API_BASE_URL}/camera/config`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Lease-ID': leaseId || '',
      'X-API-Key': apiKey || '',
    },
    body: JSON.stringify({
      cooler: {
        enabled: true,
        target_c: targetTempC,
      },
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData?.error?.message || errorData?.detail || 'Failed to set target temperature.');
  }

  return response.json();
}

/**
 * Enables or disables the CCD cooler.
 * @param {string} leaseId - The current active lease token.
 * @param {string} apiKey - The API authentication key.
 * @param {boolean} enable - true to enable, false to disable.
 * @param {number} targetTempC - Target temperature (required when enabling).
 * @returns {Promise<Object>} Applied configuration response.
 */
export async function toggleCooler(leaseId, apiKey, enable, targetTempC) {
  const response = await fetch(`${API_BASE_URL}/camera/config`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Lease-ID': leaseId || '',
      'X-API-Key': apiKey || '',
    },
    body: JSON.stringify({
      cooler: {
        enabled: enable,
        target_c: targetTempC ?? -10,
      },
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData?.error?.message || errorData?.detail || 'Failed to toggle cooler.');
  }

  return response.json();
}
