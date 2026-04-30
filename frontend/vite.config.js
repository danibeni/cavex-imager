import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    // Dev proxy: forwards /api/ and WebSocket to the backend running on :8000
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
      '/captures': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
