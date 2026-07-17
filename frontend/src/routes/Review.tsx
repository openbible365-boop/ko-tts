import { useEffect, useRef, useState } from 'react'
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
  getSegmentWaveform,
  getTrainStatus,
  listSegments,
  rejectSegment,
  startTraining,
  trimSegment,
} from '../lib/endpoints'
import type { Segment, SegmentStatus } from '../lib/types'

const TABS: { value: SegmentStatus; label: string }[] = [
  { value: 'pending_correction', label: '待校对' },
  { value: 'pending_review', label: '待审核' },
  { value: 'rejected', label: '已退回' },
  { value: 'approved', label: '已通过' },
]

const TRAIN_FLOW = [
  {
    title: '确认训练配置',
    detail: '说话人、切片数量、训练轮数与批大小',
  },
  {
    title: '筛选并打包数据',
    detail: '读取已通过切片，生成 wavs 与 train.list',
  },
  {
    title: '上传临时数据集',
    detail: 'ZIP 上传 R2，生成限时下载地址',
  },
  {
    title: 'GPU 接收任务',
    detail: '下载、解压并校验训练数据',
  },
  {
    title: '训练集格式化',
    detail: '提取文本、语义与音频特征',
  },
  {
    title: '两阶段模型训练',
    detail: '先训练 SoVITS 音色，再训练 GPT 韵律',
  },
  {
    title: '保存并发布权重',
    detail: '生成 SoVITS .pth 与 GPT .ckpt',
  },
  {
    title: '声音管理可用',
    detail: '进入声音管理合成、试听与对比',
  },
] as const

type TrainFlowState = 'waiting' | 'active' | 'complete' | 'error'

function inferTrainStep(status?: string, message?: string | null): number {
  const msg = message || ''
  if (status === 'submitting') return 0
  if (status === 'formatting') return 4
  if (status === 'training') return 5
  if (status === 'success') return TRAIN_FLOW.length - 1

  if (msg.includes('打包') || msg.includes('受理')) return 1
  if (msg.includes('上传')) return 2
  if (msg.includes('提交 GPU') || msg.includes('GPU')) return 3
  if (msg.includes('格式') || msg.includes('特征')) return 4
  if (msg.includes('训练')) return 5
  if (msg.includes('权重') || msg.includes('发布')) return 6
  return status === 'queued' ? 1 : 0
}

