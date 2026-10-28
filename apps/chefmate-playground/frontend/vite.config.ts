import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/static/',
  build: {
    outDir: '../web',
    emptyOutDir: true,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: 'vendor-react', test: /node_modules[\\/](?:react|react-dom|scheduler)(?:[\\/]|$)/, priority: 20 },
            // Capture the parser dependency tree together; React stays in its higher-priority group.
            { name: 'vendor-markdown', test: /node_modules[\\/](?:react-markdown|remark-gfm)(?:[\\/]|$)/, priority: 10 },
          ],
        },
      },
    },
  },
  // Preserve the browser Host so the backend can validate its Origin normally.
  server: { proxy: { '/api': { target: 'http://127.0.0.1:18765', changeOrigin: false } } },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    restoreMocks: true,
    clearMocks: true,
    exclude: ['node_modules/**', 'e2e/**'],
  },
});
