// Tiny markdown renderer for the PNC README (headings, paragraphs, lists, pipe tables, code).
import type { ReactNode } from 'react'

function inline(s: string): ReactNode[] {
  const parts = s.split(/(`[^`]+`|\*\*[^*]+\*\*)/g)
  return parts.map((p, i) =>
    p.startsWith('`') ? <code key={i}>{p.slice(1, -1)}</code> : p.startsWith('**') ? <strong key={i}>{p.slice(2, -2)}</strong> : p,
  )
}

export default function Markdown({ text }: { text: string }) {
  const lines = text.split('\n')
  const out: ReactNode[] = []
  let i = 0
  while (i < lines.length) {
    const l = lines[i]
    if (l.startsWith('```')) {
      const buf: string[] = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) buf.push(lines[i++])
      i++
      out.push(<pre key={i}>{buf.join('\n')}</pre>)
    } else if (/^#{1,4} /.test(l)) {
      const level = l.match(/^#+/)![0].length
      const H = `h${Math.min(level + 1, 5)}` as 'h2'
      out.push(<H key={i}>{inline(l.replace(/^#+ /, ''))}</H>)
      i++
    } else if (l.startsWith('|')) {
      const rows: string[][] = []
      while (i < lines.length && lines[i].startsWith('|')) {
        if (!/^\|[\s:-]+\|/.test(lines[i]) || /[a-z]/i.test(lines[i]))
          rows.push(lines[i].split('|').slice(1, -1).map((c) => c.trim()))
        i++
      }
      const [head, ...body] = rows
      out.push(
        <div className="table-wrap" key={i}>
          <table>
            <thead><tr>{head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr></thead>
            <tbody>{body.map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      )
    } else if (/^\s*[-*] /.test(l)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*] /.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*] /, ''))
      out.push(<ul key={i}>{items.map((t, j) => <li key={j}>{inline(t)}</li>)}</ul>)
    } else if (l.trim() === '') {
      i++
    } else {
      const buf: string[] = []
      while (i < lines.length && lines[i].trim() !== '' && !/^(#|\||```|\s*[-*] )/.test(lines[i])) buf.push(lines[i++])
      out.push(<p key={i}>{inline(buf.join(' '))}</p>)
    }
  }
  return <div className="markdown">{out}</div>
}
