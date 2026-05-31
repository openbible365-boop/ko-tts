import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { ApiError } from '../lib/api'
import { register as apiRegister } from '../lib/endpoints'
import { AuthBrand } from './AuthBrand'

export function Register() {
  const { login } = useAuth()
  const navigate = useNavigate()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (password.length < 8) {
      setError('密码至少 8 位')
      return
    }
    setBusy(true)
    try {
      await apiRegister(email, password, displayName || undefined)
      // 注册成功后自动登录, 直接进主页
      await login(email, password)
      navigate('/', { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError('该邮箱已注册')
      } else if (err instanceof ApiError && err.status === 429) {
        setError('注册过于频繁,请稍后再试')
      } else {
        setError(err instanceof Error ? err.message : '注册失败')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth">
      <div className="auth-shell">
        <AuthBrand />

        {/* 右: 注册表单 */}
        <section className="auth-form-side">
          <div className="auth-form-head">
            <h1>注册</h1>
            <div className="auth-sub">创建账号，开始您的音频采样工作。</div>
          </div>

          <form className="auth-form" onSubmit={onSubmit}>
            <div className="auth-field">
              <label htmlFor="reg-email">邮箱</label>
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
                  id="reg-email"
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
              <label htmlFor="reg-name">昵称（可选）</label>
              <div className="auth-input-wrap">
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                >
                  <circle cx="12" cy="8" r="3.5" />
                  <path d="M5 20a7 7 0 0 1 14 0" />
                </svg>
                <input
                  id="reg-name"
                  type="text"
                  placeholder="如何称呼您"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  autoComplete="name"
                />
              </div>
            </div>

            <div className="auth-field">
              <label htmlFor="reg-password">密码（至少 8 位）</label>
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
                  id="reg-password"
                  type="password"
                  placeholder="请设置登录密码"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={8}
                  autoComplete="new-password"
                />
              </div>
            </div>

            {error && <p className="auth-error">{error}</p>}

            <button className="auth-btn" type="submit" disabled={busy}>
              {busy ? '注 册 中…' : '注 册 并 登 录'}
            </button>

            <div className="auth-signup">
              已有账号？ <Link to="/login">返回登录</Link>
            </div>
          </form>
        </section>
      </div>
    </div>
  )
}
