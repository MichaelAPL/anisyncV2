import { useState } from 'react'
import { ApiError } from '../api/client'
import { ProviderChip } from '../ui/Provider'
import { relative } from '../format'
import { AdminTop } from './AdminShell'
import { can, useAdmin, useProviderName } from './util'
import { Card, Err, Loading, Stat } from './bits'
import { post, put, useAct, useAdminSite, useDataset, useProviderHealth, useUnmatched } from './hooks'
import type { AdminSite, ProviderHealth } from './types'

function SiteForm({ site }: { site: AdminSite }) {
  const [text, setText] = useState(site.banner_text ?? '')
  const [lvl, setLvl] = useState(site.banner_level)
  const [maint, setMaint] = useState(site.maintenance)
  const [msg, setMsg] = useState(site.maintenance_message ?? '')
  const save = useAct(() => put('/api/admin/site', { maintenance: maint, maintenance_message: msg.trim() || null, banner_text: text.trim() || null, banner_level: lvl }))
  return (
    <form style={{ display: 'flex', flexDirection: 'column', gap: 20 }} onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
      <Card id="ban-h" title="Announcement banner" sub="Shown above every signed-in page as plain text. Leave empty for none.">
        <div style={{ padding: '0 16px 16px', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <div className="field" style={{ flex: '1 1 320px' }}><label htmlFor="bt">Text</label><input id="bt" value={text} maxLength={300} onChange={(e) => setText(e.target.value)} /></div>
          <div className="field" style={{ width: 180 }}><label htmlFor="bl">Level</label><select id="bl" value={lvl} onChange={(e) => setLvl(e.target.value)}><option value="info">Info</option><option value="warning">Warning</option><option value="error">Error</option></select></div>
        </div>
      </Card>
      <Card id="mt-h" title="Maintenance mode" sub="Blocks list edits and pauses all syncs. Admins can still sign in.">
        <label className="toggle-row" style={{ justifyContent: 'space-between', padding: '0 16px' }}><span className="body-l">Maintenance mode</span>
          <input type="checkbox" role="switch" className="switch" checked={maint} onChange={(e) => setMaint(e.target.checked)} /></label>
        <div style={{ padding: 16 }}><div className="field"><label htmlFor="mm">Message shown to users</label><input id="mm" value={msg} maxLength={300} onChange={(e) => setMsg(e.target.value)} /></div></div>
      </Card>
      <Err error={save.error} />
      <div><button type="submit" className="btn btn-filled" disabled={save.isPending}>{save.isPending ? 'Saving…' : 'Save'}</button>{save.isSuccess && <span className="body-m muted" role="status" style={{ marginLeft: 12 }}>Saved.</span>}</div>
    </form>
  )
}

export function SitePage() {
  const site = useAdminSite()
  return (
    <>
      <AdminTop title="Banner and maintenance" sub={site.data ? `Last changed ${relative(site.data.updated_at)}${site.data.updated_by_name ? ` by ${site.data.updated_by_name}` : ''}` : undefined} />
      <main className="page" style={{ paddingTop: 0, maxWidth: 800 }}>
        <Loading q={site} />
        {site.data && <SiteForm key={site.data.updated_at} site={site.data} />}
      </main>
    </>
  )
}

function ProviderCard({ p }: { p: ProviderHealth }) {
  const [reason, setReason] = useState(p.paused_reason ?? '')
  const toggle = useAct((paused: boolean) => put(`/api/admin/providers/${p.id}`, { paused, reason: paused ? reason.trim() || null : null }))
  return (
    <article className="card-elev" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <ProviderChip id={p.id} size={44} />
        <div style={{ flex: 1 }}><h3 className="title-m">{p.name}</h3><p className="body-m muted">{p.accounts} accounts</p></div>
        <span className={`tag ${p.paused ? 'tag-warn' : 'tag-ok'}`}>{p.paused ? 'Paused' : 'Active'}</span>
      </div>
      <p className="body-m muted">Today: {p.today.requests} requests · {p.today.errors} errors · {p.today.rate_limited} rate-limited · {p.today.avg_ms} ms avg<br />
        7 days: {p.last_7d.requests} requests · {p.last_7d.errors} errors</p>
      <div className="field"><label htmlFor={`r-${p.id}`}>Reason shown to users</label><input id={`r-${p.id}`} value={reason} onChange={(e) => setReason(e.target.value)} /></div>
      <button type="button" className={p.paused ? 'btn btn-filled' : 'btn btn-outlined'} disabled={toggle.isPending} onClick={() => toggle.mutate(!p.paused)}>{p.paused ? 'Resume' : 'Pause provider'}</button>
      <Err error={toggle.error} />
    </article>
  )
}

export function Providers() {
  const admin = useAdmin()
  const health = useProviderHealth()
  const ds = useDataset()
  const name = useProviderName()
  const run = useAct((force: boolean) => post('/api/admin/dataset/import', { force }))
  return (
    <>
      <AdminTop title="Providers and ID dataset" />
      <main className="page" style={{ paddingTop: 0 }}>
        <Loading q={health} />
        <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))' }}>{health.data?.map((p) => <ProviderCard key={p.id} p={p} />)}</div>
        <Loading q={ds} />
        {ds.data && (
          <Card id="ds-h" title="ID dataset" sub={`${ds.data.media} titles. Source: ${ds.data.source_url}`}
            right={can(admin, 'providers.manage') && <button type="button" className="btn btn-tonal" disabled={run.isPending} onClick={() => run.mutate(false)}>Run import</button>}>
            <div className="grid-stats" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', padding: '0 16px 16px' }}>
              {ds.data.coverage.map((c) => <Stat key={c.provider} n={c.ids} label={`${name(c.provider)} ids (${c.seed} seed, ${c.learned} learned, ${c.manual} manual)`} />)}
            </div>
            <div className="list">{ds.data.imports.map((i) => (
              <div key={i.id} className="list-item"><div className="list-text"><span className="title-s">{i.dataset} {i.version ?? ''}</span><span className="body-m muted">{relative(i.started_at)}{i.error ? ` · ${i.error}` : ''}</span></div><span className={`tag ${i.status === 'done' || i.status === 'ok' ? 'tag-ok' : i.error ? 'tag-err' : 'tag-neutral'}`}>{i.status}</span></div>
            ))}</div>
            <Err error={run.error} />
          </Card>
        )}
      </main>
    </>
  )
}

