import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import {
  deleteRecording,
  getRecordingAudioUrl,
  getRecordingProgress,
  listRecordings,
  startSegmentation,
} from '../lib/endpoints'
import type {
  ContentCategory,
  Language,
  Recording,
  RecordingStatus,
} from '../lib/types'

const CATEGORY_LABEL: Record<ContentCategory, string> = {
  sermon: '播音',
  bible_reading: '演讲',
  hymn: '朗诵',
}

const LANGUAGE_LABEL: Record<Language, string> = {
  en: '英语',
  zh: '普通话',
  ko: '朝鲜语',
}

// 仍在流转中的状态 —— 列表自动轮询直到全部落定。
// 仍在自动流转中的状态(列表轮询直到落定); uploaded 是等用户手动操作, 不轮询。
const TRANSIENT: RecordingStatus[] = [
  'pending_upload',
  'pending_separation',
  'separating',
  'pending_segmentation',
  'segmenting',
]

// 上传者头像配色: 由名字哈希挑一个, 同一个人颜色稳定。
const AVATAR_COLORS = [
  '#3a64ff',
  '#7a4ddb',
  '#16a06a',
  '#e07a2f',
  '#e0568a',
  '#2f9bbf',
]

function avatarColor(seed: string): string {
  let h = 0
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return AVATAR_COLORS[h % AVATAR_COLORS.length]
}

function initial(s: string): string {
  return s.trim().charAt(0).toUpperCase() || '?'
}

// 声音/说话人标注里粗略判断性别, 给圆点上色(纯展示)。
function voiceClass(speaker: string | null): string {
  if (!speaker) return ''
  if (/여|女/.test(speaker)) return 'female'
  if (/남|男/.test(speaker)) return 'male'
  return ''
}

function fmtSize(bytes: number | null): string {
  if (bytes == null) return '—'
  const mb = bytes / 1024 / 1024
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${(bytes / 1024).toFixed(0)} KB`
}

function fmtTotalSize(bytes: number): string {
  const gb = bytes / 1024 / 1024 / 1024
  if (gb >= 1) return `${gb.toFixed(1)} GB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString()
}

