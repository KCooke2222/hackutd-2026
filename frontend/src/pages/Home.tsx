import { useCallback, useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, usd } from '../lib/api'
import BalanceSheet from '../components/BalanceSheet'
import type { Account, Proposal, Refill, Totals } from '../lib/api'

type Summary = { customer_id: string; accounts: Account[]; totals: Totals; card_history: { month: string; balance: number }[]; refill: Refill }
type Health = { nessie: boolean; nvidia: boolean; model: string }
type RunResult = { final: string; messages: { role: string; content: string; tool_calls?: { name: string; args: unknown }[]; name?: string }[]; proposal: Proposal | null }

export default function Home() {
  const [cid, setCid] = useState('C001')
  const [ids, setIds] = useState<string[]>([])
  const [s, setS] = useState<Summary | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [run, setRun] = useState<RunResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    api<{ customer_id: string }[]>('/customers').then((l) => setIds(l.map((c) => c.customer_id)))
    api<Health>('/health').then(setHealth)
  }, [])

  const load = useCallback(() => {
    setS(null); setRun(null); setErr(null)
    api<Summary>(`/customers/${cid}/summary`).then(setS).catch((e) => setErr(e.message))
    api<Proposal[]>(`/proposals?customer_id=${cid}`).then((ps) => setProposal(ps.find((p) => p.status === 'pending') ?? ps[0] ?? null))
  }, [cid])
  useEffect(load, [load])

  async function runCheck() {
    setBusy(true); setErr(null)
    try {
      const r = await api<RunResult>('/agent/run', { method: 'POST', body: JSON.stringify({ customer_id: cid }) })
      setRun(r)
      if (r.proposal) setProposal(r.proposal)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const r = s?.refill
  const savings = s?.accounts.find((a) => a.account_type === 'savings')

  return (
    <div className="page">
      <div className="page-head row">
        <div>
          <h1>Card payoff check</h1>
          <p className="muted">The agent reads your accounts, explains why the card balance isn't going down, and proposes one plan. Nothing moves until you approve.</p>
        </div>
        <div className="picker">
          <select value={cid} onChange={(e) => setCid(e.target.value)}>
            {(ids.length ? ids : [cid]).map((c) => <option key={c}>{c}</option>)}
          </select>
          <button className="primary" onClick={runCheck} disabled={busy}>{busy ? 'Running…' : 'Run check'}</button>
        </div>
      </div>
      {health && (!health.nvidia || !health.nessie) && (
        <div className="notice">
          {!health.nvidia && <div>No <code>NVIDIA_API_KEY</code>: the agent can't run yet. Charts and numbers below come from the PNC CSVs.</div>}
          {!health.nessie && <div>No <code>NESSIE_KEY</code>: approvals are recorded locally only.</div>}
        </div>
      )}
      {err && <div className="error">{err}</div>}

      {s && r && (
        <>
          <div className="stats">
            <div className="stat bad"><div className="stat-label">Card balance</div><div className="stat-value">{usd(s.totals.card_balance, 2)}</div><div className="stat-sub">{s.totals.card_apr_pct}% APR</div></div>
            <div className="stat"><div className="stat-label">Paid to the card, 12 mo</div><div className="stat-value">{usd(r.monthly_avg.payment * 12)}</div><div className="stat-sub">{usd(r.monthly_avg.payment)}/mo</div></div>
            <div className="stat bad"><div className="stat-label">Interest, 12 mo</div><div className="stat-value">{usd(s.totals.card_interest_last_12m)}</div></div>
            <div className="stat"><div className="stat-label">Balance change, 12 mo</div><div className="stat-value">{r.net_change >= 0 ? '+' : ''}{usd(r.net_change, 2)}</div></div>
            <div className="stat good"><div className="stat-label">Savings</div><div className="stat-value">{usd(savings?.balance)}</div><div className="stat-sub">{savings?.annual_yield_pct}% yield</div></div>
          </div>

          <div className="grid2">
            <section className="card">
              <h2>Card balance, 24 months</h2>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={s.card_history}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                  <YAxis tickFormatter={(v) => usd(v)} width={70} tick={{ fontSize: 11 }} domain={[0, 'auto']} />
                  <Tooltip formatter={(v) => usd(Number(v), 2)} />
                  <Line isAnimationActive={false} type="monotone" dataKey="balance" stroke="#dc2626" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </section>
            <section className="card">
              <h2>Where each payment goes</h2>
              <p className="muted small">Per month, last 12 months. {Math.round(r.share_of_payment_refilled * 100)}% of every payment is refilled by new spending and interest.</p>
              <RefillBar refill={r} />
            </section>
          </div>
        </>
      )}

      {s && (
        <>
          <h2 className="section-head">Balance sheet</h2>
          <BalanceSheet cid={cid} />
        </>
      )}

      {proposal && <ProposalCard p={proposal} onChange={setProposal} />}

      {run && (
        <section className="card">
          <h2>Agent</h2>
          <p>{run.final}</p>
          <details>
            <summary>Trace ({run.messages.length} messages)</summary>
            {run.messages.map((m, i) => (
              <div key={i} className="trace">
                <span className="tag">{m.role}{m.name ? `:${m.name}` : ''}</span>
                {m.tool_calls?.map((c, j) => <code key={j}>{c.name}({JSON.stringify(c.args)})</code>)}
                <pre>{m.content.slice(0, 1200)}</pre>
              </div>
            ))}
          </details>
        </section>
      )}
    </div>
  )
}

function RefillBar({ refill }: { refill: Refill }) {
  const pay = refill.monthly_avg.payment
  const segs = [
    ...Object.entries(refill.new_spending_by_category).map(([k, v]) => ({ k, v: v.monthly_avg, c: '#f59e0b' })),
    { k: 'interest', v: refill.monthly_avg.interest, c: '#dc2626' },
    { k: 'fees', v: refill.monthly_avg.fees, c: '#7f1d1d' },
  ]
  const used = segs.reduce((a, b) => a + b.v, 0)
  const left = Math.max(pay - used, 0)
  const total = Math.max(pay, used)
  return (
    <div>
      <div className="stackbar">
        {segs.map((s) => <div key={s.k} title={`${s.k}: ${usd(s.v, 2)}`} style={{ width: `${(s.v / total) * 100}%`, background: s.c }} />)}
        {left > 0 && <div title={`actually reduces balance: ${usd(left, 2)}`} style={{ width: `${(left / total) * 100}%`, background: '#16a34a' }} />}
      </div>
      <table className="compact">
        <tbody>
          <tr><td>Payment</td><td className="num">{usd(pay, 2)}</td></tr>
          {segs.map((s) => <tr key={s.k}><td><span className="dot" style={{ background: s.c }} />{s.k}</td><td className="num">−{usd(s.v, 2)}</td></tr>)}
          <tr><td><strong>Actually reduces the balance</strong></td><td className="num"><strong>{usd(pay - used, 2)}</strong></td></tr>
        </tbody>
      </table>
    </div>
  )
}

function ProposalCard({ p, onChange }: { p: Proposal; onChange: (p: Proposal) => void }) {
  const [edit, setEdit] = useState(false)
  const [plan, setPlan] = useState(p.plan)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { setPlan(p.plan); setEdit(false) }, [p])
  const { before, after } = p.simulation

  async function act(kind: 'approve' | 'reject') {
    setErr(null)
    try {
      const body = kind === 'approve' && edit ? JSON.stringify(plan) : undefined
      onChange(await api<Proposal>(`/proposals/${p.id}/${kind}`, { method: 'POST', body }))
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const chart = after.schedule.map((pt, i) => ({ month: pt.month, after: pt.balance, before: before.schedule[i]?.balance }))
  const fields: [keyof Proposal['plan'], string][] = [
    ['lump_sum_from_savings', 'Lump sum from savings'],
    ['lump_sum_from_cd', 'Lump sum from CDs'],
    ['lump_sum_from_brokerage', 'Lump sum from brokerage'],
    ['monthly_payment', 'Monthly payment'],
    ['monthly_card_spending_cap', 'Card spending cap / mo'],
    ['keep_emergency_months', 'Keep in savings (months)'],
  ]
  return (
    <section className="card proposal">
      <div className="row">
        <h2>Proposed plan</h2>
        <span className={`tag status-${p.status}`}>{p.status}</span>
      </div>
      <p>{p.rationale}</p>
      <div className="grid2">
        <div>
          <table className="compact">
            <tbody>
              {fields.map(([k, label]) => (
                <tr key={k}>
                  <td>{label}</td>
                  <td className="num">
                    {edit ? <input type="number" value={plan[k] ?? 0} onChange={(e) => setPlan({ ...plan, [k]: Number(e.target.value) })} /> : k === 'keep_emergency_months' ? plan[k] : usd(plan[k] ?? 0, 2)}
                  </td>
                </tr>
              ))}
              <tr><td>Savings after</td><td className="num">{usd(p.simulation.savings_after)} (cash floor {usd(p.simulation.emergency_floor)})</td></tr>
              {p.simulation.tap_cost_total != null && p.simulation.tap_cost_total > 0 && (
                <tr><td>One-time cost of draws</td><td className="num bad">{usd(p.simulation.tap_cost_total, 2)}</td></tr>
              )}
            </tbody>
          </table>
          <div className="compare">
            <div><div className="stat-label">Now</div><strong>{before.pays_off ? `$0 in ${before.months_to_zero} mo` : 'never reaches $0'}</strong></div>
            <div><div className="stat-label">With plan</div><strong className="good">{after.pays_off ? `$0 in ${after.months_to_zero} mo` : 'never'}</strong><div className="small muted">{usd(after.total_interest)} interest</div></div>
          </div>
        </div>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={chart}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="month" tick={{ fontSize: 11 }} />
            <YAxis tickFormatter={(v) => usd(v)} width={70} tick={{ fontSize: 11 }} />
            <Tooltip formatter={(v) => usd(Number(v), 2)} />
            <ReferenceLine y={0} stroke="#16a34a" />
            <Line isAnimationActive={false} dataKey="before" name="now" stroke="#dc2626" dot={false} strokeWidth={2} />
            <Line isAnimationActive={false} dataKey="after" name="with plan" stroke="#16a34a" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      {err && <div className="error">{err}</div>}
      {p.status === 'pending' ? (
        <div className="actions">
          <button className="primary" onClick={() => act('approve')}>{edit ? 'Approve edited plan' : 'Approve'}</button>
          <button onClick={() => setEdit(!edit)}>{edit ? 'Cancel edit' : 'Edit'}</button>
          <button className="danger" onClick={() => act('reject')}>Reject</button>
        </div>
      ) : (
        p.execution && <pre className="small">{JSON.stringify({ mode: p.execution.mode, note: p.execution.note, manual_steps: p.execution.manual_steps, error: p.execution.error }, null, 1)}</pre>
      )}
    </section>
  )
}
