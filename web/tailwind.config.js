import { fontFamily } from 'tailwindcss/defaultTheme';
import tailwindcssAnimate from 'tailwindcss-animate';

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['selector'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    container: {
      center: true,
      padding: '2rem',
      screens: { '2xl': '1536px' },
    },
    extend: {
      colors: {
        // ── Semantic surface tokens (CSS vars, light + dark) ──
        border: 'var(--border-default)',
        input: 'var(--border-default)',
        ring: 'var(--accent-primary)',
        background: 'var(--bg-base)',
        foreground: 'var(--text-primary)',

        // Surface hierarchy
        'bg-base': 'var(--bg-base)',
        'bg-surface': 'var(--bg-surface)',
        'bg-card': 'var(--bg-card)',
        'bg-overlay': 'var(--bg-overlay)',

        // Text hierarchy
        'text-primary': 'var(--text-primary)',
        'text-secondary': 'var(--text-secondary)',
        'text-muted': 'var(--text-muted)',

        // Accent
        accent: {
          DEFAULT: 'var(--accent-primary)',
          hover: 'var(--accent-hover)',
          foreground: 'var(--accent-foreground)',
        },

        // State
        success: 'var(--state-success)',
        warning: 'var(--state-warning)',
        error: 'var(--state-error)',

        // Component-level
        primary: {
          DEFAULT: 'var(--accent-primary)',
          foreground: 'var(--accent-foreground)',
        },
        secondary: {
          DEFAULT: 'var(--bg-surface)',
          foreground: 'var(--text-primary)',
        },
        destructive: {
          DEFAULT: 'var(--state-error)',
          foreground: '#ffffff',
        },
        muted: {
          DEFAULT: 'var(--bg-surface)',
          foreground: 'var(--text-muted)',
        },
        popover: {
          DEFAULT: 'var(--bg-surface)',
          foreground: 'var(--text-primary)',
        },
        card: {
          DEFAULT: 'var(--bg-card)',
          foreground: 'var(--text-primary)',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      fontFamily: {
        sans: ['Inter', 'var(--font-sans)', ...fontFamily.sans],
        mono: [
          'JetBrains Mono',
          'Fira Code',
          'Cascadia Code',
          ...fontFamily.mono,
        ],
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
        pulse: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.4' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
        pulse: 'pulse 1s infinite',
      },
    },
  },
  plugins: [tailwindcssAnimate],
};
