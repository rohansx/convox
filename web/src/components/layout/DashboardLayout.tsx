import { NavLink, Outlet } from 'react-router-dom';
import { LayoutDashboard, Bot, Phone, BarChart3, Blocks, Shield, Settings } from 'lucide-react';

const nav = [
  { to: '/app', icon: LayoutDashboard, label: 'Overview', end: true },
  { to: '/app/agents', icon: Bot, label: 'Agents' },
  { to: '/app/sessions', icon: Phone, label: 'Sessions' },
  { to: '/app/analytics', icon: BarChart3, label: 'Analytics' },
  { to: '/app/providers', icon: Blocks, label: 'Providers' },
  { to: '/app/compliance', icon: Shield, label: 'Compliance' },
  { to: '/app/settings', icon: Settings, label: 'Settings' },
];

export default function DashboardLayout() {
  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <aside className="w-56 border-r border-border bg-bg-card flex flex-col">
        <div className="p-4 border-b border-border">
          <h1 className="text-lg font-bold tracking-tight">convox</h1>
          <p className="text-xs text-text-dim mt-0.5">voice ai platform</p>
        </div>
        <nav className="flex-1 p-2 space-y-0.5">
          {nav.map(({ to, icon: Icon, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive
                    ? 'bg-accent/10 text-accent'
                    : 'text-text-muted hover:text-text-bright hover:bg-bg-elevated'
                }`
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto bg-bg">
        <div className="max-w-6xl mx-auto p-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
