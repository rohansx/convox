const BASE = import.meta.env.VITE_API_BASE || '';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });

  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }

  return res.json();
}

// Health
export const getHealth = () => request<{ status: string; postgres: string; redis: string }>('/health');

// Agents
export const listAgents = () => request<{ agents: Agent[]; total: number }>('/v1/agents');
export const getAgent = (id: string) => request<Agent>(`/v1/agents/${id}`);

// Sessions
export const listSessions = () => request<{ sessions: Session[]; total: number }>('/v1/sessions');
export const getSession = (id: string) => request<Session>(`/v1/sessions/${id}`);

// Types (re-export from types/)
import type { Agent, Session } from '@/types';
