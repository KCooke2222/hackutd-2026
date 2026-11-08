import { useEffect, useRef, useState } from 'react'
import { CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ArrowRight } from 'lucide-react'
import { usd } from '../../lib/api'
import type { PlanV2, Proposal } from '../../lib/api'
import { ym, ymShort } from '../../lib/flow'
import { C, GREY, short } from '../../lib/chart'
import { chatEdit, cushions } from '../../lib/whatif'
import type { CushionOption, Cushions } from '../../lib/whatif'

const SAVINGS = C.invested
const DEBT = C.debt
const NAMES: Record<string, string> = { cash: 'Savings', debt: 'High-cost debt', jcash: 'Savings, job loss', jdebt: 'High-cost debt, job loss' }

const months = (n: number) => `${n % 1 ? n.toFixed(1) : n} month${n === 1 ? '' : 's'}`
const signed = (v: number) => `${v < 0 ? '−' : '+'}${usd(Math.abs(v))}`

/** "What if" on the Plan step, built around one choice: how many months of expenses to keep in savings.
 *  Every number comes from the solver (scenarios.cushion_options), on the plan as edited. Nothing here changes
 *  the plan; "Use N months" sends the change through chat, and nothing moves until approve. */
export default function Scenarios({ cid, plan, ai, onAdjusted, onShowPlan }: {
  cid: string
  plan: PlanV2
  ai: Proposal
  onAdjusted: (p: Proposal) => void
  onShowPlan: () => void
}) {
  const [data, setData] = useState<Cushions | null>(null)
  const [sel, setSel] = useState<number>(plan.keep_emergency_months)
  const [jobLoss, setJobLoss] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null) // the re-plan's latest step while it runs
  const [updated, setUpdated] = useState<number | null>(null)
  const edit = useRef<{ cancel: () => void } | null>(null)

  const seq = useRef(0)
  const planKey = JSON.stringify(plan)
  useEffect(() => {
    const n = ++seq.current
    const t = setTimeout(() => {
      cushions(cid, plan)
        .then((r) => { if (n === seq.current) { setData(r); setErr(null) } })
        .catch((e) => n === seq.current && setErr(e.message))
    }, 200)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid, planKey])
  useEffect(() => { setSel(plan.keep_emergency_months) }, [cid, plan.keep_emergency_months])
  useEffect(() => () => edit.current?.cancel(), [])

  function use(n: number) {
    setBusy('Re-planning'); setErr(null); setUpdated(null)
    const e = chatEdit(ai.id, `Keep ${n} months of expenses in savings`, (label) => setBusy(label))
    edit.current = e
    e.done
      .then((p) => { onAdjusted(p); setUpdated(n) })
      .catch((x) => setErr((x as Error).message))
      .finally(() => setBusy(null))
  }

  if (!data) return <div className="card"><h2>What if</h2><p className="small muted">{err ?? 'Working out each cushion…'}</p></div>

  const base = data.options.find((o) => o.current) ?? data.options[0]
  const o = data.options.find((x) => x.months === sel) ?? base
  const runsOut = base.min_cash < 0
  const canUse = !data.same_plan && !o.current && ai.status === 'pending'

  return (
    <div className="card">
        <div className="col-head">
          <h2 style={{ margin: '10px 0 4px' }}>How much cash to keep?</h2>
          <span className="small muted">{data.monthly_expenses ? `1 month of expenses ≈ ${usd(data.monthly_expenses)}` : 'more cash = safer, a bit slower'}</span>
        </div>
        <div className="seg" role="tablist" aria-label="Months of expenses kept in savings">
          {data.options.map((x) => (
            <button key={x.months} role="tab" aria-selected={x.months === o.months} className={x.months === o.months ? 'on' : ''} onClick={() => { setSel(x.months); setUpdated(null) }}>
              {months(x.months)}
            </button>
          ))}
        </div>

        <Tiles o={o} base={base} />
        <Chart o={o} jobLoss={jobLoss} setJobLoss={setJobLoss} />

        <div className="k" style={{ margin: '16px 0 6px' }}>If something goes wrong</div>
        <div className="scn-grid">
          {o.shocks.map((s) => (
            <div key={s.id} className="scn" style={{ cursor: 'default' }}>
              <span className="lbl">{s.label}</span>
              <span><span className={`tag ${s.borrowed_on_card > 0.5 ? 'red' : 'good'}`}>{s.borrowed_on_card > 0.5 ? `${usd(s.borrowed_on_card)} on the card` : 'no card'}</span></span>
              <span className="sub">{s.borrowed_on_card > 0.5 ? 'savings run out first' : `savings dip to ${usd(s.min_cash)}`}</span>
            </div>
          ))}
        </div>

        {data.same_plan && (
          <div className="notice small">
            {runsOut
              ? <>Even with nothing going wrong, savings run out by <b>{ym(base.min_cash_month)}</b>: this plan spends more than comes in, so there is no spare cash to keep or pay with. Every cushion gives the same plan; closing the monthly gap (spending or income) is what changes it.</>
              : base.below_buffer
                ? <>This plan already spends savings down to <b>{usd(base.min_cash)}</b> by {ym(base.min_cash_month)} (more goes out than comes in), so no cushion frees cash to pay debt with today. Every cushion gives the same plan; closing the monthly gap is what changes it.</>
                : <>Savings aren't above a {data.options[0].months}-month cushion, so there's no spare cash to pay debt with today. Every cushion gives the same plan.</>}
          </div>
        )}

        {updated != null ? (
          <div className="notice small updated">
            <span><b>Plan updated:</b> keeps {months(updated)} of expenses in savings. Nothing moves until you approve.</span>
            <button className="ghost" style={{ whiteSpace: 'nowrap' }} onClick={onShowPlan}>See plan <ArrowRight size={14} strokeWidth={2} className="ic-inline" /></button>
          </div>
        ) : canUse ? (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '12px 0 10px', flexWrap: 'wrap' }}>
            <button className="primary" disabled={busy != null} onClick={() => use(o.months)}>{busy ? 'Re-planning…' : `Use ${months(o.months)}`}</button>
            <span className="small muted">{busy ?? 're-plans through chat, nothing moves until you approve'}</span>
          </div>
        ) : !data.same_plan && o.current ? (
          <p className="small muted" style={{ padding: '6px 0 4px', margin: 0 }}>This is the plan's cushion now.</p>
        ) : null}
        {err && <div className="error">{err}</div>}
    </div>
  )
}