function TrainingFlow({
  status,
  message,
  exp,
  segments,
  configuring = false,
}: {
  status?: string
  message?: string | null
  exp?: string | null
  segments?: number | null
  configuring?: boolean
}) {
  const failed = status === 'error'
  const succeeded = status === 'success'
  const activeStep = configuring ? 0 : inferTrainStep(status, message)

  const stateOf = (index: number): TrainFlowState => {
    if (succeeded) return 'complete'
    if (index < activeStep) return 'complete'
    if (index === activeStep) return failed ? 'error' : 'active'
    return 'waiting'
  }

  return (
    <section className={`train-flow${configuring ? ' train-flow-config' : ''}`}>
      <div className="train-flow-head">
        <div>
          <b>{configuring ? '训练将按以下流程执行' : '训练流程'}</b>
          <span>
            {exp ? `音色「${exp}」` : '提交后自动执行，无需手动切换步骤'}
            {segments ? ` · ${segments} 段` : ''}
          </span>
        </div>
        {!configuring && status && (
          <span className={`train-flow-state train-flow-state-${failed ? 'error' : succeeded ? 'complete' : 'active'}`}>
            {failed ? '失败' : succeeded ? '全部完成' : '执行中'}
          </span>
        )}
      </div>

      <ol className="train-flow-list">
        {TRAIN_FLOW.map((step, index) => {
          const state = stateOf(index)
          return (
            <li
              key={step.title}
              className={`train-flow-step is-${state}`}
              aria-current={state === 'active' || state === 'error' ? 'step' : undefined}
            >
              <span className="train-flow-node" aria-hidden="true">
                {state === 'complete' ? '✓' : state === 'error' ? '!' : index + 1}
              </span>
              <span className="train-flow-copy">
                <b>{step.title}</b>
                <small>{step.detail}</small>
              </span>
            </li>
          )
        })}
      </ol>

      {failed && (
        <div className="train-flow-error">
          当前步骤失败：{message || '训练后端未返回详细错误'}。排除错误后可重新点击“训练音色”从头执行。
        </div>
      )}
    </section>
  )
}

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

  // 一键微调: 发起训练 + 轮询 GPU 任务进度
  const [trainJob, setTrainJob] = useState<string | null>(null)
  const [trainModal, setTrainModal] = useState(false)
  const [trainSpeaker, setTrainSpeaker] = useState('')
  const [trainCount, setTrainCount] = useState('') // 训练条数; 空=全部
  const trainM = useMutation({
    mutationFn: (v: { speaker: string; count?: number }) =>
      startTraining(v.speaker, v.count ? { count: v.count } : {}),
    onSuccess: (d) => setTrainJob(d.job_id),
  })
  const trainStatusQ = useQuery({
    queryKey: ['train-status', trainJob],
    queryFn: () => getTrainStatus(trainJob as string),
    enabled: !!trainJob,
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s === 'success' || s === 'error' ? false : 5000
    },
  })
  const TRAIN_LABEL: Record<string, string> = {
    queued: '排队中…', formatting: '格式化中…', training: '训练中…',
    success: '训练完成 ✓', error: '训练失败',
  }
  const onTrain = () => {
    setTrainSpeaker(rec?.speaker || '')
    setTrainModal(true)
  }
  const submitTrain = () => {
    const sp = trainSpeaker.trim()
    if (!sp) return
    const c = parseInt(trainCount, 10)
    setTrainModal(false)
    trainM.mutate({ speaker: sp, count: Number.isFinite(c) && c > 0 ? c : undefined })
  }

  return (
    <div className="review">
      <style>{`
        @keyframes trainspin { to { transform: rotate(360deg) } }
        .train-spin { display:inline-block; width:14px; height:14px; border:2px solid rgba(79,70,229,.25);
          border-top-color:#4f46e5; border-radius:50%; animation:trainspin .8s linear infinite; }
        .train-banner { display:flex; align-items:flex-start; gap:.6rem; margin:.5rem 0 0;
          padding:.7rem .9rem; border-radius:10px; font-size:.88rem; line-height:1.5;
          background:rgba(79,70,229,.07); border:1px solid rgba(79,70,229,.22); color:var(--text,#222); }
        .train-banner.ok { background:rgba(22,163,74,.09); border-color:rgba(22,163,74,.3); }
        .train-banner.err { background:rgba(220,38,38,.08); border-color:rgba(220,38,38,.3); }
        .train-ico { flex-shrink:0; width:20px; height:20px; display:flex; align-items:center; justify-content:center; font-weight:700; }
        .train-banner.ok .train-ico { color:#16a34a } .train-banner.err .train-ico { color:#dc2626 }
        .train-seg { margin-left:.4rem; font-size:.74rem; padding:.05rem .4rem; border-radius:4px;
          background:rgba(0,0,0,.06); color:var(--text-tertiary,#666); }
        .train-hint { margin-top:.25rem; font-size:.8rem; color:var(--text-tertiary,#666); }
        .train-mask { position:fixed; inset:0; background:rgba(15,15,25,.45); backdrop-filter:blur(2px);
          display:flex; align-items:center; justify-content:center; z-index:1000; }
        .train-card { background:var(--surface,#fff); color:var(--text,#222); width:min(780px,94vw);
          max-height:92vh; overflow-y:auto; border-radius:16px; padding:1.4rem 1.5rem;
          box-shadow:0 20px 60px rgba(0,0,0,.3); }
        .train-card h3 { margin:0 0 .3rem; font-size:1.1rem; display:flex; align-items:center; gap:.5rem; }
        .train-card p { margin:0 0 1rem; font-size:.85rem; color:var(--text-tertiary,#666); line-height:1.6; }
        .train-card input { width:100%; padding:.6rem .7rem; border-radius:9px; font-size:.95rem;
          border:1px solid var(--border,#ddd); background:var(--bg,#fafafa); color:inherit; box-sizing:border-box; }
        .train-card .row { display:flex; gap:.6rem; justify-content:flex-end; margin-top:1.2rem; }
        .train-card button { padding:.55rem 1.1rem; border-radius:9px; font-size:.9rem; cursor:pointer; border:1px solid transparent; }
        .train-card .ghost { background:transparent; border-color:var(--border,#ccc); color:var(--text,#444); }
        .train-card .primary { background:#4f46e5; color:#fff; }
        .train-card .primary:disabled { opacity:.5; cursor:not-allowed; }
      `}</style>

      {trainModal && (
        <div className="train-mask" onClick={() => setTrainModal(false)}>
          <div className="train-card" onClick={(e) => e.stopPropagation()}>
            <h3>
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#4f46e5" strokeWidth="1.9">
                <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
              </svg>
              训练音色
            </h3>
            <p>
              把该说话人的<b>「已通过」</b>切片上传到 GPU 微调出专属音色,
              耗时几分钟到十几分钟。完成后可在「声音管理」里合成、试听和对比。
              <br />
              若同名音色<b>已训练过</b>,会用最新切片<b>重新训练并覆盖</b>。
            </p>
            <TrainingFlow configuring />
            <label style={{ fontSize: '.8rem', color: 'var(--text-tertiary,#666)' }}>说话人</label>
            <input
              autoFocus
              value={trainSpeaker}
              onChange={(e) => setTrainSpeaker(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submitTrain()}
              placeholder="例如 남성1 / 普通话男声"
            />
            <label style={{ fontSize: '.8rem', color: 'var(--text-tertiary,#666)', marginTop: '.9rem', display: 'block' }}>
              训练条数
            </label>
            <div style={{ display: 'flex', gap: '.4rem', marginBottom: '.5rem', flexWrap: 'wrap' }}>
              {['50', '200', '500', ''].map((c) => (
                <button
                  key={c || 'all'}
                  type="button"
                  onClick={() => setTrainCount(c)}
                  style={{
                    padding: '.35rem .8rem', borderRadius: 8, fontSize: '.85rem', cursor: 'pointer',
                    border: `1px solid ${trainCount === c ? 'var(--accent,#d97706)' : 'var(--border,#ddd)'}`,
                    background: trainCount === c ? 'rgba(217,119,6,.1)' : 'transparent',
                    color: trainCount === c ? 'var(--accent,#d97706)' : 'var(--text,#444)',
                    fontWeight: trainCount === c ? 600 : 400,
                  }}
                >
                  {c || '全部'}
                </button>
              ))}
            </div>
            <input
              type="number"
              min={1}
              value={trainCount}
              onChange={(e) => setTrainCount(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submitTrain()}
              placeholder="留空 = 全部;或填 N 条(均匀抽样)"
            />
            <p style={{ margin: '.5rem 0 0', fontSize: '.78rem', color: 'var(--text-tertiary,#888)', lineHeight: 1.5 }}>
              填了条数会从全部素材里<b>随机抽 N 条</b>训练,音色名带 <b>_N</b> 后缀独立成一个音色。
              想对比效果就分别用 50 / 200 / 500 各训一次(音色如 <code>{(trainSpeaker.trim() || '声音')}_50</code>),
              在合成页逐个试听比较。GPU 单卡,请一个训完再训下一个。
            </p>
            <div className="row">
              <button className="ghost" onClick={() => setTrainModal(false)}>取消</button>
              <button className="primary" disabled={!trainSpeaker.trim()} onClick={submitTrain}>
                开始训练
              </button>
            </div>
          </div>
        </div>
      )}

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
          <div style={{ display: 'flex', gap: '0.6rem', flexShrink: 0, alignItems: 'center' }}>
            <button
              className="btn-export"
              disabled={exportM.isPending}
              onClick={() => exportM.mutate()}
              title="把所有已通过的切片打包成 GPT-SoVITS 数据集(wavs + train.list)"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
                <path d="M12 3v12" />
                <path d="m7 10 5 5 5-5" />
                <path d="M5 21h14" />
              </svg>
              {exportM.isPending ? '导出中…' : '导出数据集 (已通过)'}
            </button>
            <button
              className="btn-export"
              disabled={trainM.isPending}
              onClick={onTrain}
              title="把某说话人所有已通过的切片上传到 GPU 并微调出专属音色"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
                <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
              </svg>
              {trainM.isPending ? '提交中…' : '训练音色'}
            </button>
          </div>
        )}
      </div>

      {isStaff && exportM.isError && (
        <p className="rev-msg err">{(exportM.error as Error).message}</p>
      )}
      {isStaff && exportM.isSuccess && (
        <p className="rev-msg">已导出 {exportM.data} 段为数据集 zip。</p>
      )}
      {isStaff && trainM.isError && (
        <div className="train-banner err">
          <span className="train-ico">✕</span>
          <span>{(trainM.error as Error).message}</span>
        </div>
      )}
      {isStaff && trainM.isPending && !trainJob && (
        <TrainingFlow status="submitting" message="正在提交训练请求…" />
      )}
      {isStaff && trainJob && trainStatusQ.data && (() => {
        const st = trainStatusQ.data.status
        const done = st === 'success'
        const failed = st === 'error'
        const running = !done && !failed
        return (
          <div className={`train-banner${failed ? ' err' : done ? ' ok' : ''}`}>
            <span className="train-ico">
              {running ? <span className="train-spin" /> : done ? '✓' : '✕'}
            </span>
            <span>
              <b>音色「{trainStatusQ.data.exp}」</b>
              <span className="train-seg">{trainStatusQ.data.segments ?? '?'} 段</span>
              {' · '}{TRAIN_LABEL[st] || st}
              {trainStatusQ.data.message ? ` — ${trainStatusQ.data.message}` : ''}
              {done && (
                <div className="train-hint">
                  已可进入「声音管理」页面合成、试听和对比。
                </div>
              )}
            </span>
          </div>
        )
      })()}
      {isStaff && trainJob && trainStatusQ.data && (
        <TrainingFlow
          status={trainStatusQ.data.status}
          message={trainStatusQ.data.message}
          exp={trainStatusQ.data.exp}
          segments={trainStatusQ.data.segments}
        />
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
  const [trimming, setTrimming] = useState(false)

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

      {/* key 带上时长/终点: 裁剪保存后这两个会变 -> 播放器重建、重新拉取新音频 */}
      <SegmentPlayer key={`player-${seg.duration_ms}-${seg.end_ms}`} seg={seg} />

      {seg.audio_key && (
        <div style={{ marginTop: 6, display: 'flex', justifyContent: 'flex-end' }}>
          <button
            type="button"
            onClick={() => setTrimming((v) => !v)}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              padding: '0.25rem 0.6rem', fontSize: '0.78rem', cursor: 'pointer',
              borderRadius: 6, border: '1px solid var(--border, #ddd)',
              background: trimming ? 'rgba(217,119,6,0.08)' : 'transparent',
              color: trimming ? 'var(--accent, #d97706)' : 'var(--text-secondary, #555)',
            }}
          >
            ✂ {trimming ? '收起' : '裁剪'}
          </button>
        </div>
      )}
      {trimming && seg.audio_key && (
        <TrimPanel seg={seg} onDone={() => { setTrimming(false); onDone() }} />
      )}

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

// 裁剪面板: 在真实波形上拖拽起点/终点裁掉前后, 试听选中段, 保存后端重切(覆盖原切片)。
function TrimPanel({ seg, onDone }: { seg: Segment; onDone: () => void }) {
  const MIN = 0.3
  const [url, setUrl] = useState<string | null>(null)
  const [peaks, setPeaks] = useState<number[] | null>(null)
  const [dur, setDur] = useState(Math.max(MIN, seg.duration_ms / 1000))
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(Math.max(MIN, seg.duration_ms / 1000))
  const [playing, setPlaying] = useState(false)
  const [cur, setCur] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const stopAtRef = useRef<number | null>(null)
  const laneRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    let cancelled = false
    const audio = audioRef.current
    getSegmentAudioUrl(seg.id).then((r) => { if (!cancelled) setUrl(r.url) }).catch(() => {})
    getSegmentWaveform(seg.id, 260)
      .then((w) => {
        if (cancelled) return
        setPeaks(w.peaks)
        const d = w.duration_ms / 1000
        if (d > 0) { setDur(d); setEnd((p) => (Math.abs(p - seg.duration_ms / 1000) < 0.001 ? d : Math.min(p, d))) }
      })
      .catch(() => { if (!cancelled) setErr('加载波形失败') })
    return () => { cancelled = true; audio?.pause() }
  }, [seg.duration_ms, seg.id])

  useEffect(() => {
    const cv = canvasRef.current
    if (!cv || !peaks || !peaks.length) return
    const BW = 4, H = 120
    cv.width = peaks.length * BW
    cv.height = H
    const ctx = cv.getContext('2d')
    if (!ctx) return
    ctx.clearRect(0, 0, cv.width, cv.height)
    const mx = Math.max(...peaks, 0.01) // 按最大峰值归一化, 填满高度
    for (let i = 0; i < peaks.length; i++) {
      const t = ((i + 0.5) / peaks.length) * dur
      const inSel = t >= start && t <= end
      ctx.fillStyle = inSel ? '#d97706' : 'rgba(150,140,125,0.4)'
      const bh = Math.max(3, (peaks[i] / mx) * H * 0.9)
      ctx.fillRect(i * BW, (H - bh) / 2, BW * 0.62, bh)
    }
  }, [peaks, start, end, dur])

  const m = useMutation({
    mutationFn: () => trimSegment(seg.id, start * 1000, end * 1000),
    onSuccess: onDone,
  })

  function fracFromEvent(clientX: number): number {
    const lane = laneRef.current
    if (!lane) return 0
    const rect = lane.getBoundingClientRect()
    return Math.min(1, Math.max(0, (clientX - rect.left) / rect.width))
  }
  function dragHandle(which: 'start' | 'end', e: React.PointerEvent) {
    e.preventDefault()
    const move = (ev: PointerEvent) => {
      const t = fracFromEvent(ev.clientX) * dur
      if (which === 'start') setStart(Math.max(0, Math.min(t, end - MIN)))
      else setEnd(Math.min(dur, Math.max(t, start + MIN)))
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  function playSelection() {
    const a = audioRef.current
    if (!a || !url) return
    if (playing) { a.pause(); return }
    a.currentTime = start
    stopAtRef.current = end
    void a.play()
  }

  const kept = Math.max(0, end - start)
  const changed = start > 0.02 || end < dur - 0.02
  const canSave = kept >= MIN && changed && !m.isPending
  const pct = (v: number) => `${(v / dur) * 100}%`

  return (
    <div style={{ margin: '8px 0 4px', padding: '12px 14px', border: '1px solid var(--border-default,#e8e0d4)', borderRadius: 10, background: 'var(--bg-card,#faf8f4)' }}>
      <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary,#555)', marginBottom: 10 }}>
        在波形上拖动两侧橙色手柄裁掉前 / 后，先「试听选中」，满意再「保存」（覆盖原切片；裁过头可对该录音重新切分还原）。
      </div>

      <div ref={laneRef} style={{ position: 'relative', height: 76, borderRadius: 8, background: 'var(--border,#efe9df)', overflow: 'hidden', touchAction: 'none', userSelect: 'none' }}>
        {peaks
          ? <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
          : <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', fontSize: '0.78rem', color: 'var(--text-tertiary,#999)' }}>{err || '加载波形…'}</div>}
        <div style={{ position: 'absolute', top: 0, bottom: 0, left: 0, width: pct(start), background: 'rgba(250,248,244,0.62)', pointerEvents: 'none' }} />
        <div style={{ position: 'absolute', top: 0, bottom: 0, left: pct(end), right: 0, background: 'rgba(250,248,244,0.62)', pointerEvents: 'none' }} />
        {cur != null && <div style={{ position: 'absolute', top: 0, bottom: 0, left: pct(cur), width: 2, background: '#111', opacity: 0.55, pointerEvents: 'none' }} />}
        <div onPointerDown={(e) => dragHandle('start', e)} title="起点"
          style={{ position: 'absolute', top: 0, bottom: 0, left: pct(start), width: 16, marginLeft: -8, cursor: 'ew-resize', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ width: 4, height: '100%', background: 'var(--accent,#d97706)', borderRadius: 2, boxShadow: '0 0 0 1px rgba(255,255,255,0.7)' }} />
        </div>
        <div onPointerDown={(e) => dragHandle('end', e)} title="终点"
          style={{ position: 'absolute', top: 0, bottom: 0, left: pct(end), width: 16, marginLeft: -8, cursor: 'ew-resize', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ width: 4, height: '100%', background: 'var(--accent,#d97706)', borderRadius: 2, boxShadow: '0 0 0 1px rgba(255,255,255,0.7)' }} />
        </div>
      </div>

      <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary,#555)', margin: '8px 0' }}>
        保留 <b>{kept.toFixed(2)}s</b>（起 {start.toFixed(2)}s → 止 {end.toFixed(2)}s；原 {dur.toFixed(2)}s，裁前 {start.toFixed(2)}s、裁后 {Math.max(0, dur - end).toFixed(2)}s）
      </div>
      {m.isError && <div style={{ color: '#c0392b', fontSize: '0.78rem' }}>{(m.error as Error).message}</div>}

      <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
        <button type="button" onClick={playSelection} disabled={!url}
          style={{ padding: '0.4rem 0.9rem', fontSize: '0.82rem', fontWeight: 600, borderRadius: 6, border: '1.5px solid var(--accent,#d97706)', background: playing ? 'var(--accent,#d97706)' : 'var(--bg-card,#fff)', color: playing ? '#fff' : 'var(--accent,#d97706)', cursor: url ? 'pointer' : 'not-allowed', opacity: url ? 1 : 0.5 }}>
          {playing ? '⏸ 停止' : '▶ 试听选中'}
        </button>
        <button type="button" onClick={() => m.mutate()} disabled={!canSave}
          style={{ padding: '0.4rem 1rem', fontSize: '0.82rem', fontWeight: 600, borderRadius: 6, border: 'none', background: canSave ? 'var(--accent,#d97706)' : 'var(--border,#ccc)', color: '#fff', cursor: canSave ? 'pointer' : 'not-allowed' }}>
          {m.isPending ? '保存中…' : '保存裁剪'}
        </button>
      </div>

      {url && (
        <audio ref={audioRef} src={url}
          onPlay={() => setPlaying(true)}
          onPause={() => { setPlaying(false); setCur(null) }}
          onTimeUpdate={(e) => {
            const t = e.currentTarget.currentTime
            setCur(t)
            if (stopAtRef.current != null && t >= stopAtRef.current) { e.currentTarget.pause(); stopAtRef.current = null }
          }}
          style={{ display: 'none' }} />
      )}
    </div>
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
