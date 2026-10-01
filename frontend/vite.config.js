import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // фронт ходит на относительные пути (/stations, /ws/metrics...):
    // без прокси запросы уходят на сам dev-сервер и возвращают index.html
    proxy: {
      "/stations": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
})
