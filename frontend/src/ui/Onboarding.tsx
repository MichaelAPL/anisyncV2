import { Navigate, useSearchParams } from 'react-router'
import { useMe } from '../api/hooks'
import type { Me } from '../api/types'
import { Footer, ThemeButton, Wordmark } from './Chrome'

const STEPS = ['Link lists', 'Choose master', 'Review sync']

/** Renders children with the signed-in user; sends signed-out visitors to the landing page. */
export function RequireUser({ children }: { children: (me: Me) => React.ReactNode }) {
  const me = useMe()
  if (me.isPending) return <p className="body-l muted" style={{ padding: 24 }} role="status">Loading…</p>
  if (me.isError) return <p className="body-l" style={{ padding: 24 }} role="alert">Couldn't reach AniSyncHub. Try again in a moment.</p>
  if (!me.data) return <Navigate to="/" replace />
  return <>{children(me.data)}</>
}

const ERRORS: Record<string, string> = {
  account_already_linked: 'That account is already linked to another AniSyncHub user.',
  account_banned: 'This account has been suspended.',
  token_exchange_failed: "The sign-in didn't complete. Please try again.",
  invalid_state: 'The sign-in expired. Please try again.',
  access_denied: 'Access was not granted, so nothing was linked.',
  missing_code: "The sign-in didn't complete. Please try again.",
}

/** Error from an OAuth round trip (`?error=...`), shown as plain text. */
export function ReturnError() {
  const [params] = useSearchParams()
  const code = params.get('error')
  if (!code) return null
  return (
    <div className="banner banner-warn" role="alert" style={{ borderRadius: 16, padding: '16px 20px' }}>
      <span className="body-m">{ERRORS[code] ?? 'Something went wrong. Please try again.'}</span>
    </div>
  )
}

export function OnboardingLayout({ step, wide, children }: { step: 1 | 2 | 3; wide?: boolean; children: React.ReactNode }) {
  const pct = Math.round((step / 3) * 100)
  return (
    <>
      <header style={{ maxWidth: 760, width: '100%', margin: '0 auto', padding: '16px 24px 0', display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
          <Wordmark />
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="label-l muted">Step {step} of 3 · {STEPS[step - 1]}</span>
            <ThemeButton />
          </span>
        </div>
        <div className="progress" role="progressbar" aria-label="Setup progress" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ width: `${pct}%` }} />
        </div>
      </header>
      <main className={wide ? 'page' : 'page page-narrow'} style={{ paddingTop: 32, ...(wide ? { maxWidth: 960 } : {}) }}>
        {children}
      </main>
      <Footer />
    </>
  )
}
