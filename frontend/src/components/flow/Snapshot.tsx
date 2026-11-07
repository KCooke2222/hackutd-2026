import { useState } from 'react'
import { usd } from '../../lib/api'
import type { BalanceSheet, Refill, SheetAccount } from '../../lib/api'

export type Summary = { card_history: { month: string; balance: number }[]; refill: Refill }

type Row = { id: string; name: string; bal: number; tags: [string, string?][]; dim?: boolean; hl?: boolean } | { group: string }

function Spark({ data }: { data: number[] }) {
  if (data.length < 2) return null
  const w = 150, h = 46, lo = Math.min(...data) - 60, hi = Math.max(...data) + 10
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * (w - 4) + 2},${h - 4 - ((v - lo) / (hi - lo)) * (h - 10)}`).join(' ')
  return (
    <svg width={w} height={h} aria-label="card balance, last 24 months">
      <polyline points={pts} fill="none" stroke="var(--bad)" strokeWidth="2" />
      <text x="2" y={h} fontSize="9" fill="var(--muted)">{data.length} mo</text>
    </svg>
  )
}

function Col({ title, total, rows }: { title: string; total: number; rows: Row[] }) {
  const [all, setAll] = useState(false)
  const LIMIT = 4
  let shown = 0
  const visible: Row[] = []
  for (const r of rows) {
    if ('group' in r) { if (all) visible.push(r); continue }
    if (!all && (r.dim || shown >= LIMIT)) continue
    visible.push(r); shown++
  }
  const hidden = rows.filter((r) => !('group' in r)).length - shown
  return (
    <div className="card">
      <div className="col-head"><h2 style={{ margin: 0 }}>{title}</h2><span className="total">{usd(total)}</span></div>
      {visible.map((r, i) => 'group' in r
        ? <div key={'g' + i} className="group-label">{r.group}</div>
        : (
          <div key={r.id} className={`lrow ${r.dim ? 'dim' : ''} ${r.hl ? 'hl' : ''}`}>
            <span className="name">{r.name}</span><span className="amt">{usd(r.bal)}</span>
            {r.tags.length > 0 && <span className="sub">{r.tags.map(([t, c]) => <span key={t} className={`tag ${c ?? ''}`}>{t}</span>)}</span>}
          </div>
        ))}
      {(hidden > 0 || all) && <button className="ghost" onClick={() => setAll(!all)}>{all ? 'Show less' : `Show all (${hidden} more)`}</button>}
    </div>
  )
}

export default function Snapshot({ sheet, summary }: { sheet: BalanceSheet; summary: Summary }) {
  const card = sheet.accounts.find((a) => a.account_type === 'credit_card' && a.source !== 'linked')
  const r = summary.refill
  const refill = r.monthly_avg.new_spending + r.monthly_avg.interest + r.monthly_avg.fees

  // Have: the ladder order (what can be tapped, cheapest first), then what is never tapped.
  const byId = new Map<string, SheetAccount>(sheet.accounts.map((a) => [a.account_id, a]))
  const have: Row[] = []
  const tiers = sheet.ladder.tiers
  for (const t of tiers.filter((t) => t.proposable)) {
    for (const a of t.accounts) {
      const tags: [string, string?][] = t.key === 'cash' ? [['buffer']] : t.cost_pct ? [[`~${t.cost_pct}% ${t.key === 'cd' ? 'penalty' : 'tax'}`]] : []
      have.push({ id: a.account_id, name: a.name, bal: a.balance, tags })
    }
  }
  const untapped = tiers.filter((t) => !t.proposable).flatMap((t) => t.accounts)
  if (untapped.length) have.push({ group: 'Not tapped' }, ...untapped.map((a) => ({ id: a.account_id, name: a.name, bal: a.balance, tags: [], dim: true })))
  for (const a of sheet.accounts) if (a.side === 'asset' && !have.some((h) => 'id' in h && h.id === a.account_id)) have.push({ id: a.account_id, name: a.name, bal: a.balance, tags: [], dim: true })

  // Owe: avalanche order for extra money, then minimum-only debts.
  const debts = sheet.priority.debts
  const extra = debts.filter((d) => d.extra_rank != null).sort((a, b) => (a.extra_rank ?? 0) - (b.extra_rank ?? 0))
  const minOnly = debts.filter((d) => d.extra_rank == null)
  const owe: Row[] = extra.map((d) => ({ id: d.account_id, name: byId.get(d.account_id)?.name ?? d.name, bal: d.balance, tags: [[`${d.apr_pct}%`]], hl: d.account_id === card?.account_id }))
  if (minOnly.length) {
    owe.push({ group: 'Minimum only' })
    for (const d of minOnly) {
      const tags: [string, string?][] = [[`${d.apr_pct}%`]]
      if (/garnish/i.test(d.consequence)) tags.push(['garnishment risk', 'warn'])
      else if (d.secured) tags.push(['secured', 'warn'])
      owe.push({ id: d.account_id, name: d.name, bal: d.balance, tags, dim: true })
    }
  }

  return (
    <>
      <div className="card hero">
        <div>
          <div className="k">PNC card · {card?.apr_pct}%</div>
          <div className="big num">{usd(card?.balance, 0)}</div>
          <div className="flowrow">
            <span>Pay <b>{usd(r.monthly_avg.payment)}</b>/mo</span>
            <span className="eq">≈</span>
            <span title={`Spending ${usd(r.monthly_avg.new_spending)} + interest ${usd(r.monthly_avg.interest)} + fees ${usd(r.monthly_avg.fees, 2)}`}>Refills <b>{usd(refill)}</b>/mo</span>
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <Spark data={summary.card_history.map((h) => h.balance)} />
          <div className={`small num ${r.net_change > 0 ? 'bad' : 'good'}`}>{r.net_change >= 0 ? '+' : ''}{usd(r.net_change)} / yr</div>
        </div>
      </div>
      <div className="ledger">
        <Col title="Have" total={sheet.totals.assets} rows={have} />
        <Col title="Owe" total={sheet.totals.debts} rows={owe} />
      </div>
    </>
  )
}
