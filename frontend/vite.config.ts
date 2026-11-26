import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vitest/config'

// `npm run build` writes dist/ with relative asset paths; the backend serves it (SEC-07).
//
// `S3T_BACKEND=http://<listen address>:<port> npm run dev` serves the app from Vite and sends /api,
// the WebSocket with it, to that backend. The dev server is another origin, which the backend
// refuses (SEC-02), so the proxy states the backend's own origin. No address is written here.
const backend = process.env.S3T_BACKEND
const origin = backend ? new URL(backend).origin : undefined

export default defineConfig({
  plugins: [svelte()],
  base: './',
  build: { outDir: 'dist', emptyOutDir: true },
  server: origin
    ? {
        proxy: {
          '/api': {
            target: origin,
            ws: true,
            changeOrigin: true,
            configure: (proxy) => {
              proxy.on('proxyReq', (request) => request.setHeader('origin', origin))
              proxy.on('proxyReqWs', (request) => request.setHeader('origin', origin))
            },
          },
        },
      }
    : undefined,
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
})
