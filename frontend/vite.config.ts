import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  // No source maps; no inlined assets (fonts as data: URIs would need a looser CSP font-src).
  build: { sourcemap: false, assetsInlineLimit: 0 },
})
