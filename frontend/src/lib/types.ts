// 与后端 app/schemas.py 对齐的类型。改后端 schema 时记得同步这里。

export type UserRole = 'admin' | 'reviewer' | 'contributor'

export type ContentCategory = 'sermon' | 'bible_reading' | 'hymn'

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
  title: string | null
  notes: string | null
  status: RecordingStatus
  created_at: string
  updated_at: string
}

export interface RecordingCreate {
  content_category: ContentCategory
  original_filename: string
  mime_type?: string | null
  title?: string | null
  notes?: string | null
}

export interface RecordingCreateResponse {
  recording: Recording
  upload_url: string
  upload_expires_in: number
}
