import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// PORT / API_PORT let a second checkout run beside the main one (defaults 5710 / 8710).
const port = Number(process.env.PORT ?? 5710)
const api = `http://127.0.0.1:${process.env.API_PORT ?? 8710}`

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port,
    strictPort: true,
    allowedHosts: ['zeroday.localhost', 'main.tail865d08.ts.net', '.localhost'],
    proxy: { '/api': api },
  },
})
