import type { Tone } from '../../labels'

interface Stat {
  label: string
  value: React.ReactNode
  tone?: Tone
}

/** A row of headline figures — replaces the old `.metrics`/`.metric` markup. */
function StatRow({ stats }: { stats: Stat[] }) {
  return (
    <div className="stats">
      {stats.map((s) => (
        <div className="stat" key={s.label}>
          <div className={`stat__value stat__value--${s.tone ?? 'neutral'}`}>{s.value}</div>
          <div className="stat__label">{s.label}</div>
        </div>
      ))}
    </div>
  )
}

export default StatRow
