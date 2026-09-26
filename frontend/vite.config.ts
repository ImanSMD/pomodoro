/// <reference types="vitest/config" />
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

function parsePolling(value: string | undefined): boolean {
  if (!value) return false
  // Exactly watchfiles' set — it does NOT treat '0' as off, so neither can we
  // or VITE_USE_POLLING=0 would disagree with WATCHFILES_FORCE_POLLING=0.
  return !['false', 'disable', 'disabled'].includes(value.toLowerCase())
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    host: true,
    port: 5173,
    watch: {
      // Off by default: file events do propagate through Docker Desktop's
      // VirtioFS bind mounts (verified — uvicorn --reload fires in ~1s under
      // the same mount). Older gRPC-FUSE setups and some Windows hosts don't,
      // so this is an opt-in escape hatch. WATCHFILES_FORCE_POLLING is the
      // backend's equivalent; turn both on together.
      // Parsed the way watchfiles parses WATCHFILES_FORCE_POLLING, so the
      // documented "set both or neither" pairing stays honest: a plain `!!`
      // would read VITE_USE_POLLING=false as ON while the backend read it OFF.
      usePolling: parsePolling(process.env.VITE_USE_POLLING),
    },
  },
  test: {
    environment: 'jsdom',
    // Tests must not depend on whatever API_PORT this machine uses.
    env: { VITE_API_URL: 'http://api.test' },
    restoreMocks: true,
    setupFiles: ['src/test/setup.ts'],
  },
})
