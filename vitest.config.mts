import { defineConfig } from 'vitest/config';

// Unit tests cover the pure logic in web/src/lib (transcript model, turn grouping, tool summaries,
// formatting, partial JSON…). They run in Node; nothing here touches the DOM.
export default defineConfig({
  test: {
    include: ['test/unit/**/*.test.ts'],
    environment: 'node',
    globals: false,
  },
  define: { __APP_VERSION__: JSON.stringify('0.0.0-test') },
});
