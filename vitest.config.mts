import { defineConfig } from 'vitest/config';

// Unit tests run in Node, nothing here touches the DOM: the pure logic in web/src/lib (transcript
// model, turn grouping, tool summaries, formatting, partial JSON…) and the server (server/*.js),
// each test file in a temporary HOME of its own (setup.ts).
export default defineConfig({
  test: {
    include: ['test/unit/**/*.test.ts'],
    setupFiles: ['test/unit/setup.ts'],
    environment: 'node',
    globals: false,
  },
  define: { __APP_VERSION__: JSON.stringify('0.0.0-test') },
});
