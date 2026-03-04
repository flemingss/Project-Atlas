import react from '@vitejs/plugin-react';
import path from 'path';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  base: '/app/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  // Dev server proxies API calls to the running Atlas backend
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:18080',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:18080',
        changeOrigin: true,
      },
      '/rag': {
        target: 'http://127.0.0.1:18080',
        changeOrigin: true,
      },
      '/admin': {
        target: 'http://127.0.0.1:18080',
        changeOrigin: true,
      },
    },
  },
  build: {
    // Output to static/app so FastAPI can serve it unchanged
    outDir: path.resolve(__dirname, '..', 'static', 'app'),
    emptyOutDir: true,
    sourcemap: true,
  },
});
