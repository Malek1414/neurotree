import type { GraphIndex } from './data'
import type { Filters } from './settings'
import type { GNode, GraphData } from './types'

export interface ViewState {
  expanded: Set<string>
  selected: string | null
  local: { id: string; depth: number } | null
  query: string
}

export function matches(n: GNode, query: string): boolean {
  const q = query.trim().toLowerCase()
  return !!q && (n.label.toLowerCase().includes(q) || (n.path ?? '').toLowerCase().includes(q))
}

function passes(n: GNode, f: Filters, index: GraphIndex): boolean {
  if (n.level < 3) return true
  if (n.kind === 'function') return f.functions
  if (n.kind === 'fact') return f.facts
  if (n.type === 'code' && !f.code) return false
  if (n.type === 'doc' && !f.docs) return false
  if (n.noise < 1 && !f.noise) return false
  if (!f.orphans && n.importance <= 0 && (index.neighbors.get(n.id)?.size ?? 0) <= 1) return false
  return true
}

export function visibleIds(data: GraphData, index: GraphIndex, state: ViewState, filters: Filters): Set<string> {
  const out = new Set<string>()
  if (state.local) {
    let frontier = [state.local.id]
    out.add(state.local.id)
    for (let d = 0; d < state.local.depth; d++) {
      const next: string[] = []
      for (const id of frontier) for (const nb of index.neighbors.get(id) ?? []) {
        const n = index.byId.get(nb)
        if (n && !out.has(nb) && passes(n, filters, index)) { out.add(nb); next.push(nb) }
      }
      frontier = next
    }
    return out
  }
  const selectedFile = state.selected ? index.byId.get(state.selected) : undefined
  const topFiles = new Set(data.nodes.filter((n) => n.level === 3 && n.lod === 0)
    .sort((a, b) => b.importance - a.importance).slice(0, filters.maxFiles).map((n) => n.id))
  for (const n of data.nodes) {
    let show = n.level <= 2 || topFiles.has(n.id)
    if (n.level === 3 && n.parent && state.expanded.has(n.parent)) show = true
    if (n.level === 4) show = !!selectedFile && (n.parent === selectedFile.id || state.expanded.has(n.parent ?? ''))
    if (state.query) show = matches(n, state.query)
    if (show && passes(n, filters, index)) out.add(n.id)
  }
  for (const id of [...out]) {  // keep the tree connected: every visible node's ancestors stay visible
    let cur = index.byId.get(id)
    for (let guard = 0; cur?.parent && guard < 8; guard++) {
      out.add(cur.parent)
      cur = index.byId.get(cur.parent)
    }
  }
  if (state.query) out.add('self')
  return out
}
