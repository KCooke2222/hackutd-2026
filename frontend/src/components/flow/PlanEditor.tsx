import { usd } from '../../lib/api'
import type { Proposal, SimDebt, SimV2 } from '../../lib/api'
import { SOURCE_NAME, knobsFrom, title, ym } from '../../lib/flow'
import type { Knobs, Preview } from '../../lib/flow'
import NetWorth, { ACCENT, GOOD, GREY } from './NetWorth'

type SliderDef = { label: string; min: number; max: number; step: number; value: number; ai: number; meta: string; onChange: (v: number) => void }

function Slider({ s }: { s: SliderDef }) {
  const p = s.max > s.min ? Math.min(1, Math.max(0, (s.ai - s.min) / (s.max - s.min))) : 0
  return (
    <div className="sl">
      <div className="top"><span>{s.label}</span><span className="val">{usd(s.value)}/mo</span></div>
      <div className="track">
        <input type="range" aria-label={s.label} min={s.min} max={s.max} step={s.step} value={s.value} onChange={(e) => s.onChange(+e.target.value)} />
        <span className="tick" style={{ left: `calc(8px + (100% - 16px) * ${p})` }} title={`AI: ${usd(s.ai)}`} />
      </div>
      {s.meta && <div className="meta">{s.meta}</div>}
    </div>
  )
}

const H = 24
const monthsTo = (start: string, m: string | null) => (m ? (+m.slice(0, 4) * 12 + +m.slice(5, 7)) - (+start.slice(0, 4) * 12 + +start.slice(5, 7)) : null)
const roundUp = (n: number, to: number) => Math.ceil(n / to) * to

/** Every debt, in payoff order: rank (extra money) or "min", APR, balance, a bar to its $0 month. */
function DebtStack({ sim, order }: { sim: SimV2; order: string[] }) {
  const start = sim.months[0]?.month ?? ''
  const rank = (d: SimDebt) => { const i = order.indexOf(d.account_id); return i < 0 ? 99 + (100 - d.apr_pct) : i }
  const debts = [...sim.debts].sort((a, b) => rank(a) - rank(b))
  return (
    <div style={{ display: 'grid', gap: 6, marginTop: 4 }}>
      {debts.map((d) => {
        const i = order.indexOf(d.account_id)
        const n = monthsTo(start, d.payoff_month)
        const w = n == null ? 100 : Math.max(3, (n / H) * 100)
        return (
          <div key={d.account_id} style={{ display: 'grid', gridTemplateColumns: '28px 1fr auto', gap: '2px 8px', alignItems: 'center' }}>
            <span className="small" style={{ fontWeight: 700, color: i >= 0 ? ACCENT : GREY }}>{i >= 0 ? `#${i + 1}` : 'min'}</span>
            <span>{d.name} <span className="small muted">{d.apr_pct}% · {usd(d.balance)}</span></span>
            <b className={`num ${n != null && n <= H ? 'good' : ''}`} style={{ fontSize: 13 }}>{d.payoff_month ? `$0 ${ym(d.payoff_month)}` : '> 2 yrs'}</b>
            <span />
            <span style={{ gridColumn: '2 / 4', height: 6, background: '#f3f4f6', borderRadius: 3, overflow: 'hidden' }}>
              <span style={{ display: 'block', height: '100%', width: `${w}%`, background: i >= 0 ? ACCENT : GREY, borderRadius: 3 }} />
            </span>
          </div>
        )
      })}
    </div>
  )
}

