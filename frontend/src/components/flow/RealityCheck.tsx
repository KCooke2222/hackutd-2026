import { usd } from '../../lib/api'
import type { RealityCheck as RC } from '../../lib/api'

/** Leads the plan: can the debt shrink at all with this income and spending, even after every change? */
export default function RealityCheck({ rc }: { rc: RC }) {
  const m = rc.monthly
  const g = rc.close_gap
  if (rc.realistic)
    return (
      <div className="notice small">
        <b>Reality check: the debt can shrink.</b> After every change, about {usd(m.principal_paydown)}/mo goes to principal
        ({usd(m.debt_room)} room vs {usd(m.interest)} interest + {usd(m.card_charges)} new card charges).
      </div>
    )
  return (
    <div className="card" style={{ borderColor: 'color-mix(in srgb, var(--bad) 30%, transparent)', background: 'var(--bad-soft)' }}>
      <h2 style={{ margin: '0 0 4px', color: 'var(--bad-ink)' }}>{usd(rc.gap_per_month)}/mo short</h2>
      <p style={{ margin: '0 0 10px' }}>
        Even after every realistic change ({usd(rc.levers.spending_cuts_per_month)}/mo of cuts
        {rc.levers.redirect_per_month > 0 && <>, {usd(rc.levers.redirect_per_month)}/mo redirected</>}
        {rc.levers.lump_sums.length > 0 && <>, savings and investments used</>}), the debt keeps growing.
        This plan only slows it down.
      </p>
      {g && (
        <div className="ba">
          <div><div className="k">Short each month</div><div className="to" style={{ color: 'var(--bad-ink)' }}>{usd(rc.gap_per_month)}</div></div>
          <div><div className="k">Spend less</div><div className="to">{usd(g.spend_less_per_month)}/mo</div>
            <div className="small muted">total ≤ {usd(g.spending_target_per_month)}/mo</div></div>
          <div><div className="k">Or earn more</div><div className="to">{usd(g.earn_more_per_month)}/mo</div>
            <div className="small muted">income ≥ {usd(g.income_target_per_month)}/mo</div></div>
        </div>
      )}
      {g && !g.spending_alone_can_close && (
        <p className="small" style={{ margin: '10px 0 0' }}>
          Only {usd(rc.levers.discretionary_left_per_month)}/mo of discretionary spending is left, so cuts alone can't close it:
          income has to rise by at least {usd(g.min_extra_income_if_spending_maxed)}/mo.
        </p>
      )}
      <p className="small muted" style={{ margin: '10px 0 0' }}>
        Each month: {usd(m.debt_room)} can go to debt, but it needs {usd(Math.max(m.minimums, m.interest + m.card_charges))} ({usd(m.interest)} interest + {usd(m.card_charges)} new card charges; minimums {usd(m.minimums)}).
      </p>
    </div>
  )
}
