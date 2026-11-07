import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5710,
    strictPort: true,
    allowedHosts: ['zeroday.localhost', 'main.tail865d08.ts.net', '.localhost'],
    proxy: { '/api': 'http://127.0.0.1:8710' },
  },
})
