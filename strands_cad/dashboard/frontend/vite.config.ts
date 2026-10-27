import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: `npm run dev` proxies the API to a local `strands-cad-dashboard` on :8099. Build: dist/ is COMMITTED (Thor has no
// node; the PyPI wheel ships it) and served by strands_cad/dashboard/server.py — `/assets/*` is public, everything else gated.
export default defineConfig({
  plugins: [react()],
  server: { port: 4199, proxy: { '/api': { target: 'http://localhost:8099' }, '/auth': { target: 'http://localhost:8099' } } },
  build: { outDir: 'dist', sourcemap: false, emptyOutDir: true },
})
