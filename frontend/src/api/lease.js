/**
 * Lease API Service
 * Handles HTTP requests to the CAVEX Imager Lease Manager.
 */

const API_BASE_URL = '/api/v1';

/**
 * Requests exclusive write control of the camera.
 * @param {string} owner - Identifier of the requesting client.
 * @param {string} apiKey - API authentication key.
 * @param {number} priority - Priority level (0–1000). Higher priority can preempt lower.
 * @param {number} ttlSeconds - Lease time-to-live in seconds (10–600).
 * @returns {Promise<Object>} The acquired lease data (lease_id, owner, priority, expires_at).
 */
export async function acquireLease(owner, apiKey, priority = 10, ttlSeconds = 120) {
  const response = await fetch(`${API_BASE_URL}/lease/acquire`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': apiKey || '',
    },
    body: JSON.stringify({
      owner,
      priority,
      ttl_seconds: ttlSeconds,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData?.error?.message || errorData?.detail || 'Failed to acquire lease.');
  }

  return response.json();
}

/**
 * Renews the TTL of an active lease.
 * @param {string} leaseId - The lease identifier to renew.
 * @param {string} apiKey - API authentication key.
 * @param {number} ttlSeconds - New time-to-live in seconds.
 * @returns {Promise<Object>} Updated lease_id and expires_at.
 */
export async function renewLease(leaseId, apiKey, ttlSeconds = 120) {
  const response = await fetch(`${API_BASE_URL}/lease/renew`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': apiKey || '',
    },
    body: JSON.stringify({
      lease_id: leaseId,
      ttl_seconds: ttlSeconds,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData?.error?.message || errorData?.detail || 'Failed to renew lease.');
  }

  return response.json();
}

/**
 * Releases the current lease, freeing the camera for other clients.
 * @param {string} leaseId - The lease identifier to release.
 * @param {string} apiKey - API authentication key.
 * @returns {Promise<Object>} Confirmation with released_at timestamp.
 */
export async function releaseLease(leaseId, apiKey) {
  const response = await fetch(`${API_BASE_URL}/lease/release`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': apiKey || '',
    },
    body: JSON.stringify({
      lease_id: leaseId,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData?.error?.message || errorData?.detail || 'Failed to release lease.');
  }

  return response.json();
}
