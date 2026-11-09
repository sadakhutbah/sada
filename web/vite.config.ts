import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Le front est servi par le back-office en production ; en développement, Vite relaie vers lui.
const cible = process.env.MINBAR_API ?? 'http://127.0.0.1:8774'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Le front est monté sous /app le temps du passage ; base relative à ce préfixe.
  base: '/app/',
  server: {
    port: 5173,
    proxy: { '/api': cible, '/logo': cible, '/static': cible },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
