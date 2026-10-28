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
    chunkSizeWarningLimit: 6000,
    reportCompressedSize: false,
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
