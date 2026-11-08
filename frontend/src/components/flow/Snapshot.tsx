import { usd } from '../../lib/api'
import type { BalanceSheet, SheetAccount } from '../../lib/api'

type Row = { id: string; name: string; bal: number; tags: [string, string?][]; dim?: boolean } | { group: string }

function Col({ title, total, rows }: { title: string; total: number; rows: Row[] }) {
  return (
    <div className="card">
      <div className="col-head"><h2 style={{ margin: 0 }}>{title}</h2><span className="total">{usd(total)}</span></div>
      {rows.map((r, i) => 'group' in r
        ? <div key={'g' + i} className="group-label">{r.group}</div>
        : (
          <div key={r.id} className={`lrow ${r.dim ? 'dim' : ''}`}>
            <span className="name">{r.name}</span><span className="amt">{usd(r.bal)}</span>
            {r.tags.length > 0 && <span className="sub">{r.tags.map(([t, c]) => <span key={t} className={`tag ${c ?? ''}`}>{t}</span>)}</span>}
          </div>
        ))}
    </div>
  )
}

/** What you own and what you owe, with rates. */
export default function Snapshot({ sheet }: { sheet: BalanceSheet }) {
  // Have: the ladder order (what can be tapped, cheapest first), then what is never tapped.
  const byId = new Map<string, SheetAccount>(sheet.accounts.map((a) => [a.account_id, a]))
  const have: Row[] = []
  const tiers = sheet.ladder.tiers
  for (const t of tiers.filter((t) => t.proposable)) {
    for (const a of t.accounts) {
      have.push({ id: a.account_id, name: a.name, bal: a.balance, tags: [] })
    }
  }
  const untapped = tiers.filter((t) => !t.proposable).flatMap((t) => t.accounts)
  if (untapped.length) have.push({ group: 'Not tapped' }, ...untapped.map((a) => ({ id: a.account_id, name: a.name, bal: a.balance, tags: [], dim: true })))
  for (const a of sheet.accounts) if (a.side === 'asset' && !have.some((h) => 'id' in h && h.id === a.account_id)) have.push({ id: a.account_id, name: a.name, bal: a.balance, tags: [], dim: true })

  // Owe: avalanche order for extra money, then minimum-only debts.
  const debts = sheet.priority.debts
  const extra = debts.filter((d) => d.extra_rank != null).sort((a, b) => (a.extra_rank ?? 0) - (b.extra_rank ?? 0))
  const minOnly = debts.filter((d) => d.extra_rank == null)
  const owe: Row[] = extra.map((d) => ({ id: d.account_id, name: byId.get(d.account_id)?.name ?? d.name, bal: d.balance, tags: [[`${d.apr_pct}%`]] }))
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
    <div className="ledger">
      <Col title="Have" total={sheet.totals.assets} rows={have} />
      <Col title="Owe" total={sheet.totals.debts} rows={owe} />
    </div>
  )
}
