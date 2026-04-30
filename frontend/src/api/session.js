/**
 * Camera Session API Service
 * Handles storage session configuration (path, file prefix, naming pattern).
 */

const API_BASE_URL = '/api/v1';

/**
 * Configures the camera storage session — sets the output directory and file naming.
 * @param {string} leaseId - The current active lease token.
 * @param {string} apiKey - The API authentication key.
 * @param {string} storagePath - Absolute path where FITS files will be written.
 * @param {string} filePrefix - Filename prefix (e.g. "cavex").
 * @param {string} [namingPattern='{prefix}_{seq:04d}.fits'] - File naming pattern.
 * @returns {Promise<Object>} session_id, storage_path, disk_free_gb, configured_at.
 */
export async function configureSession(leaseId, apiKey, storagePath, filePrefix, namingPattern = '{prefix}_{seq:04d}.fits') {
  const response = await fetch(`${API_BASE_URL}/camera/session`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Lease-ID': leaseId || '',
      'X-API-Key': apiKey || '',
    },
    body: JSON.stringify({
      storage_path:    storagePath,
      file_prefix:     filePrefix,
      naming_pattern:  namingPattern,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const msg = errorData?.error?.message || errorData?.detail;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg) || 'Failed to configure storage session.');
  }

  return response.json();
}
