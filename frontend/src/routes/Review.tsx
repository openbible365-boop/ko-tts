import { useRef, useState } from 'react'
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
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

const TABS: { value: SegmentStatus; label: string }[] = [
  { value: 'pending_correction', label: '待校对' },
  { value: 'pending_review', label: '待审核' },
  { value: 'rejected', label: '已退回' },
  { value: 'approved', label: '已通过' },
]

// 状态 → 卡片右上角徽章的样式与文案
const ST: Record<string, { cls: string; label: string }> = {
  pending_correction: { cls: 'wait', label: '待校对' },
  pending_review: { cls: 'review', label: '待审核' },
  rejected: { cls: 'back', label: '已退回' },
  approved: { cls: 'pass', label: '已通过' },
}

function fmtDur(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`
}

function fmtClock(sec: number): string {
  const s = Math.max(0, Math.floor(sec))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

function fmtDateTime(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : '—'
}

// 由切片 id 派生一组稳定的波形柱高(纯装饰)。
function waveBars(seed: string, n = 44): number[] {
  const out: number[] = []
  let h = 0
  for (let i = 0; i < n; i++) {
    h = (h * 31 + seed.charCodeAt(i % seed.length)) >>> 0
    out.push(28 + (h % 70))
  }
  return out
}

const ClockIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
    <circle cx="12" cy="12" r="9" />
    <path d="M12 8v4l3 2" />
  </svg>
)

const CalIcon = () => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.9"
    style={{ width: 15, height: 15 }}
  >
    <rect x="3" y="4" width="18" height="17" rx="2" />
    <path d="M16 2v4M8 2v4M3 10h18" />
  </svg>
)

export function Review() {
  const { user } = useAuth()
  const isStaff = user?.role === 'admin' || user?.role === 'reviewer'
  const [searchParams] = useSearchParams()
  const recordingId = searchParams.get('recording') || undefined
  const [tab, setTab] = useState<SegmentStatus>('pending_correction')

  const { data: rec } = useQuery({
    queryKey: ['recording', recordingId],
    queryFn: () => getRecording(recordingId as string),
    enabled: !!recordingId,
  })

  // 每个标签的计数(用 ['segments',...] 前缀, 动作后的 invalidate 会一并刷新)
  const counts = useQueries({
    queries: TABS.map((t) => ({
      queryKey: ['segments', 'count', t.value, recordingId ?? null],
      queryFn: () =>
        listSegments({ status: t.value, recordingId }).then((r) => r.length),
    })),
  })

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['segments', tab, recordingId ?? null],
    queryFn: () => listSegments({ status: tab, recordingId }),
  })

  const exportM = useMutation({
    mutationFn: () => downloadDataset({ status: 'approved' }),
  })

  return (
    <div className="review">
      <div className="review-head">
        <div>
          <h1>
            <span className="bar" />
            {isStaff ? '校对 / 审核' : '校对'}
          </h1>
          <div className="sub">
            {isStaff
              ? '逐条校对 ASR 文本并提交审核，通过后导出数据集'
              : '逐条校对 ASR 文本并提交审核'}
          </div>
        </div>
        {isStaff && (
          <button
            className="btn-export"
            disabled={exportM.isPending}
            onClick={() => exportM.mutate()}
            title="把所有已通过的切片打包成 GPT-SoVITS 数据集(wavs + train.list)"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
            >
              <path d="M12 3v12" />
              <path d="m7 10 5 5 5-5" />
              <path d="M5 21h14" />
            </svg>
            {exportM.isPending ? '导出中…' : '导出数据集 (已通过)'}
          </button>
        )}
      </div>

      {isStaff && exportM.isError && (
        <p className="rev-msg err">{(exportM.error as Error).message}</p>
      )}
      {isStaff && exportM.isSuccess && (
        <p className="rev-msg">已导出 {exportM.data} 段为数据集 zip。</p>
      )}

      {recordingId && isStaff && (
        <p className="filter-tip">
          <span>
            只显示录音「
            {rec
              ? rec.title || rec.original_filename || rec.id.slice(0, 8)
              : '…'}
            」的切片
          </span>
          <Link to="/review">查看全部</Link>
        </p>
      )}

      <div className="tabs">
        {TABS.map((t, i) => (
          <button
            key={t.value}
            className={`tab ${tab === t.value ? 'on' : ''}`}
            onClick={() => setTab(t.value)}
          >
            <span>{t.label}</span>
            <span className="cnt">{counts[i]?.data ?? 0}</span>
          </button>
        ))}
      </div>

      {isLoading && <p className="rev-msg">加载中…</p>}
      {isError && <p className="rev-msg err">{(error as Error).message}</p>}

      {data && data.length === 0 && (
        <div className="empty">
          <div className="ei">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
            >
              <path d="M9 11l3 3 8-8" />
              <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
            </svg>
          </div>
          <div className="et">这个分类下暂无切片</div>
          <div className="es">切换其它标签，或等待新的切片进入。</div>
        </div>
      )}

      {data?.map((seg) => (
        <SegmentCard key={seg.id} seg={seg} isStaff={isStaff} />
      ))}
    </div>
  )
}

function SegmentCard({ seg, isStaff }: { seg: Segment; isStaff: boolean }) {
  const queryClient = useQueryClient()
  const onDone = () => queryClient.invalidateQueries({ queryKey: ['segments'] })
  const st = ST[seg.status] ?? { cls: 'wait', label: seg.status }

  return (
    <article className="rev">
      <div className="rev-head">
        <div className="rev-id">
          <span className="num">#{seg.segment_index}</span>
          <span className="dur">
            <ClockIcon />
            {fmtDur(seg.duration_ms)}
          </span>
        </div>
        <span className={`st ${st.cls}`}>
          <span className="dot" />
          {st.label}
        </span>
      </div>

      <SegmentPlayer seg={seg} />

      {seg.status === 'pending_review' ? (
        isStaff ? (
          <ReviewActions seg={seg} onDone={onDone} />
        ) : (
          <ReadOnlyBody seg={seg} meta="pending_review" />
        )
      ) : seg.status === 'approved' ? (
        <ApprovedBody seg={seg} isStaff={isStaff} onDone={onDone} />
      ) : (
        // pending_correction | rejected —— 都可(重新)校对
        <CorrectActions seg={seg} onDone={onDone} />
      )}
    </article>
  )
}

// 自定义播放器: 点播放时拉取预签名 URL 并播放; 波形为装饰, 按进度高亮。
function SegmentPlayer({ seg }: { seg: Segment }) {
  const [url, setUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [cur, setCur] = useState(0)
  const [dur, setDur] = useState(seg.duration_ms / 1000)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const bars = waveBars(seg.id)

  if (!seg.audio_key) return <div className="player-empty">无音频切片</div>

  async function toggle() {
    if (!url) {
      setLoading(true)
      try {
        const r = await getSegmentAudioUrl(seg.id)
        setUrl(r.url) // 音频元素 autoPlay 接管首次播放
      } catch {
        // ignore; 用户可再次点击重试
      } finally {
        setLoading(false)
      }
      return
    }
    const a = audioRef.current
    if (!a) return
    if (playing) a.pause()
    else void a.play()
  }

  const frac = dur > 0 ? cur / dur : 0
  const activeBars = Math.round(frac * bars.length)

  return (
    <div className="player">
      <button
        className="play-btn"
        type="button"
        disabled={loading}
        onClick={toggle}
        aria-label={playing ? '暂停' : '播放'}
      >
        {playing ? (
          <svg viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="5" width="4" height="14" rx="1" />
            <rect x="14" y="5" width="4" height="14" rx="1" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5v14l11-7z" />
          </svg>
        )}
      </button>
      <div className="wave">
        {bars.map((h, i) => (
          <i
            key={i}
            className={i < activeBars ? 'act' : ''}
            style={{ height: `${h}%` }}
          />
        ))}
      </div>
      <span className="ptime">
        {fmtClock(cur)} / {fmtClock(dur)}
      </span>
      {url && (
        <audio
          ref={audioRef}
          src={url}
          autoPlay
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => {
            setPlaying(false)
            setCur(0)
          }}
          onTimeUpdate={(e) => setCur(e.currentTarget.currentTime)}
          onLoadedMetadata={(e) => {
            const d = e.currentTarget.duration
            if (Number.isFinite(d) && d > 0) setDur(d)
          }}
          style={{ display: 'none' }}
        />
      )}
    </div>
  )
}

function AsrBlock({ seg }: { seg: Segment }) {
  if (seg.asr_text == null) return null
  return (
    <>
      <div className="lbl">
        <span className="tg asr">ASR</span> 原文
      </div>
      <div className="asr-text">{seg.asr_text}</div>
    </>
  )
}

// 待校对 / 已退回: 可编辑校对文本并提交
function CorrectActions({ seg, onDone }: { seg: Segment; onDone: () => void }) {
  const [text, setText] = useState(seg.text ?? seg.asr_text ?? '')
  const m = useMutation({
    mutationFn: () => correctSegment(seg.id, text.trim()),
    onSuccess: onDone,
  })

  return (
    <>
      {seg.status === 'rejected' && seg.rejection_reason && (
        <div className="note">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.9"
          >
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v5M12 16h.01" />
          </svg>
          <div>
            <b>退回原因：</b>
            {seg.rejection_reason}
          </div>
        </div>
      )}
      <AsrBlock seg={seg} />
      <div className="lbl">
        <span className="tg fix">校对</span> 文本
      </div>
      <textarea
        className="fix"
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={m.isPending}
      />
      {m.isError && <p className="rev-msg err">{(m.error as Error).message}</p>}
      <div className="acts">
        <button
          className="b primary"
          disabled={!text.trim() || m.isPending}
          onClick={() => m.mutate()}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M20 6 9 17l-5-5" />
          </svg>
          {m.isPending
            ? '提交中…'
            : seg.status === 'rejected'
              ? '重新提交'
              : '提交校对'}
        </button>
      </div>
    </>
  )
}

// 待审核 / 已通过 的只读正文(contributor 视角)
function ReadOnlyBody({
  seg,
  meta,
}: {
  seg: Segment
  meta: 'pending_review' | 'approved'
}) {
  return (
    <>
      <div className="meta-row">
        <div className="mi">
          <CalIcon />
          {meta === 'approved' ? '审核时间' : '校对时间'}{' '}
          <b>
            {fmtDateTime(meta === 'approved' ? seg.reviewed_at : seg.corrected_at)}
          </b>
        </div>
      </div>
      <AsrBlock seg={seg} />
      <div className="lbl">
        <span className="tg fix">{meta === 'approved' ? '最终' : '校对'}</span>{' '}
        文本
      </div>
      <div className="asr-text ro">{seg.text}</div>
    </>
  )
}

// 待审核(staff): 通过 / 退回(带理由)
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
    <>
      <div className="meta-row">
        <div className="mi">
          <CalIcon />
          校对时间 <b>{fmtDateTime(seg.corrected_at)}</b>
        </div>
      </div>
      <AsrBlock seg={seg} />
      <div className="lbl">
        <span className="tg fix">校对</span> 文本
      </div>
      <div className="asr-text ro">{seg.text}</div>
      {err && <p className="rev-msg err">{err.message}</p>}

      {!rejecting ? (
        <div className="acts">
          <button className="b ok" disabled={busy} onClick={() => approve.mutate()}>
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M20 6 9 17l-5-5" />
            </svg>
            {approve.isPending ? '通过中…' : '通过'}
          </button>
          <button
            className="b danger"
            disabled={busy}
            onClick={() => setRejecting(true)}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M9 14 4 9l5-5" />
              <path d="M4 9h11a5 5 0 0 1 0 10h-1" />
            </svg>
            退回
          </button>
        </div>
      ) : (
        <>
          <div className="lbl" style={{ marginTop: 16 }}>
            退回理由(必填)
          </div>
          <textarea
            className="fix"
            placeholder="说明问题, 便于校对人修正"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            disabled={busy}
          />
          <div className="acts">
            <button
              className="b danger"
              disabled={!reason.trim() || busy}
              onClick={() => reject.mutate()}
            >
              {reject.isPending ? '退回中…' : '确认退回'}
            </button>
            <button
              className="b ghost"
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
    </>
  )
}

// 已通过: 展示最终文本; staff 还可退回 / 删除
function ApprovedBody({
  seg,
  isStaff,
  onDone,
}: {
  seg: Segment
  isStaff: boolean
  onDone: () => void
}) {
  const sendBack = useMutation({
    mutationFn: () => rejectSegment(seg.id),
    onSuccess: onDone,
  })
  const del = useMutation({
    mutationFn: () => deleteSegment(seg.id),
    onSuccess: onDone,
  })
  const busy = sendBack.isPending || del.isPending
  const err = (sendBack.error || del.error) as Error | null

  return (
    <>
      <div className="meta-row">
        <div className="mi">
          <CalIcon />
          审核时间 <b>{fmtDateTime(seg.reviewed_at)}</b>
        </div>
      </div>
      <div className="lbl">
        <span className="tg fix">最终</span> 文本
      </div>
      <div className="asr-text ro">{seg.text}</div>
      {err && <p className="rev-msg err">{err.message}</p>}
      {isStaff && (
        <div className="acts">
          <button
            className="b ghost"
            disabled={busy}
            onClick={() => sendBack.mutate()}
          >
            {sendBack.isPending ? '退回中…' : '退回'}
          </button>
          <button
            className="b danger"
            disabled={busy}
            onClick={() => del.mutate()}
          >
            {del.isPending ? '删除中…' : '删除'}
          </button>
        </div>
      )}
    </>
  )
}
