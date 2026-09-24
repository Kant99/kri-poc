import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the API runs on :8000; /api is proxied so no CORS setup is needed.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://localhost:8000' } },
  build: { outDir: 'dist', sourcemap: false },
});
