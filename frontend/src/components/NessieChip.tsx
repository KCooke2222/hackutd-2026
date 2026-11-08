import { Landmark } from 'lucide-react'

/** Marks anything live in Nessie: a Nessie-backed account, a step that makes a real Nessie call, a receipt row. */
export default function NessieChip({ label = 'Nessie', title }: { label?: string; title?: string }) {
  return (
    <span className="nessie-chip" title={title ?? 'Live in Nessie (Capital One sandbox)'}>
      <Landmark size={12} strokeWidth={2.2} aria-hidden />{label}
    </span>
  )
}
