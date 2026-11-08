import { api } from './api'
import type { PlanV2, Proposal } from './api'

/** One month of the cushion chart: savings (cash) and high-cost debt. */
export type CushionPoint = { month: string; cash: number; debt: number }

export type CushionShock = { id: string; label: string; borrowed_on_card: number; min_cash: number }

/** The plan with this many months of expenses kept in savings (scenarios.cushion_options). */
export type CushionOption = {
  months: number
  current: boolean
  emergency_buffer: number
  pay_from_savings: number
  sell_investments: number
  high_cost_debt_free: string | null
  net_worth_24m: number
  net_worth_delta: number | null
  min_cash: number
  min_cash_month: string
  below_buffer: boolean
  series: CushionPoint[]
  job_loss_series: CushionPoint[]
  shocks: CushionShock[]
}

export type Cushions = {
  current_months: number
  monthly_expenses: number | null
  options: CushionOption[]
  same_plan: boolean // every cushion gives the same plan (nothing spare in savings to move)
  errors: Record<string, string>
}

export const cushions = (cid: string, plan: PlanV2) =>
  api<Cushions>(`/customers/${cid}/scenarios/cushion`, { method: 'POST', body: JSON.stringify({ plan }) })

/** Send a change to the plan through the chat-edit stream (the same one Chat uses). Resolves with the new
 *  pending plan once it is saved (the stream stays open for its headline); nothing moves until approve. */
export function chatEdit(proposalId: string, message: string, onStep?: (label: string) => void): { done: Promise<Proposal>; cancel: () => void } {
  const s = new EventSource(`/api/proposals/${proposalId}/adjust/stream?message=${encodeURIComponent(message)}`)
  let settled = false
  const done = new Promise<Proposal>((resolve, reject) => {
    const settle = (f: () => void) => { if (!settled) { settled = true; f() } }
    s.addEventListener('call', (e) => { const d = JSON.parse((e as MessageEvent).data); if (d.label) onStep?.(d.label) })
    s.addEventListener('saved', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      if (d.proposal) settle(() => resolve(d.proposal))
    })
    s.addEventListener('done', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      s.close()
      settle(() => (d.proposal ? resolve(d.proposal) : reject(new Error(d.clarify ?? d.error ?? 'The plan did not change.'))))
    })
    s.addEventListener('error', (e) => {
      const raw = (e as MessageEvent).data
      s.close()
      settle(() => reject(new Error(raw ? JSON.parse(raw).error : 'Connection to the agent dropped (is the plan still pending?).')))
    })
  })
  return { done, cancel: () => { settled = true; s.close() } }
}
