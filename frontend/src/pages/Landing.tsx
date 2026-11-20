import { Navigate } from 'react-router'
import { useMe, useProviders } from '../api/hooks'
import { Footer, ThemeButton, Wordmark } from '../ui/Chrome'
import { Icon } from '../ui/Icon'
import { PosterWall } from '../ui/PosterWall'
import { ProviderChip } from '../ui/Provider'

const HOW = [
  { icon: 'link', title: 'Link your accounts', text: 'Connect MyAnimeList and AniList through their own sign-in pages, and Kitsu with a one-time login.' },
  { icon: 'shield', title: 'Choose a master', text: 'The master list is your source of truth. The others become mirrors that copy it.' },
  { icon: 'sync', title: 'Stay in sync', text: 'Mirrors update daily, and right away when you edit your list in AniSync.' },
] as const

export function Landing() {
  const me = useMe()
  const providers = useProviders()
  if (me.data) return <Navigate to="/dashboard" replace />

  const list = providers.data ?? []
  const signIn = list.filter((p) => p.can_sign_in)
  const linkOnly = list.filter((p) => !p.can_sign_in)

  return (
    <>
      <header style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap', maxWidth: 1200, width: '100%', margin: '0 auto', padding: '16px 24px' }}>
        <Wordmark />
        <nav aria-label="Site" style={{ display: 'flex', gap: 4, marginLeft: 'auto', alignItems: 'center' }}>
          <a href="#how" className="btn btn-text" style={{ color: 'var(--on-surface-variant)' }}>How it works</a>
          <ThemeButton />
        </nav>
      </header>
      <main>
        <section style={{ position: 'relative', overflow: 'hidden', minHeight: 680, display: 'flex', alignItems: 'center' }}>
          <PosterWall />
          <div style={{ position: 'relative', zIndex: 1, width: '100%', maxWidth: 1200, margin: '0 auto', padding: '32px 24px 80px' }}>
            <div className="hero-card" style={{ maxWidth: 580, padding: 32, borderRadius: 28, display: 'flex', flexDirection: 'column', gap: 24 }}>
              <div className="chip-row">
                {list.map((p) => (
                  <span key={p.id} className="tag tag-neutral" style={{ gap: 8, paddingLeft: 4 }}>
                    <ProviderChip id={p.id} size={20} /> {p.name}
                  </span>
                ))}
              </div>
              <h1 className="display-l">One anime list. Every site you track on.</h1>
              <p className="body-l muted" style={{ fontSize: 18, lineHeight: '28px', maxWidth: 540 }}>
                Pick the list you trust. AniSync keeps your other accounts matching it, every day and every time you edit.
              </p>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                {signIn.map((p, i) => (
                  <a
                    key={p.id}
                    href={p.paused ? undefined : `/auth/${p.id}/login`}
                    aria-disabled={p.paused || undefined}
                    className={`btn btn-lg ${i === 0 ? 'btn-filled' : 'btn-outlined'}`}
                    style={p.paused ? { opacity: 0.5, pointerEvents: 'none' } : undefined}
                  >
                    <ProviderChip id={p.id} size={26} /> Continue with {p.name}
                  </a>
                ))}
              </div>
              {signIn.some((p) => p.paused) && (
                <p className="body-m muted" role="status">
                  {signIn.filter((p) => p.paused).map((p) => p.name).join(', ')} is paused for now
                  {signIn.find((p) => p.paused)?.paused_reason ? `: ${signIn.find((p) => p.paused)!.paused_reason}` : '.'}
                </p>
              )}
              {linkOnly.length > 0 && (
                <p className="body-m muted" style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  <Icon name="info" size={18} style={{ flex: 'none', marginTop: 1 }} />
                  Using {linkOnly.map((p) => p.name).join(' or ')}? Sign in with {signIn.map((p) => p.name).join(' or ')}, then link {linkOnly.map((p) => p.name).join(' or ')} on the next step.
                </p>
              )}
            </div>
          </div>
        </section>

        <section id="how" style={{ background: 'var(--sc-low)' }}>
          <div style={{ maxWidth: 1200, margin: '0 auto', padding: '56px 24px', display: 'flex', flexDirection: 'column', gap: 32 }}>
            <h2 className="headline-m">How it works</h2>
            <div className="grid-auto">
              {HOW.map((s) => (
                <div key={s.title} className="card-elev" style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 12, background: 'var(--sc-lowest)' }}>
                  <span className="avatar" style={{ width: 48, height: 48, borderRadius: 16 }}><Icon name={s.icon} /></span>
                  <h3 className="title-l">{s.title}</h3>
                  <p className="body-m muted">{s.text}</p>
                </div>
              ))}
            </div>
            <div className="card-out" style={{ padding: '20px 24px', display: 'flex', flexWrap: 'wrap', gap: '12px 32px', alignItems: 'center' }}>
              <span className="title-s">What gets synced</span>
              <span className="body-m muted">Status · Episodes watched · Score · Rewatching · Start and finish dates</span>
            </div>
          </div>
        </section>

        <section style={{ maxWidth: 1200, margin: '0 auto', padding: '48px 24px', display: 'flex', gap: 16, alignItems: 'flex-start' }}>
          <span style={{ color: 'var(--primary)', flex: 'none' }}><Icon name="lock" size={28} /></span>
          <p className="body-l muted" style={{ maxWidth: 760 }}>
            AniSync never sees your MyAnimeList or AniList password. Your Kitsu password is exchanged once for an access token and
            never stored. <a href="/privacy">Read the privacy page</a>.
          </p>
        </section>
      </main>
      <Footer />
    </>
  )
}
