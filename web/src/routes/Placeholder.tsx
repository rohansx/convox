export function Analytics() {
  return <PageStub title="Analytics" description="Cost and performance dashboards coming soon." />;
}

export function Providers() {
  return <PageStub title="Providers" description="Configure STT, LLM, TTS, and telephony providers." />;
}

export function Compliance() {
  return <PageStub title="Compliance" description="DPDP consent logs and audit exports." />;
}

export function SettingsPage() {
  return <PageStub title="Settings" description="API keys, webhooks, and team management." />;
}

export function AgentNew() {
  return <PageStub title="New Agent" description="Agent creation form coming soon." />;
}

export function AgentDetail() {
  return <PageStub title="Agent Detail" description="Agent configuration and test call." />;
}

export function SessionDetail() {
  return <PageStub title="Session Detail" description="Transcript viewer and cost breakdown." />;
}

function PageStub({ title, description }: { title: string; description: string }) {
  return (
    <div>
      <h2 className="text-2xl font-bold mb-2">{title}</h2>
      <p className="text-text-muted">{description}</p>
    </div>
  );
}
