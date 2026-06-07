import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getScript, saveScriptLines, updateScript } from '../lib/endpoints'
import type { ContentCategory, Language } from '../lib/types'

const CATEGORIES: { value: ContentCategory; label: string }[] = [
  { value: 'sermon', label: '播音' },
  { value: 'bible_reading', label: '演讲' },
  { value: 'hymn', label: '朗诵' },
]
const LANGUAGES: { value: Language; label: string }[] = [
  { value: 'en', label: '英语' },
  { value: 'zh', label: '普通话' },
  { value: 'ko', label: '朝鲜语' },
]

interface LocalLine {
  key: number
  text: string
}

const PAGE_SIZE = 15

export function ScriptDetail() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['script', id],
    queryFn: () => getScript(id),
  })

  // --- 本地可编辑状态 ---
  const [title, setTitle] = useState('')
  const [language, setLanguage] = useState<Language>('ko')
  const [category, setCategory] = useState<ContentCategory>('sermon')
  const [notes, setNotes] = useState('')
  const [lines, setLines] = useState<LocalLine[]>([])

  const keyCounter = useRef(0)
  const syncedAt = useRef<string | null>(null)
  const taRefs = useRef<Record<number, HTMLTextAreaElement | null>>({})
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [page, setPage] = useState(0)

  // 服务端数据到达 / 保存后(updated_at 变化)时, 重置本地状态。
  // updated_at 不变的后台 refetch 不会清掉正在编辑的内容。
  useEffect(() => {
    if (!data) return
    if (syncedAt.current === data.updated_at) return
    syncedAt.current = data.updated_at
    setTitle(data.title)
    setLanguage(data.language)
    setCategory(data.content_category)
    setNotes(data.notes ?? '')
    setLines(data.lines.map((l) => ({ key: keyCounter.current++, text: l.text })))
  }, [data])

  const saveAttrsMut = useMutation({
    mutationFn: () =>
      updateScript(id, {
        title: title.trim(),
        language,
        content_category: category,
        notes,
      }),
    onSuccess: (fresh) => queryClient.setQueryData(['script', id], fresh),
  })

  const saveLinesMut = useMutation({
    mutationFn: () => saveScriptLines(id, lines.map((l) => l.text).filter((t) => t.trim().length > 0)),
    onSuccess: (fresh) => {
      queryClient.setQueryData(['script', id], fresh)
      queryClient.invalidateQueries({ queryKey: ['scripts'] })
    },
  })

  const statusMut = useMutation({
    mutationFn: (status: 'draft' | 'finalized') => updateScript(id, { status }),
    onSuccess: (fresh) => {
      queryClient.setQueryData(['script', id], fresh)
      queryClient.invalidateQueries({ queryKey: ['scripts'] })
    },
  })

  if (isLoading) return <div className="sdetail muted">加载中…</div>
  if (isError) return <div className="sdetail error">{(error as Error).message}</div>
  if (!data) return null

  const attrsDirty =
    title.trim() !== data.title ||
    language !== data.language ||
    category !== data.content_category ||
    notes !== (data.notes ?? '')
  const linesDirty =
    JSON.stringify(lines.map((l) => l.text)) !== JSON.stringify(data.lines.map((l) => l.text))
  const busy = saveAttrsMut.isPending || saveLinesMut.isPending || statusMut.isPending

  // --- 分页 (每页 15 行) ---
  const pageCount = Math.max(1, Math.ceil(lines.length / PAGE_SIZE))
  const safePage = Math.min(page, pageCount - 1)
  const pageStart = safePage * PAGE_SIZE
  const pageLines = lines.slice(pageStart, pageStart + PAGE_SIZE)

  // --- 行操作 ---
  function setLineText(i: number, text: string) {
    setLines((ls) => ls.map((l, j) => (j === i ? { ...l, text } : l)))
  }
  function deleteLine(i: number) {
    setLines((ls) => ls.filter((_, j) => j !== i))
  }
  function addLine(at?: number) {
    const insertAt = at ?? lines.length
    setLines((ls) => {
      const next = [...ls]
      next.splice(at ?? ls.length, 0, { key: keyCounter.current++, text: '' })
      return next
    })
    setPage(Math.floor(insertAt / PAGE_SIZE)) // 跳到新行所在页
  }
  function mergeUp(i: number) {
    if (i === 0) return
    setLines((ls) => {
      const next = [...ls]
      next[i - 1] = {
        ...next[i - 1],
        text: `${next[i - 1].text.trim()} ${next[i].text.trim()}`.trim(),
      }
      next.splice(i, 1)
      return next
    })
  }
  function splitAtCaret(i: number) {
    const ta = taRefs.current[lines[i].key]
    const pos = ta ? ta.selectionStart : lines[i].text.length
    setLines((ls) => {
      const next = [...ls]
      const t = next[i].text
      next[i] = { ...next[i], text: t.slice(0, pos).trim() }
      next.splice(i + 1, 0, { key: keyCounter.current++, text: t.slice(pos).trim() })
      return next
    })
  }
  function move(i: number, dir: -1 | 1) {
    const j = i + dir
    if (j < 0 || j >= lines.length) return
    setLines((ls) => {
      const next = [...ls]
      ;[next[i], next[j]] = [next[j], next[i]]
      return next
    })
    setPage(Math.floor(j / PAGE_SIZE)) // 跨页移动时视图跟随
  }
  // 拖拽重排
  function onDragOverRow(i: number) {
    if (dragIndex === null || dragIndex === i) return
    setLines((ls) => {
      const next = [...ls]
      const [moved] = next.splice(dragIndex, 1)
      next.splice(i, 0, moved)
      return next
    })
    setDragIndex(i)
  }

  const isFinalized = data.status === 'finalized'

  return (
    <div className="sdetail">
      <div className="sdetail-top">
        <Link to="/scripts" className="back">
          ← 返回范文列表
        </Link>
        <span className={`sd-status ${isFinalized ? 'final' : 'draft'}`}>
          {isFinalized ? '已定稿' : '草稿'}
        </span>
      </div>

      {/* 属性卡 */}
      <section className="sd-card">
        <div className="sd-attrs">
          <div className="f grow">
            <label>标题</label>
            <input
              className="ctl"
              value={title}
              disabled={busy}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div className="f">
            <label>语种</label>
            <div className="pills">
              {LANGUAGES.map((l) => (
                <button
                  key={l.value}
                  type="button"
                  className={`pill ${language === l.value ? 'on' : ''}`}
                  disabled={busy}
                  onClick={() => setLanguage(l.value)}
                >
                  {l.label}
                </button>
              ))}
            </div>
          </div>
          <div className="f">
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
        </div>
        <div className="f">
          <label>备注（可选）</label>
          <textarea
            className="ctl"
            rows={2}
            value={notes}
            disabled={busy}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
        <div className="sd-attrs-foot">
          <button
            className={`btn ${isFinalized ? 'btn-secondary' : ''}`}
            disabled={busy}
            onClick={() => statusMut.mutate(isFinalized ? 'draft' : 'finalized')}
          >
            {isFinalized ? '撤回定稿' : '定稿（供采集）'}
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !attrsDirty}
            onClick={() => saveAttrsMut.mutate()}
          >
            {saveAttrsMut.isPending ? '保存中…' : '保存属性'}
          </button>
        </div>
        {saveAttrsMut.isError && <p className="err">{(saveAttrsMut.error as Error).message}</p>}
        {statusMut.isError && <p className="err">{(statusMut.error as Error).message}</p>}
      </section>

      {/* 行编辑卡 */}
      <section className="sd-card">
        <div className="sd-lines-head">
          <h2>切分（{lines.length} 行）</h2>
          <div className="sd-lines-tools">
            <button className="btn btn-secondary" disabled={busy} onClick={() => addLine()}>
              + 末尾加行
            </button>
            <button
              className="btn btn-primary"
              disabled={busy || !linesDirty}
              onClick={() => saveLinesMut.mutate()}
            >
              {saveLinesMut.isPending ? '保存中…' : linesDirty ? '保存行（未保存）' : '保存行'}
            </button>
          </div>
        </div>
        {saveLinesMut.isError && <p className="err">{(saveLinesMut.error as Error).message}</p>}

        <ol className="sd-lines" start={pageStart + 1}>
          {pageLines.map((l, localI) => {
            const i = pageStart + localI
            return (
            <li
              key={l.key}
              className={`sd-line ${dragIndex === i ? 'dragging' : ''}`}
              onDragOver={(e) => {
                e.preventDefault()
                onDragOverRow(i)
              }}
            >
              <span
                className="grip"
                draggable
                title="拖拽重排"
                onDragStart={() => setDragIndex(i)}
                onDragEnd={() => setDragIndex(null)}
              >
                ⠿
              </span>
              <span className="ln">{i + 1}</span>
              <textarea
                ref={(el) => {
                  taRefs.current[l.key] = el
                }}
                className="line-ta"
                rows={1}
                value={l.text}
                disabled={busy}
                placeholder="（空行）"
                onChange={(e) => setLineText(i, e.target.value)}
              />
              <div className="line-ops">
                <button title="上移" disabled={busy || i === 0} onClick={() => move(i, -1)}>
                  ▲
                </button>
                <button
                  title="下移"
                  disabled={busy || i === lines.length - 1}
                  onClick={() => move(i, 1)}
                >
                  ▼
                </button>
                <button title="在光标处拆成两行" disabled={busy} onClick={() => splitAtCaret(i)}>
                  拆
                </button>
                <button title="并入上一行" disabled={busy || i === 0} onClick={() => mergeUp(i)}>
                  合
                </button>
                <button title="在此行下方插入" disabled={busy} onClick={() => addLine(i + 1)}>
                  ＋
                </button>
                <button className="del" title="删除此行" disabled={busy} onClick={() => deleteLine(i)}>
                  ✕
                </button>
              </div>
            </li>
            )
          })}
        </ol>

        {pageCount > 1 && (
          <div className="sd-pager">
            <button disabled={safePage === 0} onClick={() => setPage(0)} title="第一页">
              «
            </button>
            <button disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>
              ‹ 上一页
            </button>
            <span className="sd-pageinfo">
              第 {safePage + 1} / {pageCount} 页
            </span>
            <button disabled={safePage >= pageCount - 1} onClick={() => setPage(safePage + 1)}>
              下一页 ›
            </button>
            <button
              disabled={safePage >= pageCount - 1}
              onClick={() => setPage(pageCount - 1)}
              title="最后一页"
            >
              »
            </button>
          </div>
        )}

        {lines.length === 0 && (
          <div className="sd-empty">
            没有任何行。点「末尾加行」手动添加，或返回重新上传 Word。
          </div>
        )}
      </section>
    </div>
  )
}