export function Titles() {
  const [q, setQ] = useState('')
  const list = useUnmatched({ q: q.trim() || undefined })
  const name = useProviderName()
  const [target, setTarget] = useState<Record<string, string>>({})
  const link = useAct((v: { source: string; source_id: string; target: string; target_id: string }) => post('/api/admin/titles/link', v))
  const key = (u: { source: string; source_id: string; target: string }) => `${u.source}:${u.source_id}:${u.target}`
  const canLink = can(useAdmin(), 'titles.link')
  return (
    <>
      <AdminTop title="Unmatched titles" sub="Titles users have that no provider could translate. Link them by hand." />
      <main className="page" style={{ paddingTop: 0 }}>
        <div className="search" role="search"><input type="search" aria-label="Search titles" placeholder="Search titles" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <Loading q={list} />
        <section className="card-out list" aria-label="Unmatched titles" style={{ overflow: 'hidden' }}>
          {list.data?.map((u) => (
            <div key={key(u)} className="list-item" style={{ flexWrap: 'wrap', gap: '8px 16px' }}>
              <div className="list-text" style={{ flex: '1 1 280px' }}>
                <span className="title-m">{u.title}</span>
                <span className="body-m muted">{name(u.source)} #{u.source_id} → {name(u.target)} · {u.users} user{u.users === 1 ? '' : 's'} · last seen {relative(u.last_seen_at)}</span>
              </div>
              {canLink && (
                <form style={{ display: 'flex', gap: 8, alignItems: 'center' }} onSubmit={(e) => { e.preventDefault(); link.mutate({ ...u, target_id: (target[key(u)] ?? '').trim() }) }}>
                  <div className="field" style={{ width: 160 }}><label htmlFor={`t-${key(u)}`}>{name(u.target)} id</label>
                    <input id={`t-${key(u)}`} inputMode="numeric" value={target[key(u)] ?? ''} onChange={(e) => setTarget({ ...target, [key(u)]: e.target.value })} /></div>
                  <button type="submit" className="btn btn-tonal" disabled={link.isPending || !(target[key(u)] ?? '').trim()}>Link</button>
                </form>
              )}
            </div>
          ))}
          {list.data?.length === 0 && <p className="body-m muted" style={{ padding: 16 }}>Nothing unmatched.</p>}
        </section>
        {link.error instanceof ApiError ? <p className="body-m" role="alert" style={{ color: 'var(--error)' }}>{link.error.message}</p> : <Err error={link.error} />}
      </main>
    </>
  )
}
