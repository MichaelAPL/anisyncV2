import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { clock, relative } from '../format'
import { Avatar } from '../ui/Avatar'
import { ProviderChip } from '../ui/Provider'
import { AdminTop } from './AdminShell'
import { can, level, useAdmin, useProviderName } from './util'
import { Card, Err, Loading } from './bits'
import { del, post, put, useAct, useUser, useUsers } from './hooks'
import type { AdminAccount } from './types'

export function Users() {
  const [q, setQ] = useState('')
  const [banned, setBanned] = useState(false)
  const users = useUsers({ q: q.trim() || undefined, banned: banned || undefined })
  const name = useProviderName()
  return (
    <>
      <AdminTop title="Users" sub="Search by id, display name or a provider username." />
      <main className="page" style={{ paddingTop: 0 }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <div className="search" role="search" style={{ flex: '1 1 260px' }}><input type="search" aria-label="Search users" placeholder="Search users" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <button type="button" className={`chip${banned ? ' chip-on' : ''}`} aria-pressed={banned} onClick={() => setBanned(!banned)}>Banned only</button>
        </div>
        <Loading q={users} />
        <section className="card-out list" aria-label="Users" style={{ overflow: 'hidden' }}>
          {users.data?.map((u) => (
            <Link key={u.id} to={`/admin/users/${u.id}`} className="list-item list-link">
              <Avatar me={u} size={40} />
              <span className="list-text">
                <span className="title-m">{u.display_name} <span className="muted body-m">#{u.id}</span> {u.role !== 'user' && <span className="tag tag-info">{u.role}</span>} {u.banned_at && <span className="tag tag-err">Banned</span>}</span>
                <span className="body-m muted">{u.providers.map(name).join(', ') || 'no accounts'} · master {name(u.master_provider)} · last seen {relative(u.last_seen_at)}</span>
              </span>
            </Link>
          ))}
          {users.data?.length === 0 && <p className="body-m muted" style={{ padding: 16 }}>No users match.</p>}
        </section>
      </main>
    </>
  )
}

function AccountRow({ userId, a }: { userId: number; a: AdminAccount }) {
  const admin = useAdmin()
  const name = useProviderName()
  const revoke = useAct(() => post(`/api/admin/users/${userId}/accounts/${a.id}/revoke-tokens`))
  const unlink = useAct(() => del(`/api/admin/users/${userId}/accounts/${a.id}`))
  const [confirm, setConfirm] = useState(false)
  return (
    <div className="list-item" style={{ flexWrap: 'wrap', gap: '8px 16px' }}>
      <ProviderChip id={a.provider} size={40} />
      <div className="list-text" style={{ flex: '1 1 240px' }}>
        <span className="title-m">{name(a.provider)} · {a.remote_username} {a.is_master && <span className="tag tag-info">Master</span>} {a.needs_relink && <span className="tag tag-warn">Needs relink</span>}</span>
        <span className="body-m muted">{a.entries} entries · synced {relative(a.last_synced_at)} · full read {relative(a.last_full_read_at)}{a.sync_enabled ? '' : ' · sync off'}</span>
      </div>
      {can(admin, 'users.manage_tokens') && (
        <>
          <button type="button" className="btn btn-text" disabled={revoke.isPending} onClick={() => revoke.mutate()}>Revoke tokens</button>
          {confirm ? (
            <><button type="button" className="btn btn-text" style={{ color: 'var(--error)' }} onClick={() => unlink.mutate()}>Yes, unlink</button><button type="button" className="btn btn-text" onClick={() => setConfirm(false)}>Cancel</button></>
          ) : <button type="button" className="btn btn-text" style={{ color: 'var(--error)' }} onClick={() => setConfirm(true)}>Unlink</button>}
        </>
      )}
      <Err error={revoke.error ?? unlink.error} />
    </div>
  )
}

export function UserDetail() {
  const id = Number(useParams().id)
  const admin = useAdmin()
  const navigate = useNavigate()
  const user = useUser(id)
  const [reason, setReason] = useState('')
  const [role, setRole] = useState<string | null>(null)
  const [delText, setDelText] = useState('')
  const [preview, setPreview] = useState<unknown>(null)
  const ban = useAct(() => post(`/api/admin/users/${id}/ban`, { reason }))
  const unban = useAct(() => post(`/api/admin/users/${id}/unban`, {}))
  const sync = useAct(() => post(`/api/admin/users/${id}/sync`))
  const full = useAct(() => post(`/api/admin/users/${id}/full-check`))
  const prev = useAct(async () => setPreview(await post(`/api/admin/users/${id}/preview`)))
  const setRoleM = useAct((r: string) => put(`/api/admin/users/${id}/role`, { role: r }))
  const remove = useAct(() => del(`/api/admin/users/${id}?confirm=${id}`).then(() => navigate('/admin/users')))
  const u = user.data
  const anyErr = ban.error ?? unban.error ?? sync.error ?? full.error ?? prev.error ?? setRoleM.error ?? remove.error
  return (
    <>
      <AdminTop title={u ? u.display_name : 'User'} sub={u ? `#${u.id} · joined ${clock(u.created_at)} · last seen ${relative(u.last_seen_at)}` : undefined}
        right={<Link to="/admin/users" className="btn btn-text">All users</Link>} />
      <main className="page" style={{ paddingTop: 0 }}>
        <Loading q={user} />
        {u && (
          <>
            {u.banned_at && <div className="banner banner-crit" role="status" style={{ borderRadius: 16, padding: '16px 20px' }}>Banned {relative(u.banned_at)}{u.banned_by_name ? ` by ${u.banned_by_name}` : ''}: {u.ban_reason}</div>}
            <Card id="acc-h" title="Accounts" sub={`${u.sessions} active sessions · ${u.manual_syncs_7d} manual syncs in 7 days · role ${u.role}`}>
              <div className="list">{u.accounts.map((a) => <AccountRow key={a.id} userId={u.id} a={a} />)}</div>
            </Card>
            {can(admin, 'syncs.retry') && (
              <Card id="act-h" title="Sync tools">
                <div style={{ padding: '0 16px 16px', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button type="button" className="btn btn-tonal" disabled={sync.isPending} onClick={() => sync.mutate()}>Sync now</button>
                  <button type="button" className="btn btn-tonal" disabled={full.isPending} onClick={() => full.mutate()}>Full re-check</button>
                  <button type="button" className="btn btn-outlined" disabled={prev.isPending} onClick={() => prev.mutate()}>{prev.isPending ? 'Reading…' : 'Preview changes'}</button>
                </div>
                {preview !== null && <pre className="body-s" style={{ margin: 0, padding: '0 16px 16px', overflow: 'auto', maxHeight: 280 }}>{JSON.stringify(preview, null, 2)}</pre>}
              </Card>
            )}
            <Card id="runs-h" title="Recent runs">
              <div className="list">{u.recent_runs.map((r) => (
                <div key={r.id} className="list-item"><div className="list-text"><span className="title-s">#{r.id} · {r.trigger}</span><span className="body-m muted">{clock(r.started_at)} · {r.changes} changes · {r.errors} errors</span></div><span className={`tag ${level(r.errors ? 'error' : r.status)}`}>{r.errors ? 'Errors' : r.status}</span></div>
              ))}</div>
              {u.recent_runs.length === 0 && <p className="body-m muted" style={{ padding: 16 }}>No runs yet.</p>}
            </Card>
            {can(admin, 'users.ban') && (
              <Card id="ban-h" title={u.banned_at ? 'Unban' : 'Ban'} sub={u.banned_at ? undefined : 'Signs the user out and stops their syncs. They can be unbanned later.'}>
                <div style={{ padding: '0 16px 16px', display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
                  {u.banned_at ? (
                    <button type="button" className="btn btn-tonal" disabled={unban.isPending} onClick={() => unban.mutate()}>Unban</button>
                  ) : (
                    <>
                      <div className="field" style={{ flex: '1 1 280px' }}><label htmlFor="ban-reason">Reason (kept in the audit log)</label><input id="ban-reason" value={reason} onChange={(e) => setReason(e.target.value)} /></div>
                      <button type="button" className="btn btn-danger" style={{ minHeight: 56 }} disabled={ban.isPending || !reason.trim()} onClick={() => ban.mutate()}>Ban user</button>
                    </>
                  )}
                </div>
              </Card>
            )}
            {can(admin, 'roles.manage') && (
              <Card id="role-h" title="Role">
                <div style={{ padding: '0 16px 16px', display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                  <div className="field" style={{ width: 200 }}><label htmlFor="role">Role</label>
                    <select id="role" value={role ?? u.role} onChange={(e) => setRole(e.target.value)}><option value="user">user</option><option value="admin">admin</option></select></div>
                  <button type="button" className="btn btn-tonal" style={{ minHeight: 56 }} disabled={setRoleM.isPending || (role ?? u.role) === u.role} onClick={() => setRoleM.mutate(role ?? u.role)}>Change role</button>
                </div>
              </Card>
            )}
            <Card id="audit-h" title="Audit trail" sub="What admins did to this user.">
              <div className="list">{u.actions.map((a) => (
                <div key={a.id} className="list-item"><div className="list-text"><span className="title-s">{a.action}</span><span className="body-m muted">{clock(a.created_at)} · {a.actor_name ?? 'system'}{a.note ? ` · ${a.note}` : ''}</span></div></div>
              ))}</div>
              {u.actions.length === 0 && <p className="body-m muted" style={{ padding: 16 }}>Nothing yet.</p>}
            </Card>
            {can(admin, 'users.delete') && !u.banned_at && (
              <section className="card-out" aria-labelledby="del-h" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12, borderColor: 'var(--error)' }}>
                <h2 id="del-h" className="title-l" style={{ color: 'var(--error)' }}>Delete user</h2>
                <p className="body-m muted">Removes the user and all their data. This cannot be undone. Banned users can't be deleted; unban first.</p>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
                  <div className="field" style={{ flex: '1 1 260px' }}><label htmlFor="del-id">Type the user id ({u.id}) to confirm</label><input id="del-id" value={delText} onChange={(e) => setDelText(e.target.value)} /></div>
                  <button type="button" className="btn btn-danger" style={{ minHeight: 56 }} disabled={remove.isPending || delText.trim() !== String(u.id)} onClick={() => remove.mutate()}>Delete user</button>
                </div>
              </section>
            )}
            <Err error={anyErr} />
          </>
        )}
      </main>
    </>
  )
}