export default function PlanEditor({ ai, headline, knobs, setKnobs, preview, busy, error, onApprove, onReject }: {
  ai: Proposal; headline: string; knobs: Knobs; setKnobs: (k: Knobs) => void; preview: Preview | null
  busy: boolean; error: string | null; onApprove: () => void; onReject: () => void
}) {
  const aiK = knobsFrom(ai)
  const sim = preview?.sim ?? ai.simulation
  const pl = ai.plan
  const b = sim.budget
  const minimums = b?.minimums ?? sim.debts.reduce((a, d) => a + (d.min_payment ?? 0), 0)
  const budgetMax = roundUp(Math.max(b?.max_monthly_debt_budget ?? 0, aiK.budget, knobs.budget) * 1.05, 50)
  const redirectMax = roundUp(Math.max(aiK.redirect, knobs.redirect, 500) * 1.25, 100)
  const debtNames = Object.fromEntries(sim.debts.map((d) => [d.account_id, d.name]))

  const sliders: SliderDef[] = [
    { label: 'Paid to all debts', min: Math.ceil(minimums), max: budgetMax, step: 25, value: knobs.budget, ai: aiK.budget,
      meta: b ? `min ${usd(minimums)} · room ${usd(b.max_monthly_debt_budget)}` : '', onChange: (v) => setKnobs({ ...knobs, budget: v }) },
  ]
  if (aiK.redirect > 0 || knobs.redirect > 0)
    sliders.push({ label: 'Redirect unknown transfers', min: 0, max: redirectMax, step: 50, value: knobs.redirect, ai: aiK.redirect, meta: '',
      onChange: (v) => setKnobs({ ...knobs, redirect: v }) })

  const reasons = preview ? [...preview.reasons, ...(preview.sim.problems ?? [])] : []
  const valid = preview ? preview.valid : true
  return (
    <>
      <div className="card">
        <div className="col-head">
          <h2 style={{ margin: 0 }}>Plan</h2>
          <button className="ghost" onClick={() => setKnobs(aiK)}>Reset to AI</button>
        </div>
        {headline && <p className="why1">{headline}</p>}
        <NetWorth sim={sim} baseline={preview?.baseline ?? null} />
      </div>

      <div className="card">
        <h3 style={{ margin: '0 0 6px' }}>Debts</h3>
        <DebtStack sim={sim} order={pl.extra_order} />
        <div className="small muted" style={{ marginTop: 8 }}>
          {pl.lump_sums.length
            ? pl.lump_sums.map((l) => `${SOURCE_NAME[l.source]} ${usd(l.amount)} → ${debtNames[l.to_debt] ?? l.to_debt}`).join(' · ') + ` · one-time cost ${usd(sim.upfront_cost)}`
            : 'No savings or investments used'}
          {pl.autopay_full_cards.length > 0 && ` · autopay full once at $0`}
        </div>
        {sliders.map((s) => <Slider key={s.label} s={s} />)}
      </div>

      {pl.spending_targets.length > 0 && (
        <div className="card">
          <h3 style={{ margin: '0 0 6px' }}>Spending</h3>
          {pl.spending_targets.map((t) => {
            const v = knobs.cuts[t.category] ?? t.target
            const lo = Math.floor(t.current * 0.7)
            return (
              <div key={t.category} className="sl">
                <div className="top"><span>{title(t.category)}</span>
                  <span className="val">{usd(v)} <span className="small good">−{usd(t.current - v)}</span></span></div>
                <div style={{ display: 'grid', gap: 3, marginTop: 6 }}>
                  <span style={{ height: 8, width: '100%', background: GREY, borderRadius: 4, opacity: 0.5 }} title={`now ${usd(t.current)}`} />
                  <span style={{ height: 8, width: `${(v / t.current) * 100}%`, background: GOOD, borderRadius: 4 }} title={`target ${usd(v)}`} />
                </div>
                <div className="track" style={{ marginTop: 10 }}>
                  <input type="range" aria-label={`${t.category} target`} min={lo} max={Math.ceil(t.current)} step={5} value={v}
                    onChange={(e) => setKnobs({ ...knobs, cuts: { ...knobs.cuts, [t.category]: +e.target.value } })} />
                </div>
                <div className="meta">now {usd(t.current)} · spent ≤ target in {ym(t.proof_month)}{t.how.length ? ` · ${t.how.join(' · ')}` : ''}</div>
              </div>
            )
          })}
        </div>
      )}

      <div className="card">
        <div className="ba">
          <div><div className="k">High-cost $0</div><div className="from">{ym(preview?.baseline.high_cost_debt_free ?? null)}</div><div className="to good">{ym(sim.high_cost_debt_free)}</div></div>
          <div><div className="k">Interest, 2 yrs</div><div className="from">{usd(sim.baseline_interest_total)}</div><div className="to">{usd(sim.interest_total)}</div></div>
          <div><div className="k">Net worth, 2 yrs</div><div className="from">{usd(sim.baseline_net_worth_24m)}</div><div className="to">{usd(sim.net_worth_24m)}</div></div>
        </div>
        {(error || reasons.length > 0) && <div className="error">{error ?? reasons.slice(0, 3).join(' · ')}</div>}
        <div className="actions">
          <button className="primary big" onClick={onApprove} disabled={busy || !valid}>{busy ? 'Approving…' : 'Approve'}</button>
          <button className="big" onClick={onReject} disabled={busy}>Reject</button>
        </div>
      </div>
    </>
  )
}
