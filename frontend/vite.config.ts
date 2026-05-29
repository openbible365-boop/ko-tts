import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// dev 时浏览器同源访问 /api/*, 由 Vite 代理转发到后端(默认生产 API),
// 并剥掉 /api 前缀 —— 后端路由是 /auth、/recordings 等, 没有 /api 前缀。
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
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  }
})
