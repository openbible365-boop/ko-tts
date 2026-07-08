import { api, getToken } from './api'
import type {
  ContentCategory,
  Language,
  PresignedUrl,
  Recording,
  RecordingCreate,
  RecordingCreateResponse,
  RecordingProgress,
  Script,
  ScriptDetail,
  ScriptStatus,
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

// --- 范文管理 (仅 admin) ---

export function listScripts(status?: ScriptStatus): Promise<Script[]> {
  const qs = status ? `?status=${status}` : ''
  return api.request<Script[]>(`/scripts${qs}`)
}

export function getScript(id: string): Promise<ScriptDetail> {
  return api.request<ScriptDetail>(`/scripts/${id}`)
}

export function uploadScript(data: {
  file: File
  title: string
  language: Language
  content_category: ContentCategory
  notes?: string
}): Promise<ScriptDetail> {
  const fd = new FormData()
  fd.append('file', data.file)
  fd.append('title', data.title)
  fd.append('language', data.language)
  fd.append('content_category', data.content_category)
  if (data.notes?.trim()) fd.append('notes', data.notes.trim())
  return api.request<ScriptDetail>('/scripts', { formData: fd })
}

export function updateScript(
  id: string,
  data: {
    title?: string
    language?: Language
    content_category?: ContentCategory
    notes?: string | null
    status?: ScriptStatus
  },
): Promise<ScriptDetail> {
  return api.request<ScriptDetail>(`/scripts/${id}`, { method: 'PATCH', json: data })
}

export function saveScriptLines(id: string, lines: string[]): Promise<ScriptDetail> {
  return api.request<ScriptDetail>(`/scripts/${id}/lines`, {
    method: 'PUT',
    json: { lines: lines.map((text) => ({ text })) },
  })
}

export function deleteScript(id: string): Promise<void> {
  return api.request<void>(`/scripts/${id}`, { method: 'DELETE' })
}

// --- 录音 (基于定稿范文逐行录音) ---

export function listRecordableScripts(): Promise<Script[]> {
  return api.request<Script[]>('/scripts/recordable')
}

// 取或建当前用户对该范文的录音样品(每人一份, 可续录)
export function startRecordingFromScript(scriptId: string): Promise<Recording> {
  return api.request<Recording>(`/recordings/from-script/${scriptId}`, { method: 'POST' })
}

// 设置/修改录音样品声音昵称(录音前必填)
export function setRecordingSpeaker(recId: string, speaker: string): Promise<Recording> {
  return api.request<Recording>(`/recordings/${recId}/speaker`, { json: { speaker } })
}

export function getLineRecordUrl(segmentId: string): Promise<PresignedUrl> {
  return api.request<PresignedUrl>(`/segments/${segmentId}/record-url`, { method: 'POST' })
}

export function completeLineRecording(
  segmentId: string,
  durationMs?: number,
): Promise<Segment> {
  return api.request<Segment>(`/segments/${segmentId}/record-complete`, {
    json: { duration_ms: durationMs ?? null },
  })
}

export function passLine(segmentId: string): Promise<Segment> {
  return api.request<Segment>(`/segments/${segmentId}/pass`, { method: 'POST' })
}

export function rerecordLine(segmentId: string): Promise<Segment> {
  return api.request<Segment>(`/segments/${segmentId}/rerecord`, { method: 'POST' })
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

// 手动「开始切分」: 把就绪录音置为 pending_segmentation, worker 领取后切分
export function startSegmentation(id: string): Promise<Recording> {
  return api.request<Recording>(`/recordings/${id}/segment`, { method: 'POST' })
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

// 切片波形峰值(0..1)+时长, 供前端在波形上裁剪(服务端算, 同源免 CORS)
export function getSegmentWaveform(
  id: string,
  buckets = 240,
): Promise<{ peaks: number[]; duration_ms: number }> {
  return api.request(`/segments/${id}/waveform?buckets=${buckets}`)
}

// 裁剪切片: 只保留 [startMs, endMs](相对切片自身), 后端重切存回 R2
export function trimSegment(id: string, startMs: number, endMs: number): Promise<Segment> {
  return api.request<Segment>(`/segments/${id}/trim`, {
    json: { start_ms: Math.round(startMs), end_ms: Math.round(endMs) },
  })
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

// 一键微调: 把某说话人的"已通过"切片发到 GPU 训练。返回 GPU 任务 id。
export interface TrainStartResp {
  job_id: string
  exp: string
  segments: number
}
export async function startTraining(
  speaker: string,
  opts: { count?: number; sovits_ep?: number; gpt_ep?: number; batch?: number } = {},
): Promise<TrainStartResp> {
  const q = new URLSearchParams({ speaker })
  if (opts.count) q.set('count', String(opts.count))
  if (opts.sovits_ep) q.set('sovits_ep', String(opts.sovits_ep))
  if (opts.gpt_ep) q.set('gpt_ep', String(opts.gpt_ep))
  if (opts.batch) q.set('batch', String(opts.batch))
  return api.request<TrainStartResp>(`/export/train?${q.toString()}`, { method: 'POST' })
}

export interface TrainStatus {
  job_id: string
  exp: string
  status: string
  segments?: number | null
  message?: string | null
  weights?: { sovits: string[]; gpt: string[] } | null
  log_tail?: string
}
export async function getTrainStatus(jobId: string): Promise<TrainStatus> {
  return api.request<TrainStatus>(`/export/train/${jobId}`)
}

// 训练好的音色: 列出 GPU 上的权重(代理), 删除某个音色
export function listVoices(): Promise<{ sovits: string[]; gpt: string[] }> {
  return api.request(`/export/voices`)
}
export function deleteVoiceModel(exp: string): Promise<{ status?: string }> {
  return api.request(`/export/voices/${encodeURIComponent(exp)}`, { method: 'DELETE' })
}

// 用训练好的音色合成一段文本(代理 GPU); 免上传参考, 自动取训练样本作参考
export function synthVoice(
  text: string, sovitsWeights: string, gptWeights: string, language: string,
): Promise<{ task_id: string }> {
  return api.request(`/export/tts`, {
    json: { text, sovits_weights: sovitsWeights, gpt_weights: gptWeights, language },
  })
}
export interface VoiceTTSStatus {
  task_id: string
  status: string
  audio_url?: string | null
  message?: string | null
}
export function getVoiceTTSStatus(taskId: string): Promise<VoiceTTSStatus> {
  return api.request<VoiceTTSStatus>(`/export/tts/${taskId}`)
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
