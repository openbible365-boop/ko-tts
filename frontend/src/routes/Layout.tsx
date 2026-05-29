import { Link, NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export function Layout() {
  const { user, logout } = useAuth()

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          ko-tts
        </Link>
        <nav>
          <NavLink to="/" end>
            我的录音
          </NavLink>
          <NavLink to="/upload">上传</NavLink>
        </nav>
        <div className="spacer" />
        {user && (
          <div className="user">
            <span className="muted">
              {user.display_name || user.email}
              <span className="role">{user.role}</span>
            </span>
            <button className="link-btn" onClick={logout}>
              退出
            </button>
          </div>
        )}
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  )
}
