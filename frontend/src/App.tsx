import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth/AuthContext'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { Layout } from './routes/Layout'
import { Login } from './routes/Login'
import { Recordings } from './routes/Recordings'
import { Register } from './routes/Register'
import { Review } from './routes/Review'
import { Upload } from './routes/Upload'

// 校对/审核仅限 reviewer / admin; contributor 撞到这条路由就退回首页。
// 此处 user 必非空(已被 ProtectedRoute 拦过)。
function RequireStaff() {
  const { user } = useAuth()
  if (user && user.role !== 'admin' && user.role !== 'reviewer') {
    return <Navigate to="/" replace />
  }
  return <Outlet />
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<Layout />}>
          <Route path="/" element={<Recordings />} />
          <Route path="/upload" element={<Upload />} />
          <Route element={<RequireStaff />}>
            <Route path="/review" element={<Review />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
