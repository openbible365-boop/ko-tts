import { useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteScript, listScripts, uploadScript } from '../lib/endpoints'
import type { ContentCategory, Language, Script, ScriptStatus } from '../lib/types'

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
const CAT_LABEL = Object.fromEntries(CATEGORIES.map((c) => [c.value, c.label])) as Record<
  ContentCategory,
  string
>
const LANG_LABEL = Object.fromEntries(LANGUAGES.map((l) => [l.value, l.label])) as Record<
  Language,
  string
>
const STATUS_LABEL: Record<ScriptStatus, string> = { draft: '草稿', finalized: '已定稿' }

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString()
}

export function Scripts() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [showUpload, setShowUpload] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<Script | null>(null)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['scripts'],
    queryFn: () => listScripts(),
  })

  const delMut = useMutation({
    mutationFn: (id: string) => deleteScript(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scripts'] })
      setPendingDelete(null)
    },
  })

  if (isLoading) return <div className="umgmt muted">加载中…</div>
  if (isError) return <div className="umgmt error">{(error as Error).message}</div>

  const all = data ?? []
  const stats = {
    total: all.length,
    finalized: all.filter((s) => s.status === 'finalized').length,
    draft: all.filter((s) => s.status === 'draft').length,
  }

  return (
    <div className="umgmt smgmt">
      <div className="umgmt-head">
        <div>
          <h1>
            <span className="bar" />
            范文管理
          </h1>
          <div className="sub">上传 Word 范文，自动拆分成待录脚本，编辑定稿后供采集</div>
        </div>
        <button className="btn" onClick={() => setShowUpload(true)}>
          + 上传范文
        </button>
      </div>

      <section className="umgmt-stats">
        <div className="umgmt-stat">
          <div className="ico a">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <path d="M14 2v6h6" />
            </svg>
          </div>
          <div>
            <div className="n">{stats.total}</div>
            <div className="l">范文总数</div>
          </div>
        </div>
        <div className="umgmt-stat">
          <div className="ico b">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="9" />
              <path d="m9 12 2 2 4-4" />
            </svg>
          </div>
          <div>
            <div className="n">{stats.finalized}</div>
            <div className="l">已定稿</div>
          </div>
        </div>
        <div className="umgmt-stat">
          <div className="ico c">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
              <path d="M12 20h9" />
              <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
            </svg>
          </div>
          <div>
            <div className="n">{stats.draft}</div>
            <div className="l">草稿</div>
          </div>
        </div>
      </section>

      {delMut.isError && <p className="umgmt-err">{(delMut.error as Error).message}</p>}

      <section className="umgmt-card">
        <table>
          <thead>
            <tr>
              <th>标题</th>
              <th>语种</th>
              <th>类别</th>
              <th>行数</th>
              <th>状态</th>
              <th>创建时间</th>
              <th style={{ textAlign: 'right' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {all.map((s) => (
              <tr key={s.id}>
                <td>
                  <button className="smgmt-title" onClick={() => navigate(`/scripts/${s.id}`)}>
                    {s.title}
                  </button>
                </td>
                <td>{LANG_LABEL[s.language] ?? s.language}</td>
                <td>{CAT_LABEL[s.content_category] ?? s.content_category}</td>
                <td>{s.line_count}</td>
                <td>
                  <span className={`ubadge ${s.status === 'finalized' ? 'on' : 'off'}`}>
                    <span className="dot" />
                    {STATUS_LABEL[s.status]}
                  </span>
                </td>
                <td>
                  <span className="date">{fmtDate(s.created_at)}</span>
                </td>
                <td>
                  <div className="actions">
                    <button className="act" onClick={() => navigate(`/scripts/${s.id}`)}>
                      查看
                    </button>
                    <button
                      className="act disable"
                      disabled={delMut.isPending}
                      onClick={() => setPendingDelete(s)}
                    >
                      删除
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {all.length === 0 && (
              <tr>
                <td colSpan={7}>
                  <div className="umgmt-empty">还没有范文，点右上角「上传范文」开始。</div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="umgmt-foot">共 {all.length} 篇范文</div>
      </section>

      {showUpload && (
        <UploadModal
          onClose={() => setShowUpload(false)}
          onDone={() => {
            queryClient.invalidateQueries({ queryKey: ['scripts'] })
            setShowUpload(false)
          }}
        />
      )}

      {pendingDelete && (
        <div className="modal-overlay" onClick={() => !delMut.isPending && setPendingDelete(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>删除范文</h2>
            <p>
              确定删除范文「{pendingDelete.title}」及其 {pendingDelete.line_count} 行内容吗？此操作不可撤销。
            </p>
            <div className="modal-actions">
              <button
                className="btn btn-secondary"
                disabled={delMut.isPending}
                onClick={() => setPendingDelete(null)}
              >
                取消
              </button>
              <button
                className="btn btn-danger"
                disabled={delMut.isPending}
                onClick={() => delMut.mutate(pendingDelete.id)}
              >
                {delMut.isPending ? '删除中…' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function UploadModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [title, setTitle] = useState('')
  const [language, setLanguage] = useState<Language>('ko')
  const [category, setCategory] = useState<ContentCategory>('sermon')
  const [notes, setNotes] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [formErr, setFormErr] = useState<string | null>(null)

  const mut = useMutation({
    mutationFn: () =>
      uploadScript({ file: file as File, title: title.trim(), language, content_category: category, notes }),
    onSuccess: onDone,
  })

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setFormErr(null)
    if (!title.trim()) return setFormErr('请填写标题')
    if (!file) return setFormErr('请选择 .docx 文件')
    if (!file.name.toLowerCase().endsWith('.docx')) return setFormErr('只支持 .docx 格式')
    mut.mutate()
  }

  const busy = mut.isPending

  return (
    <div className="modal-overlay" onClick={() => !busy && onClose()}>
      <div className="modal smgmt-modal" onClick={(e) => e.stopPropagation()}>
        <h2>上传范文</h2>
        <form className="smgmt-form" onSubmit={handleSubmit}>
          <div className="f">
            <label>标题</label>
            <input
              className="ctl"
              type="text"
              value={title}
              placeholder="例如：주일 설교 1"
              disabled={busy}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>

          <div className="row2">
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
              value={notes}
              rows={2}
              disabled={busy}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>

          <div className="f">
            <label>Word 文件（.docx）</label>
            <label className={`drop ${file ? 'has' : ''}`}>
              <input
                type="file"
                accept=".docx"
                hidden
                disabled={busy}
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
              {file ? (
                <span className="fn">{file.name}</span>
              ) : (
                <span>点击选择 .docx 文件（每段落一行，自动拆分）</span>
              )}
            </label>
          </div>

          {formErr && <p className="err">{formErr}</p>}
          {mut.isError && <p className="err">{(mut.error as Error).message}</p>}

          <div className="modal-actions">
            <button className="btn btn-secondary" type="button" disabled={busy} onClick={onClose}>
              取消
            </button>
            <button className="btn" type="submit" disabled={busy}>
              {busy ? '上传解析中…' : '上传'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
