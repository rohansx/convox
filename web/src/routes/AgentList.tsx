import { Link } from 'react-router-dom';
import { Plus } from 'lucide-react';

export default function AgentList() {
  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">Agents</h2>
        <Link
          to="/app/agents/new"
          className="flex items-center gap-1.5 px-4 py-2 bg-accent hover:bg-accent-hover text-white rounded-lg text-sm font-medium transition-colors"
        >
          <Plus size={16} />
          New Agent
        </Link>
      </div>

      <div className="bg-bg-card border border-border rounded-lg p-8 text-center">
        <p className="text-text-muted">No agents yet. Create your first voice agent to get started.</p>
      </div>
    </div>
  );
}
