import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { ApiError } from '../lib/api'
import { register as apiRegister } from '../lib/endpoints'

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
    <div className="centered">
      <form className="card" onSubmit={onSubmit}>
        <h1>注册</h1>
        <label>
          邮箱
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
          />
        </label>
        <label>
          昵称(可选)
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            autoComplete="name"
          />
        </label>
        <label>
          密码(至少 8 位)
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            autoComplete="new-password"
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? '注册中…' : '注册并登录'}
        </button>
        <p className="muted">
          已有账号? <Link to="/login">登录</Link>
        </p>
      </form>
    </div>
  )
}
