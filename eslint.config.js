// ESLint: typescript-eslint recommended + react-hooks for the interface; plain JS rules for the tests.
// `any` is an error: the interface is fully typed (2.9.0) and must stay that way.
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';

export default tseslint.config(
  { ignores: ['node_modules/**', 'web/dist/**', 'out/**', 'test/out/**', 'web/ui-bundle/**'] },
  ...tseslint.configs.recommended,
  {
    // The interface: typescript-eslint recommended + react-hooks, no `any`.
    files: ['web/src/**/*.{ts,tsx}'],
    plugins: { 'react-hooks': reactHooks },
    languageOptions: { globals: { ...globals.browser, __APP_VERSION__: 'readonly' } },
    rules: {
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' }],
      '@typescript-eslint/no-non-null-assertion': 'off',
    },
  },
  {
    // Tests and tooling: plain JavaScript, Node globals, the core recommended rules on top.
    files: ['test/**/*.{js,mjs,ts}', 'scripts/**/*.{js,mjs}', 'vite.config.mjs', 'vitest.config.mts', 'eslint.config.js'],
    ...js.configs.recommended,
    languageOptions: { globals: { ...globals.node } },
    rules: {
      ...js.configs.recommended.rules,
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-cond-assign': ['error', 'except-parens'],
      'no-undef': 'off', // typescript-eslint handles TS; the .mjs files are checked by Node at run time
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', caughtErrors: 'none' }],
    },
  },
  {
    // The driver scripts run inside the page (page.evaluate); DOM globals there, no module scope.
    files: ['test/e2e/**/*.mjs', 'test/webkit/**/*.mjs'],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
  },
);
