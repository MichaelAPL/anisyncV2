import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { finishSetup } from '../setup'
import { ApiError } from '../api/client'
import { useLinkWithPassword, useProviders } from '../api/hooks'
import type { Me, ProviderInfo } from '../api/types'
import { Icon } from '../ui/Icon'
import { OnboardingLayout, RequireUser, ReturnError } from '../ui/Onboarding'
import { ProviderChip } from '../ui/Provider'

function PasswordForm({ provider }: { provider: ProviderInfo }) {
  const link = useLinkWithPassword()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const error = link.error instanceof ApiError ? link.error.message : link.isError ? "Couldn't reach AniSync." : null
  return (
    <>
      <form
        style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}
        onSubmit={(e) => {
          e.preventDefault()
          link.mutate({ provider: provider.id, username, password }, { onSuccess: () => setPassword('') })
        }}
      >
        <div className="field" style={{ flex: '1 1 220px' }}>
          <label htmlFor={`${provider.id}-user`}>{provider.name} email</label>
          <input id={`${provider.id}-user`} type="text" autoComplete="username" required value={username} onChange={(e) => setUsername(e.target.value)} />
        </div>
        <div className="field" style={{ flex: '1 1 220px' }}>
          <label htmlFor={`${provider.id}-pass`}>Password</label>
          <input id={`${provider.id}-pass`} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <button type="submit" className="btn btn-tonal" style={{ minHeight: 56 }} disabled={link.isPending}>
          {link.isPending ? 'Linking…' : `Link ${provider.name}`}
        </button>
      </form>
      {error && <p className="body-m" role="alert" style={{ color: 'var(--error)' }}>{error}</p>}
      <p className="body-s muted" style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
        <Icon name="lock" size={16} style={{ flex: 'none' }} /> Your password goes straight to {provider.name} in exchange for an access token. AniSync never stores it.
      </p>
    </>
  )
}

function ProviderCard({ provider, me }: { provider: ProviderInfo; me: Me }) {
  const account = me.accounts.find((a) => a.provider === provider.id)
  const form = !account && provider.auth_kind === 'password'
  return (
    <section className="card-out" style={{ padding: form ? 20 : '16px 20px', display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <ProviderChip id={provider.id} size={48} />
        <div style={{ flex: '1 1 220px', minWidth: 0 }}>
          <h2 className="title-m">{provider.name}</h2>
          <p className="body-m muted">
            {account ? (
              <>Signed in as <strong style={{ color: 'var(--on-surface)' }}>{account.remote_username}</strong></>
            ) : provider.paused ? (
              `Paused for now${provider.paused_reason ? `: ${provider.paused_reason}` : '.'}`
            ) : form ? (
              `${provider.name} has no "Sign in with ${provider.name}" page, so log in here once.`
            ) : (
              `You'll approve access on ${provider.name} and come straight back.`
            )}
          </p>
        </div>
        {account ? (
          account.needs_relink ? (
            <a className="btn btn-tonal" href={`/auth/${provider.id}/login`}><Icon name="link" size={18} /> Reconnect</a>
          ) : (
            <span className="tag tag-ok"><Icon name="check" size={14} stroke={2.6} /> Linked</span>
          )
        ) : !form && !provider.paused ? (
          <a className="btn btn-tonal" href={`/auth/${provider.id}/login`}><Icon name="link" size={18} /> Connect {provider.name}</a>
        ) : null}
      </div>
      {form && !provider.paused && <PasswordForm provider={provider} />}
    </section>
  )
}

export function LinkLists() {
  const providers = useProviders()
  const navigate = useNavigate()
  return (
    <RequireUser>
      {(me) => {
        const list = providers.data ?? []
        const linked = me.accounts.length
        return (
          <OnboardingLayout step={1}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <h1 className="headline-l">Link the lists you use</h1>
              <p className="body-l muted">Link at least two. You can add or remove accounts later in Settings.</p>
            </div>
            <ReturnError />
            {list.map((p) => <ProviderCard key={p.id} provider={p} me={me} />)}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
              <span className="body-m muted">{linked} of {list.length} linked</span>
              <span style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <button type="button" className="btn btn-text" onClick={() => { finishSetup(); navigate('/dashboard') }}>Skip for now</button>
                {linked >= 2 ? (
                  <Link to="/master" className="btn btn-filled">Continue <Icon name="arrow-right" size={18} /></Link>
                ) : (
                  <span className="body-m muted">Link one more list to continue</span>
                )}
              </span>
            </div>
          </OnboardingLayout>
        )
      }}
    </RequireUser>
  )
}
