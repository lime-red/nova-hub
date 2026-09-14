import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

import pkg from './package.json' with { type: 'json' }

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  define: {
    // The login page shows a version but cannot ask the API for one -- it is
    // the page you are on because you are not authenticated yet. Baking it in
    // at build time beats the hardcoded string that used to live there and
    // went stale the moment the backend moved. tests/test_version.py asserts
    // this stays in step with backend/core/version.py.
    __APP_VERSION__: JSON.stringify(pkg.version)
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 5173,
    proxy: {
      // Proxy management API requests to FastAPI backend
      '/management/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      // Proxy WebSocket connections
      '/management/api/v1/ws': {
        target: 'ws://localhost:8000',
        ws: true
      }
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false
  }
})
