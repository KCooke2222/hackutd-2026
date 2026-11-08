import type { Explain } from '../../lib/api'

/** The agent's read of the first plan: what drives the debt and why each call. Figures come from code. */
export default function WhyPlan({ ex }: { ex?: Explain }) {
  if (!ex?.driver) return null
  const cuts = (ex.cuts ?? []).filter((c) => c.suggested && c.why)
  return (
    <div className="card">
      <h2 style={{ margin: '0 0 4px' }}>{ex.headline}</h2>
      {ex.summary && <p className="small muted" style={{ margin: '0 0 8px' }}>{ex.summary}</p>}
      <p style={{ margin: '0 0 8px' }}><b>What's driving it:</b> {ex.driver}</p>
      <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
        {ex.buffer_why && <li><b>Keep {ex.buffer_months} months of cash:</b> {ex.buffer_why}</li>}
        {ex.lumps_why && <li><b>Lump sums:</b> {ex.lumps_why}</li>}
        {cuts.map((c) => (
          <li key={c.category}><b>{c.category.replace(/_/g, ' ')}:</b> {c.why}{c.buys && <span className="muted"> ({c.buys})</span>}</li>
        ))}
      </ul>
      {!!ex.downsides?.length && <p className="small muted" style={{ margin: '8px 0 0' }}>Downsides: {ex.downsides.join(' ')}</p>}
    </div>
  )
}
