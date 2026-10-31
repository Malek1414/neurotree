import type { GraphIndex } from './data'
import type { Forces } from './settings'

export type Vec = [number, number, number]

/** Base radius per level: self, area, project, file, detail. Hot nodes sit at the base, cold ones up to 1.8x out. */
export const LEVEL_BASE = [0, 150, 360, 660, 840]
const GOLDEN = Math.PI * (3 - Math.sqrt(5))

export function fibonacciSphere(n: number): Vec[] {
  if (n <= 0) return []
  if (n === 1) return [[0, 1, 0]]
  const out: Vec[] = []
  for (let i = 0; i < n; i++) {
    const y = 1 - (2 * (i + 0.5)) / n
    const r = Math.sqrt(1 - y * y)
    const phi = i * GOLDEN
    out.push([Math.cos(phi) * r, y, Math.sin(phi) * r])
  }
  return out
}

function normalize(v: Vec): Vec {
  const l = Math.hypot(...v) || 1
  return [v[0] / l, v[1] / l, v[2] / l]
}

function cross(a: Vec, b: Vec): Vec {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
}

/** n unit directions spread evenly (Fibonacci spiral) inside a cone of halfAngle around axis; the first is the axis. */
export function coneDirections(axis: Vec, n: number, halfAngle: number): Vec[] {
  const a = normalize(axis)
  const helper: Vec = Math.abs(a[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0]
  const u = normalize(cross(a, helper))
  const v = cross(a, u)
  const out: Vec[] = []
  for (let i = 0; i < n; i++) {
    const theta = n === 1 ? 0 : halfAngle * Math.sqrt(i / (n - 1))
    const phi = i * GOLDEN
    const s = Math.sin(theta), c = Math.cos(theta)
    out.push(normalize([
      c * a[0] + s * (Math.cos(phi) * u[0] + Math.sin(phi) * v[0]),
      c * a[1] + s * (Math.cos(phi) * u[1] + Math.sin(phi) * v[1]),
      c * a[2] + s * (Math.cos(phi) * u[2] + Math.sin(phi) * v[2]),
    ]))
  }
  return out
}

export function targetRadius(level: number, attentionRank: number, spread = 0.8): number {
  const base = LEVEL_BASE[Math.min(level, LEVEL_BASE.length - 1)]
  return base * (1 + spread * (1 - Math.max(0, Math.min(1, attentionRank))))
}

/** Self at the origin, areas on a sphere, every parent's children in a cone around the parent's direction. */
export function computeTargets(index: GraphIndex, forces: Forces): Map<string, Vec> {
  const out = new Map<string, Vec>([['self', [0, 0, 0]]])
  const dirOf = new Map<string, Vec>()
  const areas = index.children.get('self') ?? []
  const areaDirs = fibonacciSphere(areas.length)
  const areaCone = Math.min(1.05, 2.3 / Math.sqrt(Math.max(1, areas.length))) * forces.cohesion
  const place = (id: string, dir: Vec, level: number, rank: number) => {
    dirOf.set(id, dir)
    const r = targetRadius(level, rank) * forces.linkDistance
    out.set(id, [dir[0] * r, dir[1] * r, dir[2] * r])
  }
  areas.forEach((a, i) => place(a.id, areaDirs[i], 1, a.attentionRank))
  const queue = [...areas]
  while (queue.length) {
    const parent = queue.shift()!
    const kids = index.children.get(parent.id) ?? []
    if (!kids.length) continue
    const half = parent.level === 1 ? areaCone
      : parent.level === 2 ? Math.min(0.55, areaCone / Math.sqrt(Math.max(1, (index.children.get(parent.parent ?? '') ?? []).length)) * 2.2) * forces.cohesion
        : 0.12 * forces.cohesion
    const dirs = coneDirections(dirOf.get(parent.id)!, kids.length, half)
    kids.forEach((k, i) => { place(k.id, dirs[i], k.level, k.attentionRank); queue.push(k) })
  }
  for (const [id, n] of index.byId) if (!out.has(id)) place(id, [0, -1, 0], n.level, n.attentionRank)
  return out
}
