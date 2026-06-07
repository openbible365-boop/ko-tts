import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  completeLineRecording,
  getLineRecordUrl,
  getSegmentAudioUrl,
  listRecordableScripts,
  listSegments,
  passLine,
  rerecordLine,
  setRecordingSpeaker,
  startRecordingFromScript,
  uploadFileToR2,
} from '../lib/endpoints'
import type { ContentCategory, Language, Recording, Segment } from '../lib/types'

const CAT_LABEL: Record<ContentCategory, string> = {
  sermon: '播音',
  bible_reading: '演讲',
  hymn: '朗诵',
}
const LANG_LABEL: Record<Language, string> = { en: '英语', zh: '普通话', ko: '朝鲜语' }

// ---- 词级 diff: 标红范文中与识别不一致的词(与后端 textcompare 规整规则一致) ----
function normWord(w: string): string {
  return w.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '')
}
function diffExpected(expected: string, asr: string | null): { word: string; ok: boolean }[] {
  const tokens = (expected || '').split(/\s+/).filter(Boolean)
  if (!asr) return tokens.map((word) => ({ word, ok: true }))
  const exp = tokens.map(normWord)
  const act = asr.split(/\s+/).map(normWord).filter(Boolean)
  const n = exp.length
  const m = act.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = exp[i] === act[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const ok = new Array(n).fill(false)
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (exp[i] === act[j]) {
      ok[i] = true
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      i++
    } else {
      j++
    }
  }
  return tokens.map((word, idx) => ({ word, ok: normWord(word) === '' ? true : ok[idx] }))
}

// ---- 录音 hook: MediaRecorder + AnalyserNode(供实时波形) ----
function useRecorder() {
  const mr = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const stream = useRef<MediaStream | null>(null)
  const ac = useRef<AudioContext | null>(null)
  const analyser = useRef<AnalyserNode | null>(null)
  const startedAt = useRef(0)

  function cleanup() {
    stream.current?.getTracks().forEach((t) => t.stop())
    ac.current?.close().catch(() => {})
    mr.current = null
    stream.current = null
    ac.current = null
    analyser.current = null
  }

  async function start() {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true })
    stream.current = s
    const ctx = new AudioContext()
    ac.current = ctx
    const src = ctx.createMediaStreamSource(s)
    const an = ctx.createAnalyser()
    an.fftSize = 1024
    src.connect(an)
    analyser.current = an
    const rec = new MediaRecorder(s)
    chunks.current = []
    rec.ondataavailable = (e) => {
      if (e.data.size) chunks.current.push(e.data)
    }
    mr.current = rec
    startedAt.current = performance.now()
    rec.start()
  }

  function stop(): Promise<{ blob: Blob; durationMs: number }> {
    return new Promise((resolve) => {
      const rec = mr.current
      if (!rec) return resolve({ blob: new Blob(), durationMs: 0 })
      rec.onstop = () => {
        const blob = new Blob(chunks.current, { type: rec.mimeType || 'audio/webm' })
        const durationMs = Math.round(performance.now() - startedAt.current)
        cleanup()
        resolve({ blob, durationMs })
      }
      rec.stop()
    })
  }

  useEffect(() => () => cleanup(), [])
  return { start, stop, analyser }
}

// ---- 实时波形(录音中): 直接 canvas 绘制, 不触发 React 重渲染 ----
function LiveWave({ analyser }: { analyser: { current: AnalyserNode | null } }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  useEffect(() => {
    let raf = 0
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    const draw = () => {
      raf = requestAnimationFrame(draw)
      const an = analyser.current
      if (!canvas || !ctx || !an) return
      const buf = new Uint8Array(an.fftSize)
      an.getByteTimeDomainData(buf)
      const w = canvas.width
      const h = canvas.height
      ctx.clearRect(0, 0, w, h)
      ctx.fillStyle = '#e15b5b'
      const bars = 56
      const step = Math.floor(buf.length / bars)
      for (let i = 0; i < bars; i++) {
        let peak = 0
        for (let k = 0; k < step; k++) {
          const d = Math.abs(buf[i * step + k] - 128)
          if (d > peak) peak = d
        }
        const bh = Math.max(2, (peak / 128) * h)
        ctx.fillRect(i * (w / bars), (h - bh) / 2, (w / bars) * 0.55, bh)
      }
    }
    draw()
    return () => cancelAnimationFrame(raf)
  }, [analyser])
  return <canvas ref={canvasRef} width={360} height={40} className="livewave" />
}

// ---- 录后播放器: 点击播放该行 clip + 装饰性波形条 ----
const DECO_BARS = [8, 14, 20, 12, 24, 16, 28, 18, 10, 22, 14, 26, 12, 20, 8, 16, 24, 10, 18, 14]

