import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { deleteRecording, listRecordings } from '../lib/endpoints'
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
              <td>
                <span className={`badge badge-${r.status}`}>
                  {STATUS_LABEL[r.status]}
                </span>
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
