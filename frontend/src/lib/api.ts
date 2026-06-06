// 轻量 fetch 封装。所有请求走同源 /api/*(dev 由 Vite 代理转发到后端)。
// token 存 localStorage(纯 SPA、access-only token、内部工具的权衡)。

const TOKEN_KEY = 'kotts_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

// 后端错误体通常是 {detail: "..."} 或 {error: "..."}(限速); 尽力取一条人话。
async function extractError(res: Response): Promise<string> {
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') return body.detail
    if (Array.isArray(body?.detail)) {
      // FastAPI 422 校验错误数组
      return body.detail.map((e: { msg?: string }) => e.msg).filter(Boolean).join('; ')
    }
    if (typeof body?.error === 'string') return body.error
  } catch {
    // 非 JSON 响应, 落到下面
  }
  return res.statusText || `HTTP ${res.status}`
}

interface RequestOptions {
  method?: string
  // JSON body(自动 stringify + 设 Content-Type)
  json?: unknown
  // 表单体(application/x-www-form-urlencoded), 用于 OAuth2 登录
  form?: Record<string, string>
  // multipart 表单体(文件上传), 用于范文 .docx 上传。
  // 注意: 不要手动设 Content-Type, 让浏览器带 boundary。
  formData?: FormData
  // 是否带 Authorization 头(默认带, 有 token 时)
  auth?: boolean
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {}
  let body: BodyInit | undefined

  if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.json)
  } else if (opts.form) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded'
    body = new URLSearchParams(opts.form).toString()
  } else if (opts.formData) {
    // 不设 Content-Type: 浏览器会自动带 multipart/form-data; boundary=...
    body = opts.formData
  }

  if (opts.auth !== false) {
    const token = getToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
  }

  const res = await fetch(`/api${path}`, {
    method: opts.method ?? (body ? 'POST' : 'GET'),
    headers,
    body,
  })

  if (!res.ok) {
    throw new ApiError(res.status, await extractError(res))
  }

  // 204 或空体
  if (res.status === 204) return undefined as T
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const api = { request }