function fmtDuration(ms: number | null): string {
  if (ms == null) return '—'
  const total = Math.round(ms / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`
}

function recLabel(r: Recording): string {
  return r.title || r.original_filename || r.id.slice(0, 8)
}

function fmtElapsed(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

const WaveIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
    <path d="M9 18V5l12-2v13" />
    <circle cx="6" cy="18" r="3" />
    <circle cx="18" cy="16" r="3" />
  </svg>
)

const CheckIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
    <path d="M20 6 9 17l-5-5" />
  </svg>
)

const PlayIcon = () => (
  <svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M8 5.5v13l11-6.5z" />
  </svg>
)

const PauseIcon = () => (
  <svg viewBox="0 0 24 24" fill="currentColor">
    <rect x="6.5" y="5" width="3.5" height="14" rx="1" />
    <rect x="14" y="5" width="3.5" height="14" rx="1" />
  </svg>
)

const SpinIcon = () => (
  <svg className="spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
    <path d="M12 3a9 9 0 1 0 9 9" strokeLinecap="round" />
  </svg>
)

// 列表里的播放按钮: 点一下懒取预签名 URL 播放, 再点暂停。
// 同一时刻只放一个 —— 用模块级单例 <audio>, 切换录音自动停掉上一个。
let sharedAudio: HTMLAudioElement | null = null

function PlayButton({ recId }: { recId: string }) {
  const [state, setState] = useState<'idle' | 'loading' | 'playing'>('idle')
  const audioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    // 卸载时若正放本录音, 停掉
    return () => {
      const a = audioRef.current
      if (a && sharedAudio === a) {
        a.pause()
        sharedAudio = null
      }
    }
  }, [])

  async function toggle() {
    const cur = audioRef.current
    if (state === 'playing' && cur) {
      cur.pause()
      return
    }
    if (cur && cur.paused && cur.src) {
      // 已加载过, 直接续播
      stopOthers(cur)
      void cur.play()
      return
    }
    setState('loading')
    try {
      const { url } = await getRecordingAudioUrl(recId)
      const a = new Audio(url)
      audioRef.current = a
      a.onplay = () => setState('playing')
      a.onpause = () => setState('idle')
      a.onended = () => setState('idle')
      a.onerror = () => setState('idle')
      stopOthers(a)
      await a.play()
    } catch {
      setState('idle')
    }
  }

  function stopOthers(a: HTMLAudioElement) {
    if (sharedAudio && sharedAudio !== a) sharedAudio.pause()
    sharedAudio = a
  }

  return (
    <button
      type="button"
      className={`file-ico play ${state === 'playing' ? 'on' : ''}`}
      onClick={toggle}
      aria-label={state === 'playing' ? '暂停' : '播放'}
      title={state === 'playing' ? '暂停' : '播放'}
    >
      {state === 'loading' ? (
        <SpinIcon />
      ) : state === 'playing' ? (
        <PauseIcon />
      ) : (
        <PlayIcon />
      )}
    </button>
  )
}

const DownloadIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
    <path d="M12 3v12" />
    <path d="m7 10 5 5 5-5" />
    <path d="M5 21h14" />
  </svg>
)

// 下载原始音频到本地, 保留原文件名。
// 用 fetch+blob 触发下载(预签名 URL 跨域, 裸 <a download> 不保证用文件名)。
function DownloadButton({ rec }: { rec: Recording }) {
  const [busy, setBusy] = useState(false)

  async function download() {
    if (busy) return
    setBusy(true)
    try {
      const { url } = await getRecordingAudioUrl(rec.id)
      const res = await fetch(url)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const blob = await res.blob()
      const href = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = href
      a.download = rec.original_filename || `${recLabel(rec)}.audio`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(href)
    } catch {
      // 静默失败即可; 重试再点一次
    } finally {
      setBusy(false)
    }
  }

  return (
    <button className="act dl" onClick={download} disabled={busy}>
      {busy ? <SpinIcon /> : <DownloadIcon />}
      下载
    </button>
  )
}

// 状态格: 处理中的录音轮询 /progress, 按设计展示徽章 + 进度条 + 阶段说明。
// uploaded(就绪)时, 在徽章下方出现「开始切分」按钮, 点击手动触发切分。
function StatusCell({ rec }: { rec: Recording }) {
  const queryClient = useQueryClient()
  // 仍在流转/可能在转写的状态需要轮询 /progress
  const maybeActive =
    rec.status === 'separating' ||
    rec.status === 'pending_separation' ||
    rec.status === 'pending_segmentation' ||
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

  const startSeg = useMutation({
    mutationFn: () => startSegmentation(rec.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['recordings'] })
      queryClient.invalidateQueries({ queryKey: ['progress', rec.id] })
    },
  })

  if (rec.status === 'failed') return <span className="cbadge fail">失败</span>
  if (rec.status === 'pending_upload')
    return <span className="cbadge run">待上传</span>

  // 去背景音乐阶段(自动): 不定进度动画条
  if (rec.status === 'pending_separation' || rec.status === 'separating') {
    return (
      <div>
        <span className="cbadge run">
          <span className="pulse" />
          去背景音乐
        </span>
        <div className="prog">
          <div className="prog-track">
            <div className="prog-fill" />
          </div>
          <div className="prog-meta">
            分离人声中{p ? ` · 已用 ${fmtElapsed(p.phase_elapsed_sec)}` : ''}
          </div>
        </div>
      </div>
    )
  }

  // 已上传/就绪: 显示「已上传」+「开始切分」按钮
  if (rec.status === 'uploaded') {
    return (
      <div className="status-ready">
        <span className="cbadge ready">已上传</span>
        <button
          className="seg-btn"
          disabled={startSeg.isPending}
          onClick={() => startSeg.mutate()}
        >
          {startSeg.isPending ? '提交中…' : '开始切分'}
        </button>
      </div>
    )
  }

  // 切分阶段: 不定进度动画条 + 已用时
  if (rec.status === 'pending_segmentation' || rec.status === 'segmenting') {
    return (
      <div>
        <span className="cbadge run">
          <span className="pulse" />
          切分中
        </span>
        <div className="prog">
          <div className="prog-track">
            <div className="prog-fill" />
          </div>
          <div className="prog-meta">
            {rec.status === 'pending_segmentation' ? '排队中' : '切分中'}
            {p ? ` · 已用 ${fmtElapsed(p.phase_elapsed_sec)}` : ''}
          </div>
        </div>
      </div>
    )
  }

  // 已切分但 ASR 还在跑: 真实进度条 X/N
  if (p && p.segment_total > 0 && p.in_transcription > 0) {
    const pct = Math.round((p.transcribed / p.segment_total) * 100)
    return (
      <div>
        <span className="cbadge run">
          <span className="pulse" />
          转写中
        </span>
        <div className="prog">
          <div className="prog-track">
            <div
              className="prog-fill determinate"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="prog-meta">
            {p.transcribed}/{p.segment_total} 段
          </div>
        </div>
      </div>
    )
  }

  return (
    <span className="cbadge done">
      <CheckIcon />
      已切分
    </span>
  )
}

export function Recordings() {
  const { user } = useAuth()
  // staff(admin/reviewer)看到所有人的采集; 加「上传者」列分辨归属。
  const isStaff = user?.role === 'admin' || user?.role === 'reviewer'
  const queryClient = useQueryClient()
  const [deleteTarget, setDeleteTarget] = useState<Recording | null>(null)
  const [query, setQuery] = useState('')

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['recordings'],
    queryFn: listRecordings,
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

  if (isLoading) return <div className="coll muted">加载中…</div>
  if (isError)
    return <div className="coll error">{(error as Error).message}</div>

  const all = data ?? []
  const stats = {
    total: all.length,
    segmented: all.filter((r) => r.status === 'segmented').length,
    running: all.filter((r) =>
      [
        'pending_separation',
        'separating',
        'pending_segmentation',
        'segmenting',
      ].includes(r.status),
    ).length,
    bytes: all.reduce((sum, r) => sum + (r.file_size_bytes ?? 0), 0),
  }

  const q = query.trim().toLowerCase()
  const rows = q
    ? all.filter((r) =>
        `${recLabel(r)} ${r.original_filename ?? ''}`.toLowerCase().includes(q),
      )
    : all

  return (
    <div className="coll">
      <div className="coll-head">
        <div>
          <h1>
            <span className="bar" />
            {isStaff ? '全部采集' : '我的采集'}
          </h1>
          <div className="sub">
            {isStaff ? '管理与校对所有已采集的音频样本' : '管理与校对我的音频样本'}
          </div>
        </div>
        <div className="coll-toolbar">
          <div className="coll-search">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.2-3.2" />
            </svg>
            <input
              type="text"
              placeholder="搜索标题 / 文件名"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <Link to="/upload" className="coll-upload">
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
            上传
          </Link>
        </div>
      </div>

      <section className="coll-stats">
        <div className="coll-stat">
          <div className="ico a">
            <WaveIcon />
          </div>
          <div>
            <div className="n">{stats.total}</div>
            <div className="l">采集总数</div>
          </div>
        </div>
        <div className="coll-stat">
          <div className="ico b">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M20 6 9 17l-5-5" />
            </svg>
          </div>
          <div>
            <div className="n">{stats.segmented}</div>
            <div className="l">已切分</div>
          </div>
        </div>
        <div className="coll-stat">
          <div className="ico c">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
            >
              <circle cx="12" cy="12" r="9" />
              <path d="M12 7v5l3 2" />
            </svg>
          </div>
          <div>
            <div className="n">{stats.running}</div>
            <div className="l">切分中</div>
          </div>
        </div>
        <div className="coll-stat">
          <div className="ico d">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
            >
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
          </div>
          <div>
            <div className="n">{fmtTotalSize(stats.bytes)}</div>
            <div className="l">样本容量</div>
          </div>
        </div>
      </section>

      <section className="coll-card">
        <table>
          <thead>
            <tr>
              <th>标题 / 文件名</th>
              {isStaff && <th>上传者</th>}
              <th>声音 / 语种 / 类别</th>
              <th>状态</th>
              <th>大小</th>
              <th style={{ textAlign: 'center' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const who = r.uploader_name || r.uploader_email || '—'
              return (
                <tr key={r.id}>
                  <td>
                    <div className="file-cell">
                      <PlayButton recId={r.id} />
                      <div className="file-meta">
                        <div className="fname" title={recLabel(r)}>
                          {recLabel(r)}
                        </div>
                        <div
                          className="fmeta"
                          title={r.original_filename || undefined}
                        >
                          {r.original_filename || '—'}
                        </div>
                        <div className="fdate">{fmtDate(r.created_at)}</div>
                      </div>
                    </div>
                  </td>
                  {isStaff && (
                    <td>
                      <div className="uploader">
                        <span
                          className="ua"
                          style={{ background: avatarColor(who) }}
                        >
                          {initial(who)}
                        </span>
                        {who}
                      </div>
                    </td>
                  )}
                  <td>
                    <div className="meta-combo">
                      {r.speaker ? (
                        <span className={`voice ${voiceClass(r.speaker)}`}>
                          <span className="vd" />
                          {r.speaker}
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                      <span className="sep">/</span>
                      <span className="tag">
                        {LANGUAGE_LABEL[r.language] ?? r.language}
                      </span>
                      <span className="sep">/</span>
                      <span className="tag">
                        {CATEGORY_LABEL[r.content_category]}
                      </span>
                    </div>
                  </td>
                  <td>
                    <StatusCell rec={r} />
                  </td>
                  <td>
                    <div className="size-cell">
                      <span className="size">{fmtSize(r.file_size_bytes)}</span>
                      <span className="size-dur">{fmtDuration(r.duration_ms)}</span>
                    </div>
                  </td>
                  <td>
                    <div className="actions">
                      <Link className="act check" to={`/review?recording=${r.id}`}>
                        <CheckIcon />
                        校对
                      </Link>
                      <DownloadButton rec={r} />
                      <button
                        className="act del"
                        onClick={() => setDeleteTarget(r)}
                      >
                        <svg
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="1.9"
                        >
                          <path d="M3 6h18" />
                          <path d="M8 6V4h8v2" />
                          <path d="M19 6l-1 14H6L5 6" />
                        </svg>
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={isStaff ? 6 : 5}>
                  <div className="coll-empty">
                    {all.length === 0
                      ? '还没有采集，点右上角「上传」开始。'
                      : '没有匹配的采集。'}
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="coll-foot">共 {rows.length} 条记录</div>
      </section>

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
