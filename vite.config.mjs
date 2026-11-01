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
    rollupOptions: {
      output: {
        manualChunks(id) {
          // React and the libraries every screen draws with, apart from the app's own code: they change
          // only with a dependency bump, and the app's chunk stays under Vite's 500 kB warning, so the
          // warning means something again when our own code grows. Both load at start either way.
          if (/node_modules\/(react|react-dom|scheduler|zustand|lucide-react|marked|dompurify|diff)\//.test(id)) return 'vendor';
        },
      },
      // lucide-react ships "use client" directives; harmless here, so keep the build log clean.
      onwarn(warning, warn) {
        if (warning.code === 'MODULE_LEVEL_DIRECTIVE') return;
        warn(warning);
      },
    },
  },
  server: { port: 5199 },
});
