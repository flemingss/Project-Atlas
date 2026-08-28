/**
 * Shared fetch helpers — used by all API service modules.
 * Extracted from the original api.ts to avoid circular imports.
 */

export const ADMIN_TOKEN_KEY = 'atlas_admin_token';

/**
 * Read the admin token, accepting a one-time `?token=` bootstrap.
 *
 * The query parameter is consumed and immediately erased from the URL. A
 * credential left sitting in the address bar ends up in browser history, in
 * anything the operator copy-pastes, and in the referrer of any outbound link
 * — so it is captured once and the URL is rewritten in place.
 *
 * This is the single implementation on purpose: the same read-and-store logic
 * had been copied into four modules, and only one of them would ever have been
 * remembered when the handling needed to change.
 */
export function getAdminToken(): string | null {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(ADMIN_TOKEN_KEY);
  } catch {
    /* storage unavailable (private mode) — fall through to the query param */
  }
  if (stored && stored.trim()) return stored;

  const params = new URLSearchParams(window.location.search);
  const token = (params.get('token') || '').trim();
  if (!token) return null;

  try {
    localStorage.setItem(ADMIN_TOKEN_KEY, token);
  } catch {
    /* not persistable — still usable for this page load */
  }

  // Strip it from the visible URL without adding a history entry.
  try {
    params.delete('token');
    const qs = params.toString();
    window.history.replaceState(
      null,
      '',
      `${window.location.pathname}${qs ? `?${qs}` : ''}${window.location.hash}`,
    );
  } catch {
    /* replaceState unavailable — the token is stored either way */
  }
  return token;
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
