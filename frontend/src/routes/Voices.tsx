import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  deleteVoiceModel,
  getVoiceTTSStatus,
  listVoices,
  synthVoice,
} from '../lib/endpoints'

type Voice = {
  exp: string
  sovits: string[]
  gpt: string[]
  epoch: number
  bestSovits: string
  bestGpt: string
}
type SynthState = { status: 'running' | 'success' | 'error'; audioUrl?: string; err?: string }

const LANGS: { value: string; label: string }[] = [
  { value: 'zh', label: '普通话' },
  { value: 'ko', label: '朝鲜语' },
  { value: 'en', label: '英语' },
]

// 对比的引擎: 微调(GPT-SoVITS 用该音色权重) vs 零样本(CosyVoice2 用同一条训练参考)
const ENGINE_LABEL: Record<string, string> = {
  sovits: 'GPT-SoVITS 微调',
  cosyvoice: 'CosyVoice2 零样本',
}

// 从权重文件名还原音色名(exp): <exp>_e8_s200.pth 或 <exp>-e15.ckpt
function expOf(path: string): string {
  const b = path.split('/').pop() || ''
  const m = b.match(/^(.+?)[-_]e\d+/)
  return m ? m[1] : b.replace(/\.(pth|ckpt)$/, '')
}
function epochOf(path: string): number {
  const m = (path.split('/').pop() || '').match(/[-_]e(\d+)/)
  return m ? parseInt(m[1], 10) : 0
}
const highestEpoch = (arr: string[]) =>
  [...arr].sort((a, b) => epochOf(a) - epochOf(b)).at(-1) || ''

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export function Voices() {
  const qc = useQueryClient()
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['voices'],
    queryFn: listVoices,
  })
  const [delTarget, setDelTarget] = useState<string | null>(null)
  const del = useMutation({
    mutationFn: (exp: string) => deleteVoiceModel(exp),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['voices'] })
      setDelTarget(null)
    },
  })

  // 试听 / 对比
  const [text, setText] = useState('神爱世人，甚至将他的独生子赐给他们。')
  const [language, setLanguage] = useState('zh')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [compareCosy, setCompareCosy] = useState(true) // 同时用 CosyVoice2 零样本对比
  // 结果按 `${exp}::${engine}` 存
  const [results, setResults] = useState<Record<string, SynthState>>({})
  const [synthing, setSynthing] = useState(false)

  const voices = useMemo<Voice[]>(() => {
    const map: Record<string, { sovits: string[]; gpt: string[] }> = {}
    for (const w of data?.sovits ?? []) {
      const e = expOf(w)
      ;(map[e] ??= { sovits: [], gpt: [] }).sovits.push(w)
    }
    for (const w of data?.gpt ?? []) {
      const e = expOf(w)
      ;(map[e] ??= { sovits: [], gpt: [] }).gpt.push(w)
    }
    return Object.entries(map)
      .map(([exp, v]) => ({
        exp,
        sovits: v.sovits,
        gpt: v.gpt,
        epoch: Math.max(0, ...v.sovits.map(epochOf), ...v.gpt.map(epochOf)),
        bestSovits: highestEpoch(v.sovits),
        bestGpt: highestEpoch(v.gpt),
      }))
      .sort((a, b) => a.exp.localeCompare(b.exp))
  }, [data])

  const usable = (v: Voice) => !!(v.bestSovits && v.bestGpt)
  const toggle = (exp: string) =>
    setSelected((s) => {
      const n = new Set(s)
      n.has(exp) ? n.delete(exp) : n.add(exp)
      return n
    })

  async function runOne(v: Voice, engine: string) {
    const key = `${v.exp}::${engine}`
    try {
      const { task_id } = await synthVoice(text.trim(), v.bestSovits, v.bestGpt, language, engine)
      for (let i = 0; i < 90; i++) {
        await sleep(2000)
        const st = await getVoiceTTSStatus(task_id)
        if (st.status === 'success') {
          setResults((r) => ({ ...r, [key]: { status: 'success', audioUrl: st.audio_url || undefined } }))
          return
        }
        if (st.status === 'error') {
          setResults((r) => ({ ...r, [key]: { status: 'error', err: st.message || '合成失败' } }))
          return
        }
      }
      setResults((r) => ({ ...r, [key]: { status: 'error', err: '超时' } }))
    } catch (e) {
      setResults((r) => ({ ...r, [key]: { status: 'error', err: (e as Error).message } }))
    }
  }

  async function runCompare() {
    const picks = voices.filter((v) => selected.has(v.exp) && usable(v))
    if (!text.trim() || picks.length === 0 || synthing) return
    const engines = compareCosy ? ['sovits', 'cosyvoice'] : ['sovits']
    setSynthing(true)
    const init: Record<string, SynthState> = {}
    for (const v of picks) for (const e of engines) init[`${v.exp}::${e}`] = { status: 'running' }
    setResults(init)
    await Promise.all(picks.flatMap((v) => engines.map((e) => runOne(v, e))))
    setSynthing(false)
  }

  if (isLoading) return <div className="coll muted">加载中…</div>
  if (isError) return <div className="coll error">{(error as Error).message}</div>

  const selectedUsable = voices.filter((v) => selected.has(v.exp) && usable(v))

  return (
    <div className="coll">
      <div className="coll-head">
        <div>
          <h1>
            <span className="bar" />
            声音管理
          </h1>
          <div className="sub">
            管理训练好的音色；勾选一个或多个，在下方输入文本，用它们朗读并对比效果。
          </div>
        </div>
      </div>

      <section className="coll-card">
        <table>
          <thead>
            <tr>
              <th style={{ width: 40, textAlign: 'center' }}>选</th>
              <th>音色名</th>
              <th>权重</th>
              <th>最高 epoch</th>
              <th>状态</th>
              <th style={{ textAlign: 'center' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {voices.map((v) => (
              <tr key={v.exp}>
                <td style={{ textAlign: 'center' }}>
                  <input
                    type="checkbox"
                    checked={selected.has(v.exp)}
                    disabled={!usable(v)}
                    onChange={() => toggle(v.exp)}
                  />
                </td>
                <td>
                  <b>{v.exp}</b>
                </td>
                <td>
                  SoVITS {v.sovits.length} · GPT {v.gpt.length}
                </td>
                <td>e{v.epoch}</td>
                <td>
                  {usable(v) ? (
                    <span style={{ color: '#16a06a' }}>✓ 可用</span>
                  ) : (
                    <span style={{ color: '#c0392b' }}>缺权重</span>
                  )}
                </td>
                <td style={{ textAlign: 'center' }}>
                  <button className="act del" onClick={() => setDelTarget(v.exp)}>
                    删除
                  </button>
                </td>
              </tr>
            ))}
            {voices.length === 0 && (
              <tr>
                <td colSpan={6}>
                  <div className="coll-empty">
                    还没有训练好的音色。去「校对」页用「训练音色」训练一个。
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="coll-foot">共 {voices.length} 个音色</div>
      </section>

      {/* 试听 / 对比 */}
      <section
        className="coll-card"
        style={{ marginTop: '1.2rem', padding: '1.1rem 1.3rem' }}
      >
        <h3 style={{ margin: '0 0 0.2rem', fontSize: '1.02rem' }}>试听 / 对比</h3>
        <div style={{ fontSize: '0.82rem', color: 'var(--text-tertiary, #888)', marginBottom: '0.7rem' }}>
          勾选上方一个或多个音色，输入文本，点「合成对比」，各音色读同一段文本并排出结果。
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="输入要朗读的文本…"
          style={{
            width: '100%', minHeight: 72, boxSizing: 'border-box', resize: 'vertical',
            padding: '0.7rem 0.85rem', borderRadius: 10, fontSize: '1rem',
            border: '1px solid var(--border-default, #e8e0d4)', background: 'var(--bg-card, #fff)',
            color: 'var(--text-primary, #333)',
          }}
        />
        <div style={{ display: 'flex', gap: '0.7rem', alignItems: 'center', flexWrap: 'wrap', marginTop: '0.7rem' }}>
          <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary, #555)' }}>
            语言{' '}
            <select value={language} onChange={(e) => setLanguage(e.target.value)} style={{ padding: '0.3rem 0.5rem', borderRadius: 6 }}>
              {LANGS.map((l) => (
                <option key={l.value} value={l.value}>{l.label}</option>
              ))}
            </select>
          </label>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.85rem', color: 'var(--text-secondary, #555)', cursor: 'pointer' }}>
            <input type="checkbox" checked={compareCosy} onChange={(e) => setCompareCosy(e.target.checked)} style={{ margin: 0 }} />
            同时用 CosyVoice2 零样本对比
          </label>
          <span style={{ fontSize: '0.82rem', color: 'var(--text-tertiary, #888)' }}>
            已选 {selectedUsable.length} 个音色
          </span>
          <button
            onClick={runCompare}
            disabled={synthing || !text.trim() || selectedUsable.length === 0}
            style={{
              marginLeft: 'auto', padding: '0.5rem 1.2rem', borderRadius: 8, border: 'none',
              fontSize: '0.9rem', fontWeight: 600, cursor: synthing || selectedUsable.length === 0 ? 'not-allowed' : 'pointer',
              background: synthing || !text.trim() || selectedUsable.length === 0 ? 'var(--border, #ccc)' : 'var(--accent, #d97706)',
              color: '#fff',
            }}
          >
            {synthing ? '合成中…' : `合成对比（${selectedUsable.length}）`}
          </button>
        </div>

        {Object.keys(results).length > 0 && (
          <div style={{ marginTop: '1rem', display: 'flex', flexDirection: 'column', gap: '0.7rem' }}>
            {voices
              .filter((v) => ['sovits', 'cosyvoice'].some((e) => results[`${v.exp}::${e}`]))
              .map((v) => (
                <div
                  key={v.exp}
                  style={{
                    border: '1px solid var(--border-default, #e8e0d4)', borderRadius: 10,
                    padding: '0.7rem 0.9rem', background: 'var(--bg-card, #faf8f4)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: 8 }}>
                    <b style={{ fontSize: '0.95rem' }}>{v.exp}</b>
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary, #999)' }}>e{v.epoch}</span>
                  </div>
                  <div style={{ display: 'flex', gap: '0.7rem', flexWrap: 'wrap' }}>
                    {['sovits', 'cosyvoice']
                      .filter((e) => results[`${v.exp}::${e}`])
                      .map((e) => {
                        const r = results[`${v.exp}::${e}`]
                        return (
                          <div key={e} style={{ flex: '1 1 300px', minWidth: 0 }}>
                            <div style={{ fontSize: '0.78rem', fontWeight: 600, color: e === 'cosyvoice' ? '#2f9bbf' : 'var(--accent, #d97706)', marginBottom: 4 }}>
                              {ENGINE_LABEL[e]}
                              {r.status === 'running' && <span style={{ fontWeight: 400, color: 'var(--text-tertiary,#999)' }}> · 合成中…</span>}
                              {r.status === 'error' && <span style={{ fontWeight: 400, color: '#c0392b' }}> · 失败：{r.err}</span>}
                            </div>
                            {r.audioUrl && <audio controls src={r.audioUrl} style={{ width: '100%', height: 36 }} />}
                          </div>
                        )
                      })}
                  </div>
                </div>
              ))}
          </div>
        )}
      </section>

      {delTarget && (
        <div
          className="modal-overlay"
          onClick={() => !del.isPending && setDelTarget(null)}
        >
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>删除音色「{delTarget}」？</h2>
            <p>
              将永久删除该音色在 GPU 上的<strong>权重、训练缓存与数据</strong>，
              合成页将不再能选用它。
              <br />
              此操作<strong>不可撤销</strong>（日后可重新训练）。
            </p>
            {del.isError && <p className="error">{(del.error as Error).message}</p>}
            <div className="modal-actions">
              <button
                className="btn-secondary"
                disabled={del.isPending}
                onClick={() => setDelTarget(null)}
              >
                取消
              </button>
              <button
                className="btn-danger"
                disabled={del.isPending}
                onClick={() => del.mutate(delTarget)}
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
