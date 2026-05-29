import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import { clearToken, getToken, setToken } from '../lib/api'
import { getMe, login as apiLogin } from '../lib/endpoints'
import type { User } from '../lib/types'

interface AuthState {
  user: User | null
  // 启动时仍在校验已存 token 的过程中
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  // 无存 token 时无需校验, loading 直接 false(避免在 effect 里同步 setState)
  const [loading, setLoading] = useState(() => getToken() !== null)

  // 启动时若有存的 token, 用 /auth/me 验活(失效则清掉)
  useEffect(() => {
    if (!getToken()) return
    let cancelled = false
    getMe()
      .then((u) => {
        if (!cancelled) setUser(u)
      })
      .catch(() => clearToken())
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const token = await apiLogin(email, password)
    setToken(token.access_token)
    const me = await getMe()
    setUser(me)
  }, [])

  const logout = useCallback(() => {
    clearToken()
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
