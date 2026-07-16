import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Redesign SPA (CLE-44). Builds straight into web/new/ — Netlify serves it
// at cleanreel.app/new/ with zero config change (built output is committed).
export default defineConfig({
  base: '/new/',
  plugins: [react()],
  build: { outDir: '../web/new', emptyOutDir: true, assetsDir: 'assets' },
})
