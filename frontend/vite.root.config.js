import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// ROOT build (the swap-over): same app served at cleanreel.app/ with SEO meta.
export default defineConfig({
  base: '/',
  plugins: [react()],
  build: { outDir: 'dist-root', emptyOutDir: true, assetsDir: 'assets' },
})
