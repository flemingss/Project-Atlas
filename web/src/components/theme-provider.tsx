/**
 * ThemeProvider — Applies the theme on mount and provides context.
 *
 * Wraps the app root to ensure the correct class is applied to <html> on first render.
 * Uses the useTheme hook under the hood; exposes theme state via React context
 * so any component can access it via useThemeContext().
 */
import * as React from 'react';
import { useTheme, type Theme } from '@/hooks/use-theme';

interface ThemeContextValue {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
  isDark: boolean;
}

const ThemeContext = React.createContext<ThemeContextValue | undefined>(undefined);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const themeState = useTheme();

  return <ThemeContext.Provider value={themeState}>{children}</ThemeContext.Provider>;
}

export function useThemeContext(): ThemeContextValue {
  const ctx = React.useContext(ThemeContext);
  if (!ctx) {
    throw new Error('useThemeContext must be used within a ThemeProvider');
  }
  return ctx;
}
