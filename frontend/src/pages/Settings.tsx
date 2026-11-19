import { useState } from 'react'
import { Link, useNavigate, useOutletContext } from 'react-router'
import { ApiError } from '../api/client'
import {
  useDeleteAccount, usePatchAccount, useProviders, useRemoveAvatar, useRevokeSession, useSaveProfile, useSessions,
  useSetMaster, useSignOut, useUnlink, useUseAvatar,
} from '../api/hooks'
import type { Account, Me } from '../api/types'
import { relative } from '../format'
import { setTheme, useTheme, type Theme } from '../theme'
import { Avatar } from '../ui/Avatar'
import { Icon } from '../ui/Icon'
import { Loading } from '../ui/Loading'
import { ProviderChip } from '../ui/Provider'
import { Topbar } from '../ui/Shell'
import { safeImage } from '../ui/images'

const msg = (e: unknown) => (e instanceof ApiError ? e.message : e ? "Couldn't reach AniSync." : null)

function Err({ error }: { error: unknown }) {
  const m = msg(error)
  return m ? <p className="body-m" role="alert" style={{ color: 'var(--error)', padding: '0 16px 8px' }}>{m}</p> : null
}

function Profile({ me, name }: { me: Me; name: (id: string) => string }) {
  const save = useSaveProfile()
  const useAvatar = useUseAvatar()
  const remove = useRemoveAvatar()
  const [text, setText] = useState(me.display_name)
  return (
    <section className="card-out" aria-labelledby="prof-h" style={{ overflow: 'hidden' }}>
      <div style={{ padding: '16px 16px 8px', display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <Avatar me={me} size={72} />
        <div style={{ flex: '1 1 260px' }}><h2 id="prof-h" className="title-l">Profile</h2><p className="body-m muted">Only you and the AniSync admins see your name and picture.</p></div>
      </div>
      <form style={{ padding: '8px 16px 16px', display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}
        onSubmit={(e) => { e.preventDefault(); save.mutate({ display_name: text }) }}>
        <div className="field" style={{ flex: '1 1 260px', maxWidth: 420 }}>
          <label htmlFor="dn">Display name</label>
          <input id="dn" type="text" value={text} maxLength={40} onChange={(e) => setText(e.target.value)} />
          <span className="support">1–40 characters. Not shown to anyone else.</span>
        </div>
        <button type="submit" className="btn btn-filled" style={{ marginTop: 4 }} disabled={save.isPending || !text.trim() || text === me.display_name}>Save name</button>
      </form>
      <Err error={save.error} />
      <div style={{ padding: '0 16px' }}>
        <h3 className="title-m">Profile picture</h3>
        <p className="body-m muted">Pictures are loaded from the provider, never uploaded to AniSync. Yours stays as it is when you change master; use the master's row below to refresh it after changing it there.</p>
      </div>
      <div className="list">
        {me.accounts.map((a) => {
          const current = me.avatar_provider === a.provider
          const pic = safeImage(a.avatar_url)
          return (
            <div key={a.id} className="list-item" style={{ flexWrap: 'wrap', gap: '12px 16px' }}>
              <Avatar me={{ display_name: a.remote_username, avatar_url: a.avatar_url }} size={48} />
              <div className="list-text" style={{ flex: '1 1 200px' }}>
                <span className="title-m" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <ProviderChip id={a.provider} size={24} /> {name(a.provider)}
                  {current && <span className="tag tag-info">Current</span>}
                  {a.is_master && <span className="tag tag-neutral">Master</span>}
                  {!pic && <span className="tag tag-warn">No picture</span>}
                </span>
                <span className="body-m muted">{a.remote_username}</span>
              </div>
              <button type="button" className={current || a.is_master ? 'btn btn-text' : 'btn btn-tonal'} disabled={useAvatar.isPending || (!pic && !a.is_master)}
                onClick={() => useAvatar.mutate(a.id)}>
                {current || (a.is_master && me.avatar_provider) ? `Update from ${name(a.provider)}` : 'Use this picture'}
              </button>
            </div>
          )
        })}
      </div>
      <Err error={useAvatar.error ?? remove.error} />
      <div style={{ padding: '4px 16px 16px' }}>
        <button type="button" className="btn btn-text" disabled={!me.avatar_url || remove.isPending} onClick={() => remove.mutate()}>Remove picture (show my initial)</button>
      </div>
    </section>
  )
}

function AccountRow({ a, me, name }: { a: Account; me: Me; name: (id: string) => string }) {
  const setMaster = useSetMaster()
  const unlink = useUnlink()
  const [confirming, setConfirming] = useState(false)
  const detail = a.needs_relink ? 'Connection expired' : a.expires_soon ? `Connection expires ${relative(a.expires_at)}` : a.expires_at ? `Valid until ${new Date(a.expires_at).toLocaleDateString('en')}` : 'Connected'
  return (
    <div className="list-item" style={{ flexWrap: 'wrap', gap: '12px 16px' }}>
      <ProviderChip id={a.provider} size={44} />
      <div className="list-text" style={{ flex: '1 1 200px' }}>
        <span className="title-m" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {name(a.provider)} <span className={`tag ${a.is_master ? 'tag-info' : 'tag-neutral'}`}>{a.is_master ? 'Master' : 'Mirror'}</span>
        </span>
        <span className="body-m muted">{a.remote_username} · <span style={a.needs_relink || a.expires_soon ? { color: 'var(--warning)' } : undefined}>{detail}</span></span>
      </div>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
        {(a.needs_relink || a.expires_soon) && <a className="btn btn-tonal" href={`/auth/${a.provider}/login`}>Reconnect</a>}
        {!a.is_master && <button type="button" className="btn btn-text" disabled={setMaster.isPending} onClick={() => setMaster.mutate(a.id)}>Make master</button>}
        {me.accounts.length > 1 && (confirming ? (
          <>
            <span className="body-m">Unlink {name(a.provider)}?</span>
            <button type="button" className="btn btn-text" style={{ color: 'var(--error)' }} disabled={unlink.isPending} onClick={() => unlink.mutate(a.id)}>Yes, unlink</button>
            <button type="button" className="btn btn-text" onClick={() => setConfirming(false)}>Cancel</button>
          </>
        ) : <button type="button" className="btn btn-text" style={{ color: 'var(--error)' }} onClick={() => setConfirming(true)}>Unlink</button>)}
      </div>
      <Err error={setMaster.error ?? unlink.error} />
    </div>
  )
}

function SyncOptions({ me, name }: { me: Me; name: (id: string) => string }) {
  const patch = usePatchAccount()
  const mirrors = me.accounts.filter((a) => !a.is_master)
  return (
    <section className="card-out" aria-labelledby="sync-h" style={{ padding: 16 }}>
      <h2 id="sync-h" className="title-l">Sync</h2>
      <p className="body-m muted" style={{ marginBottom: 8 }}>
        Your lists sync once a day{me.next_sync_at ? ` (next: ${relative(me.next_sync_at)})` : ''} and right after you edit in AniSync. Changes made directly on a mirror are caught at its weekly full check.
      </p>
      {mirrors.map((a) => (
        <div key={a.id}>
          <label className="toggle-row" style={{ justifyContent: 'space-between', borderTop: '1px solid var(--outline-variant)' }}>
            <span className="body-l">Keep {name(a.provider)} in sync</span>
            <input type="checkbox" role="switch" className="switch" checked={a.sync_enabled} disabled={patch.isPending} onChange={(e) => patch.mutate({ id: a.id, sync_enabled: e.target.checked })} />
          </label>
          <label className="toggle-row" style={{ justifyContent: 'space-between', padding: '8px 0' }}>
            <span style={{ display: 'flex', flexDirection: 'column' }}>
              <span className="body-l">Remove titles not on master from {name(a.provider)}</span>
              <span className="body-m muted">Deletes {name(a.provider)} entries that aren't on your master list</span>
            </span>
            <input type="checkbox" role="switch" className="switch" checked={a.prune_missing} disabled={patch.isPending} onChange={(e) => patch.mutate({ id: a.id, prune_missing: e.target.checked })} />
          </label>
        </div>
      ))}
      {mirrors.length === 0 && <p className="body-m muted">Link another list to choose what syncs. <Link to="/link">Link lists</Link></p>}
      <Err error={patch.error} />
    </section>
  )
}

function Appearance() {
  const theme = useTheme()
  const save = useSaveProfile()
  const pick = (t: Theme) => { setTheme(t); save.mutate({ theme: t }) }
  const opts: [Theme, string][] = [['system', 'Match system'], ['light', 'Light'], ['dark', 'Dark']]
  return (
    <section className="card-out" aria-labelledby="look-h" style={{ padding: 16, display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
      <div style={{ flex: '1 1 260px' }}><h2 id="look-h" className="title-l">Appearance</h2><p className="body-m muted">Saved to your account, so it follows you to every device. The moon/sun button at the top switches it too.</p></div>
      <div className="seg" role="group" aria-label="Theme">
        {opts.map(([v, l]) => <button key={v} type="button" aria-pressed={theme === v} onClick={() => pick(v)}>{l}</button>)}
      </div>
    </section>
  )
}

function Sessions() {
  const sessions = useSessions()
  const revoke = useRevokeSession()
  const out = useSignOut()
  const navigate = useNavigate()
  const done = () => navigate('/', { replace: true })
  const others = (sessions.data ?? []).filter((s) => !s.current)
  return (
    <section className="card-out" aria-labelledby="sess-h" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 260px' }}>
          <h2 id="sess-h" className="title-l">Sessions</h2>
          <p className="body-m muted">{sessions.data ? `Signed in on ${sessions.data.length} device${sessions.data.length === 1 ? '' : 's'}.` : 'Loading…'}</p>
        </div>
        <button type="button" className="btn btn-tonal" disabled={out.isPending} onClick={() => out.mutate(false, { onSuccess: done })}><Icon name="logout" size={18} /> Sign out</button>
        <button type="button" className="btn btn-outlined" disabled={out.isPending} onClick={() => out.mutate(true, { onSuccess: done })}>Sign out everywhere</button>
      </div>
      {others.map((s) => (
        <div key={s.id} className="list-item" style={{ padding: 0 }}>
          <div className="list-text"><span className="body-l">{s.user_agent || 'Unknown device'}</span><span className="body-m muted">Last active {relative(s.last_seen_at)}</span></div>
          <button type="button" className="btn btn-text" disabled={revoke.isPending} onClick={() => revoke.mutate(s.id)}>Sign out</button>
        </div>
      ))}
      <Err error={out.error ?? revoke.error} />
    </section>
  )
}

function DeleteAccount() {
  const del = useDeleteAccount()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  return (
    <section className="card-out" aria-labelledby="del-h" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16, borderColor: 'var(--error)' }}>
      <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 300px' }}>
          <h2 id="del-h" className="title-l" style={{ color: 'var(--error)' }}>Delete account</h2>
          <p className="body-m muted">Removes your AniSync account, tokens, list copies and sync history right away. Your lists on MyAnimeList, AniList and Kitsu are not touched.</p>
        </div>
        {!open && <button type="button" className="btn btn-danger" onClick={() => setOpen(true)}><Icon name="trash" size={18} /> Delete account</button>}
      </div>
      {open && (
        <form style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}
          onSubmit={(e) => { e.preventDefault(); del.mutate(text, { onSuccess: () => navigate('/', { replace: true }) }) }}>
          <div className="field" style={{ flex: '1 1 280px' }}>
            <label htmlFor="del-confirm">Type "delete my account" to confirm</label>
            <input id="del-confirm" value={text} onChange={(e) => setText(e.target.value)} autoComplete="off" />
          </div>
          <button type="submit" className="btn btn-danger" style={{ minHeight: 56 }} disabled={del.isPending || text.trim().toLowerCase() !== 'delete my account'}>Delete everything</button>
          <button type="button" className="btn btn-text" style={{ minHeight: 56 }} onClick={() => { setOpen(false); setText('') }}>Cancel</button>
        </form>
      )}
      <Err error={del.error} />
    </section>
  )
}

export function Settings() {
  const me = useOutletContext<Me>()
  const providers = useProviders()
  const name = (id: string) => providers.data?.find((p) => p.id === id)?.name ?? id
  if (!providers.data) return <Loading />
  return (
    <>
      <Topbar title="Settings" me={me} />
      <main className="page" style={{ paddingTop: 0, maxWidth: 900 }}>
        <Profile me={me} name={name} />
        <section className="card-out" aria-labelledby="acc-h" style={{ overflow: 'hidden' }}>
          <div style={{ padding: '16px 16px 4px', display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 240px' }}><h2 id="acc-h" className="title-l">Linked accounts</h2><p className="body-m muted">The master is copied to every mirror.</p></div>
            <Link to="/link" className="btn btn-tonal"><Icon name="link" size={18} /> Link another list</Link>
          </div>
          <div className="list">{me.accounts.map((a) => <AccountRow key={a.id} a={a} me={me} name={name} />)}</div>
        </section>
        <SyncOptions me={me} name={name} />
        <Appearance />
        <Sessions />
        <DeleteAccount />
      </main>
    </>
  )
}
