import { Navigate } from 'react-router'
import { useMe } from '../api/hooks'

export function Dashboard() {
  const me = useMe()
  if (me.isPending) return <p className="body-l" style={{ padding: 24 }}>Loading…</p>
  if (!me.data) return <Navigate to="/" replace />
  return <h1 className="headline-m" style={{ padding: 24 }}>Hello, {me.data.display_name}</h1>
}
