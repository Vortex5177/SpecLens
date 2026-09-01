import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite 配置：React 插件 + 开发环境代理
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 开发环境下 /api 请求转发到后端，避免浏览器跨域
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
