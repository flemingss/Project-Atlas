/* ESLint config (flat-config migration pending with ESLint 9).
 * Focused on correctness rules — formatting is left to the editor. */
module.exports = {
  root: true,
  env: { browser: true, es2022: true },
  parser: '@typescript-eslint/parser',
  parserOptions: { ecmaVersion: 'latest', sourceType: 'module' },
  plugins: ['@typescript-eslint', 'react-hooks', 'react-refresh'],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  ignorePatterns: ['dist', 'src/api-types.gen.ts'],
  rules: {
    // Vite HMR nicety, not a correctness issue.
    'react-refresh/only-export-components': 'off',
    // `any` appears at untyped-dict API boundaries; tighten over time.
    '@typescript-eslint/no-explicit-any': 'off',
    // Intentionally-unused function args are prefixed with _.
    '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    // ── Registered debt (2026-08-28, eslint-plugin-react-hooks 7) ─────────
    // set-state-in-effect flags 11 sites, nearly all the "load data on mount"
    // pattern (effect calls a loader that setStates) plus the canonical
    // matchMedia sync in use-mobile. Fixing them properly means migrating
    // those pages to React Query (already a dependency) and use-mobile to
    // useSyncExternalStore - a refactor with regression risk, not a lint
    // tweak. Turned off deliberately so the rest of the v7 rules stay
    // active; re-enable when that migration is scheduled.
    'react-hooks/set-state-in-effect': 'off',
  },
};
