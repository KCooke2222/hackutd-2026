import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ACCOUNT_COLORS, api, pct, usd } from '../lib/api'
import type { Account, Profile, Statement, Totals } from '../lib/api'
import Markdown from '../lib/Markdown'

type Overview = {
  counts: Record<string, number>
  date_range: { from: string; to: string }
  account_types: { account_type: string; count: number; total_balance: number; avg_balance: number; apr_pct_values: Record<string, number>; yield_pct_values: Record<string, number> }[]
  transaction_types: Record<string, number>
  flow_classes: Record<string, number>
  categories: { category: string; flow_class: string; total: number }[]
  card_vs_savings: {
    customer_id: string
    card_balance: number | null
    card_apr_pct: number | null
    savings_balance: number | null
    savings_yield_pct: number | null
    monthly_expenses: number
    card_interest_last_12m: number | null
    could_pay_off_card_from_savings: boolean
  }[]
}

type CustomerRow = Profile & { net_worth: number; card_balance: number; liquid: number }
type CustomerDetail = { profile: Profile; accounts: Account[]; totals: Totals; statements: Statement[] }
type Tx = {
  transaction_id: string
  account_id: string
  account_type: string
  posted_date: string
  transaction_type: string
  category: string
  description: string
  amount: number
  balance_change: number
  balance_after: number
  flow_class: string
  transfer_id: string
}
type TxPage = { total: number; page: number; page_size: number; rows: Tx[]; facets: Record<string, string[]> }

const TABS = ['overview', 'customer', 'docs'] as const

export default function DataExplorer() {
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as (typeof TABS)[number]) ?? 'overview'
  const cid = params.get('c') ?? 'C001'
  const go = (next: Record<string, string>) => setParams({ tab, c: cid, ...next })

  return (
    <div className="page">
      <div className="page-head">
        <h1>PNC data explorer</h1>
        <p className="muted">Everything in the PNC Lab_Package: 30 synthetic customers, 24 months of history.</p>
      </div>
      <div className="tabs">
        <button className={tab === 'overview' ? 'on' : ''} onClick={() => go({ tab: 'overview' })}>Dataset overview</button>
        <button className={tab === 'customer' ? 'on' : ''} onClick={() => go({ tab: 'customer' })}>Customer</button>
        <button className={tab === 'docs' ? 'on' : ''} onClick={() => go({ tab: 'docs' })}>Field docs</button>
      </div>
      {tab === 'overview' && <OverviewTab onPick={(c) => go({ tab: 'customer', c })} />}
      {tab === 'customer' && <CustomerTab cid={cid} onPick={(c) => go({ c })} />}
      {tab === 'docs' && <DocsTab />}
    </div>
  )
}

