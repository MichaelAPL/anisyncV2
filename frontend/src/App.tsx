import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router'
import { Dashboard } from './pages/Dashboard'
import { AppShell } from './ui/Shell'
import { FirstSync } from './pages/FirstSync'
import { ListEditor } from './pages/ListEditor'
import { Landing } from './pages/Landing'
import { LinkLists } from './pages/LinkLists'
import { PickMaster } from './pages/PickMaster'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } })

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/link" element={<LinkLists />} />
          <Route path="/master" element={<PickMaster />} />
          <Route path="/review" element={<FirstSync />} />
          <Route element={<AppShell />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/list" element={<ListEditor />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
