import path from "node:path"

import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// 开发期把 /api 代理到后端：这样浏览器里只有**一个源**，
// 会话 Cookie 不需要 SameSite=None、SSE 也不涉及跨域预检。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // 用 import.meta.dirname（vite 未来默认的 native config loader 不支持 __dirname）
    alias: { "@": path.resolve(import.meta.dirname, "./src") },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
})
