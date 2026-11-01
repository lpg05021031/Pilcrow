import { readFileSync } from 'node:fs';
import { defineConfig } from 'vite';

const { version } = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'));

// No framework plugins on purpose: fewer packages to fetch on restricted networks.
export default defineConfig({
  root: 'web',
  base: './',
  define: { __APP_VERSION__: JSON.stringify(version) },
  esbuild: { jsx: 'automatic' },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
    // The app is served from localhost, so what counts is the JavaScript parsed at start: 900 kB is that
    // measured size (one chunk) plus headroom, and `npm run test:perf` guards the real figure.
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      // lucide-react ships "use client" directives; harmless here, so keep the build log clean.
      onwarn(warning, warn) {
        if (warning.code === 'MODULE_LEVEL_DIRECTIVE') return;
        warn(warning);
      },
    },
  },
  server: { port: 5199 },
});
