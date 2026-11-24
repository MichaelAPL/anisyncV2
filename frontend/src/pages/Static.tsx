import { Link } from 'react-router'
import { useProviders, useSite } from '../api/hooks'
import { Footer, ThemeButton, Wordmark } from '../ui/Chrome'
import { Icon } from '../ui/Icon'
import type { IconName } from '../ui/icons'

function PublicHeader() {
  return (
    <header style={{ display: 'flex', alignItems: 'center', gap: 16, maxWidth: 1200, width: '100%', margin: '0 auto', padding: '16px 24px' }}>
      <Wordmark />
      <span style={{ marginLeft: 'auto' }}><ThemeButton /></span>
    </header>
  )
}

const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}><h2 className="title-l">{title}</h2>{children}</section>
)
const ul = { paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 6 } as const

export function Privacy() {
  return (
    <>
      <PublicHeader />
      <main className="page page-narrow" style={{ paddingTop: 32, gap: 32 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <h1 className="display-m">Privacy</h1>
          <p className="body-l muted">What AniSyncHub keeps, what it never keeps, and how to delete it.</p>
        </div>
        <Section title="What AniSyncHub stores">
          <ul className="body-l" style={ul}>
            <li>Your usernames and account IDs on the sites you link.</li>
            <li>Access tokens for those accounts, encrypted. They let AniSyncHub read and update your lists.</li>
            <li>A copy of your list entries, used only to work out what needs syncing.</li>
            <li>Sync history (what changed and when), kept for 30 days.</li>
          </ul>
        </Section>
        <Section title="What it never stores">
          <ul className="body-l" style={ul}>
            <li>Your MyAnimeList or AniList password. You sign in on their sites, not here.</li>
            <li>Your Kitsu password. It goes to Kitsu once in exchange for a token and is then discarded.</li>
            <li>Your email address.</li>
          </ul>
        </Section>
        <Section title="Deleting your data">
          <p className="body-l">Settings → Delete account removes your AniSyncHub account, tokens, list copies and sync history right away. Unlinking a single account deletes its token and list copy. Your lists on MyAnimeList, AniList and Kitsu are never touched.</p>
        </Section>
        <Section title="Cookies"><p className="body-l">One cookie keeps you signed in. No tracking or advertising cookies.</p></Section>
        <Section title="Data sources">
          <p className="body-l">
            Anime ID mappings come from the <a href="https://github.com/cedya77/anime-offline-database">cedya77 anime-offline-database</a>, the maintained continuation of the{' '}
            <a href="https://github.com/manami-project/anime-offline-database">manami-project anime-offline-database</a>. Both are available under the ODbL 1.0 licence.
          </p>
        </Section>
        <Section title="Contact"><p className="body-l">Questions or deletion requests: [CONTACT EMAIL]</p></Section>
      </main>
      <Footer />
    </>
  )
}

/** The big round icon at the top of the status pages (maintenance, not open yet). */
function StatusBadge({ icon }: { icon: IconName }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 96, height: 96, borderRadius: 32, background: 'var(--tertiary-container)', color: 'var(--on-tertiary-container)' }}>
      <Icon name={icon} size={44} stroke={1.8} />
    </span>
  )
}

export function Maintenance() {
  const { data: site } = useSite()
  const { data: providers } = useProviders()
  const signIn = providers?.find((p) => p.can_sign_in)
  return (
    <>
      <PublicHeader />
      <main className="page page-narrow" style={{ alignItems: 'center', textAlign: 'center', paddingTop: 64, gap: 20 }}>
        <StatusBadge icon="wrench" />
        <h1 className="headline-l">AniSyncHub is down for maintenance</h1>
        <p className="body-l muted" style={{ maxWidth: 520 }}>Syncing is paused and list editing is off for now. Your lists on MyAnimeList, AniList and Kitsu are untouched.</p>
        {site?.maintenance_message && (
          <div className="card" style={{ padding: '16px 20px', display: 'flex', gap: 12, alignItems: 'flex-start', textAlign: 'left', maxWidth: 520 }}>
            <Icon name="info" size={20} style={{ flex: 'none', marginTop: 2, color: 'var(--primary)' }} />
            <p className="body-m"><strong>Update from the team:</strong> {site.maintenance_message}</p>
          </div>
        )}
        {signIn && <a href={`/auth/${signIn.id}/login`} className="btn btn-text">Admin sign-in</a>}
      </main>
      <Footer />
    </>
  )
}

export function NotFound() {
  return (
    <>
      <PublicHeader />
      <main className="page page-narrow" style={{ alignItems: 'center', textAlign: 'center', paddingTop: 64, gap: 16 }}>
        <h1 className="headline-l">We couldn't find that page</h1>
        <p className="body-l muted">The link may be old, or the page may have moved.</p>
        <Link to="/" className="btn btn-filled">Go to AniSyncHub</Link>
      </main>
      <Footer />
    </>
  )
}

/** Where a sign-in lands when the account isn't invited yet (the backend refused to create a user for it). */
export function NotOpen() {
  return (
    <>
      <PublicHeader />
      <main className="page page-narrow" style={{ alignItems: 'center', textAlign: 'center', paddingTop: 64, gap: 20 }}>
        <StatusBadge icon="lock" />
        <h1 className="headline-l">AniSyncHub isn't open yet</h1>
        <p className="body-l muted" style={{ maxWidth: 520 }}>Only invited accounts can sign up for now, so no AniSyncHub account was created and nothing was saved. Your lists on MyAnimeList, AniList and Kitsu are untouched.</p>
        <Link to="/" className="btn btn-filled">Back to AniSyncHub</Link>
      </main>
      <Footer />
    </>
  )
}