function Tiles({ o, base }: { o: CushionOption; base: CushionOption }) {
  const now = o.series[0]?.month
  const free = (m: string | null) => (m && m === now ? 'Now' : ym(m))
  const d = o.net_worth_delta ?? 0
  return (
    <div className="kpis">
      <div>
        <span>Pay from savings now</span>
        <b>{usd(o.pay_from_savings)}</b>
        <span>{o.sell_investments > 0.5 ? `+ ${usd(o.sell_investments)} from investments` : `keeps ${usd(o.emergency_buffer)} back`}</span>
      </div>
      <div>
        <span>High-cost debt $0</span>
        <b className={o.high_cost_debt_free ? 'good' : 'bad'}>{free(o.high_cost_debt_free)}</b>
        <span>{o.current ? 'this plan' : `this plan: ${free(base.high_cost_debt_free)}`}</span>
      </div>
      <div>
        <span>Net worth in 2 yrs</span>
        <b>{usd(o.net_worth_24m)}</b>
        <span className={d < -0.5 ? 'bad' : ''}>{o.current ? 'this plan' : Math.abs(d) < 0.5 ? 'same as this plan' : `${signed(d)} vs this plan`}</span>
      </div>
      <div>
        <span>Lowest savings</span>
        <b className={o.min_cash < 0 ? 'bad' : ''}>{usd(o.min_cash)}</b>
        <span className={o.below_buffer ? 'bad' : ''}>{o.below_buffer ? `under the ${usd(o.emergency_buffer)} cushion · ${ym(o.min_cash_month)}` : 'in 2 yrs, if nothing goes wrong'}</span>
      </div>
    </div>
  )
}

function Chart({ o, jobLoss, setJobLoss }: { o: CushionOption; jobLoss: boolean; setJobLoss: (v: boolean) => void }) {
  const rows = o.series.map((p, i) => ({ m: p.month, cash: p.cash, debt: p.debt, jcash: o.job_loss_series[i]?.cash, jdebt: o.job_loss_series[i]?.debt }))
  const job = o.shocks.find((s) => s.id === 'job_loss_3m')
  return (
    <div style={{ marginTop: 16 }}>
      <div className="col-head" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <div className="legend">
          <span><i style={{ borderColor: SAVINGS }} />Savings</span>
          <span><i style={{ borderColor: DEBT }} />High-cost debt</span>
          {jobLoss && <span><i className="dash" style={{ borderColor: GREY }} />With a 3-month job loss</span>}
          <span><i className="dash" style={{ borderColor: C.tick }} />Cushion</span>
        </div>
        <div className="seg" role="tablist" aria-label="Scenario">
          <button role="tab" aria-selected={!jobLoss} className={!jobLoss ? 'on' : ''} onClick={() => setJobLoss(false)}>Nothing goes wrong</button>
          <button role="tab" aria-selected={jobLoss} className={jobLoss ? 'on' : ''} onClick={() => setJobLoss(true)}>3-month job loss</button>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={rows} margin={{ top: 12, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={C.grid} vertical={false} />
          <XAxis dataKey="m" tickFormatter={ymShort} interval={5} tick={{ fontSize: 11, fill: C.tick }} tickLine={false} axisLine={{ stroke: C.grid }} />
          <YAxis tickFormatter={short} tick={{ fontSize: 11, fill: C.tick }} width={48} tickLine={false} axisLine={false} domain={['auto', 'auto']} />
          <Tooltip formatter={(v, n) => [usd(Number(v)), NAMES[String(n)] ?? n]} labelFormatter={(m) => ym(String(m))} />
          <ReferenceLine y={o.emergency_buffer} stroke={C.tick} strokeDasharray="2 4" />
          <Line dataKey="cash" stroke={SAVINGS} strokeWidth={jobLoss ? 2 : 3} strokeOpacity={jobLoss ? 0.45 : 1} dot={false} isAnimationActive={false} />
          <Line dataKey="debt" stroke={DEBT} strokeWidth={jobLoss ? 2 : 3} strokeOpacity={jobLoss ? 0.45 : 1} dot={false} isAnimationActive={false} />
          {jobLoss && <Line dataKey="jcash" stroke={SAVINGS} strokeDasharray="6 4" strokeWidth={3} dot={false} isAnimationActive={false} />}
          {jobLoss && <Line dataKey="jdebt" stroke={DEBT} strokeDasharray="6 4" strokeWidth={3} dot={false} isAnimationActive={false} />}
        </ComposedChart>
      </ResponsiveContainer>
      {jobLoss && job && (
        <div className="small" style={{ marginTop: 4 }}>
          {job.borrowed_on_card > 0.5
            ? <span className="bad">With {months(o.months)} kept, savings hit $0 and {usd(job.borrowed_on_card)} goes on the card.</span>
            : <><span className="good">Covered.</span> <span className="muted">With {months(o.months)} kept, savings dip to {usd(job.min_cash)} and nothing goes on the card.</span></>}
        </div>
      )}
    </div>
  )
}
