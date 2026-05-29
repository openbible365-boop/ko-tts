import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from './AuthContext'

// 守卫: 未登录跳 /login(记下来源, 登录后回跳); 校验中先占位。
export function ProtectedRoute() {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) return <div className="centered muted">加载中…</div>
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />
  return <Outlet />
}
