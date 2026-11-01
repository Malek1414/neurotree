/** Area review: rename, recolor, merge areas and move projects. Every edit marks areas.json as reviewed, so the
 * indexer keeps Malek's placement from then on. Edits are applied to the loaded graph right away (no reindex). */
import type { GraphData, GNode } from './types'

export interface AreaDef { name: string; color: string; projects: string[] }
export interface AreasFile { reviewed: boolean; areas: Record<string, AreaDef> }
export const UNSORTED = 'unsorted'

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

export function parseAreas(text: string): AreasFile | null {
  let raw: unknown
  try { raw = JSON.parse(text) } catch { return null }
  if (!isObj(raw) || !isObj(raw.areas)) return null
  const areas: Record<string, AreaDef> = {}
  for (const [slug, a] of Object.entries(raw.areas)) {
    if (!isObj(a) || typeof a.name !== 'string' || typeof a.color !== 'string' || !Array.isArray(a.projects)
      || !a.projects.every((p) => typeof p === 'string')) return null
    areas[slug] = { name: a.name, color: a.color, projects: [...(a.projects as string[])] }
  }
  return { reviewed: raw.reviewed === true, areas }
}

const edit = (a: AreasFile): AreasFile => ({
  reviewed: true,
  areas: Object.fromEntries(Object.entries(a.areas).map(([k, v]) => [k, { ...v, projects: [...v.projects] }])),
})

export function moveProject(a: AreasFile, project: string, to: string): AreasFile {
  const out = edit(a)
  if (!out.areas[to]) throw new Error(`unknown area ${to}`)
  for (const def of Object.values(out.areas)) def.projects = def.projects.filter((p) => p !== project)
  out.areas[to].projects.push(project)
  return out
}

export function renameArea(a: AreasFile, slug: string, name: string): AreasFile {
  const out = edit(a)
  const clean = name.trim().slice(0, 60)
  if (clean && out.areas[slug]) out.areas[slug].name = clean
  return out
}

export function recolorArea(a: AreasFile, slug: string, color: string): AreasFile {
  const out = edit(a)
  if (out.areas[slug] && /^#[0-9a-f]{6}$/i.test(color)) out.areas[slug].color = color
  return out
}

export function mergeAreas(a: AreasFile, from: string, into: string): AreasFile {
  if (from === UNSORTED || from === into) throw new Error('cannot merge this area')
  const out = edit(a)
  const src = out.areas[from], dst = out.areas[into]
  if (!src || !dst) throw new Error('unknown area')
  dst.projects.push(...src.projects.filter((p) => !dst.projects.includes(p)))
  delete out.areas[from]
  return out
}

function areaNode(id: string, def: AreaDef): GNode {
  return {
    id, kind: 'area', level: 1, parent: 'self', label: def.name, path: null, project: null, lod: 0, color: def.color,
    type: null, importance: 0, attentionRank: 0, signals: { attention: 0, history: 0, structure: null, runtime: null },
    noise: 1, evidence: [], lastActivity: null,
  }
}

/** Mutates the loaded graph to match the areas file; returns whether anything changed. */
export function applyAreas(data: GraphData, a: AreasFile): boolean {
  const byId = new Map(data.nodes.map((n) => [n.id, n]))
  const target = new Map<string, string>()
  let changed = false
  for (const [slug, def] of Object.entries(a.areas)) {
    const id = `area:${slug}`
    let n = byId.get(id)
    if (!n) {
      n = areaNode(id, def)
      data.nodes.push(n)
      byId.set(id, n)
      data.edges.push({ source: 'self', target: id, kind: 'tree', weight: 1 })
      changed = true
    }
    if (n.label !== def.name || n.color !== def.color) { n.label = def.name; n.color = def.color; changed = true }
    for (const p of def.projects) target.set(p, id)
  }
  for (const n of data.nodes) {
    const t = n.level === 2 ? target.get(n.id) : undefined
    if (t && n.parent !== t) { n.parent = t; changed = true }
  }
  if (!changed) return false
  data.edges = data.edges.filter((e) => !(e.kind === 'tree' && byId.get(e.target)?.level === 2))
  for (const n of data.nodes) if (n.level === 2 && n.parent) data.edges.push({ source: n.parent, target: n.id, kind: 'tree', weight: 1 })
  const live = new Set(Object.keys(a.areas).map((s) => `area:${s}`))
  const used = new Set(data.nodes.filter((n) => n.level === 2).map((n) => n.parent))
  const dead = new Set(data.nodes.filter((n) => n.level === 1 && !live.has(n.id) && !used.has(n.id)).map((n) => n.id))
  if (dead.size) {
    data.nodes = data.nodes.filter((n) => !dead.has(n.id))
    data.edges = data.edges.filter((e) => !dead.has(e.source) && !dead.has(e.target))
  }
  return true
}
