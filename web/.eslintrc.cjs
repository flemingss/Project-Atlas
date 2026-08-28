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
    // Pre-set for eslint-plugin-react-hooks 7, which is currently pinned back
    // to 4.6.2 (its React Compiler analysis triggers a flaky V8 JIT crash
    // during lint — see .github/dependabot.yml). Harmless on 4.x, where the
    // rule does not exist, because ESLint only resolves rules it must enable.
    // When v7 is re-adopted this keeps its 11 set-state-in-effect findings
    // from blocking: nearly all are the "load data on mount" pattern plus the
    // canonical matchMedia sync, whose real fix is migrating those pages to
    // React Query (already a dependency) and use-mobile to
    // useSyncExternalStore - a refactor, not a lint tweak.
    'react-hooks/set-state-in-effect': 'off',
  },
};
