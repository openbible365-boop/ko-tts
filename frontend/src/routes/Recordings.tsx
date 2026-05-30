import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import {
  deleteRecording,
  getRecordingProgress,
  listRecordings,
} from '../lib/endpoints'
import type { ContentCategory, Recording, RecordingStatus } from '../lib/types'

const STATUS_LABEL: Record<RecordingStatus, string> = {
  pending_upload: '待上传',
  uploaded: '已上传',
  segmenting: '切分中',
  segmented: '已切分',
  failed: '失败',
}

const CATEGORY_LABEL: Record<ContentCategory, string> = {
  sermon: '讲道',
  bible_reading: '圣经朗读',
  hymn: '赞美诗',
}

// 仍在流转中的状态 —— 列表自动轮询直到全部落定。
const TRANSIENT: RecordingStatus[] = ['pending_upload', 'uploaded', 'segmenting']

function fmtSize(bytes: number | null): string {
  if (bytes == null) return '—'
  const mb = bytes / 1024 / 1024
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${(bytes / 1024).toFixed(0)} KB`
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString()
}

function recLabel(r: Recording): string {
  return r.title || r.original_filename || r.id.slice(0, 8)
}

function fmtElapsed(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

// 状态格: 处理中的录音轮询 /progress, 展示进度条 + 阶段说明 + 已用时,
// 让「切分中」(尤其去背景音乐那 ~28 分钟)不再是个不动的字。
function StatusCell({ rec }: { rec: Recording }) {
  const maybeActive =
    rec.status === 'uploaded' ||
    rec.status === 'segmenting' ||
    rec.status === 'segmented'

  const { data: p } = useQuery({
    queryKey: ['progress', rec.id],
    queryFn: () => getRecordingProgress(rec.id),
    enabled: maybeActive,
    refetchInterval: (q) => {
      const d = q.state.data
      if (!d) return 3000
      const done = d.status === 'segmented' && d.in_transcription === 0
      return done ? false : 3000
    },
  })

  const badge = (
    <span className={`badge badge-${rec.status}`}>{STATUS_LABEL[rec.status]}</span>
  )

  // 待上传(等客户端传完)/ 失败: 只显示徽章
  if (rec.status === 'pending_upload' || rec.status === 'failed') return badge

  // 切分/分离阶段: 此阶段无细粒度信号, 用动画条 + 阶段 + 已用时
  if (rec.status === 'uploaded' || rec.status === 'segmenting') {
    const label =
      rec.status === 'uploaded'
        ? '排队中…'
        : rec.remove_music
          ? '去背景音乐 + 切分中'
          : '切分中'
    return (
      <div className="status-cell">
        {badge}
        <div className="progress-bar indeterminate">
          <span />
        </div>
        <span className="progress-text">
          {label}
          {p ? ` · 已用 ${fmtElapsed(p.phase_elapsed_sec)}` : ''}
        </span>
      </div>
    )
  }

  // 已切分但 ASR 还在跑: 真实进度条 X/N
  if (p && p.segment_total > 0 && p.in_transcription > 0) {
    const pct = Math.round((p.transcribed / p.segment_total) * 100)
    return (
      <div className="status-cell">
        {badge}
        <div className="progress-bar">
          <span style={{ width: `${pct}%` }} />
        </div>
        <span className="progress-text">
          转写 {p.transcribed}/{p.segment_total} 段
        </span>
      </div>
    )
  }

  // 已切分且转写完成 / 无切片: 只显示徽章
  return badge
}

export function Recordings() {
  const { user } = useAuth()
  const isStaff = user?.role === 'admin' || user?.role === 'reviewer'
  const queryClient = useQueryClient()
  const [deleteTarget, setDeleteTarget] = useState<Recording | null>(null)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['recordings'],
    queryFn: listRecordings,
    // 若有录音在流转中, 每 5s 轮询一次追踪进度
    refetchInterval: (q) =>
      q.state.data?.some((r) => TRANSIENT.includes(r.status)) ? 5000 : false,
  })

  const del = useMutation({
    mutationFn: (id: string) => deleteRecording(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['recordings'] })
      setDeleteTarget(null)
    },
  })

  if (isLoading) return <p className="muted">加载中…</p>
  if (isError) return <p className="error">{(error as Error).message}</p>

  if (!data || data.length === 0) {
    return (
      <div className="empty">
        <p>还没有录音。</p>
        <Link to="/upload" className="btn">
          上传第一条
        </Link>
      </div>
    )
  }

  return (
    <div>
      <div className="page-head">
        <h1>我的录音</h1>
        <Link to="/upload" className="btn">
          上传
        </Link>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>标题 / 文件名</th>
            <th>类别</th>
            <th>声音</th>
            <th>状态</th>
            <th>大小</th>
            <th>创建时间</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.map((r) => (
            <tr key={r.id}>
              <td>{recLabel(r)}</td>
              <td>{CATEGORY_LABEL[r.content_category]}</td>
              <td>{r.speaker || <span className="muted">—</span>}</td>
              <td>
                <StatusCell rec={r} />
              </td>
              <td>{fmtSize(r.file_size_bytes)}</td>
              <td className="muted">{fmtDate(r.created_at)}</td>
              <td className="row-actions">
                {isStaff && <Link to={`/review?recording=${r.id}`}>校对</Link>}
                <button
                  className="link-danger"
                  onClick={() => setDeleteTarget(r)}
                >
                  删除
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {deleteTarget && (
        <div
          className="modal-overlay"
          onClick={() => !del.isPending && setDeleteTarget(null)}
        >
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>删除录音?</h2>
            <p>
              将永久删除「{recLabel(deleteTarget)}」及其
              <strong>所有切片</strong>(音频文件与校对 / 审核记录)。
              <br />
              此操作<strong>不可撤销</strong>。
            </p>
            {del.isError && <p className="error">{(del.error as Error).message}</p>}
            <div className="modal-actions">
              <button
                className="btn-secondary"
                disabled={del.isPending}
                onClick={() => setDeleteTarget(null)}
              >
                取消
              </button>
              <button
                className="btn-danger"
                disabled={del.isPending}
                onClick={() => del.mutate(deleteTarget.id)}
              >
                {del.isPending ? '删除中…' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
