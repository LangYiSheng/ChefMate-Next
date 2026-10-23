import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/static/',
  build: { outDir: '../web', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:18765' } },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    restoreMocks: true,
    clearMocks: true,
    exclude: ['node_modules/**', 'e2e/**'],
  },
});
