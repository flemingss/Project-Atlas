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
  },
};
