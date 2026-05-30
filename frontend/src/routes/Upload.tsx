import { useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import {
  completeUpload,
  createRecording,
  uploadFileToR2,
} from '../lib/endpoints'
import type { ContentCategory } from '../lib/types'

const CATEGORIES: { value: ContentCategory; label: string }[] = [
  { value: 'sermon', label: '讲道' },
  { value: 'bible_reading', label: '圣经朗读' },
  { value: 'hymn', label: '赞美诗' },
]

// 建行 → 直传 R2 → complete 三步; 用 phase 驱动按钮与进度提示。
type Phase = 'idle' | 'creating' | 'uploading' | 'finalizing' | 'error'

const PHASE_LABEL: Record<Exclude<Phase, 'idle' | 'error'>, string> = {
  creating: '创建录音…',
  uploading: '上传中…',
  finalizing: '确认入库…',
}

export function Upload() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [category, setCategory] = useState<ContentCategory>('sermon')
  const [title, setTitle] = useState('')
  const [speaker, setSpeaker] = useState('')
  const [notes, setNotes] = useState('')
  const [removeMusic, setRemoveMusic] = useState(false)
  const [file, setFile] = useState<File | null>(null)

  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const busy = phase === 'creating' || phase === 'uploading' || phase === 'finalizing'

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!file || busy) return
    setError(null)
    try {
      setPhase('creating')
      const { recording, upload_url } = await createRecording({
        content_category: category,
        original_filename: file.name,
        mime_type: file.type || null,
        title: title.trim() || null,
        speaker: speaker.trim() || null,
        notes: notes.trim() || null,
        remove_music: removeMusic,
      })

      setPhase('uploading')
      setProgress(0)
      await uploadFileToR2(upload_url, file, setProgress)

      setPhase('finalizing')
      await completeUpload(recording.id)

      // 列表里能立刻看到这条新录音(随后它会自动轮询切分进度)
      await queryClient.invalidateQueries({ queryKey: ['recordings'] })
      navigate('/')
    } catch (err) {
      setPhase('error')
      setError((err as Error).message)
    }
  }

  return (
    <div>
      <h1>上传录音</h1>
      <form className="form-stack" onSubmit={handleSubmit}>
        <label>
          类别
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value as ContentCategory)}
            disabled={busy}
          >
            {CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </label>

        <label>
          标题(可选)
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="例如:2026 春季主日讲道"
            disabled={busy}
          />
        </label>

        <label>
          声音 / 说话人(训练按它分组,同一个声音请填一致)
          <input
            type="text"
            value={speaker}
            onChange={(e) => setSpeaker(e.target.value)}
            placeholder="例如:남성1 / 여성1 / 朗读者名字"
            disabled={busy}
          />
        </label>

        <label>
          备注(可选)
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            disabled={busy}
          />
        </label>

        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={removeMusic}
            onChange={(e) => setRemoveMusic(e.target.checked)}
            disabled={busy}
          />
          <span>消除背景音乐(有伴奏/音乐时勾选;切分前先分离人声,处理较慢)</span>
        </label>

        <label>
          音频文件
          <input
            type="file"
            accept="audio/*"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            disabled={busy}
          />
        </label>

        {phase === 'uploading' && (
          <div className="progress" aria-label="上传进度">
            <span style={{ width: `${progress}%` }} />
          </div>
        )}

        {busy && <p className="status-line">{PHASE_LABEL[phase]} {phase === 'uploading' ? `${progress}%` : ''}</p>}
        {error && <p className="error">{error}</p>}

        <button type="submit" disabled={!file || busy}>
          {busy ? '处理中…' : '上传'}
        </button>
      </form>
    </div>
  )
}
