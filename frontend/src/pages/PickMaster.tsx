import { Link, useNavigate } from 'react-router'
import { usePatchAccount, useProviders, useSetMaster } from '../api/hooks'
import type { Account, Me } from '../api/types'
import { Icon } from '../ui/Icon'
import { OnboardingLayout, RequireUser } from '../ui/Onboarding'
import { ProviderChip } from '../ui/Provider'

function ago(iso: string | null): string {
  if (!iso) return 'not synced yet'
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000)
  return days < 1 ? 'today' : days === 1 ? 'yesterday' : `${days} days ago`
}

function MirrorRow({ account, name }: { account: Account; name: string }) {
  const patch = usePatchAccount()
  return (
    <div className="list-item" style={{ flexWrap: 'wrap', gap: '12px 16px' }}>
      <ProviderChip id={account.provider} size={40} />
      <span className="title-m" style={{ flex: '1 1 140px' }}>{name}</span>
      <label className="toggle-row body-m" style={{ gap: 12 }}>
        <input type="checkbox" role="switch" className="switch" checked={account.sync_enabled} disabled={patch.isPending}
          onChange={(e) => patch.mutate({ id: account.id, sync_enabled: e.target.checked })} />
        Keep in sync
      </label>
      <label className="toggle-row body-m" style={{ gap: 12 }}>
        <input type="checkbox" role="switch" className="switch" checked={account.prune_missing} disabled={patch.isPending}
          onChange={(e) => patch.mutate({ id: account.id, prune_missing: e.target.checked })} />
        Remove titles not on master
      </label>
    </div>
  )
}

function Body({ me }: { me: Me }) {
  const providers = useProviders()
  const setMaster = useSetMaster()
  const navigate = useNavigate()
  const name = (id: string) => providers.data?.find((p) => p.id === id)?.name ?? id
  const master = me.accounts.find((a) => a.is_master)
  const mirrors = me.accounts.filter((a) => !a.is_master)
  if (!providers.data) return <p className="body-l muted" style={{ padding: 24 }} role="status">Loading…</p>
  const recent = [...me.accounts].sort((a, b) => (b.last_synced_at ?? '').localeCompare(a.last_synced_at ?? ''))[0]

  return (
    <OnboardingLayout step={2} wide>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <h1 className="headline-l">Which list is the source of truth?</h1>
        <p className="body-l muted">Choose the one you keep most up to date. The other lists will be changed to match it.</p>
      </div>
      <fieldset style={{ border: 'none', margin: 0, padding: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
        <legend className="title-s" style={{ marginBottom: 12 }}>Master list</legend>
        {me.accounts.map((a) => {
          const checked = a.is_master
          return (
            <label key={a.id} style={{
              borderRadius: 20, padding: 20, display: 'flex', flexDirection: 'column', gap: 12, cursor: 'pointer',
              ...(checked
                ? { background: 'var(--primary-container)', color: 'var(--on-primary-container)', outline: '2px solid var(--primary)', outlineOffset: -2 }
                : { background: 'var(--surface)', border: '1px solid var(--outline-variant)' }),
            }}>
              <span style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <ProviderChip id={a.provider} size={48} />
                <input type="radio" name="master" className="radio" checked={checked} disabled={setMaster.isPending}
                  onChange={() => setMaster.mutate(a.id)} />
              </span>
              <span className="title-l">{name(a.provider)}</span>
              <span className="body-m" style={{ opacity: 0.85 }}>{a.remote_username}</span>
              {recent && a.id === recent.id && recent.last_synced_at && <span className="tag" style={{ alignSelf: 'flex-start', background: 'var(--sc-lowest)', color: 'var(--on-primary-container)' }}>Synced {ago(recent.last_synced_at)}</span>}
            </label>
          )
        })}
      </fieldset>
      {setMaster.isError && <p className="body-m" role="alert" style={{ color: 'var(--error)' }}>Couldn't change the master list. Try again.</p>}
      <section className="card-out" style={{ overflow: 'hidden' }}>
        <h2 className="title-m" style={{ padding: '16px 16px 4px' }}>Mirrors</h2>
        <p className="body-m muted" style={{ padding: '0 16px 8px' }}>Removing titles is off by default, so nothing is deleted from your mirrors unless you turn it on.</p>
        <div className="list">{mirrors.map((a) => <MirrorRow key={a.id} account={a} name={name(a.provider)} />)}</div>
      </section>
      <div className="banner banner-warn" style={{ borderRadius: 16, alignItems: 'flex-start', padding: '16px 20px' }}>
        <Icon name="warn" size={22} style={{ flex: 'none' }} />
        <span className="body-m" style={{ flex: '1 1 300px' }}>
          Changes you make directly on {mirrors.map((a) => name(a.provider)).join(' or ')} will be overwritten at the next full check (at least weekly).
          Edit on {master ? name(master.provider) : 'your master'} or in AniSync instead.
        </span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <Link to="/link" className="btn btn-text"><Icon name="arrow-left" size={18} /> Back</Link>
        <button type="button" className="btn btn-filled" disabled={!master} onClick={() => navigate('/review')}>
          Preview first sync <Icon name="arrow-right" size={18} />
        </button>
      </div>
    </OnboardingLayout>
  )
}

export const PickMaster = () => <RequireUser>{(me) => <Body me={me} />}</RequireUser>
