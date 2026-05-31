// 与后端 app/schemas.py 对齐的类型。改后端 schema 时记得同步这里。

export type UserRole = 'admin' | 'reviewer' | 'contributor'

export type ContentCategory = 'sermon' | 'bible_reading' | 'hymn'

// 转写语种(基础语言码), 须与后端 app/models.py 的 Language 一致。
export type Language = 'en' | 'zh' | 'ko'

export type RecordingStatus =
  | 'pending_upload'
  | 'uploaded'
  | 'segmenting'
  | 'segmented'
  | 'failed'

export interface User {
  id: string
  email: string
  role: UserRole
  display_name: string | null
  is_active: boolean
  created_at: string
}

export interface Token {
  access_token: string
  token_type: string
}

export interface Recording {
  id: string
  uploaded_by: string
  audio_key: string
  original_filename: string | null
  mime_type: string | null
  file_size_bytes: number | null
  duration_ms: number | null
  sample_rate: number | null
  channels: number | null
  codec: string | null
  content_category: ContentCategory
  language: Language
  title: string | null
  notes: string | null
  remove_music: boolean
  speaker: string | null
  status: RecordingStatus
  created_at: string
  updated_at: string
  // 仅 staff 看「全部采集」时后端填充, contributor 看自己的为 null
  uploader_email: string | null
  uploader_name: string | null
}

export interface RecordingCreate {
  content_category: ContentCategory
  language: Language
  original_filename: string
  mime_type?: string | null
  title?: string | null
  notes?: string | null
  remove_music?: boolean
  speaker?: string | null
}

export interface RecordingProgress {
  status: RecordingStatus
  remove_music: boolean
  segment_total: number
  transcribed: number
  in_transcription: number
  phase_elapsed_sec: number
}

export interface RecordingCreateResponse {
  recording: Recording
  upload_url: string
  upload_expires_in: number
}

export type SegmentStatus =
  | 'pending_transcription'
  | 'transcribing'
  | 'transcription_failed'
  | 'pending_correction'
  | 'pending_review'
  | 'approved'
  | 'rejected'

export interface Segment {
  id: string
  recording_id: string
  segment_index: number
  start_ms: number
  end_ms: number
  duration_ms: number
  audio_key: string | null
  asr_text: string | null
  text: string | null
  status: SegmentStatus
  corrected_by: string | null
  corrected_at: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  rejection_reason: string | null
  created_at: string
  updated_at: string
}

export interface PresignedUrl {
  url: string
  expires_in: number
}
