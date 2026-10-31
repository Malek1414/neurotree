export interface Filters { code: boolean; docs: boolean; functions: boolean; facts: boolean; noise: boolean; orphans: boolean }
export interface Display { arrows: boolean; labelFade: number; nodeSize: number; linkThickness: number; bloom: number; showImports: boolean }
export interface Forces { corePull: number; repel: number; linkStrength: number; linkDistance: number; cohesion: number }
export interface Group { query: string; color: string }
export interface Settings {
  filters: Filters
  display: Display
  forces: Forces
  groups: Group[]
  areaColors: Record<string, string>
  replaySeconds: number
  localDepth: number
}

export const DEFAULTS: Settings = {
  filters: { code: true, docs: true, functions: true, facts: true, noise: false, orphans: true },
  display: { arrows: false, labelFade: 0.55, nodeSize: 1, linkThickness: 1, bloom: 1.35, showImports: false },
  forces: { corePull: 0.08, repel: 18, linkStrength: 0.05, linkDistance: 1, cohesion: 1 },
  groups: [],
  areaColors: {},
  replaySeconds: 40,
  localDepth: 1,
}

const KEY = 'neurotree.settings.v1'
type Reader = Pick<Storage, 'getItem'> | null
type Writer = Pick<Storage, 'setItem'> | null
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

function section<T extends object>(defaults: T, raw: unknown): T {
  const out = { ...defaults }
  if (!isObj(raw)) return out
  for (const key of Object.keys(defaults) as Array<keyof T>) {
    const v = raw[key as string]
    if (typeof v === typeof defaults[key] && !(typeof v === 'number' && !Number.isFinite(v))) out[key] = v as T[keyof T]
  }
  return out
}

export function mergeSettings(raw: unknown): Settings {
  const r = isObj(raw) ? raw : {}
  const groups = Array.isArray(r.groups)
    ? r.groups.filter((g): g is Group => isObj(g) && typeof g.query === 'string' && typeof g.color === 'string')
        .map((g) => ({ query: g.query, color: g.color }))
    : []
  const areaColors: Record<string, string> = {}
  if (isObj(r.areaColors)) for (const [k, v] of Object.entries(r.areaColors)) if (typeof v === 'string') areaColors[k] = v
  const scalar = section({ replaySeconds: DEFAULTS.replaySeconds, localDepth: DEFAULTS.localDepth }, r)
  return {
    filters: section(DEFAULTS.filters, r.filters),
    display: section(DEFAULTS.display, r.display),
    forces: section(DEFAULTS.forces, r.forces),
    groups,
    areaColors,
    ...scalar,
  }
}

const defaultStorage = (): (Storage | null) => { try { return globalThis.localStorage ?? null } catch { return null } }

export function loadSettings(storage: Reader = defaultStorage()): Settings {
  if (!storage) return mergeSettings(null)
  try {
    return mergeSettings(JSON.parse(storage.getItem(KEY) ?? 'null'))
  } catch {
    return mergeSettings(null)
  }
}

export function saveSettings(s: Settings, storage: Writer = defaultStorage()): void {
  try { storage?.setItem(KEY, JSON.stringify(s)) } catch { /* storage full or blocked: settings stay in memory */ }
}
