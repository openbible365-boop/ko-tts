import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import {
  approveSegment,
  correctSegment,
  deleteSegment,
  downloadDataset,
  getRecording,
  getSegmentAudioUrl,
  listSegments,
  rejectSegment,
} from '../lib/endpoints'
import type { Segment, SegmentStatus } from '../lib/types'

// 工作台只关心这四个可操作/可查看的状态; 转写中/失败不在此露出。
const TABS: { value: SegmentStatus; label: string }[] = [
  { value: 'pending_correction', label: '待校对' },
  { value: 'pending_review', label: '待审核' },
  { value: 'rejected', label: '已退回' },
  { value: 'approved', label: '已通过' },
]

const STATUS_LABEL: Record<SegmentStatus, string> = {
  pending_transcription: '待转写',
  transcribing: '转写中',
  transcription_failed: '转写失败',
  pending_correction: '待校对',
  pending_review: '待审核',
  approved: '已通过',
  rejected: '已退回',
}

function fmtDur(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`
}

export function Review() {
  const [searchParams] = useSearchParams()
  const recordingId = searchParams.get('recording') || undefined
  const [tab, setTab] = useState<SegmentStatus>('pending_correction')

  // 带 ?recording= 时, 只看这条录音的切片 (并取它的标题做提示)
  const { data: rec } = useQuery({
    queryKey: ['recording', recordingId],
    queryFn: () => getRecording(recordingId as string),
    enabled: !!recordingId,
  })

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['segments', tab, recordingId ?? null],
    queryFn: () => listSegments({ status: tab, recordingId }),
  })

  // 一键把已通过的切片打包成 GPT-SoVITS 数据集 zip
  const exportM = useMutation({
    mutationFn: () => downloadDataset({ status: 'approved' }),
  })

  return (
    <div>
      <div className="page-head">
        <h1>校对 / 审核</h1>
        <button
          className="btn-secondary"
          disabled={exportM.isPending}
          onClick={() => exportM.mutate()}
          title="把所有已通过的切片打包成 GPT-SoVITS 数据集(wavs + train.list)"
        >
          {exportM.isPending ? '导出中…' : '⬇ 导出数据集(已通过)'}
        </button>
      </div>
      {exportM.isError && (
        <p className="error">{(exportM.error as Error).message}</p>
      )}
      {exportM.isSuccess && (
        <p className="muted">已导出 {exportM.data} 段为 ko-tts-dataset.zip。</p>
      )}
      {recordingId && (
        <p className="filter-banner">
          只显示录音「{rec ? rec.title || rec.original_filename || rec.id.slice(0, 8) : '…'}」的切片
          <Link to="/review">查看全部</Link>
        </p>
      )}
      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.value}
            className={`tab ${tab === t.value ? 'tab-active' : ''}`}
            onClick={() => setTab(t.value)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {isLoading && <p className="muted">加载中…</p>}
      {isError && <p className="error">{(error as Error).message}</p>}
      {data && data.length === 0 && (
        <p className="muted">这个分类下暂无切片。</p>
      )}

      <div className="seg-list">
        {data?.map((seg) => (
          <SegmentCard key={seg.id} seg={seg} />
        ))}
      </div>
    </div>
  )
}

function SegmentCard({ seg }: { seg: Segment }) {
  const queryClient = useQueryClient()
  // 动作后失效全部 segments 查询(当前 tab 会少一条, 目标 tab 会多一条)
  const onDone = () => queryClient.invalidateQueries({ queryKey: ['segments'] })

  // 已通过的段: 头部「已通过」徽章后跟 退回(→已退回再改) / 删除
  const sendBack = useMutation({
    mutationFn: () => rejectSegment(seg.id),
    onSuccess: onDone,
  })
  const del = useMutation({
    mutationFn: () => deleteSegment(seg.id),
    onSuccess: onDone,
  })
  const approvedBusy = sendBack.isPending || del.isPending

  return (
    <div className="seg-card">
      <div className="seg-head">
        <span className="muted">
          #{seg.segment_index} · {fmtDur(seg.duration_ms)}
        </span>
        <div className="seg-head-actions">
          <span className={`badge badge-seg-${seg.status}`}>
            {STATUS_LABEL[seg.status]}
          </span>
          {seg.status === 'approved' && (
            <>
              <button
                className="link-btn"
                disabled={approvedBusy}
                onClick={() => sendBack.mutate()}
              >
                {sendBack.isPending ? '退回中…' : '退回'}
              </button>
              <button
                className="link-danger"
                disabled={approvedBusy}
                onClick={() => del.mutate()}
              >
                {del.isPending ? '删除中…' : '删除'}
              </button>
            </>
          )}
        </div>
      </div>

      <SegmentAudio segmentId={seg.id} hasAudio={!!seg.audio_key} />

      {seg.asr_text != null && (
        <div className="seg-field">
          <span className="seg-label">ASR 原文</span>
          <p className="seg-asr">{seg.asr_text}</p>
        </div>
      )}

      {seg.status === 'pending_review' ? (
        <ReviewActions seg={seg} onDone={onDone} />
      ) : seg.status === 'approved' ? (
        <div className="seg-field">
          <span className="seg-label">已通过文本</span>
          <p className="seg-text">{seg.text}</p>
          {(sendBack.isError || del.isError) && (
            <p className="error">
              {((sendBack.error || del.error) as Error).message}
            </p>
          )}
        </div>
      ) : (
        // pending_correction | rejected —— 都可(重新)校对
        <CorrectActions seg={seg} onDone={onDone} />
      )}
    </div>
  )
}

function SegmentAudio({ segmentId, hasAudio }: { segmentId: string; hasAudio: boolean }) {
  const [load, setLoad] = useState(false)
  const { data, isLoading, isError } = useQuery({
    queryKey: ['segment-audio', segmentId],
    queryFn: () => getSegmentAudioUrl(segmentId),
    enabled: load,
    staleTime: 50 * 60 * 1000, // 预签名 URL 有效 1h, 50 分钟内复用
  })

  if (!hasAudio) return <p className="muted">无音频切片</p>
  if (!load) {
    return (
      <button className="btn-secondary" onClick={() => setLoad(true)}>
        ▶ 加载音频
      </button>
    )
  }
  if (isLoading) return <p className="muted">加载音频…</p>
  if (isError || !data) return <p className="error">音频加载失败</p>
  return <audio controls src={data.url} className="seg-audio" />
}

function CorrectActions({ seg, onDone }: { seg: Segment; onDone: () => void }) {
  // 优先编辑已有 text, 否则以 ASR 原文打底
  const [text, setText] = useState(seg.text ?? seg.asr_text ?? '')
  const m = useMutation({
    mutationFn: () => correctSegment(seg.id, text.trim()),
    onSuccess: onDone,
  })

  return (
    <div className="seg-edit">
      {seg.rejection_reason && (
        <p className="error">退回理由:{seg.rejection_reason}</p>
      )}
      <span className="seg-label">校对文本</span>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        disabled={m.isPending}
      />
      {m.isError && <p className="error">{(m.error as Error).message}</p>}
      <div className="seg-actions">
        <button disabled={!text.trim() || m.isPending} onClick={() => m.mutate()}>
          {m.isPending ? '提交中…' : '提交校对'}
        </button>
      </div>
    </div>
  )
}

function ReviewActions({ seg, onDone }: { seg: Segment; onDone: () => void }) {
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const approve = useMutation({
    mutationFn: () => approveSegment(seg.id),
    onSuccess: onDone,
  })
  const reject = useMutation({
    mutationFn: () => rejectSegment(seg.id, reason.trim()),
    onSuccess: onDone,
  })
  const busy = approve.isPending || reject.isPending
  const err = (approve.error || reject.error) as Error | null

  return (
    <div className="seg-edit">
      <span className="seg-label">校对文本</span>
      <p className="seg-text">{seg.text}</p>
      {err && <p className="error">{err.message}</p>}

      {!rejecting ? (
        <div className="seg-actions">
          <button disabled={busy} onClick={() => approve.mutate()}>
            {approve.isPending ? '通过中…' : '通过'}
          </button>
          <button
            className="btn-secondary"
            disabled={busy}
            onClick={() => setRejecting(true)}
          >
            退回
          </button>
        </div>
      ) : (
        <>
          <textarea
            placeholder="退回理由(必填)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
            disabled={busy}
          />
          <div className="seg-actions">
            <button
              className="btn-danger"
              disabled={!reason.trim() || busy}
              onClick={() => reject.mutate()}
            >
              {reject.isPending ? '退回中…' : '确认退回'}
            </button>
            <button
              className="btn-secondary"
              disabled={busy}
              onClick={() => {
                setRejecting(false)
                setReason('')
              }}
            >
              取消
            </button>
          </div>
        </>
      )}
    </div>
  )
}
