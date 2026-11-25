import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vitest/config'

// `npm run build` writes dist/ with relative asset paths; the backend serves it (SEC-07).
export default defineConfig({
  plugins: [svelte()],
  base: './',
  build: { outDir: 'dist', emptyOutDir: true },
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
})
