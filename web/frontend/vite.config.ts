import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 开发期代理：前端相对路径 /api → 后端(5000)，浏览器视角同源，绕开 CORS。
    // 生产由后端同源伺服 dist（server.py 已挂 /），无需代理。
    proxy: {
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true,
      },
    },
  },
})
