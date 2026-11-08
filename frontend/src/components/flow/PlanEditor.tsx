import type { ReactNode } from 'react'
import { usd } from '../../lib/api'
import type { Proposal, SimDebt, SimV2 } from '../../lib/api'
import { CONTRIB_SHORT, SOURCE_NAME, knobsFrom, title, ym } from '../../lib/flow'
import type { Knobs, Preview } from '../../lib/flow'
import NetWorth from './NetWorth'
import { ACCENT, GOOD, GREY } from '../../lib/chart'

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
            <span style={{ gridColumn: '2 / 4', height: 6, background: 'var(--soft)', borderRadius: 3, overflow: 'hidden' }}>
              <span style={{ display: 'block', height: '100%', width: `${w}%`, background: i >= 0 ? ACCENT : GREY, borderRadius: 3 }} />
            </span>
          </div>
        )
      })}
    </div>
  )
}

export default function PlanEditor({ ai, knobs, setKnobs, preview, busy, error, onApprove, onReject, chat }: {
  ai: Proposal; knobs: Knobs; setKnobs: (k: Knobs) => void; preview: Preview | null
  busy: boolean; error: string | null; onApprove: () => void; onReject: () => void; chat?: ReactNode
}) {
  const aiK = knobsFrom(ai)
  const sim = preview?.sim ?? ai.simulation
  const pl = ai.plan
  const b = sim.budget
  // Slider ranges come from the AI plan only: if they followed the dragged value (or the live preview),
  // the scale would stretch under the thumb and it would run away while dragging.
  const aiB = ai.simulation.budget
  const minimums = aiB?.minimums ?? ai.simulation.debts.reduce((a, d) => a + (d.min_payment ?? 0), 0)
  const budgetMax = roundUp(Math.max(aiB?.max_monthly_debt_budget ?? 0, aiK.budget) * 1.05, 50)
  const redirectMax = roundUp(Math.max(aiK.redirect, 500) * 1.25, 100)
  const debtNames = Object.fromEntries(sim.debts.map((d) => [d.account_id, d.name]))
  // Rows from the live preview (resume month moves with the plan), falling back to the agent's.
  const contribs = sim.contributions ?? ai.simulation.contributions ?? []
  const highCost = sim.debts.some((d) => d.apr_pct > 7 && d.balance > 0)

  const sliders: SliderDef[] = [
    { label: 'Paid to all debts', min: Math.ceil(minimums), max: budgetMax, step: 25, value: knobs.budget, ai: aiK.budget,
      meta: b ? `min ${usd(minimums)} · room ${usd(b.max_monthly_debt_budget)}` : '', onChange: (v) => setKnobs({ ...knobs, budget: v }) },
  ]
  if (aiK.redirect > 0 || knobs.redirect > 0)
    sliders.push({ label: 'Savings & investing to debt', min: 0, max: redirectMax, step: 50, value: knobs.redirect, ai: aiK.redirect, meta: '',
      onChange: (v) => setKnobs({ ...knobs, redirect: v }) })

  const reasons = preview ? [...preview.reasons, ...(preview.sim.problems ?? [])] : []
  const valid = preview ? preview.valid : true
  return (
    <>
      <div className="card">
        <h2>Plan</h2>
        <NetWorth sim={sim} baseline={preview?.baseline ?? null} />
      </div>

      {chat}

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

      {contribs.length > 0 && (
        <div className="card">
          <h3 style={{ margin: '0 0 6px' }}>Contributions</h3>
          <div className="small muted" style={{ marginBottom: 8 }}>
            {highCost ? 'Paused while any debt costs more than 7% a year; they restart automatically once it is $0.' : 'No debt costs more than 7% a year, so contributions keep going.'}
          </div>
          {contribs.map((c) => {
            const room = Math.max(c.per_month - c.by_option, 0)
            const v = knobs.contrib?.[c.flow] ?? 0
            const locked = room < 0.5
            const pick = (n: number) => setKnobs({ ...knobs, contrib: { ...knobs.contrib, [c.flow]: n }, budget: Math.max(knobs.budget + n - v, minimums) })
            const choices: [string, number][] = [['Pause', room], ...(c.dest === 'ira' ? [['Cut in half', room / 2] as [string, number]] : []), ['Keep', 0]]
            return (
              <div key={c.flow} className="sl">
                <div className="top"><span>{CONTRIB_SHORT[c.dest] ?? c.label} <span className="small muted">{usd(c.per_month)}/mo</span></span>
                  {locked ? <span className="small muted">paused by an option you applied</span> : (
                    <div className="seg" role="radiogroup" aria-label={`${c.label} choice`}>
                      {choices.map(([lbl, n]) => (
                        <button key={lbl} className={Math.abs(v - n) < 0.5 ? 'on' : ''} role="radio" aria-checked={Math.abs(v - n) < 0.5} onClick={() => pick(n)}>{lbl}</button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="meta">{v > 0.5 || locked
                  ? `${usd(v + c.by_option)}/mo to debt${c.resume ? ` until ${ym(c.resume)}; restarts automatically` : ''}`
                  : 'keeps going as today'}</div>
              </div>
            )
          })}
          <div className="meta" style={{ marginTop: 6 }}>An employer 401(k) match is never paused.</div>
        </div>
      )}

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
