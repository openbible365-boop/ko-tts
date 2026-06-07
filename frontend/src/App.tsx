import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth/AuthContext'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { Layout } from './routes/Layout'
import { Login } from './routes/Login'
import { Record } from './routes/Record'
import { Recordings } from './routes/Recordings'
import { Register } from './routes/Register'
import { Review } from './routes/Review'
import { ScriptDetail } from './routes/ScriptDetail'
import { Scripts } from './routes/Scripts'
import { Upload } from './routes/Upload'
import { Users } from './routes/Users'

// 用户管理仅限 admin; 其它角色撞到这条路由退回首页(后端 /admin/users 也是 admin-only)。
// 此处 user 必非空(已被 ProtectedRoute 拦过)。
function RequireAdmin() {
  const { user } = useAuth()
  if (user && user.role !== 'admin') return <Navigate to="/" replace />
  return <Outlet />
}

// 校对页对所有登录用户开放: contributor 只能校对自己录音的切片(后端按
// uploaded_by 收口), reviewer/admin 还能审核 + 导出(页内按角色显隐)。
function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<Layout />}>
          <Route path="/" element={<Recordings />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/record" element={<Record />} />
          <Route path="/review" element={<Review />} />
          <Route element={<RequireAdmin />}>
            <Route path="/users" element={<Users />} />
            <Route path="/scripts" element={<Scripts />} />
            <Route path="/scripts/:id" element={<ScriptDetail />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
