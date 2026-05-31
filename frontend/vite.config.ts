import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// dev 时浏览器同源访问 /api/*, 由 Vite 代理转发到默认生产域名。
// 不剥 /api 前缀 —— 目标是生产 Caddy, 它自己用 handle_path /api/* 剥掉前缀
// 再转给后端(后端路由是 /auth、/recordings…, 无 /api)。在此剥会变成剥两次,
// 路径掉进 Caddy 静态 SPA(只允许 GET/HEAD), 导致 POST 返回 405。
// 这样浏览器视角始终同源, 不触发 CORS(R2 直传 PUT 走绝对 URL, 不经此代理)。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_PROXY_TARGET || 'https://kr-tts.openbible.live'

  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          secure: true,
        },
      },
    },
  }
})
