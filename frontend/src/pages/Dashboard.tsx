import { Link, Navigate } from 'react-router'
import { RequireUser } from '../ui/Onboarding'

const SKIP = 'anisync-skip-onboarding'
const skipped = () => {
  try { return localStorage.getItem(SKIP) === '1' } catch { return false }
}

/** Placeholder until the dashboard is built: new users with fewer than two lists go through setup first. */
export function Dashboard() {
  return (
    <RequireUser>
      {(me) =>
        me.accounts.length < 2 && !skipped() ? (
          <Navigate to="/link" replace />
        ) : (
          <main style={{ padding: 24 }}>
            <h1 className="headline-m">Hello, {me.display_name}</h1>
            <p className="body-l muted"><Link to="/link">Link lists</Link></p>
          </main>
        )
      }
    </RequireUser>
  )
}
