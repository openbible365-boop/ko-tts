import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteVoiceModel, listVoices } from '../lib/endpoints'

type Voice = { exp: string; sovits: string[]; gpt: string[]; epoch: number }

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

  // 权重按音色(exp)分组
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
      }))
      .sort((a, b) => a.exp.localeCompare(b.exp))
  }, [data])

  if (isLoading) return <div className="coll muted">加载中…</div>
  if (isError) return <div className="coll error">{(error as Error).message}</div>

  return (
    <div className="coll">
      <div className="coll-head">
        <div>
          <h1>
            <span className="bar" />
            声音管理
          </h1>
          <div className="sub">
            管理已训练好的音色（在 GPU 上）。「可用」的音色会出现在合成页的「微调音色」里。
          </div>
        </div>
      </div>

      <section className="coll-card">
        <table>
          <thead>
            <tr>
              <th>音色名</th>
              <th>权重</th>
              <th>最高 epoch</th>
              <th>状态</th>
              <th style={{ textAlign: 'center' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {voices.map((v) => {
              const usable = v.sovits.length > 0 && v.gpt.length > 0
              return (
                <tr key={v.exp}>
                  <td>
                    <b>{v.exp}</b>
                  </td>
                  <td>
                    SoVITS {v.sovits.length} · GPT {v.gpt.length}
                  </td>
                  <td>e{v.epoch}</td>
                  <td>
                    {usable ? (
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
              )
            })}
            {voices.length === 0 && (
              <tr>
                <td colSpan={5}>
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