function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!path) return
    let live = true
    setError(null)
    api<T>(path).then((d) => live && setData(d)).catch((e) => live && setError(String(e.message ?? e)))
    return () => { live = false }
  }, [path])
  return { data, error }
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'bad' | 'good' }) {
  return (
    <div className={`stat ${tone ?? ''}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

/* ---------------- overview ---------------- */

function OverviewTab({ onPick }: { onPick: (c: string) => void }) {
  const { data: o, error } = useApi<Overview>('/overview')
  if (error) return <div className="error">{error}</div>
  if (!o) return <div className="muted">Loading…</div>

  const cards = o.card_vs_savings.filter((r) => r.card_balance != null)
  const payable = cards.filter((r) => r.could_pay_off_card_from_savings).length
  const interest = cards.reduce((s, r) => s + (r.card_interest_last_12m ?? 0), 0)
  const apr = Object.keys(o.account_types.find((a) => a.account_type === 'credit_card')?.apr_pct_values ?? {}).join(', ')
  const flowBars = Object.entries(o.flow_classes).map(([k, v]) => ({ name: k, count: v }))
  const typeBars = Object.entries(o.transaction_types).map(([k, v]) => ({ name: k, count: v })).sort((a, b) => b.count - a.count)

  return (
    <>
      <section className="callout">
        <strong>The pattern:</strong> all {cards.length} credit cards are at <strong>{apr}% APR</strong>, while every savings account earns
        about 2.5%. {payable} of {cards.length} customers could pay their card off from savings today and still keep 3 months of
        expenses. Together they paid <strong>{usd(interest)}</strong> in card interest over the last 12 months.
      </section>

      <div className="stats">
        {Object.entries(o.counts).map(([k, v]) => <Stat key={k} label={k} value={v.toLocaleString()} />)}
        <Stat label="history" value={`${o.date_range.from} → ${o.date_range.to}`} />
      </div>

      <section className="card">
        <h2>Account types</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Type</th><th className="num">Count</th><th className="num">Total balance</th><th className="num">Avg balance</th><th>APR values</th><th>Yield values</th></tr></thead>
            <tbody>
              {o.account_types.map((a) => (
                <tr key={a.account_type}>
                  <td><span className="dot" style={{ background: ACCOUNT_COLORS[a.account_type] }} />{a.account_type}</td>
                  <td className="num">{a.count}</td>
                  <td className="num">{usd(a.total_balance)}</td>
                  <td className="num">{usd(a.avg_balance)}</td>
                  <td>{fmtCounts(a.apr_pct_values, '%')}</td>
                  <td>{fmtCounts(a.yield_pct_values, '%')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="grid2">
        <section className="card">
          <h2>Transaction types</h2>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={typeBars} layout="vertical" margin={{ left: 40 }}>
              <CartesianGrid strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" />
              <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="count" fill="#2563eb" />
            </BarChart>
          </ResponsiveContainer>
        </section>
        <section className="card">
          <h2>Flow classes</h2>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={flowBars} layout="vertical" margin={{ left: 40 }}>
              <CartesianGrid strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" />
              <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="count" fill="#0d9488" />
            </BarChart>
          </ResponsiveContainer>
          <p className="muted small">internal_transfer = money moving between a customer's own accounts (e.g. card payments, linked by transfer_id). non_cash = interest accrual.</p>
        </section>
      </div>

      <section className="card">
        <h2>Category totals (all customers, 24 months)</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Category</th><th>Flow class</th><th className="num">Total</th></tr></thead>
            <tbody>
              {o.categories.map((c) => (
                <tr key={c.category + c.flow_class}><td>{c.category}</td><td><span className="tag">{c.flow_class}</span></td><td className="num">{usd(c.total)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <h2>Card vs savings, per customer</h2>
        <p className="muted small">Click a row to open that customer.</p>
        <div className="table-wrap">
          <table className="clickable">
            <thead><tr><th>Customer</th><th className="num">Card balance</th><th className="num">APR</th><th className="num">Savings</th><th className="num">Yield</th><th className="num">Monthly expenses</th><th className="num">Card interest, 12 mo</th><th>Pay off from savings?</th></tr></thead>
            <tbody>
              {o.card_vs_savings.map((r) => (
                <tr key={r.customer_id} onClick={() => onPick(r.customer_id)}>
                  <td>{r.customer_id}</td>
                  <td className="num">{usd(r.card_balance)}</td>
                  <td className="num">{pct(r.card_apr_pct)}</td>
                  <td className="num">{usd(r.savings_balance)}</td>
                  <td className="num">{pct(r.savings_yield_pct)}</td>
                  <td className="num">{usd(r.monthly_expenses)}</td>
                  <td className="num bad">{usd(r.card_interest_last_12m)}</td>
                  <td>{r.could_pay_off_card_from_savings ? <span className="tag good">yes, keeps 3 mo</span> : <span className="tag">no</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}

function fmtCounts(m: Record<string, number>, suffix = '') {
  const e = Object.entries(m)
  return e.length ? e.map(([k, v]) => `${k}${suffix} ×${v}`).join(', ') : '—'
}

/* ---------------- customer ---------------- */

function CustomerTab({ cid, onPick }: { cid: string; onPick: (c: string) => void }) {
  const { data: list } = useApi<CustomerRow[]>('/customers')
  const { data: d, error } = useApi<CustomerDetail>(`/customers/${cid}`)
  const [hidden, setHidden] = useState<Set<string>>(new Set())

  const chart = useMemo(() => {
    if (!d) return []
    const byMonth = new Map<string, Record<string, number | string>>()
    for (const s of d.statements) {
      const row = byMonth.get(s.month) ?? { month: s.month }
      row[s.account_id] = s.closing_balance
      byMonth.set(s.month, row)
    }
    return [...byMonth.values()].sort((a, b) => String(a.month).localeCompare(String(b.month)))
  }, [d])

  return (
    <>
      <div className="picker">
        <label>Customer</label>
        <select value={cid} onChange={(e) => onPick(e.target.value)}>
          {(list ?? [{ customer_id: cid } as CustomerRow]).map((c) => (
            <option key={c.customer_id} value={c.customer_id}>
              {c.customer_id}{c.card_balance != null ? ` · card ${usd(c.card_balance)} · net worth ${usd(c.net_worth)}` : ''}
            </option>
          ))}
        </select>
      </div>
      {error && <div className="error">{error}</div>}
      {d && (
        <>
          <div className="stats">
            <Stat label="Monthly income" value={usd(d.profile.monthly_income)} />
            <Stat label="Monthly non-debt expenses" value={usd(d.profile.monthly_non_debt_expenses)} />
            <Stat label="Credit limit" value={usd(d.profile.credit_limit)} />
            <Stat label="Assets" value={usd(d.totals.assets)} />
            <Stat label="Debts" value={usd(d.totals.debts)} tone="bad" />
            <Stat label="Net worth" value={usd(d.totals.net_worth)} />
            <Stat label="Card interest, last 12 mo" value={usd(d.totals.card_interest_last_12m)} tone="bad" />
          </div>

          <section className="card">
            <h2>Accounts</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Account</th><th>Type</th><th className="num">Opening</th><th className="num">Balance</th><th className="num">APR</th><th className="num">Yield</th><th className="num">Monthly payment</th><th>History</th></tr></thead>
                <tbody>
                  {d.accounts.map((a) => (
                    <tr key={a.account_id}>
                      <td className="mono">{a.account_id}</td>
                      <td><span className="dot" style={{ background: ACCOUNT_COLORS[a.account_type] }} />{a.account_type}</td>
                      <td className="num">{usd(a.opening_balance, 2)}</td>
                      <td className="num">{usd(a.balance, 2)}</td>
                      <td className="num">{a.apr_pct ? pct(a.apr_pct) : '—'}</td>
                      <td className="num">{a.annual_yield_pct ? pct(a.annual_yield_pct) : '—'}</td>
                      <td className="num">{a.monthly_payment ? usd(a.monthly_payment, 2) : '—'}</td>
                      <td className="small muted">{a.history_start} → {a.as_of_date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card">
            <h2>Balance over time (monthly statement closing balance)</h2>
            <div className="chips">
              {d.accounts.map((a) => (
                <button
                  key={a.account_id}
                  className={`chip ${hidden.has(a.account_id) ? 'off' : ''}`}
                  style={{ borderColor: ACCOUNT_COLORS[a.account_type] }}
                  onClick={() => setHidden((h) => { const n = new Set(h); if (n.has(a.account_id)) n.delete(a.account_id); else n.add(a.account_id); return n })}
                >
                  <span className="dot" style={{ background: ACCOUNT_COLORS[a.account_type] }} />{a.account_type}
                </button>
              ))}
            </div>
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={chart}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                <YAxis tickFormatter={(v) => usd(v)} width={80} tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v) => usd(Number(v), 2)} />
                <Legend />
                {d.accounts.filter((a) => !hidden.has(a.account_id)).map((a) => (
                  <Line isAnimationActive={false} key={a.account_id} type="monotone" dataKey={a.account_id} name={a.account_type} stroke={ACCOUNT_COLORS[a.account_type]} dot={false} strokeWidth={2} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </section>

          <StatementsTable statements={d.statements} accounts={d.accounts} />
          <Transactions cid={cid} />
        </>
      )}
    </>
  )
}

function StatementsTable({ statements, accounts }: { statements: Statement[]; accounts: Account[] }) {
  const [acct, setAcct] = useState(accounts.find((a) => a.account_type === 'credit_card')?.account_id ?? accounts[0]?.account_id)
  const rows = statements.filter((s) => s.account_id === acct)
  return (
    <section className="card">
      <h2>Monthly statements</h2>
      <div className="filters">
        <select value={acct} onChange={(e) => setAcct(e.target.value)}>
          {accounts.map((a) => <option key={a.account_id} value={a.account_id}>{a.account_id}</option>)}
        </select>
      </div>
      <div className="table-wrap scroll">
        <table>
          <thead><tr><th>Month</th><th className="num">Opening</th><th className="num">Increases</th><th className="num">Decreases</th><th className="num">Closing</th><th className="num">Txns</th></tr></thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.month}>
                <td>{s.month}</td>
                <td className="num">{usd(s.opening_balance, 2)}</td>
                <td className="num">{usd(s.balance_increases, 2)}</td>
                <td className="num">{usd(s.balance_decreases, 2)}</td>
                <td className="num">{usd(s.closing_balance, 2)}</td>
                <td className="num">{s.transaction_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

const FILTERS = ['account_id', 'transaction_type', 'category', 'flow_class'] as const

function Transactions({ cid }: { cid: string }) {
  const [f, setF] = useState<Record<string, string>>({})
  const [page, setPage] = useState(1)
  useEffect(() => { setF({}); setPage(1) }, [cid])
  const qs = new URLSearchParams({ ...Object.fromEntries(Object.entries(f).filter(([, v]) => v)), page: String(page), page_size: '50' })
  const { data, error } = useApi<TxPage>(`/customers/${cid}/transactions?${qs}`)
  const set = (k: string, v: string) => { setF((o) => ({ ...o, [k]: v })); setPage(1) }
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1

  return (
    <section className="card">
      <h2>Transactions {data && <span className="muted small">({data.total.toLocaleString()} matching)</span>}</h2>
      <div className="filters">
        {FILTERS.map((k) => (
          <select key={k} value={f[k] ?? ''} onChange={(e) => set(k, e.target.value)}>
            <option value="">all {k.replace('_id', '').replace('_', ' ')}s</option>
            {(data?.facets[k] ?? []).map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        ))}
        <label className="small">from <input type="date" value={f.date_from ?? ''} onChange={(e) => set('date_from', e.target.value)} /></label>
        <label className="small">to <input type="date" value={f.date_to ?? ''} onChange={(e) => set('date_to', e.target.value)} /></label>
        <button className="link" onClick={() => { setF({}); setPage(1) }}>clear</button>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Date</th><th>Account</th><th>Type</th><th>Category</th><th>Description</th><th className="num">Amount</th><th className="num">Δ balance</th><th className="num">Balance after</th><th>Flow</th><th>Transfer</th></tr></thead>
          <tbody>
            {data?.rows.map((t) => (
              <tr key={t.transaction_id}>
                <td className="nowrap">{t.posted_date}</td>
                <td><span className="dot" style={{ background: ACCOUNT_COLORS[t.account_type] }} />{t.account_type}</td>
                <td>{t.transaction_type}</td>
                <td>{t.category}</td>
                <td>{t.description}</td>
                <td className="num">{usd(t.amount, 2)}</td>
                <td className={`num ${t.balance_change < 0 ? 'neg' : 'pos'}`}>{usd(t.balance_change, 2)}</td>
                <td className="num">{usd(t.balance_after, 2)}</td>
                <td><span className="tag">{t.flow_class}</span></td>
                <td className="mono small">{t.transfer_id || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <button disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Prev</button>
        <span>Page {page} of {pages}</span>
        <button disabled={page >= pages} onClick={() => setPage(page + 1)}>Next ›</button>
      </div>
    </section>
  )
}

/* ---------------- docs ---------------- */

function DocsTab() {
  const { data, error } = useApi<{ markdown: string }>('/readme')
  if (error) return <div className="error">{error}</div>
  return <section className="card">{data ? <Markdown text={data.markdown} /> : 'Loading…'}</section>
}
