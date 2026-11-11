import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Le front est servi par le back-office en production ; en développement, Vite relaie vers lui.
const cible = process.env.SADA_API ?? 'http://127.0.0.1:8774'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Le front est servi à la racine du back-office : ses fichiers sont donc à la racine des URL.
  base: '/',
  server: {
    port: 5173,
    proxy: { '/api': cible, '/logo': cible, '/media': cible, '/static': cible },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
