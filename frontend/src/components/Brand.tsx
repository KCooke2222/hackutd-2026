/** Paidway wordmark: a path that steps down to zero. */
export function Mark({ size = 22 }: { size?: number }) {
  return (
    <svg className="mark" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <rect width="24" height="24" rx="6.5" fill="var(--accent)" />
      <path d="M5.5 7H10v5h4v5h3" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="18.2" cy="17" r="1.9" fill="#fff" />
    </svg>
  )
}

export default function Brand() {
  return (
    <a className="brand" href="/" aria-label="Paidway home">
      <Mark />
      <span>Paidway</span>
    </a>
  )
}
