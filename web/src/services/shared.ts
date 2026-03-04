/**
 * Shared fetch helpers — used by all API service modules.
 * Extracted from the original api.ts to avoid circular imports.
 */

/** Read admin token from localStorage, falling back to ?token= query param. */
export function getAdminToken(): string | null {
  const stored = localStorage.getItem('atlas_admin_token');
  if (stored && stored.trim()) return stored;

  const token = (new URLSearchParams(window.location.search).get('token') || '').trim();
  if (token) {
    localStorage.setItem('atlas_admin_token', token);
    return token;
  }
  return null;
}

/** Build standard JSON headers, including admin token if available. */
export function headers(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = getAdminToken();
  if (token) h['X-Atlas-Admin-Token'] = token;
  return h;
}

/** Build headers WITHOUT Content-Type (for FormData uploads). */
export function authHeaders(): Record<string, string> {
  const h: Record<string, string> = {};
  const token = getAdminToken();
  if (token) h['X-Atlas-Admin-Token'] = token;
  return h;
}

/** Generic JSON fetch with auth headers. Throws on non-2xx. */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, { headers: headers(), ...init });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`API ${resp.status}: ${text}`);
  }
  return resp.json() as Promise<T>;
}

/** Raw Response fetch (for downloads / non-JSON). Throws on non-2xx. */
export async function apiFetchRaw(path: string, init?: RequestInit): Promise<Response> {
  const h: Record<string, string> = {};
  const token = getAdminToken();
  if (token) h['X-Atlas-Admin-Token'] = token;
  const resp = await fetch(path, { headers: h, ...init });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`API ${resp.status}: ${text}`);
  }
  return resp;
}
