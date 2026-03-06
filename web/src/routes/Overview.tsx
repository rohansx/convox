import { useEffect, useState } from 'react';
import { getHealth } from '@/lib/api';

export default function Overview() {
  const [health, setHealth] = useState<{ status: string; postgres: string; redis: string } | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">Overview</h2>

      <div className="grid grid-cols-3 gap-4 mb-8">
        <StatCard label="Active Calls" value="0" />
        <StatCard label="Total Cost (USD)" value="$0.00" />
        <StatCard label="Calls Today" value="0" />
      </div>

      <div className="bg-bg-card border border-border rounded-lg p-4">
        <h3 className="text-sm font-medium text-text-muted mb-2">System Health</h3>
        {health ? (
          <div className="space-y-1 text-sm">
            <div>Status: <span className={health.status === 'ok' ? 'text-green-500' : 'text-amber-500'}>{health.status}</span></div>
            <div>Postgres: <span className={health.postgres === 'ok' ? 'text-green-500' : 'text-red-500'}>{health.postgres}</span></div>
            <div>Redis: <span className={health.redis === 'ok' ? 'text-green-500' : 'text-text-dim'}>{health.redis}</span></div>
          </div>
        ) : (
          <p className="text-sm text-text-dim">Connecting to API...</p>
        )}
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-bg-card border border-border rounded-lg p-4">
      <div className="text-sm text-text-muted">{label}</div>
      <div className="text-2xl font-bold mt-1">{value}</div>
    </div>
  );
}
