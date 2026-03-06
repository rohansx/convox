import { BrowserRouter, Routes, Route } from 'react-router-dom';
import DashboardLayout from '@/components/layout/DashboardLayout';
import Landing from '@/routes/Landing';
import Overview from '@/routes/Overview';
import AgentList from '@/routes/AgentList';
import SessionList from '@/routes/SessionList';
import {
  Analytics,
  Providers,
  Compliance,
  SettingsPage,
  AgentNew,
  AgentDetail,
  SessionDetail,
} from '@/routes/Placeholder';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />

        <Route path="/app" element={<DashboardLayout />}>
          <Route index element={<Overview />} />
          <Route path="agents" element={<AgentList />} />
          <Route path="agents/new" element={<AgentNew />} />
          <Route path="agents/:id" element={<AgentDetail />} />
          <Route path="sessions" element={<SessionList />} />
          <Route path="sessions/:id" element={<SessionDetail />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="providers" element={<Providers />} />
          <Route path="compliance" element={<Compliance />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
