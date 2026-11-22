import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vitest/config'

// `npm run build` writes dist/ with relative asset paths; the backend serves it from WP-0.7
// (SEC-07). Until then `make e2e` serves it with `vite preview`.
export default defineConfig({
  plugins: [svelte()],
  base: './',
  build: { outDir: 'dist', emptyOutDir: true },
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
})
