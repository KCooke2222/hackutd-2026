export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
  })
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body.detail ?? `${r.status} ${r.statusText}`)
  return body as T
}

export const usd = (n: number | null | undefined, digits = 0) =>
  n == null ? '—' : n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: digits, minimumFractionDigits: digits })

export const pct = (n: number | null | undefined) => (n == null ? '—' : `${n}%`)

export type Account = {
  customer_id: string
  account_id: string
  account_type: string
  balance: number
  monthly_payment: number
  apr_pct: number
  annual_yield_pct: number
  opening_balance: number
  history_start: string
  as_of_date: string
}

export type Statement = {
  account_id: string
  account_type: string
  month: string
  opening_balance: number
  balance_increases: number
  balance_decreases: number
  closing_balance: number
  transaction_count: number
}

export type Profile = { customer_id: string; monthly_income: number; monthly_non_debt_expenses: number; credit_limit: number }

export type Totals = {
  assets: number
  debts: number
  net_worth: number
  liquid: number
  card_balance: number
  card_apr_pct: number
  card_interest_last_12m: number
}

export type Refill = {
  has_card: boolean
  apr_pct: number
  period: { from: string; to: string }
  balance_start: number
  balance_end: number
  net_change: number
  monthly_avg: { payment: number; new_spending: number; interest: number; fees: number }
  new_spending_by_category: Record<string, { monthly_avg: number; share_of_payment: number }>
  share_of_payment_refilled: number
}

export type Sim = {
  pays_off: boolean
  months_to_zero: number | null
  total_interest: number | null
  interest_first_12m: number
  start_balance: number
  schedule: { month: number; balance: number }[]
}

// ---------- plan v2 (docs/agent-v2/CONTRACT.md) ----------

export type LumpSum = { source: 'cash' | 'cd' | 'brokerage'; amount: number; to_debt: string }
export type Redirect = { flow: string; amount_per_month: number }
export type SpendingTarget = { category: string; current: number; target: number; proof_month: string; how: string[] }

export type PlanV2 = {
  version: 2
  monthly_debt_budget: number
  extra_order: string[]
  lump_sums: LumpSum[]
  redirects: Redirect[]
  spending_targets: SpendingTarget[]
  autopay_full_cards: string[]
  keep_emergency_months: number
}

export type SimDebt = { account_id: string; name: string; apr_pct: number; balance: number; min_payment?: number; high_cost?: boolean; payoff_month: string | null; interest: number }
export type SimMonth = { month: string; total_debt: number; high_cost_debt: number; cash: number; invested: number; net_worth: number }

export type SimV2 = {
  feasible: boolean
  problems: string[]
  debts: SimDebt[]
  months: SimMonth[]
  net_worth_24m: number
  baseline_net_worth_24m: number
  interest_total: number
  baseline_interest_total: number
  high_cost_debt_free: string | null
  upfront_cost: number
  budget?: { current_debt_payments: number; redirects: number; checking_cuts: number; card_cuts: number; max_monthly_debt_budget: number; minimums: number }
}

export type Explain = { headline: string; levers?: { kind: string; label: string; amount?: number }[]; cuts?: { category: string; how: string }[] }

export type Proposal = {
  id: string
  customer_id: string
  status: string
  created_at: string
  plan: PlanV2
  simulation: SimV2 & { explain?: Explain; balances_snapshot?: unknown }
  rationale: string
  execution?: {
    mode?: string
    note?: string
    amount?: number
    transfer?: unknown
    transfers_pending?: { to_debt: string; amount: number }[]
    scheduled?: { kind: string; step: string; how?: string[] }[]
    error?: string
  }
}

export type Source = 'pnc' | 'nessie' | 'linked'

export type SheetAccount = {
  account_id: string
  name: string
  account_type: string
  kind: string | null
  institution: string | null
  source: Source
  side: 'asset' | 'debt'
  balance: number
  apr_pct: number
  annual_yield_pct: number
  min_payment: number
  cost_basis: number | null
  secured: boolean
  consequence_tier: number | null
  secures_account_id: string | null
}

export type LinkedAccount = Omit<SheetAccount, 'side'> & { customer_id: string; as_of_date: string }

export type Draw = { tier: number; key: string; amount: number; cost: number }

export type Tier = {
  tier: number
  key: string
  label: string
  proposable: boolean
  balance: number
  available: number
  cost_pct: number | null
  note: string | null
  accounts: { account_id: string; name: string; balance: number; source: Source }[]
}

export type Ladder = {
  keep_months: number
  monthly_expenses: number
  emergency_buffer: number
  tiers: Tier[]
  proposable_available: number
  tap?: { amount: number; draws: Draw[]; total_cost: number; shortfall: number } | null
}

export type RankedDebt = {
  account_id: string
  name: string
  account_type: string
  kind: string | null
  source: Source
  balance: number
  apr_pct: number
  min_payment: number
  secured: boolean
  consequence_tier: number
  consequence: string
  minimum_order: number
  extra_rank: number | null
  action: string
  yearly_interest: number
}

export type Priority = {
  debts: RankedDebt[]
  monthly_minimums: number
  monthly_income: number
  monthly_expenses: number
  monthly_surplus: number
  prepay_hurdle_pct: number
  rule: string
}

export type BalanceSheet = {
  customer_id: string
  accounts: SheetAccount[]
  totals: { assets: number; debts: number; net_worth: number; liquid: number; by_source: Record<string, number> }
  ladder: Ladder
  priority: Priority
}

export const SOURCE_LABEL: Record<Source, string> = { pnc: 'PNC data', nessie: 'Nessie (live)', linked: 'Linked' }

export const ACCOUNT_COLORS: Record<string, string> = {
  checking: '#2563eb',
  savings: '#16a34a',
  credit_card: '#dc2626',
  auto_loan: '#ea580c',
  personal_loan: '#d97706',
  mortgage: '#7c3aed',
  brokerage: '#0891b2',
  ira: '#0d9488',
  cd: '#65a30d',
  student_loan: '#be185d',
  property: '#78716c',
}

/** A recorded agent run (backend replays.py); replayed through /api/replays/{id}/stream?speed=N. */
export type ReplayMeta = { id: string; customer_id: string; recorded_at: string; duration_ms: number; model: string | null; headline: string | null; proposal_id: string | null }
export type ReplayRun = { id: string; speed: number; duration_ms: number }

export const fmtDuration = (ms: number) => {
  const s = Math.round(ms / 1000)
  return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`
}
