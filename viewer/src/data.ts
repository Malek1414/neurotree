import type { GNode, GraphData } from './types'

export interface GraphIndex {
  byId: Map<string, GNode>
  children: Map<string, GNode[]>
  areaOf: Map<string, string>
  neighbors: Map<string, Set<string>>
  firstSeen: Map<string, number>
  activity7d: Map<string, number>
  files: Map<string, string>
  roots: Array<[string, string]>
}

const DAY = 86400

function ancestors(byId: Map<string, GNode>, id: string): GNode[] {
  const out: GNode[] = []
  let cur = byId.get(id)
  for (let guard = 0; cur && guard < 8; guard++) {
    out.push(cur)
    cur = cur.parent ? byId.get(cur.parent) : undefined
  }
  return out
}

export function buildIndex(g: GraphData, now: number = g.meta.generated_at): GraphIndex {
  const byId = new Map(g.nodes.map((n) => [n.id, n]))
  const children = new Map<string, GNode[]>()
  for (const n of g.nodes) {
    if (!n.parent || !byId.has(n.parent)) continue
    const list = children.get(n.parent) ?? []
    list.push(n)
    children.set(n.parent, list)
  }
  for (const list of children.values()) list.sort((a, b) => b.importance - a.importance || a.id.localeCompare(b.id))
  const areaOf = new Map<string, string>()
  for (const n of g.nodes) {
    const area = ancestors(byId, n.id).find((a) => a.level === 1)
    if (area) areaOf.set(n.id, area.id)
  }
  const neighbors = new Map<string, Set<string>>()
  const link = (a: string, b: string) => {
    let s = neighbors.get(a)
    if (!s) neighbors.set(a, (s = new Set()))
    s.add(b)
  }
  for (const e of g.edges) {
    if (byId.has(e.source) && byId.has(e.target)) { link(e.source, e.target); link(e.target, e.source) }
  }
  const firstSeen = new Map<string, number>()
  const activity7d = new Map<string, number>()
  for (const [id, ts, , weight] of g.events) {
    for (const a of ancestors(byId, id)) {
      const seen = firstSeen.get(a.id)
      if (seen === undefined || ts < seen) firstSeen.set(a.id, ts)
      if (now - ts <= 7 * DAY) activity7d.set(a.id, (activity7d.get(a.id) ?? 0) + weight)
    }
  }
  const files = new Map<string, string>()
  const roots: Array<[string, string]> = []
  for (const n of g.nodes) {
    if (!n.path) continue
    if (n.level === 3) files.set(n.path, n.id)
    else if (n.level === 2) roots.push([n.path, n.id])
  }
  roots.sort((a, b) => b[0].length - a[0].length)
  return { byId, children, areaOf, neighbors, firstSeen, activity7d, files, roots }
}

export function resolvePath(index: GraphIndex, path: string): string | null {
  const hit = index.files.get(path)
  if (hit) return hit
  for (const [root, id] of index.roots) {
    if (path === root || path.startsWith(root.endsWith('/') ? root : `${root}/`)) return id
  }
  return null
}
