import { useState } from 'react'
import type { DragEvent, FormEvent } from 'react'
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

function fmtSize(b: number): string {
  return b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${(b / 1024).toFixed(0)} KB`
}

const WAVE_BARS = [6, 12, 9, 15, 8]

export function Upload() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [category, setCategory] = useState<ContentCategory>('sermon')
  const [title, setTitle] = useState('')
  const [speaker, setSpeaker] = useState('')
  const [notes, setNotes] = useState('')
  const [removeMusic, setRemoveMusic] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [dragOver, setDragOver] = useState(false)

  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const busy = phase === 'creating' || phase === 'uploading' || phase === 'finalizing'

  function reset() {
    if (busy) return
    setCategory('sermon')
    setTitle('')
    setSpeaker('')
    setNotes('')
    setRemoveMusic(false)
    setFile(null)
    setError(null)
    setPhase('idle')
    setProgress(0)
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragOver(false)
    if (busy) return
    const f = e.dataTransfer.files?.[0]
    if (f) setFile(f)
  }

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
    <div className="upload">
      <div className="upload-head">
        <h1>
          <span className="bar" />
          上传音频
        </h1>
        <div className="sub">添加新的音频样本，填写信息后提交切分</div>
      </div>

      <form className="upload-card" onSubmit={handleSubmit}>
        {/* 类别: 药丸单选 */}
        <div className="field full">
          <label>类别</label>
          <div className="pills">
            {CATEGORIES.map((c) => (
              <button
                key={c.value}
                type="button"
                className={`pill ${category === c.value ? 'on' : ''}`}
                disabled={busy}
                onClick={() => setCategory(c.value)}
              >
                {c.label}
              </button>
            ))}
          </div>
        </div>

        <div className="grid">
          <div className="field">
            <label>
              标题 <span className="opt">(可选)</span>
            </label>
            <input
              className="control"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例如:2026 春季主日讲道"
              disabled={busy}
            />
          </div>
          <div className="field">
            <label>声音 / 说话人</label>
            <input
              className="control"
              type="text"
              value={speaker}
              onChange={(e) => setSpeaker(e.target.value)}
              placeholder="例如:남성1 / 여성1 / 朗读者名字"
              disabled={busy}
            />
            <div className="hint">训练按它分组,同一个声音请填一致</div>
          </div>
        </div>

        <div className="field full">
          <label>
            备注 <span className="opt">(可选)</span>
          </label>
          <textarea
            className="control"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="补充说明,例如录制环境、章节范围等"
            disabled={busy}
          />
        </div>

        {/* 消除背景音乐: 开关 */}
        <div className="toggle-row">
          <label className="switch">
            <input
              type="checkbox"
              checked={removeMusic}
              onChange={(e) => setRemoveMusic(e.target.checked)}
              disabled={busy}
            />
            <span className="slider" />
          </label>
          <div>
            <div className="tt">消除背景音乐</div>
            <div className="td">
              有伴奏 / 音乐时勾选；切分前先分离人声，处理较慢
            </div>
          </div>
        </div>

        {/* 音频文件: 拖拽区 */}
        <div className="field full">
          <label>音频文件</label>
          <label
            className={`dropzone ${file ? 'has-file' : ''} ${dragOver ? 'drag' : ''}`}
            onDragOver={(e) => {
              e.preventDefault()
              if (!busy) setDragOver(true)
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
          >
            <input
              type="file"
              accept="audio/*"
              hidden
              disabled={busy}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <div className="dz-ico">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M12 16V4" />
                <path d="m7 9 5-5 5 5" />
                <path d="M5 20h14" />
              </svg>
            </div>
            <div className="dz-title">
              <b>点击选择</b> 或拖拽音频文件到此处
            </div>
            <div className="dz-sub">支持 WAV / MP3 / M4A · 单文件最大 500 MB</div>
            {file && (
              <div className="dz-file">
                <div className="chip">
                  <div className="wf">
                    {WAVE_BARS.map((h, i) => (
                      <i key={i} style={{ height: `${h}px` }} />
                    ))}
                  </div>
                  <span>{file.name}</span>
                  <span style={{ color: '#8b93a7' }}>· {fmtSize(file.size)}</span>
                </div>
              </div>
            )}
          </label>
        </div>

        {phase === 'uploading' && (
          <div className="up-progress" aria-label="上传进度">
            <span style={{ width: `${progress}%` }} />
          </div>
        )}
        {busy && (
          <p className="up-status">
            {PHASE_LABEL[phase]} {phase === 'uploading' ? `${progress}%` : ''}
          </p>
        )}
        {error && <p className="up-error">{error}</p>}

        <div className="form-actions">
          <button
            className="btn btn-ghost"
            type="button"
            disabled={busy}
            onClick={reset}
          >
            重置
          </button>
          <button className="btn btn-primary" type="submit" disabled={!file || busy}>
            {busy ? (
              '处理中…'
            ) : (
              <>
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M12 19V6" />
                  <path d="m6 11 6-6 6 6" />
                  <path d="M5 21h14" />
                </svg>
                上 传
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  )
}
