import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export function Layout() {
  const { user, logout } = useAuth()
  const name = user?.display_name || user?.email || ''
  const initial = name.trim().charAt(0).toUpperCase() || '?'

  return (
    <div className="app">
      <header className="appnav">
        <div className="brand-mark">
          <div className="logo">OB</div>
          <div className="brand-name">音频采样</div>
        </div>
        <nav className="menu">
          <NavLink to="/" end>
            我的采集
          </NavLink>
          <NavLink to="/upload">上传</NavLink>
          {user && <NavLink to="/review">校对</NavLink>}
          {user?.role === 'admin' && <NavLink to="/users">用户管理</NavLink>}
        </nav>
        {user && (
          <div className="nav-right">
            <div className="user">
              <div className="avatar">{initial}</div>
              <span className="uname">{name}</span>
              <span className="role">{user.role}</span>
            </div>
            <button className="logout" onClick={logout}>
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.9"
              >
                <path d="M15 17l5-5-5-5" />
                <path d="M20 12H9" />
                <path d="M9 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3" />
              </svg>
              退出
            </button>
          </div>
        )}
      </header>
      <main className="appmain">
        <Outlet />
      </main>
    </div>
  )
}
