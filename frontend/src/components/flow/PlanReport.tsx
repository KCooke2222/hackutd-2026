import { useState } from 'react'
import type { ReactNode } from 'react'
import { Area, CartesianGrid, ComposedChart, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { usd } from '../../lib/api'
import type { ApprovePreview, Explain, NessieCall, Proposal, SimDebt, SimV2 } from '../../lib/api'
import { SOURCE_NAME, title, ym, ymShort } from '../../lib/flow'
import type { Schedule } from '../../lib/flow'
import { ACCENT, C, DEBT_COLORS, GOOD, GREY, short } from '../../lib/chart'
import NessieChip from '../NessieChip'

const money = (n: number) => usd(Math.round(n))
const signed = (n: number) => `${n >= 0 ? '+' : '−'}${money(Math.abs(n))}`
const pct = (n: number) => `${Math.round(n * 10) / 10}%`
const tick = (m: string) => (m === 'now' ? 'Now' : ymShort(m))

type Runs = 'nessie' | 'bill' | 'manual' | 'tracked' | 'recorded'
function RunsTag({ r, label }: { r: Runs; label?: string }) {
  if (r === 'nessie') return <NessieChip label={label ?? 'Nessie transfer'} />
  if (r === 'bill') return <NessieChip label={label ?? 'Recurring Nessie bills'} />
  if (r === 'manual') return <span className="tag warn">You do it</span>
  if (r === 'tracked') return <span className="tag">Tracked monthly</span>
  return <span className="tag">Recorded</span>
}

/** "What it would do": the plan's moves, generic, each tagged with how it runs on approve. */
function steps(ai: Proposal, calls: NessieCall[], manual: { step: string; how?: string[] | string }[]) {
  const pl = ai.plan, sim = ai.simulation, ex: Explain = sim.explain ?? { headline: '' }
  const debt = new Map(sim.debts.map((d) => [d.account_id, d]))
  const name = (id: string) => debt.get(id)?.name ?? id
  const moves = calls.some((c) => !c.tool.endsWith('create_bill'))
  const bills = calls.filter((c) => c.tool.endsWith('create_bill'))
  const autopayBill = bills.some((c) => /autopay/i.test(c.step ?? ''))
  const payBill = bills.some((c) => !/autopay/i.test(c.step ?? ''))
  const out: { text: ReactNode; sub?: ReactNode; runs: Runs; label?: string }[] = []

  const bySource = new Map<string, typeof pl.lump_sums>()
  for (const l of pl.lump_sums) bySource.set(l.source, [...(bySource.get(l.source) ?? []), l])
  for (const [src, ls] of bySource) {
    const total = ls.reduce((a, l) => a + l.amount, 0)
    const parts = ls.map((l) => {
      const d = debt.get(l.to_debt)
      const clears = d && l.amount >= d.balance - 1
      return `${clears ? 'clears' : `${money(l.amount)} to`} ${name(l.to_debt)}`
    })
    out.push({
      text: <>{src === 'cash' ? 'Pay' : 'Sell'} <b>{money(total)}</b> {src === 'cash' ? `from ${SOURCE_NAME.cash}` : `of ${SOURCE_NAME[src]?.toLowerCase() ?? src}`}</>,
      sub: <>{parts.join(', ')}{src === 'brokerage' && sim.upfront_cost > 0 ? `, tax ≈ ${money(sim.upfront_cost)}` : ''}</>,
      runs: moves ? 'nessie' : 'recorded',
    })
  }
  const order = pl.extra_order.map(name)
  out.push({
    text: <>Pay <b>{money(pl.monthly_debt_budget)}/mo</b> to debt</>,
    sub: order.length ? <>Extra goes {order.join(' → ')}</> : undefined,
    runs: payBill ? 'bill' : 'recorded',
  })
  const red = pl.redirects.reduce((a, r) => a + r.amount_per_month, 0)
  if (red > 0) out.push({ text: <>Redirect <b>{money(red)}/mo</b> to debt</>, sub: pl.redirects.map((r) => title(r.flow).toLowerCase()).join(', '), runs: 'manual' })
  const cuts = pl.spending_targets.filter((t) => t.current > t.target)
  if (cuts.length) {
    const sum = cuts.reduce((a, t) => a + t.current - t.target, 0)
    out.push({ text: <>{cuts.length} spending cut{cuts.length > 1 ? 's' : ''}, <b>{money(sum)}/mo</b></>, sub: cuts.map((t) => title(t.category)).join(', '), runs: 'tracked' })
  }
  if (pl.autopay_full_cards.length)
    out.push({ text: <>Autopay in full once a card hits $0</>, sub: pl.autopay_full_cards.map(name).join(', '), runs: autopayBill ? 'bill' : 'recorded', label: 'Nessie bill' })
  out.push({ text: <>Keep {pl.keep_emergency_months} months of cash</>, sub: brief(ex.buffer_why) || undefined, runs: 'recorded' })
  for (const m of manual) out.push({ text: m.step, sub: brief(Array.isArray(m.how) ? m.how[0] : m.how, 60), runs: 'manual' })
  return out
}

/** First clause of agent text, capped: the plan reads as fragments, not sentences. */
function brief(s?: string | null, max = 48): string {
  const c = (s ?? '').split(/(?<=[a-z])[.;:]\s|\s[—–-]\s|, (?:so|which|because|and) /)[0].trim().replace(/\.$/, '')
  return c.length > max ? c.slice(0, max).replace(/\s+\S*$/, '') + '…' : c
}

function Kpis({ sim, base }: { sim: SimV2; base: SimV2 | null }) {
  const b = sim.budget
  const baseFree = sim.baseline_high_cost_debt_free ?? base?.high_cost_debt_free ?? null
  const nw = sim.net_worth_24m - sim.baseline_net_worth_24m
  const int = sim.interest_total - sim.baseline_interest_total
  return (
    <div className="kpis">
      <div><span>High-cost debt $0</span><b className="good">{ym(sim.high_cost_debt_free)}</b><span>do nothing: {baseFree ? ym(baseFree) : 'not in 2 yrs'}</span></div>
      <div><span>Net worth in 2 yrs</span><b>{money(sim.net_worth_24m)}</b><span className={nw >= 0 ? 'good' : 'bad'}>{signed(nw)} vs do nothing</span></div>
      <div><span>Interest, 2 yrs</span><b>{money(sim.interest_total)}</b><span className={int <= 0 ? 'good' : 'bad'}>{signed(int)} vs do nothing</span></div>
      {b?.net_paydown_per_month != null && (
        <div><span>Debt shrinks by</span><b>{money(b.net_paydown_per_month)}/mo</b><span>now {money(b.current_net_paydown_per_month ?? 0)}/mo</span></div>
      )}
    </div>
  )
}

function PaydownBar({ sim }: { sim: SimV2 }) {
  const b = sim.budget
  if (!b?.monthly_debt_budget || b.net_paydown_per_month == null) return null
  const buys = b.new_card_purchases ?? 0, shrink = Math.max(0, b.net_paydown_per_month)
  const tot = buys + shrink || 1
  return (
    <div style={{ marginTop: 16 }}>
      <div className="col-head"><h3 style={{ margin: 0 }}>Where the {money(b.monthly_debt_budget)}/mo to debt goes</h3></div>
      <div className="pd-bar" role="img" aria-label={`${money(buys)} covers new card purchases, ${money(shrink)} shrinks the debt`}>
        <div style={{ width: `${(buys / tot) * 100}%`, background: GREY }} />
        <div style={{ width: `${(shrink / tot) * 100}%`, background: GOOD }} />
      </div>
      <div className="legend">
        <span><i className="sw" style={{ background: GREY }} />New card purchases {money(buys)}</span>
        <span><i className="sw" style={{ background: GOOD }} />Shrinks the debt {money(shrink)}</span>
      </div>
    </div>
  )
}

function Cuts({ ai }: { ai: Proposal }) {
  const why = new Map((ai.simulation.explain?.cuts ?? []).map((c) => [c.category, c.why]))
  const cuts = ai.plan.spending_targets.filter((t) => t.current > t.target)
  if (!cuts.length) return null
  const all = ai.simulation.no_cuts?.suggested_buys
  return (
    <div className="card">
      <h2 style={{ margin: '0 0 8px' }}>The cuts</h2>
      <table className="pr-table">
        <thead><tr><th>Category</th><th>Per month</th><th>Without it</th><th>Why</th></tr></thead>
        <tbody>
          {cuts.map((t) => (
            <tr key={t.category}>
              <td><b>{title(t.category)}</b>{t.source === 'suggested' && <div className="small muted">suggested</div>}</td>
              <td className="num" style={{ whiteSpace: 'nowrap' }}>{money(t.current)} → <b>{money(t.target)}</b></td>
              <td className="bad" style={{ whiteSpace: 'nowrap' }}>
                {t.buys ? <>{t.buys.months_sooner ? `${t.buys.months_sooner} mo later, ` : ''}−{money(t.buys.net_worth_gain)} at 2 yrs</> : '—'}
              </td>
              <td className="why">{brief(why.get(t.category) ?? t.how?.[0], 40)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="small muted" style={{ margin: '8px 0 0' }}>
        “Without it” drops just that cut and keeps the rest.
        {all && <> Together: {all.months_sooner ? `${all.months_sooner} mo sooner, ` : ''}<b className="good">{signed(all.net_worth_gain)}</b> at 2 yrs.</>}
      </p>
    </div>
  )
}

/** Each debt over 24 months (stacked, lowest rate at the bottom) so you see the highest rate go first. */
function DebtChart({ sim, base }: { sim: SimV2; base: SimV2 | null }) {
  const [only, setOnly] = useState(true)
  const all = sim.debts.filter((d) => d.balance > 0)
  const hasHigh = all.some((d) => d.high_cost) && all.some((d) => !d.high_cost)
  const shown = (hasHigh && only ? all.filter((d) => d.high_cost) : all)
  const byApr = [...all].sort((a, b) => b.apr_pct - a.apr_pct)
  const color = (d: SimDebt) => DEBT_COLORS[byApr.indexOf(d) % DEBT_COLORS.length]
  const stack = [...shown].sort((a, b) => a.apr_pct - b.apr_pct)
  const ids = new Set(shown.map((d) => d.account_id))
  const sumOf = (m: SimV2['months'][number] | undefined) => {
    if (!m) return null
    if (m.balances) return Object.entries(m.balances.debts).filter(([k]) => ids.has(k)).reduce((a, [, v]) => a + v, 0)
    return only && hasHigh ? m.high_cost_debt : m.total_debt
  }
  const now: Record<string, number | string | null> = { m: 'now', base: shown.reduce((a, d) => a + d.balance, 0) }
  for (const d of shown) now[d.account_id] = d.balance
  const data = [now, ...sim.months.map((m, i) => {
    const row: Record<string, number | string | null> = { m: m.month, base: sumOf(base?.months[i]) }
    for (const d of shown) row[d.account_id] = m.balances?.debts[d.account_id] ?? null
    return row
  })]
  if (!sim.months[0]?.balances) return null
  return (
    <div className="card">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>Debt, next 24 months</h2>
        {hasHigh && (
          <div className="seg" role="tablist">
            <button role="tab" className={only ? 'on' : ''} onClick={() => setOnly(true)}>High-cost</button>
            <button role="tab" className={!only ? 'on' : ''} onClick={() => setOnly(false)}>All debt</button>
          </div>
        )}
      </div>
      <div className="small muted" style={{ margin: '0 0 6px' }}>High-cost $0 · <b className="good">{ym(sim.high_cost_debt_free)}</b></div>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={C.grid} vertical={false} />
          <XAxis dataKey="m" tickFormatter={tick} interval={5} tick={{ fontSize: 11, fill: C.tick }} tickLine={false} axisLine={{ stroke: C.grid }} />
          <YAxis tickFormatter={short} tick={{ fontSize: 11, fill: C.tick }} width={48} tickLine={false} axisLine={false} />
          <Tooltip formatter={(v, n) => [money(Number(v)), n === 'base' ? 'Do nothing (total)' : sim.debts.find((d) => d.account_id === n)?.name ?? String(n)]} labelFormatter={(m) => (m === 'now' ? 'Now' : ym(String(m)))} />
          {stack.map((d) => (
            <Area key={d.account_id} dataKey={d.account_id} stackId="d" type="monotone" stroke={color(d)} fill={color(d)} fillOpacity={0.55} strokeWidth={1.5} isAnimationActive={false} />
          ))}
          {base && <Line dataKey="base" stroke={GREY} strokeDasharray="5 4" strokeWidth={1.5} dot={false} isAnimationActive={false} />}
        </ComposedChart>
      </ResponsiveContainer>
      <div className="legend" style={{ marginTop: 6 }}>
        {[...shown].sort((a, b) => b.apr_pct - a.apr_pct).map((d) => (
          <span key={d.account_id}><i className="sw" style={{ background: color(d) }} />{d.name} {pct(d.apr_pct)} · {d.payoff_month ? `$0 ${ymShort(d.payoff_month)}` : 'not paid off'}</span>
        ))}
        {base && <span><i className="dash" style={{ borderColor: GREY }} />Do nothing</span>}
      </div>
    </div>
  )
}

function NetWorthCompare({ sim, base, noCuts }: { sim: SimV2; base: SimV2 | null; noCuts: SimV2 | null }) {
  const data = sim.months.map((m, i) => ({ m: m.month, plan: m.net_worth, bare: noCuts?.months[i]?.net_worth ?? null, base: base?.months[i]?.net_worth ?? null }))
  const last = data[data.length - 1]
  const gain = sim.net_worth_24m - sim.baseline_net_worth_24m
  const names: Record<string, string> = { plan: 'Plan with cuts', bare: 'Plan without cuts', base: 'Do nothing' }
  return (
    <div className="card">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>Net worth, next 24 months</h2>
        <b className={gain >= 0 ? 'good' : 'bad'} style={{ fontVariantNumeric: 'tabular-nums' }}>{signed(gain)} vs do nothing</b>
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={C.grid} vertical={false} />
          <XAxis dataKey="m" tickFormatter={ymShort} interval={5} tick={{ fontSize: 11, fill: C.tick }} tickLine={false} axisLine={{ stroke: C.grid }} />
          <YAxis tickFormatter={short} tick={{ fontSize: 11, fill: C.tick }} width={48} tickLine={false} axisLine={false} domain={['auto', 'auto']} />
          <Tooltip itemSorter={(it) => -Number(it.value ?? 0)} formatter={(v, n) => [money(Number(v)), names[String(n)] ?? String(n)]} labelFormatter={(m) => ym(String(m))} />
          {base && <Line dataKey="base" stroke={GREY} strokeDasharray="5 4" strokeWidth={2} dot={false} isAnimationActive={false} />}
          {noCuts && <Line dataKey="bare" stroke={ACCENT} strokeDasharray="2 4" strokeWidth={2} dot={false} isAnimationActive={false} />}
          <Line dataKey="plan" stroke={ACCENT} strokeWidth={3} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
      {last && (
        <div className="legend" style={{ marginTop: 6 }}>
          <span><i style={{ borderColor: ACCENT }} />With cuts {money(last.plan)}</span>
          {noCuts && last.bare != null && <span><i className="dash" style={{ borderColor: ACCENT, borderTopStyle: 'dotted' }} />Without cuts {money(last.bare)}</span>}
          {base && last.base != null && <span><i className="dash" style={{ borderColor: GREY }} />Do nothing {money(last.base)}</span>}
        </div>
      )}
    </div>
  )
}

/** Screen 2: the plan as a short report. Figures from the saved proposal (code), words from the agent. */
export default function PlanReport({ ai, baseline, noCuts, preview, schedule, underwater, builtOn, children }: {
  ai: Proposal
  baseline: SimV2 | null
  noCuts: SimV2 | null
  preview: ApprovePreview | null
  schedule: Schedule | null
  underwater?: ReactNode // the gap and the options that close it; replaces the plan until it's feasible
  builtOn?: ReactNode // options the customer chose that this plan depends on
  children?: ReactNode // "What the agent will do on approve" + Approve
}) {
  const sim = ai.simulation, ex = sim.explain
  const calls = preview?.nessie_calls ?? (schedule?.steps ?? []).filter((s) => s.runs === 'nessie').map((s) => ({ tool: `create_${(s.nessie as { op?: string } | undefined)?.op ?? 'transfer'}`, method: 'POST', path: '', amount: 0, step: s.step }))
  const manual = preview?.manual ?? []
  const list = steps(ai, calls, manual)
  return (
    <>
      <div className="card pr-head">
        <h1>{ex?.headline ?? 'Your plan'}</h1>
        {ex?.summary && !underwater && <p>{ex.summary}</p>}
        {ex?.driver && <p className="small"><b>What's driving it:</b> {ex.driver}</p>}
        {!underwater && <Kpis sim={sim} base={baseline} />}
        {!underwater && <PaydownBar sim={sim} />}
      </div>
      {underwater}
      {!underwater && builtOn}
      {!underwater && (
        <>
          <div className="card">
            <h2 style={{ margin: '0 0 4px' }}>What it would do</h2>
            <ol className="pr-steps">
              {list.map((s, i) => (
                <li key={i}><span>{s.text}</span><span className="runs"><RunsTag r={s.runs} label={s.label} /></span>{s.sub && <span className="sub">{s.sub}</span>}</li>
              ))}
            </ol>
          </div>
          <Cuts ai={ai} />
          <div className="pr-charts">
            <DebtChart sim={sim} base={baseline} />
            <NetWorthCompare sim={sim} base={baseline} noCuts={noCuts} />
          </div>
          {(ex?.downsides?.length ?? 0) > 0 && (
            <div className="card">
              <h2 style={{ margin: '0 0 6px' }}>Downsides</h2>
              <ul className="pr-down">{ex!.downsides!.map((d) => <li key={d}>{d}</li>)}</ul>
            </div>
          )}
          {children}
        </>
      )}
    </>
  )
}
