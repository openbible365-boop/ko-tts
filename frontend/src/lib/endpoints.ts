import { api, getToken } from './api'
import type {
  PresignedUrl,
  Recording,
  RecordingCreate,
  RecordingCreateResponse,
  RecordingProgress,
  Segment,
  SegmentStatus,
  Token,
  User,
  UserRole,
} from './types'

// --- auth ---

export function login(email: string, password: string): Promise<Token> {
  // OAuth2 password flow: form-encoded, 字段名是 username/password
  return api.request<Token>('/auth/login', {
    form: { username: email, password },
    auth: false,
  })
}

export function register(
  email: string,
  password: string,
  displayName?: string,
): Promise<User> {
  return api.request<User>('/auth/register', {
    json: { email, password, display_name: displayName || null },
    auth: false,
  })
}

export function getMe(): Promise<User> {
  return api.request<User>('/auth/me')
}

// --- admin: 用户管理 (仅 admin) ---

export function listUsers(): Promise<User[]> {
  return api.request<User[]>('/admin/users')
}

export function updateUser(
  id: string,
  data: { role?: UserRole; is_active?: boolean },
): Promise<User> {
  return api.request<User>(`/admin/users/${id}`, { method: 'PATCH', json: data })
}

// --- recordings ---

export function listRecordings(): Promise<Recording[]> {
  return api.request<Recording[]>('/recordings')
}

export function getRecording(id: string): Promise<Recording> {
  return api.request<Recording>(`/recordings/${id}`)
}

export function createRecording(
  data: RecordingCreate,
): Promise<RecordingCreateResponse> {
  return api.request<RecordingCreateResponse>('/recordings', { json: data })
}

export function completeUpload(id: string): Promise<Recording> {
  return api.request<Recording>(`/recordings/${id}/complete`, { method: 'POST' })
}

export function deleteRecording(id: string): Promise<void> {
  return api.request<void>(`/recordings/${id}`, { method: 'DELETE' })
}

export function getRecordingProgress(id: string): Promise<RecordingProgress> {
  return api.request<RecordingProgress>(`/recordings/${id}/progress`)
}

// 预签名 GET 原始录音(用于列表里直接播放)
export function getRecordingAudioUrl(id: string): Promise<PresignedUrl> {
  return api.request<PresignedUrl>(`/recordings/${id}/download-url`)
}

// 浏览器直传 R2 —— PUT 到预签名 URL(绝对地址, 不经 /api 代理)。
// 用 XHR 而非 fetch 是为了拿上传进度。预签名 URL 未签 Content-Type,
// 所以带上 file.type 不会破坏签名(只有 host 被签名)。
// 前置: R2 bucket 已配 CORS 放行本源(见 deploy/r2-cors.json), 否则会被拦。
export function uploadFileToR2(
  url: string,
  file: File,
  onProgress?: (pct: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url)
    if (file.type) xhr.setRequestHeader('Content-Type', file.type)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve()
      else reject(new Error(`上传到存储失败 (HTTP ${xhr.status})`))
    }
    xhr.onerror = () => reject(new Error('上传到存储失败(网络或 CORS 错误)'))
    xhr.send(file)
  })
}

// --- segments(校对 / 审核)---

export function listSegments(
  params: { status?: SegmentStatus; recordingId?: string } = {},
): Promise<Segment[]> {
  const q = new URLSearchParams()
  if (params.status) q.set('status', params.status)
  if (params.recordingId) q.set('recording_id', params.recordingId)
  const qs = q.toString()
  return api.request<Segment[]>(`/segments${qs ? `?${qs}` : ''}`)
}

export function getSegmentAudioUrl(id: string): Promise<PresignedUrl> {
  return api.request<PresignedUrl>(`/segments/${id}/download-url`)
}

export function correctSegment(id: string, text: string): Promise<Segment> {
  return api.request<Segment>(`/segments/${id}/correct`, { json: { text } })
}

export function approveSegment(id: string): Promise<Segment> {
  return api.request<Segment>(`/segments/${id}/approve`, { method: 'POST' })
}

export function rejectSegment(
  id: string,
  rejectionReason?: string,
): Promise<Segment> {
  return api.request<Segment>(`/segments/${id}/reject`, {
    json: { rejection_reason: rejectionReason ?? null },
  })
}

export function deleteSegment(id: string): Promise<void> {
  return api.request<void>(`/segments/${id}`, { method: 'DELETE' })
}

// 下载 GPT-SoVITS 数据集 zip(wavs/ + train.list)。带鉴权头, 故用 fetch+blob
// 自己触发下载, 不能用裸 <a href>。返回打包的段数。
export async function downloadDataset(
  opts: { status?: string; speaker?: string } = {},
): Promise<number> {
  const q = new URLSearchParams()
  if (opts.status) q.set('status', opts.status)
  if (opts.speaker) q.set('speaker', opts.speaker)
  const qs = q.toString()
  const token = getToken()
  const res = await fetch(`/api/export/dataset.zip${qs ? `?${qs}` : ''}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) throw new Error(`导出失败 (HTTP ${res.status})`)
  const count = Number(res.headers.get('X-Export-Segments') || '0')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'ko-tts-dataset.zip'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  return count
}
