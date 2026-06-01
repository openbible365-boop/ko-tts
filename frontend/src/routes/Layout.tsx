import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

// Ezra Studio 竖版锁定 · Reverse(反白): 白色书形+字标, 金色 Z 与 AI 光点。
// 取自品牌设计稿(SYM + 竖版 wordmark), v-dark 配色用于深色导航栏。
function EzraMarkReverse() {
  const gold = '#e89a2c'
  return (
    <svg
      className="ezra-mark"
      viewBox="0 0 120 124"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="Ezra"
      style={{ color: '#ffffff' }}
    >
      <polygon points="60,13 62,21 70,23 62,25 60,33 58,25 50,23 58,21" fill={gold} />
      <polygon
        points="43,24.5 44.1,28.9 48.5,30 44.1,31.1 43,35.5 41.9,31.1 37.5,30 41.9,28.9"
        fill="currentColor"
      />
      <polygon
        points="77,22 78.2,26.8 83,28 78.2,29.2 77,34 75.8,29.2 71,28 75.8,26.8"
        fill="currentColor"
      />
      <path
        d="M60 44 C 50 38, 34 38, 26 44 L 26 74 C 34 68, 50 68, 60 74 Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="5"
        strokeLinejoin="round"
      />
      <path
        d="M60 44 C 70 38, 86 38, 94 44 L 94 74 C 86 68, 70 68, 60 74 Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="5"
        strokeLinejoin="round"
      />
      <line x1="60" y1="44" x2="60" y2="74" stroke="currentColor" strokeWidth="4" />
      <text
        x="60"
        y="103"
        textAnchor="middle"
        fontFamily="Sora, sans-serif"
        fontWeight="800"
        fontSize="27"
        letterSpacing="2"
      >
        <tspan fill="currentColor">E</tspan>
        <tspan fill={gold}>Z</tspan>
        <tspan fill="currentColor">RA</tspan>
      </text>
    </svg>
  )
}

export function Layout() {
  const { user, logout } = useAuth()
  const name = user?.display_name || user?.email || ''
  const initial = name.trim().charAt(0).toUpperCase() || '?'

  return (
    <div className="app">
      <header className="appnav">
        <div className="brand-mark">
          <EzraMarkReverse />
          <div className="brand-name">
            音频<span className="hl">采样</span>
          </div>
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
