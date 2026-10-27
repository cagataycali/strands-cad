// A small markdown renderer for Fomo's answers: paragraphs, **bold**, `code`, bullet/numbered lists,
// pipe tables, headings. No HTML passthrough, no dependencies — the agent writes tables often.
import { Fragment, type ReactNode } from 'react'

function inline(s: string, key = 0): ReactNode {
  const parts: ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g
  let last = 0, m: RegExpExecArray | null, i = 0
  while ((m = re.exec(s))) {
    if (m.index > last) parts.push(s.slice(last, m.index))
    const t = m[0]
    if (t.startsWith('**')) parts.push(<b key={i++}>{t.slice(2, -2)}</b>)
    else if (t.startsWith('`')) parts.push(<code key={i++}>{t.slice(1, -1)}</code>)
    else parts.push(<em key={i++}>{t.slice(1, -1)}</em>)
    last = m.index + t.length
  }
  if (last < s.length) parts.push(s.slice(last))
  return <Fragment key={key}>{parts}</Fragment>
}

const cells = (row: string) => row.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
const isSep = (row: string) => /^\s*\|?\s*:?-{2,}/.test(row)

export function Md({ text }: { text: string }) {
  const lines = text.replace(/\r/g, '').split('\n')
  const out: ReactNode[] = []
  let i = 0, k = 0
  while (i < lines.length) {
    const l = lines[i]
    if (!l.trim()) { i++; continue }
    if (/^\s*\|/.test(l) && i + 1 < lines.length && isSep(lines[i + 1])) {
      const head = cells(l); const rows: string[][] = []; i += 2
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(cells(lines[i++]))
      out.push(<table key={k++}><thead><tr>{head.map((h, j) => <th key={j}>{inline(h)}</th>)}</tr></thead><tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody></table>)
      continue
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(l)) {
      const items: string[] = []; const ordered = /^\s*\d/.test(l)
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*•]|\d+[.)])\s+/, ''))
      out.push(ordered ? <ol key={k++}>{items.map((t, j) => <li key={j}>{inline(t)}</li>)}</ol> : <ul key={k++}>{items.map((t, j) => <li key={j}>{inline(t)}</li>)}</ul>)
      continue
    }
    if (/^#{1,3}\s/.test(l)) { out.push(<h4 key={k++}>{inline(l.replace(/^#+\s/, ''))}</h4>); i++; continue }
    if (l.startsWith('```')) { const buf: string[] = []; i++; while (i < lines.length && !lines[i].startsWith('```')) buf.push(lines[i++]); i++; out.push(<pre key={k++}>{buf.join('\n')}</pre>); continue }
    const buf: string[] = []
    while (i < lines.length && lines[i].trim() && !/^\s*\|/.test(lines[i]) && !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i]) && !/^#{1,3}\s/.test(lines[i]) && !lines[i].startsWith('```')) buf.push(lines[i++])
    // a lone `| a | b |` with no separator yet (a table still streaming in) is a paragraph for now —
    // without this the loop never advanced and the tab died (found by the voice harness, U4)
    if (!buf.length) buf.push(lines[i++])
    out.push(<p key={k++}>{inline(buf.join(' '))}</p>)
  }
  return <>{out}</>
}