function ClipPlayer({ segmentId }: { segmentId: string }) {
  const [state, setState] = useState<'idle' | 'loading' | 'playing'>('idle')
  const audioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => () => audioRef.current?.pause(), [])

  async function toggle() {
    const cur = audioRef.current
    if (state === 'playing' && cur) {
      cur.pause()
      return
    }
    if (cur && cur.paused && cur.src) {
      void cur.play()
      return
    }
    setState('loading')
    try {
      const { url } = await getSegmentAudioUrl(segmentId)
      const a = new Audio(url)
      audioRef.current = a
      a.onplay = () => setState('playing')
      a.onpause = () => setState('idle')
      a.onended = () => setState('idle')
      a.onerror = () => setState('idle')
      await a.play()
    } catch {
      setState('idle')
    }
  }

  return (
    <button type="button" className="clip-player" onClick={toggle} title="播放">
      <span className="cp-ico">{state === 'playing' ? '❚❚' : state === 'loading' ? '…' : '▶'}</span>
      <span className="cp-bars">
        {DECO_BARS.map((b, i) => (
          <i key={i} style={{ height: `${b}px` }} />
        ))}
      </span>
    </button>
  )
}

// ==================== 选范文 ====================
export function Record() {
  const [params] = useSearchParams()
  const scriptId = params.get('script')
  return scriptId ? <Session scriptId={scriptId} /> : <ScriptPicker />
}

