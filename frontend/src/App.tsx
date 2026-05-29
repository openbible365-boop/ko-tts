import { Navigate, Route, Routes } from 'react-router-dom'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { Layout } from './routes/Layout'
import { Login } from './routes/Login'
import { Recordings } from './routes/Recordings'
import { Register } from './routes/Register'
import { Upload } from './routes/Upload'

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<Layout />}>
          <Route path="/" element={<Recordings />} />
          <Route path="/upload" element={<Upload />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
