/**
 * Connection store — tracks Atlas API connection state and admin token.
 */
import { create } from 'zustand';

export interface ConnectionState {
  /** Base API URL (relative paths for same-origin requests). */
  apiUrl: string;
  /** Whether we've verified /health returns ok. */
  isConnected: boolean;
  /** Whether the token grants admin access. */
  isAdmin: boolean;
  /** Raw health response. */
  healthData: Record<string, unknown> | null;
  /** Last connection error. */
  error: string | null;
  /** Loading state for connection check. */
  isChecking: boolean;
}

export interface ConnectionActions {
  /** Check /health and admin access. */
  checkConnection: () => Promise<void>;
  /** Clear connection state. */
  disconnect: () => void;
  /** Set admin token in localStorage. */
  setToken: (token: string) => void;
  /** Get current admin token. */
  getToken: () => string | null;
}

export const useConnectionStore = create<ConnectionState & ConnectionActions>((set) => ({
  apiUrl: '',
  isConnected: false,
  isAdmin: false,
  healthData: null,
  error: null,
  isChecking: false,

  checkConnection: async () => {
    set({ isChecking: true, error: null });
    try {
      // Check health
      const healthResp = await fetch('/health');
      if (!healthResp.ok) throw new Error(`Health check failed: ${healthResp.status}`);
      const healthData = await healthResp.json();

      // Check admin access — probe the admin API.
      // In dev mode the backend may bypass token auth entirely, so always try
      // even when no token is stored locally.
      let isAdmin = false;
      const token = localStorage.getItem('atlas_admin_token');
      try {
        const headers: Record<string, string> = {};
        if (token && token.trim()) {
          headers['X-Atlas-Admin-Token'] = token;
        }
        const adminResp = await fetch('/admin/config/effective', { headers });
        isAdmin = adminResp.ok;
      } catch {
        // Admin check failed — not admin, but still connected
      }

      set({ isConnected: true, isAdmin, healthData, isChecking: false });
    } catch (e) {
      set({
        isConnected: false,
        isAdmin: false,
        healthData: null,
        error: e instanceof Error ? e.message : String(e),
        isChecking: false,
      });
    }
  },

  disconnect: () => {
    set({ isConnected: false, isAdmin: false, healthData: null, error: null });
  },

  setToken: (token: string) => {
    localStorage.setItem('atlas_admin_token', token);
  },

  getToken: () => {
    return localStorage.getItem('atlas_admin_token');
  },
}));
