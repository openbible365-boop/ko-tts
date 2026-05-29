import { api } from './api'
import type {
  PresignedUrl,
  Recording,
  RecordingCreate,
  RecordingCreateResponse,
  Segment,
  SegmentStatus,
  Token,
  User,
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

export function rejectSegment(id: string, rejectionReason: string): Promise<Segment> {
  return api.request<Segment>(`/segments/${id}/reject`, {
    json: { rejection_reason: rejectionReason },
  })
}
