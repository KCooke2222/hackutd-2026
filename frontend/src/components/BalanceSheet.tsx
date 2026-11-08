import { useEffect, useState } from 'react'
import { ACCOUNT_COLORS, SOURCE_LABEL, api, usd } from '../lib/api'
import type { BalanceSheet as Sheet, Ladder, Source } from '../lib/api'

export function SourceTag({ source }: { source: Source }) {
  return <span className={`src src-${source}`}>{SOURCE_LABEL[source] ?? source}</span>
}

const TIER_TAG: Record<string, string> = { retirement: 'flag only', property: 'mention only' }

export default function BalanceSheet({ cid }: { cid: string }) {
  const [s, setS] = useState<Sheet | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [keep, setKeep] = useState(3)

  useEffect(() => {
    let live = true
    setErr(null)
    api<Sheet>(`/customers/${cid}/balance-sheet?keep_months=${keep}`).then((d) => live && setS(d)).catch((e) => live && setErr(e.message))
    return () => { live = false }
  }, [cid, keep])

  if (err) return <div className="error">{err}</div>
  if (!s) return null
  const { totals, ladder, priority } = s
  const assets = s.accounts.filter((a) => a.side === 'asset').sort((a, b) => b.balance - a.balance)

  return (
    <>
      <div className="stats">
        <div className="stat"><div className="stat-label">Net worth</div><div className="stat-value">{usd(totals.net_worth)}</div><div className="stat-sub">all {s.accounts.length} accounts</div></div>
        <div className="stat good"><div className="stat-label">Assets</div><div className="stat-value">{usd(totals.assets)}</div></div>
        <div className="stat bad"><div className="stat-label">Debts</div><div className="stat-value">{usd(totals.debts)}</div><div className="stat-sub">{priority.debts.length} debts</div></div>
        <div className="stat"><div className="stat-label">Can tap for payoff</div><div className="stat-value">{usd(ladder.proposable_available)}</div><div className="stat-sub">ladder tiers 1–3</div></div>
        <div className={`stat ${priority.monthly_surplus < 0 ? 'bad' : ''}`}><div className="stat-label">Left after minimums</div><div className="stat-value">{usd(priority.monthly_surplus)}/mo</div><div className="stat-sub">{usd(priority.monthly_minimums)} in minimums</div></div>
      </div>

      <div className="grid2">
        <LadderCard cid={cid} ladder={ladder} keep={keep} setKeep={setKeep} />
        <section className="card">
          <h2>Assets</h2>
          <table className="compact">
            <tbody>
              {assets.map((a) => (
                <tr key={a.account_id}>
                  <td><span className="dot" style={{ background: ACCOUNT_COLORS[a.account_type] ?? '#9ca3af' }} />{a.name}</td>
                  <td><SourceTag source={a.source} /></td>
                  <td className="num">{usd(a.balance, 2)}</td>
                  <td className="small muted">
                    {a.annual_yield_pct ? `${a.annual_yield_pct}% yield` : a.cost_basis != null ? `basis ${usd(a.cost_basis)}` : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted">
            Nessie holds checking, savings and card balances. Student loans, brokerage, property and other banks' cards are linked accounts stored in our SQLite.
          </p>
        </section>
      </div>

      <section className="card">
        <h2>Debts, ranked</h2>
        <p className="muted small">{priority.rule} Below {priority.prepay_hurdle_pct}% APR or secured: minimum only.</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Min. order</th><th>Debt</th><th>Source</th><th className="num">Balance</th><th className="num">APR</th><th className="num">Min / mo</th><th className="num">Interest / yr</th><th>If missed</th><th>Extra money</th></tr>
            </thead>
            <tbody>
              {priority.debts.map((d) => (
                <tr key={d.account_id} className={d.extra_rank ? '' : 'dim'}>
                  <td className="num">{d.minimum_order}</td>
                  <td>
                    <span className="dot" style={{ background: ACCOUNT_COLORS[d.account_type] ?? '#9ca3af' }} />{d.name}
                    {d.secured && <span className="tag" style={{ marginLeft: 6 }}>secured</span>}
                  </td>
                  <td><SourceTag source={d.source} /></td>
                  <td className="num">{usd(d.balance, 2)}</td>
                  <td className="num">{d.apr_pct}%</td>
                  <td className="num">{usd(d.min_payment, 2)}</td>
                  <td className="num">{usd(d.yearly_interest)}</td>
                  <td className="small">{d.consequence}</td>
                  <td className="small">{d.extra_rank ? <strong className="bad">#{d.extra_rank}</strong> : null} {d.extra_rank ? '' : d.action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}

function LadderCard({ cid, ladder, keep, setKeep }: { cid: string; ladder: Ladder; keep: number; setKeep: (n: number) => void }) {
  const [amount, setAmount] = useState('')
  const [tap, setTap] = useState<Ladder['tap']>(null)
  useEffect(() => setTap(null), [cid, keep])

  async function check() {
    const n = Number(amount)
    if (!n) return setTap(null)
    const r = await api<Ladder>(`/customers/${cid}/ladder?amount=${n}&keep_months=${keep}`)
    setTap(r.tap ?? null)
  }

  const max = Math.max(...ladder.tiers.map((t) => t.balance), 1)
  return (
    <section className="card">
      <div className="row">
        <h2>Liquidity ladder</h2>
        <label className="small muted">
          Emergency buffer{' '}
          <select value={keep} onChange={(e) => setKeep(Number(e.target.value))}>
            {[3, 4, 6, 9, 12].map((m) => <option key={m} value={m}>{m} months</option>)}
          </select>
        </label>
      </div>
      <p className="muted small">Cheapest money first. Keeps {usd(ladder.emergency_buffer)} ({ladder.keep_months} months of expenses) in cash.</p>
      <table className="compact">
        <thead><tr><th>Tier</th><th className="num">Balance</th><th className="num">Usable</th><th className="num">Cost to tap</th><th></th></tr></thead>
        <tbody>
          {ladder.tiers.map((t) => (
            <tr key={t.key} className={t.proposable ? '' : 'dim'} title={t.note ?? ''}>
              <td>
                {t.tier}. {t.label}
                {!t.proposable && <span className="tag" style={{ marginLeft: 6 }}>{TIER_TAG[t.key]}</span>}
                {t.accounts.length > 0 && <div className="small muted">{t.accounts.map((a) => a.name).join(', ')}</div>}
              </td>
              <td className="num">{usd(t.balance)}</td>
              <td className="num">{usd(t.available)}</td>
              <td className="num">{t.cost_pct == null ? '—' : `${t.cost_pct}%`}</td>
              <td style={{ width: 90 }}>
                <div className="ladder-bar"><div style={{ width: `${(t.available / max) * 100}%`, background: t.proposable ? '#16a34a' : '#9ca3af' }} /></div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="tap">
        <span>Cost to raise</span>
        <input type="number" placeholder="$ amount" value={amount} onChange={(e) => setAmount(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && check()} />
        <button onClick={check}>Check</button>
        {tap && (
          <span className="small">
            {tap.draws.map((d) => `${usd(d.amount)} from ${d.key}${d.cost ? ` (${usd(d.cost, 2)})` : ''}`).join(' + ') || 'nothing available'}
            {' → '}<strong>{usd(tap.total_cost, 2)} one-time cost</strong>
            {tap.shortfall > 0 && <span className="bad"> · {usd(tap.shortfall)} short</span>}
          </span>
        )}
      </div>
    </section>
  )
}