function ScriptPicker() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['recordable'],
    queryFn: listRecordableScripts,
  })
  if (isLoading) return <div className="recpage muted">加载中…</div>
  if (isError) return <div className="recpage error">{(error as Error).message}</div>
  const scripts = data ?? []

  return (
    <div className="recpage">
      <div className="rec-head">
        <h1>
          <span className="bar" />
          选择范文开始录音
        </h1>
        <div className="sub">选一篇已定稿的范文，按行朗读录音</div>
      </div>
      {scripts.length === 0 ? (
        <div className="rec-empty">还没有已定稿的范文。请管理员先在「范文管理」里定稿。</div>
      ) : (
        <div className="rec-pick-list">
          {scripts.map((s) => (
            <Link key={s.id} to={`/record?script=${s.id}`} className="rec-pick-item">
              <div className="rp-title">{s.title}</div>
              <div className="rp-meta">
                <span className="tag">{LANG_LABEL[s.language] ?? s.language}</span>
                <span className="tag">{CAT_LABEL[s.content_category]}</span>
                <span className="rp-lines">{s.line_count} 行</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

// ---- 声音昵称: 录音前必填, 可修改 ----
function SpeakerBar({ rec, onSaved }: { rec: Recording; onSaved: () => void }) {
  const [editing, setEditing] = useState(!rec.speaker)
  const [val, setVal] = useState(rec.speaker ?? '')
  const mut = useMutation({
    mutationFn: () => setRecordingSpeaker(rec.id, val.trim()),
    onSuccess: () => {
      setEditing(false)
      onSaved()
    },
  })

  if (!editing) {
    return (
      <div className="rec-speaker">
        声音：<b>{rec.speaker}</b>
        <button className="sp-edit" onClick={() => setEditing(true)}>
          修改
        </button>
      </div>
    )
  }
  return (
    <div className="rec-speaker need">
      <label>
        声音昵称 <span className="req">*</span>
      </label>
      <input
        className="sp-input"
        value={val}
        placeholder="例如：평양남성1"
        onChange={(e) => setVal(e.target.value)}
      />
      <button
        className="sp-save"
        disabled={!val.trim() || mut.isPending}
        onClick={() => mut.mutate()}
      >
        {mut.isPending ? '保存中…' : '保存'}
      </button>
      {!rec.speaker && (
        <span className="sp-hint">训练按它分组，同一个声音请填一致；填写后才能开始录音</span>
      )}
      {mut.isError && <span className="sp-hint err">{(mut.error as Error).message}</span>}
    </div>
  )
}

// ==================== 录音会话 ====================
function Session({ scriptId }: { scriptId: string }) {
  const queryClient = useQueryClient()
  const { data: rec, isLoading, isError, error } = useQuery({
    queryKey: ['rec-sample', scriptId],
    queryFn: () => startRecordingFromScript(scriptId),
  })
  const recId = rec?.id

  const { data: segData } = useQuery({
    queryKey: ['rec-segments', recId],
    queryFn: () => listSegments({ recordingId: recId as string }),
    enabled: !!recId,
    refetchInterval: (q) => {
      const d = q.state.data
      const active = d?.some(
        (s) => s.status === 'pending_transcription' || s.status === 'transcribing',
      )
      return active ? 2500 : false
    },
  })

  const recorder = useRecorder()
  const [activeSeg, setActiveSeg] = useState<string | null>(null)
  const [busySeg, setBusySeg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['rec-segments', recId] })

  const passMut = useMutation({ mutationFn: passLine, onSuccess: invalidate })
  const redoMut = useMutation({ mutationFn: rerecordLine, onSuccess: invalidate })

  async function startRec(seg: Segment) {
    setErr(null)
    try {
      setActiveSeg(seg.id)
      await recorder.start()
    } catch (e) {
      setActiveSeg(null)
      setErr('无法访问麦克风：' + (e as Error).message)
    }
  }

  async function stopAndUpload(seg: Segment) {
    const { blob, durationMs } = await recorder.stop()
    setActiveSeg(null)
    setBusySeg(seg.id)
    try {
      const { url } = await getLineRecordUrl(seg.id)
      const file = new File([blob], 'clip', { type: blob.type || 'audio/webm' })
      await uploadFileToR2(url, file)
      await completeLineRecording(seg.id, durationMs)
      await invalidate()
    } catch (e) {
      setErr('上传失败：' + (e as Error).message)
    } finally {
      setBusySeg(null)
    }
  }

  if (isLoading) return <div className="recpage muted">准备录音…</div>
  if (isError) return <div className="recpage error">{(error as Error).message}</div>

  const rows = [...(segData ?? [])].sort((a, b) => a.segment_index - b.segment_index)
  const passed = rows.filter((s) => s.status === 'approved').length
  const needSpeaker = !rec?.speaker // 未填声音昵称前不能录
  const recordingElsewhere = activeSeg !== null || busySeg !== null || needSpeaker

  return (
    <div className="recpage">
      <div className="rec-head">
        <Link to="/record" className="back">
          ← 重新选择范文
        </Link>
        <h1>
          <span className="bar" />
          {rec?.title || '录音'}
        </h1>
        <div className="sub">
          已通过 {passed} / {rows.length} 行 · 一行一行朗读，识别不一致处会标红
        </div>
        {rec && (
          <SpeakerBar
            rec={rec}
            onSaved={() => queryClient.invalidateQueries({ queryKey: ['rec-sample', scriptId] })}
          />
        )}
      </div>

      {needSpeaker && <p className="rec-err">请先填写「声音昵称」，填写后才能开始录音。</p>}
      {err && <p className="rec-err">{err}</p>}

      <ol className="rec-list">
        {rows.map((seg) => {
          const isActive = activeSeg === seg.id
          const isBusy = busySeg === seg.id
          const transcribing =
            seg.status === 'pending_transcription' || seg.status === 'transcribing'
          const hasAudio =
            seg.status === 'approved' ||
            seg.status === 'pending_review' ||
            seg.status === 'transcription_failed' ||
            transcribing
          const words = diffExpected(
            seg.text ?? '',
            seg.status === 'pending_review' || seg.status === 'approved' ? seg.asr_text : null,
          )

          return (
            <li key={seg.id} className="rec-row">
              <div className="rec-main">
                {/* 录音/停止按钮 */}
                {isActive ? (
                  <button className="rec-btn stop" onClick={() => stopAndUpload(seg)}>
                    <span className="sq" /> 停止
                  </button>
                ) : isBusy ? (
                  <button className="rec-btn" disabled>
                    上传中…
                  </button>
                ) : transcribing ? (
                  <button className="rec-btn" disabled>
                    识别中…
                  </button>
                ) : seg.status === 'pending_recording' ? (
                  <button
                    className="rec-btn"
                    disabled={recordingElsewhere}
                    onClick={() => startRec(seg)}
                  >
                    <span className="dot" /> 录音
                  </button>
                ) : (
                  <span className="rec-idx-done">#{seg.segment_index + 1}</span>
                )}

                {/* 波形区 */}
                <div className="rec-wave">
                  {isActive ? (
                    <LiveWave analyser={recorder.analyser} />
                  ) : hasAudio ? (
                    <ClipPlayer segmentId={seg.id} />
                  ) : (
                    <span className="wave-ph">
                      {isBusy ? '上传中…' : '点左侧「录音」开始朗读这一行'}
                    </span>
                  )}
                </div>

                {/* 状态按钮 */}
                <div className="rec-status">
                  {seg.status === 'approved' && (
                    <>
                      <span className="st-pass">✓ 通过</span>
                      <button
                        className="st-redo"
                        disabled={recordingElsewhere}
                        onClick={() => redoMut.mutate(seg.id)}
                      >
                        重录
                      </button>
                    </>
                  )}
                  {seg.status === 'pending_review' && (
                    <>
                      <button
                        className="st-force"
                        disabled={recordingElsewhere || passMut.isPending}
                        onClick={() => passMut.mutate(seg.id)}
                      >
                        通过
                      </button>
                      <button
                        className="st-redo"
                        disabled={recordingElsewhere}
                        onClick={() => redoMut.mutate(seg.id)}
                      >
                        重录
                      </button>
                    </>
                  )}
                  {seg.status === 'transcription_failed' && (
                    <button
                      className="st-redo"
                      disabled={recordingElsewhere}
                      onClick={() => redoMut.mutate(seg.id)}
                    >
                      识别失败 · 重录
                    </button>
                  )}
                </div>
              </div>

              {/* 范文文字(只读): 识别不一致的词标红 */}
              <div className="rec-text">
                {words.map((w, i) => (
                  <span key={i} className={w.ok ? '' : 'w-bad'}>
                    {w.word}{' '}
                  </span>
                ))}
              </div>
              {seg.status === 'pending_review' && seg.asr_text && (
                <div className="rec-asr">识别：{seg.asr_text}</div>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
