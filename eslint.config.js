// ESLint: typescript-eslint recommended + react-hooks for the interface; the core recommended rules for
// the server, the Electron shell and the tests. `any` is an error in the interface: it is fully typed
// (2.9.0) and must stay that way. The server is plain JavaScript, type-checked by tsc (tsconfig.server.json).
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
      // An effect's dependencies are complete, with no exception: a handler an effect must not re-run
      // for is a useEvent (components/ui/hooks.ts), so nothing reads a stale closure.
      'react-hooks/exhaustive-deps': 'error',
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' }],
      '@typescript-eslint/no-non-null-assertion': 'error',
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
      'no-unused-vars': 'off', // the typescript-eslint rule, which understands parameters in type signatures
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', caughtErrors: 'none' }],
    },
  },
  {
    // The server and the Electron shell: plain JavaScript on Node.
    files: ['server/**/*.js', 'electron/**/*.cjs'],
    ...js.configs.recommended,
    languageOptions: { globals: { ...globals.node } },
    rules: {
      ...js.configs.recommended.rules,
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-cond-assign': ['error', 'except-parens'],
      'no-unused-vars': 'off', // the typescript-eslint rule below, with the `_` convention
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' }],
      // `ok ? pass() : fail()` reads as one line of a report (doctor); a bare sequence or value does not.
      '@typescript-eslint/no-unused-expressions': ['error', { allowTernary: true, allowShortCircuit: true }],
    },
  },
  {
    files: ['electron/**/*.cjs'],
    languageOptions: { sourceType: 'commonjs' },
    rules: { '@typescript-eslint/no-require-imports': 'off' },
  },
  {
    // The driver scripts run inside the page (page.evaluate); DOM globals there, no module scope.
    files: ['test/e2e/**/*.mjs', 'test/webkit/**/*.mjs'],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
  },
);
