import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { ApiError } from '../lib/api'
import { AuthBrand } from './AuthBrand'

export function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: Location })?.from?.pathname || '/'

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await login(email, password)
      navigate(from, { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError('登录尝试过于频繁,请稍后再试')
      } else {
        setError(err instanceof Error ? err.message : '登录失败')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth">
      <div className="auth-shell">
        <AuthBrand />

        {/* 右: 登录表单 */}
        <section className="auth-form-side">
          <div className="auth-form-head">
            <h1>登录</h1>
            <div className="auth-sub">欢迎回来，请登录以继续您的采样工作。</div>
          </div>

          <form className="auth-form" onSubmit={onSubmit}>
            <div className="auth-field">
              <label htmlFor="auth-email">邮箱</label>
              <div className="auth-input-wrap">
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                >
                  <rect x="3" y="5" width="18" height="14" rx="2.5" />
                  <path d="m3.5 7 8.5 6 8.5-6" />
                </svg>
                <input
                  id="auth-email"
                  type="email"
                  placeholder="name@company.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                />
              </div>
            </div>

            <div className="auth-field">
              <label htmlFor="auth-password">密码</label>
              <div className="auth-input-wrap">
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                >
                  <rect x="4" y="10" width="16" height="10" rx="2.5" />
                  <path d="M8 10V7a4 4 0 0 1 8 0v3" />
                </svg>
                <input
                  id="auth-password"
                  type="password"
                  placeholder="请输入密码"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                />
              </div>
            </div>

            <div className="auth-row">
              <label className="auth-remember">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                />
                记住我
              </label>
            </div>

            {error && <p className="auth-error">{error}</p>}

            <button className="auth-btn" type="submit" disabled={busy}>
              {busy ? '登 录 中…' : '登 录'}
            </button>

            <div className="auth-signup">
              还没有账号？ <Link to="/register">立即注册</Link>
            </div>
          </form>
        </section>
      </div>
    </div>
  )
}
