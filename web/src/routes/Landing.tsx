import { Link } from 'react-router-dom'
import { Radio, ArrowRight } from 'lucide-react'

export default function Landing() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-8">
      <div className="flex items-center gap-3 mb-4">
        <Radio size={24} className="text-accent" strokeWidth={1.5} />
        <h1 className="text-4xl font-bold tracking-tight" style={{ fontFamily: 'var(--font-display)' }}>
          convox
        </h1>
      </div>
      <p className="text-text-muted text-lg mb-8">Voice AI orchestration platform</p>
      <Link
        to="/app"
        className="inline-flex items-center gap-2 px-6 py-2.5 bg-accent hover:bg-accent-hover text-white rounded-lg text-sm font-medium transition-colors"
      >
        Open Dashboard
        <ArrowRight size={16} />
      </Link>
    </div>
  )
}
