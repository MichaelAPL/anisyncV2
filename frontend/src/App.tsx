import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes, useLocation } from 'react-router'
import { AdminShell } from './admin/AdminShell'
import { Overview } from './admin/Overview'
import { Syncs } from './admin/Syncs'
import { UserDetail, Users } from './admin/Users'
import { Providers, SitePage, Titles } from './admin/Site'
import { Dashboard } from './pages/Dashboard'
import { AppShell } from './ui/Shell'
import { useMe, useSite } from './api/hooks'
import { FirstSync, PreviewChanges } from './pages/FirstSync'
import { ListEditor } from './pages/ListEditor'
import { Settings } from './pages/Settings'
import { Maintenance, NotFound, NotOpen, Privacy } from './pages/Static'
import { Landing } from './pages/Landing'
import { LinkLists } from './pages/LinkLists'
import { PickMaster } from './pages/PickMaster'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } })

/** While maintenance mode is on, everyone except admins sees the maintenance page (the privacy page stays reachable). */
function Gate({ children }: { children: React.ReactNode }) {
  const site = useSite()
  const me = useMe()
  const { pathname } = useLocation()
  const blocked = site.data?.maintenance && !me.isPending && !(me.data && me.data.permissions.length > 0) && pathname !== '/privacy'
  return blocked ? <Maintenance /> : <>{children}</>
}

export function AppRoutes() {
  return (
    <Gate>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/link" element={<LinkLists />} />
          <Route path="/master" element={<PickMaster />} />
          <Route path="/review" element={<FirstSync />} />
          <Route element={<AppShell />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/preview" element={<PreviewChanges />} />
            <Route path="/list" element={<ListEditor />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
          <Route path="/admin" element={<AdminShell />}>
            <Route index element={<Overview />} />
            <Route path="syncs" element={<Syncs />} />
            <Route path="users" element={<Users />} />
            <Route path="users/:id" element={<UserDetail />} />
            <Route path="titles" element={<Titles />} />
            <Route path="providers" element={<Providers />} />
            <Route path="site" element={<SitePage />} />
          </Route>
          <Route path="/privacy" element={<Privacy />} />
          <Route path="/not-open" element={<NotOpen />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
    </Gate>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
