import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthContext'
import { listUsers, updateUser } from '../lib/endpoints'
import type { UserRole } from '../lib/types'

const ROLE_LABEL: Record<UserRole, string> = {
  admin: '管理员',
  reviewer: '审核员',
  contributor: '采集员',
}
const ROLES: UserRole[] = ['contributor', 'reviewer', 'admin']

// 头像渐变: 由名字哈希挑一对色, 同一个人稳定。
const AVATAR_GRADIENTS: [string, string][] = [
  ['#3a64ff', '#7aa0ff'],
  ['#7a4ddb', '#a87aff'],
  ['#16a06a', '#43c98c'],
  ['#e07a2f', '#f5a960'],
  ['#e0568a', '#ff8ab3'],
  ['#2f9bbf', '#5bc4e0'],
]

function gradient(seed: string): string {
  let h = 0
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  const [a, b] = AVATAR_GRADIENTS[h % AVATAR_GRADIENTS.length]
  return `linear-gradient(140deg, ${a}, ${b})`
}

function initial(s: string): string {
  return s.trim().charAt(0).toUpperCase() || '?'
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString()
}

export function Users() {
  const { user: me } = useAuth()
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['users'],
    queryFn: listUsers,
  })

  const mut = useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string
      data: { role?: UserRole; is_active?: boolean }
    }) => updateUser(id, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  })

  if (isLoading) return <div className="umgmt muted">加载中…</div>
  if (isError)
    return <div className="umgmt error">{(error as Error).message}</div>

  const all = data ?? []
  const stats = {
    total: all.length,
    active: all.filter((u) => u.is_active).length,
    admins: all.filter((u) => u.role === 'admin').length,
    disabled: all.filter((u) => !u.is_active).length,
  }

  const q = query.trim().toLowerCase()
  const rows = q
    ? all.filter((u) =>
        `${u.email} ${u.display_name ?? ''}`.toLowerCase().includes(q),
      )
    : all

  return (
    <div className="umgmt">
      <div className="umgmt-head">
        <div>
          <h1>
            <span className="bar" />
            用户管理
          </h1>
          <div className="sub">管理成员账号、角色权限与启用状态</div>
        </div>
        <div className="umgmt-search">
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
            placeholder="搜索邮箱 / 昵称"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>

      <section className="umgmt-stats">
        <div className="umgmt-stat">
          <div className="ico a">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
            >
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
            </svg>
          </div>
          <div>
            <div className="n">{stats.total}</div>
            <div className="l">成员总数</div>
          </div>
        </div>
        <div className="umgmt-stat">
          <div className="ico b">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="9" />
              <path d="m9 12 2 2 4-4" />
            </svg>
          </div>
          <div>
            <div className="n">{stats.active}</div>
            <div className="l">已启用</div>
          </div>
        </div>
        <div className="umgmt-stat">
          <div className="ico c">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
            >
              <path d="M12 2 4 5v6c0 5 3.5 9 8 11 4.5-2 8-6 8-11V5z" />
            </svg>
          </div>
          <div>
            <div className="n">{stats.admins}</div>
            <div className="l">管理员</div>
          </div>
        </div>
        <div className="umgmt-stat">
          <div className="ico d">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
            >
              <circle cx="12" cy="12" r="9" />
              <path d="M12 8v4l3 2" />
            </svg>
          </div>
          <div>
            <div className="n">{stats.disabled}</div>
            <div className="l">已停用</div>
          </div>
        </div>
      </section>

      {mut.isError && <p className="umgmt-err">{(mut.error as Error).message}</p>}

      <section className="umgmt-card">
        <table>
          <thead>
            <tr>
              <th>用户</th>
              <th>角色</th>
              <th>状态</th>
              <th>注册时间</th>
              <th style={{ textAlign: 'right' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((u) => {
              const isSelf = u.id === me?.id
              const rowBusy = mut.isPending && mut.variables?.id === u.id
              const name = u.display_name || u.email
              return (
                <tr key={u.id}>
                  <td>
                    <div className="user-cell">
                      <span className="ua" style={{ background: gradient(name) }}>
                        {initial(name)}
                      </span>
                      <div>
                        <div className="nm">
                          {name}
                          {isSelf && <span className="me-tag">我</span>}
                        </div>
                        <div className="em">{u.email}</div>
                      </div>
                    </div>
                  </td>
                  <td>
                    <div className="role-select">
                      <select
                        className="rs"
                        value={u.role}
                        disabled={isSelf || rowBusy}
                        title={isSelf ? '不能修改自己的角色' : undefined}
                        onChange={(e) =>
                          mut.mutate({
                            id: u.id,
                            data: { role: e.target.value as UserRole },
                          })
                        }
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {ROLE_LABEL[r]}
                          </option>
                        ))}
                      </select>
                    </div>
                  </td>
                  <td>
                    {u.is_active ? (
                      <span className="ubadge on">
                        <span className="dot" />
                        启用
                      </span>
                    ) : (
                      <span className="ubadge off">
                        <span className="dot" />
                        停用
                      </span>
                    )}
                  </td>
                  <td>
                    <span className="date">{fmtDate(u.created_at)}</span>
                  </td>
                  <td>
                    <div className="actions">
                      {isSelf ? (
                        <span className="act muted">本人</span>
                      ) : u.is_active ? (
                        <button
                          className="act disable"
                          disabled={rowBusy}
                          onClick={() =>
                            mut.mutate({ id: u.id, data: { is_active: false } })
                          }
                        >
                          <svg
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="1.9"
                          >
                            <circle cx="12" cy="12" r="9" />
                            <path d="M5.6 5.6l12.8 12.8" />
                          </svg>
                          停用
                        </button>
                      ) : (
                        <button
                          className="act enable"
                          disabled={rowBusy}
                          onClick={() =>
                            mut.mutate({ id: u.id, data: { is_active: true } })
                          }
                        >
                          <svg
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                          >
                            <path d="M20 6 9 17l-5-5" />
                          </svg>
                          启用
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={5}>
                  <div className="umgmt-empty">没有匹配的用户。</div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="umgmt-foot">共 {rows.length} 条记录</div>
      </section>
    </div>
  )
}
