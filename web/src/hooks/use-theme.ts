/**
 * useTheme — Dark/light mode hook with localStorage persistence.
 *
 * Adapted from RAGFlow's ThemeProvider pattern.
 * Toggles the `dark` class on `<html>` (matching Tailwind's selector strategy).
 */
import { useCallback, useEffect, useState } from 'react';

export type Theme = 'light' | 'dark' | 'system';

const STORAGE_KEY = 'atlas-theme';

function getSystemTheme(): 'light' | 'dark' {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(theme: Theme) {
  const resolved = theme === 'system' ? getSystemTheme() : theme;
  const root = document.documentElement;
  root.classList.remove('light', 'dark');
  root.classList.add(resolved);
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => {
    try {
      return (localStorage.getItem(STORAGE_KEY) as Theme) || 'dark';
    } catch {
      return 'dark';
    }
  });

  // Apply on mount and when theme changes
  useEffect(() => {
    applyTheme(theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Storage unavailable
    }
  }, [theme]);

  // Listen for system theme changes when using 'system'
  useEffect(() => {
    if (theme !== 'system') return;
    const mql = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => applyTheme('system');
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [theme]);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeState((prev) => {
      const resolved = prev === 'system' ? getSystemTheme() : prev;
      return resolved === 'dark' ? 'light' : 'dark';
    });
  }, []);

  const isDark = theme === 'system' ? getSystemTheme() === 'dark' : theme === 'dark';

  return { theme, setTheme, toggleTheme, isDark };
}
