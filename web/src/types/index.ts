export interface Agent {
  id: string;
  name: string;
  description: string | null;
  config: Record<string, unknown>;
  user_id: string;
  created_at: string;
  updated_at: string;
}

export interface Session {
  id: string;
  agent_id: string;
  direction: 'inbound' | 'outbound';
  status: 'pending' | 'active' | 'completed' | 'failed';
  caller_number: string | null;
  telephony_provider: string | null;
  cost_usd_total: number;
  started_at: string | null;
  ended_at: string | null;
  user_id: string;
  created_at: string;
}

export interface Transcript {
  id: string;
  session_id: string;
  turn_index: number;
  speaker: 'user' | 'agent';
  text: string;
  timestamp_ms: number;
  stt_provider: string | null;
  stt_confidence: number | null;
  stt_cost_usd: number;
  created_at: string;
}
