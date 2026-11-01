# Neurotree Viewer Implementation Plan (plan 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The 3D attention tree in the browser/Tauri webview: radial "attention core" layout, importance-weighted firing (live, replay, breathing), Obsidian-style interactions (hover, click, local graph, search, filters, groups, display, forces).

**Architecture:** Vite + TypeScript. Pure, unit-tested modules (`data`, `layout`, `visibility`, `firing`, `settings`) feed an imperative `Scene` on 3d-force-graph (three.js) and a DOM `UI`. `bridge` talks to the Tauri shell when present (contract in plan 3) and falls back to `/graph.json` from the Vite dev server.

**Tech Stack:** three 0.187, 3d-force-graph 1.80, d3-force-3d 3, three-spritetext 1.10, Vite 8, TypeScript 5.9, Vitest 5, @tauri-apps/api 2.

## Global Constraints
- Dev server binds to `127.0.0.1:5173` only (graph.json is personal data); `/graph.json` comes from `NEUROTREE_GRAPH` or `~/Library/Application Support/Neurotree/graph.json`, else `fixtures/graph.json`; `?fixture=<name>` (letters, digits, dashes only) serves `fixtures/<name>.json`.
- All repo-derived text goes into the DOM via `textContent`; tooltips escape HTML.
- Tree links point child -> parent so particles travel toward the core.
- Visible-node cap by default: levels 0–2 plus the exported `lod 0` files (indexer caps at 6,000).
- Tauri contract: commands `load_graph`, `status`, `open_path{path}`, `reveal_path{path}`, `reindex`; events `fs-change {paths, source}`, `index-status {running, finished_at, error}`.

## File Structure
```
viewer/package.json, tsconfig.json, vite.config.ts, index.html
viewer/src/types.ts        graph.json types
viewer/src/data.ts         buildIndex(), resolvePath()
viewer/src/layout.ts       fibonacciSphere(), coneDirections(), targetRadius(), computeTargets()
viewer/src/visibility.ts   visibleIds()
viewer/src/firing.ts       chainToCore(), pickBurst(), intensity(), Replay
viewer/src/settings.ts     Settings, DEFAULTS, mergeSettings(), load/save
viewer/src/bridge.ts       Tauri / dev-server bridge
viewer/src/scene.ts        3d-force-graph scene: nodes, links, bloom, firing, camera
viewer/src/ui.ts           top bar, side panel, settings drawer, replay bar
viewer/src/main.ts         wiring, keyboard, render loop
viewer/src/style.css
viewer/src/shims.d.ts      d3-force-3d types
viewer/scripts/make-fixture.mjs   deterministic synthetic graphs (small fixture, 20k perf fixture)
viewer/fixtures/graph.json        committed small fixture
viewer/tests/*.test.ts
```

---

### Task 1: Scaffold, types, data index, settings

**Files:** Create `viewer/package.json`, `viewer/tsconfig.json`, `viewer/vite.config.ts`, `viewer/src/types.ts`, `viewer/src/data.ts`, `viewer/src/settings.ts`, `viewer/scripts/make-fixture.mjs`, `viewer/tests/data.test.ts`, `viewer/tests/settings.test.ts`

**Interfaces:**
- Produces: types `GNode, GEdge, GEvent, GraphData`; `buildIndex(g, now?) -> GraphIndex` (`byId, children (sorted by importance desc), areaOf, neighbors, firstSeen (rolled up to ancestors, min), activity7d (rolled up), files (level-3 path -> id), roots ([project path, id] longest first)`); `resolvePath(index, path) -> id|null`; `Settings` (`filters, display, forces, groups, areaColors, replaySeconds, localDepth`), `DEFAULTS`, `mergeSettings(raw)`, `loadSettings(storage?)`, `saveSettings(s, storage?)`; fixture generator `node scripts/make-fixture.mjs <out> <files> [projects] [areas]`.

- [ ] **Step 1: Scaffold and fixture**

**File:** `viewer/package.json`
```json
{
  "name": "neurotree-viewer",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "fixture": "node scripts/make-fixture.mjs fixtures/graph.json 60 6 3 && node scripts/make-fixture.mjs fixtures/big.json 20000 240 9"
  },
  "dependencies": {
    "3d-force-graph": "^1.80.1",
    "@tauri-apps/api": "^2.12.4",
    "d3-force-3d": "^3.0.6",
    "three": "^0.187.0",
    "three-spritetext": "^1.10.0"
  },
  "devDependencies": {
    "@types/three": "^0.187.0",
    "typescript": "~5.9.2",
    "vite": "^8.3.5",
    "vitest": "^5.0.5"
  }
}
```

**File:** `viewer/tsconfig.json`
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "types": ["vite/client"]
  },
  "include": ["src", "tests", "vite.config.ts"]
}
```

**File:** `viewer/vite.config.ts`
```ts
/// <reference types="vitest/config" />
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { defineConfig } from 'vite'

const here = path.dirname(new URL(import.meta.url).pathname)
const dataGraph = process.env.NEUROTREE_GRAPH ?? path.join(os.homedir(), 'Library/Application Support/Neurotree/graph.json')

export default defineConfig({
  clearScreen: false,
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
  build: { chunkSizeWarningLimit: 2000 },
  plugins: [{
    name: 'neurotree-graph',
    configureServer(server) {
      server.middlewares.use('/graph.json', (req, res) => {
        const name = new URL(req.url ?? '/', 'http://local').searchParams.get('fixture')
        const file = name && /^[a-z0-9-]+$/.test(name)
          ? path.join(here, 'fixtures', `${name}.json`)
          : fs.existsSync(dataGraph) ? dataGraph : path.join(here, 'fixtures/graph.json')
        if (!fs.existsSync(file)) { res.statusCode = 404; res.end('missing'); return }
        res.setHeader('Content-Type', 'application/json')
        res.setHeader('Cache-Control', 'no-store')
        fs.createReadStream(file).pipe(res)
      })
    },
  }],
  test: { include: ['tests/**/*.test.ts'], environment: 'node' },
})
```

**File:** `viewer/scripts/make-fixture.mjs`
```js
// Deterministic synthetic graph in the graph.json format: node scripts/make-fixture.mjs <out> <files> [projects] [areas]
import fs from 'node:fs'
import path from 'node:path'

const [out = 'fixtures/graph.json', filesArg = '60', projectsArg = '6', areasArg = '3'] = process.argv.slice(2)
const FILES = Number(filesArg), PROJECTS = Number(projectsArg), AREAS = Number(areasArg)
const NOW = 1793500000, DAY = 86400
let seed = 7
const rnd = () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296 }
const COLORS = ['#f5a524', '#5b8def', '#3ecf8e', '#e5484d', '#a78bfa', '#2dd4bf', '#f472b6', '#facc15', '#60a5fa']
const sig = (a) => ({ attention: a, history: +(rnd().toFixed(3)), structure: rnd() < 0.5 ? +(rnd().toFixed(3)) : null, runtime: null })
const node = (o) => ({ path: null, project: null, lod: 0, color: null, type: null, noise: 1, evidence: [], lastActivity: null, ...o })
const nodes = [node({ id: 'self', kind: 'self', level: 0, parent: null, label: 'Malek', importance: 1, attentionRank: 1, signals: sig(1) })]
const edges = []
for (let a = 0; a < AREAS; a++) {
  const imp = +(0.4 + 0.6 * rnd()).toFixed(3)
  nodes.push(node({ id: `area:a${a}`, kind: 'area', level: 1, parent: 'self', label: `Area ${a}`, color: COLORS[a % COLORS.length], importance: imp, attentionRank: imp, signals: sig(imp) }))
  edges.push({ source: 'self', target: `area:a${a}`, kind: 'tree', weight: 1 })
}
for (let p = 0; p < PROJECTS; p++) {
  const imp = +(rnd() ** 1.5).toFixed(3), area = `area:a${p % AREAS}`
  nodes.push(node({ id: `project:p${p}`, kind: 'project', level: 2, parent: area, label: `project-${p}`, path: `/fx/p${p}`, importance: imp, attentionRank: imp, signals: sig(imp), evidence: [`${Math.round(imp * 40)} Claude touches in 30 days`] }))
  edges.push({ source: area, target: `project:p${p}`, kind: 'tree', weight: 1 })
}
const fileIds = []
for (let f = 0; f < FILES; f++) {
  const p = Math.floor(rnd() * PROJECTS), imp = +(rnd() ** 2).toFixed(3), id = `file:/fx/p${p}/f${f}.${f % 3 ? 'py' : 'md'}`
  fileIds.push([id, p])
  nodes.push(node({ id, kind: 'file', level: 3, parent: `project:p${p}`, project: `project:p${p}`, label: `f${f}.${f % 3 ? 'py' : 'md'}`, path: id.slice(5), lod: f < 6000 ? 0 : 1, type: f % 3 ? 'code' : 'doc', importance: imp, attentionRank: imp, signals: sig(imp), noise: f % 17 === 0 ? 0.1 : 1, lastActivity: NOW - Math.floor(rnd() * 90) * DAY }))
  edges.push({ source: `project:p${p}`, target: id, kind: 'tree', weight: 1 })
}
for (let i = 0; i < Math.min(5, FILES); i++) {
  const [fid, p] = fileIds[i]
  nodes.push(node({ id: `fn:${fid.slice(5)}#run`, kind: 'function', level: 4, parent: fid, project: `project:p${p}`, label: 'run', path: fid.slice(5), lod: 2, importance: 0, attentionRank: 0, signals: sig(0) }))
  edges.push({ source: fid, target: `fn:${fid.slice(5)}#run`, kind: 'tree', weight: 1 })
}
if (FILES) {
  nodes.push(node({ id: 'fact:1', kind: 'fact', level: 4, parent: fileIds[0][0], project: `project:p${fileIds[0][1]}`, label: 'Core returns 2 now', lod: 2, importance: 0, attentionRank: 0, signals: sig(0) }))
  edges.push({ source: fileIds[0][0], target: 'fact:1', kind: 'tree', weight: 1 })
}
for (let i = 0; i < FILES * 0.6; i++) {
  const [a, pa] = fileIds[Math.floor(rnd() * FILES)], [b, pb] = fileIds[Math.floor(rnd() * FILES)]
  if (a !== b && pa === pb) edges.push({ source: a, target: b, kind: 'import', weight: 1 })
}
for (let i = 0; i < PROJECTS; i++) {
  const a = Math.floor(rnd() * PROJECTS), b = Math.floor(rnd() * PROJECTS)
  if (a !== b) edges.push({ source: `project:p${a}`, target: `project:p${b}`, kind: 'co_attention', weight: +(1 + rnd() * 4).toFixed(2) })
}
const events = []
for (let i = 0; i < FILES * 4; i++) {
  const [id] = fileIds[Math.floor(rnd() * FILES)]
  events.push([id, NOW - Math.floor(rnd() * 120 * DAY), ['edit', 'read', 'commit'][i % 3], [3, 1, 2][i % 3]])
}
events.sort((x, y) => x[1] - y[1])
const data = { meta: { generated_at: NOW, counts: { nodes: nodes.length, edges: edges.length, events: events.length }, status: {} }, nodes, edges, events }
fs.mkdirSync(path.dirname(out), { recursive: true })
fs.writeFileSync(out, JSON.stringify(data))
console.log(`wrote ${out}: ${nodes.length} nodes, ${edges.length} edges, ${events.length} events`)
```

- [ ] **Step 2: Write the failing tests**

**File:** `viewer/tests/data.test.ts`
```ts
import { describe, expect, it } from 'vitest'
import { buildIndex, resolvePath } from '../src/data'
import type { GraphData, GNode } from '../src/types'

const n = (o: Partial<GNode> & Pick<GNode, 'id' | 'level'>): GNode => ({
  kind: 'file', parent: null, label: o.id, path: null, project: null, lod: 0, color: null, type: null, importance: 0,
  attentionRank: 0, signals: { attention: 0, history: 0, structure: null, runtime: null }, noise: 1, evidence: [],
  lastActivity: null, ...o,
})
export const tiny: GraphData = {
  meta: { generated_at: 1000 * 86400, counts: {}, status: {} },
  nodes: [
    n({ id: 'self', level: 0, kind: 'self', importance: 1 }),
    n({ id: 'area:a', level: 1, kind: 'area', parent: 'self', color: '#f5a524' }),
    n({ id: 'project:p', level: 2, kind: 'project', parent: 'area:a', path: '/x/p', importance: 0.9, attentionRank: 0.9 }),
    n({ id: 'project:q', level: 2, kind: 'project', parent: 'area:a', path: '/x/p/sub', importance: 0.2, attentionRank: 0.2 }),
    n({ id: 'file:/x/p/a.py', level: 3, type: 'code', parent: 'project:p', project: 'project:p', path: '/x/p/a.py', importance: 0.3 }),
    n({ id: 'file:/x/p/b.py', level: 3, type: 'code', parent: 'project:p', project: 'project:p', path: '/x/p/b.py', importance: 0.8 }),
    n({ id: 'fn:/x/p/a.py#run', level: 4, kind: 'function', parent: 'file:/x/p/a.py', path: '/x/p/a.py' }),
  ],
  edges: [
    { source: 'self', target: 'area:a', kind: 'tree', weight: 1 },
    { source: 'area:a', target: 'project:p', kind: 'tree', weight: 1 },
    { source: 'project:p', target: 'file:/x/p/a.py', kind: 'tree', weight: 1 },
    { source: 'file:/x/p/a.py', target: 'fn:/x/p/a.py#run', kind: 'tree', weight: 1 },
    { source: 'file:/x/p/a.py', target: 'file:/x/p/b.py', kind: 'import', weight: 1 },
  ],
  events: [['file:/x/p/a.py', 995 * 86400, 'edit', 3], ['file:/x/p/b.py', 990 * 86400, 'read', 1], ['file:/x/p/a.py', 900 * 86400, 'commit', 2]],
}

describe('buildIndex', () => {
  const ix = buildIndex(tiny)
  it('maps parents, areas and neighbors', () => {
    expect(ix.children.get('project:p')!.map((c) => c.id)).toEqual(['file:/x/p/b.py', 'file:/x/p/a.py'])
    expect(ix.areaOf.get('fn:/x/p/a.py#run')).toBe('area:a')
    expect(ix.areaOf.has('self')).toBe(false)
    expect([...ix.neighbors.get('file:/x/p/a.py')!].sort()).toEqual(['file:/x/p/b.py', 'fn:/x/p/a.py#run', 'project:p'])
  })
  it('rolls activity and first-seen up to ancestors', () => {
    expect(ix.activity7d.get('file:/x/p/a.py')).toBe(3)
    expect(ix.activity7d.get('project:p')).toBe(3)
    expect(ix.activity7d.get('self')).toBe(3)
    expect(ix.firstSeen.get('project:p')).toBe(900 * 86400)
  })
  it('resolves live paths to files, then to the deepest project', () => {
    expect(resolvePath(ix, '/x/p/a.py')).toBe('file:/x/p/a.py')
    expect(resolvePath(ix, '/x/p/sub/new.md')).toBe('project:q')
    expect(resolvePath(ix, '/x/p/new.md')).toBe('project:p')
    expect(resolvePath(ix, '/elsewhere')).toBeNull()
  })
})
```

**File:** `viewer/tests/settings.test.ts`
```ts
import { describe, expect, it } from 'vitest'
import { DEFAULTS, loadSettings, mergeSettings, saveSettings } from '../src/settings'

const memory = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) } }

describe('settings', () => {
  it('falls back to defaults on bad input', () => {
    expect(mergeSettings(null)).toEqual(DEFAULTS)
    expect(mergeSettings({ filters: { code: 'yes' }, forces: { repel: 'x' } })).toEqual(DEFAULTS)
  })
  it('keeps valid stored values and drops unknown keys', () => {
    const s = mergeSettings({ display: { bloom: 2.5, nope: 1 }, groups: [{ query: 'arv', color: '#fff' }, { bad: 1 }], areaColors: { 'area:a': '#123456' } })
    expect(s.display.bloom).toBe(2.5)
    expect((s.display as unknown as Record<string, unknown>).nope).toBeUndefined()
    expect(s.groups).toEqual([{ query: 'arv', color: '#fff' }])
    expect(s.areaColors).toEqual({ 'area:a': '#123456' })
  })
  it('round-trips through storage and survives broken JSON', () => {
    const st = memory()
    const s = mergeSettings({ forces: { cohesion: 1.4 } })
    saveSettings(s, st)
    expect(loadSettings(st).forces.cohesion).toBe(1.4)
    st.setItem('neurotree.settings.v1', '{broken')
    expect(loadSettings(st)).toEqual(DEFAULTS)
    expect(loadSettings(null)).toEqual(DEFAULTS)
  })
})
```

- [ ] **Step 3: Run** `cd viewer && npm install && npm test` -> FAIL (cannot resolve `../src/data`)

- [ ] **Step 4: Implement**

**File:** `viewer/src/types.ts`
```ts
export interface Signals { attention: number | null; history: number | null; structure: number | null; runtime: number | null }

export interface GNode {
  id: string
  kind: string
  level: number
  parent: string | null
  label: string
  path: string | null
  project: string | null
  lod: number
  color: string | null
  type: string | null
  importance: number
  attentionRank: number
  signals: Signals
  noise: number
  evidence: string[]
  lastActivity: number | null
}

export interface GEdge { source: string; target: string; kind: string; weight: number }

/** [node id, unix seconds, kind, weight] */
export type GEvent = [string, number, string, number]

export interface GraphData {
  meta: { generated_at: number; counts: Record<string, number>; status: Record<string, unknown>; weights?: Record<string, number> }
  nodes: GNode[]
  edges: GEdge[]
  events: GEvent[]
}
```

**File:** `viewer/src/data.ts`
```ts
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
```

**File:** `viewer/src/settings.ts`
```ts
export interface Filters { code: boolean; docs: boolean; functions: boolean; facts: boolean; noise: boolean; orphans: boolean; maxFiles: number }
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
  filters: { code: true, docs: true, functions: true, facts: true, noise: false, orphans: true, maxFiles: 1500 },
  display: { arrows: false, labelFade: 0.55, nodeSize: 1, linkThickness: 1, bloom: 0.9, showImports: false },
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
```

- [ ] **Step 5: Run** `cd viewer && npm run fixture && npm test` -> data and settings tests pass
- [ ] **Step 6: Commit** `git add viewer && git commit -m "Viewer scaffold: types, data index, settings, fixtures"`

---

### Task 2: Layout

**Files:** Create `viewer/src/layout.ts`, `viewer/tests/layout.test.ts`

**Interfaces:**
- Produces: `type Vec = [number, number, number]`; `LEVEL_BASE`; `fibonacciSphere(n) -> Vec[]`; `coneDirections(axis, n, halfAngle) -> Vec[]`; `targetRadius(level, attentionRank, spread=0.8) -> number`; `computeTargets(index, forces) -> Map<id, Vec>` (self at origin; areas on a sphere; each parent's children in a cone around the parent's direction; radius by level and attention).

- [ ] **Step 1: Write the failing test**

**File:** `viewer/tests/layout.test.ts`
```ts
import { describe, expect, it } from 'vitest'
import { buildIndex } from '../src/data'
import { coneDirections, computeTargets, fibonacciSphere, targetRadius, type Vec } from '../src/layout'
import { DEFAULTS } from '../src/settings'
import { tiny } from './data.test'

const len = (v: Vec) => Math.hypot(...v)
const dot = (a: Vec, b: Vec) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

describe('layout', () => {
  it('spreads unit directions over the sphere', () => {
    const dirs = fibonacciSphere(12)
    expect(dirs).toHaveLength(12)
    for (const d of dirs) expect(len(d)).toBeCloseTo(1, 6)
    let minAngle = Math.PI
    for (let i = 0; i < dirs.length; i++) for (let j = i + 1; j < dirs.length; j++) minAngle = Math.min(minAngle, Math.acos(Math.min(1, dot(dirs[i], dirs[j]))))
    expect(minAngle).toBeGreaterThan(0.5)
  })
  it('keeps cone directions inside the cone', () => {
    const axis: Vec = [0, 0.6, 0.8]
    const dirs = coneDirections(axis, 25, 0.4)
    for (const d of dirs) { expect(len(d)).toBeCloseTo(1, 6); expect(dot(d, axis)).toBeGreaterThanOrEqual(Math.cos(0.4) - 1e-9) }
    expect(coneDirections(axis, 1, 0.4)[0].map((x) => +x.toFixed(6))).toEqual(axis)
  })
  it('puts hot nodes closer to the core within a level', () => {
    expect(targetRadius(0, 1)).toBe(0)
    expect(targetRadius(2, 1)).toBeLessThan(targetRadius(2, 0))
    expect(targetRadius(3, 1)).toBeGreaterThan(targetRadius(2, 0) * 0.9)
  })
  it('computes targets for every node', () => {
    const ix = buildIndex(tiny)
    const t = computeTargets(ix, DEFAULTS.forces)
    expect(t.get('self')).toEqual([0, 0, 0])
    expect(t.size).toBe(tiny.nodes.length)
    const p = t.get('project:p')!, q = t.get('project:q')!, a = t.get('area:a')!
    expect(len(p)).toBeLessThan(len(q))
    expect(dot(p, a) / (len(p) * len(a))).toBeGreaterThan(Math.cos(1.2))
  })
})
```

- [ ] **Step 2: Run** `cd viewer && npx vitest run tests/layout.test.ts` -> FAIL (cannot resolve `../src/layout`)

- [ ] **Step 3: Implement**

**File:** `viewer/src/layout.ts`
```ts
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
```

- [ ] **Step 4: Run** `cd viewer && npx vitest run tests/layout.test.ts` -> 4 passed
- [ ] **Step 5: Commit** `git add viewer && git commit -m "Viewer layout: attention core, area sphere, branch cones"`

---

### Task 3: Visibility (level of detail, filters, search, local graph)

**Files:** Create `viewer/src/visibility.ts`, `viewer/tests/visibility.test.ts`

**Interfaces:**
- Consumes: `GraphIndex`, `Filters`.
- Produces: `ViewState { expanded: Set<string>; selected: string | null; local: { id: string; depth: number } | null; query: string }`; `visibleIds(data, index, state, filters) -> Set<string>`; `matches(node, query) -> boolean`.

- [ ] **Step 1: Write the failing test**

**File:** `viewer/tests/visibility.test.ts`
```ts
import { describe, expect, it } from 'vitest'
import { buildIndex } from '../src/data'
import { DEFAULTS } from '../src/settings'
import { visibleIds, type ViewState } from '../src/visibility'
import { tiny } from './data.test'

const ix = buildIndex(tiny)
const base: ViewState = { expanded: new Set(), selected: null, local: null, query: '' }
const ids = (s: Set<string>) => [...s].sort()

describe('visibleIds', () => {
  it('shows levels 0-2 and lod-0 files, details only for the selected file', () => {
    const v = visibleIds(tiny, ix, base, DEFAULTS.filters)
    expect(v.has('fn:/x/p/a.py#run')).toBe(false)
    expect(v.has('file:/x/p/a.py') && v.has('project:q') && v.has('self')).toBe(true)
    expect(visibleIds(tiny, ix, { ...base, selected: 'file:/x/p/a.py' }, DEFAULTS.filters).has('fn:/x/p/a.py#run')).toBe(true)
  })
  it('applies type filters and keeps ancestors for search hits', () => {
    const noCode = visibleIds(tiny, ix, base, { ...DEFAULTS.filters, code: false })
    expect(noCode.has('file:/x/p/a.py')).toBe(false)
    const hit = visibleIds(tiny, ix, { ...base, query: 'b.py' }, DEFAULTS.filters)
    expect(ids(hit)).toEqual(['area:a', 'file:/x/p/b.py', 'project:p', 'self'])
  })
  it('caps visible files at the most important ones', () => {
    const v = visibleIds(tiny, ix, base, { ...DEFAULTS.filters, maxFiles: 1 })
    expect(v.has('file:/x/p/b.py')).toBe(true)
    expect(v.has('file:/x/p/a.py')).toBe(false)
    const expanded = visibleIds(tiny, ix, { ...base, expanded: new Set(['project:p']) }, { ...DEFAULTS.filters, maxFiles: 1 })
    expect(expanded.has('file:/x/p/a.py')).toBe(true)
  })
  it('local graph is a BFS over every edge kind', () => {
    const local = visibleIds(tiny, ix, { ...base, local: { id: 'file:/x/p/a.py', depth: 1 } }, DEFAULTS.filters)
    expect(ids(local)).toEqual(['file:/x/p/a.py', 'file:/x/p/b.py', 'fn:/x/p/a.py#run', 'project:p'])
  })
})
```

- [ ] **Step 2: Run** `cd viewer && npx vitest run tests/visibility.test.ts` -> FAIL

- [ ] **Step 3: Implement**

**File:** `viewer/src/visibility.ts`
```ts
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
```

- [ ] **Step 4: Run** `cd viewer && npx vitest run tests/visibility.test.ts` -> 3 passed
- [ ] **Step 5: Commit** `git add viewer && git commit -m "Viewer visibility: level of detail, filters, search, local graph"`

---

### Task 4: Firing and replay

**Files:** Create `viewer/src/firing.ts`, `viewer/tests/firing.test.ts`

**Interfaces:**
- Produces: `chainToCore(index, id) -> string[]` (node, parent, …, self); `pickBurst(events, cap) -> GEvent[]` (heaviest first when over cap); `intensity(node?, weight=1) -> number` in (0, 1]; `class Replay(events, seconds, windowDays=120)` with `playing`, `cursor`, `start`, `end`, `step(dtMs, cap=40) -> GEvent[]`, `seek(fraction)`, `progress()`, `setSeconds(s)`.

- [ ] **Step 1: Write the failing test**

**File:** `viewer/tests/firing.test.ts`
```ts
import { describe, expect, it } from 'vitest'
import { buildIndex } from '../src/data'
import { chainToCore, intensity, isIdle, pickBurst, Replay } from '../src/firing'
import type { GEvent } from '../src/types'
import { tiny } from './data.test'

const DAY = 86400

describe('firing', () => {
  it('walks from a node to the core', () => {
    expect(chainToCore(buildIndex(tiny), 'fn:/x/p/a.py#run')).toEqual(['fn:/x/p/a.py#run', 'file:/x/p/a.py', 'project:p', 'area:a', 'self'])
  })
  it('caps bursts by weight and scales intensity by importance', () => {
    const evs: GEvent[] = [['a', 1, 'read', 1], ['b', 2, 'edit', 3], ['c', 3, 'mention', 0.5]]
    expect(pickBurst(evs, 2).map((e) => e[0])).toEqual(['b', 'a'])
    const ix = buildIndex(tiny)
    expect(intensity(ix.byId.get('file:/x/p/b.py'), 3)).toBeGreaterThan(intensity(ix.byId.get('file:/x/p/a.py'), 3))
    expect(intensity(undefined)).toBeGreaterThan(0)
  })
  it('goes idle after 20 s without interaction unless a replay is playing', () => {
    expect(isIdle(25_000, 0, false)).toBe(true)
    expect(isIdle(15_000, 0, false)).toBe(false)
    expect(isIdle(25_000, 0, true)).toBe(false)
  })
  it('replays events in order over the chosen duration', () => {
    const evs: GEvent[] = Array.from({ length: 10 }, (_, i) => [`n${i}`, 1000 * DAY + i * DAY, 'edit', 1] as GEvent)
    const r = new Replay(evs, 10)
    expect(r.start).toBe(1000 * DAY)
    expect(r.step(1000)).toEqual([])  // not playing yet
    r.playing = true
    const first = r.step(5000)
    expect(first.map((e) => e[0])).toEqual(['n0', 'n1', 'n2', 'n3', 'n4'])  // half of 9 days = 4.5 days
    expect(r.progress()).toBeCloseTo(0.5)
    expect(r.step(5000).map((e) => e[0])).toEqual(['n5', 'n6', 'n7', 'n8', 'n9'])
    expect(r.playing).toBe(false)
    r.seek(0.25)
    r.playing = true
    expect(r.step(1000)[0]?.[0]).toBe('n3')  // 2.25 days + 0.9 days passes n3
  })
})
```

- [ ] **Step 2: Run** `cd viewer && npx vitest run tests/firing.test.ts` -> FAIL

- [ ] **Step 3: Implement**

**File:** `viewer/src/firing.ts`
```ts
import type { GraphIndex } from './data'
import type { GEvent, GNode } from './types'

const DAY = 86400

export function chainToCore(index: GraphIndex, id: string): string[] {
  const out: string[] = []
  let cur = index.byId.get(id)
  for (let guard = 0; cur && guard < 8; guard++) {
    out.push(cur.id)
    cur = cur.parent ? index.byId.get(cur.parent) : undefined
  }
  return out
}

/** Rendering pauses after idleMs without interaction, unless a replay is playing (the app runs all day). */
export function isIdle(now: number, lastActive: number, replayPlaying: boolean, idleMs = 20_000): boolean {
  return !replayPlaying && now - lastActive > idleMs
}

export function pickBurst(events: GEvent[], cap: number): GEvent[] {
  return events.length <= cap ? events : [...events].sort((a, b) => b[3] - a[3]).slice(0, cap)
}

/** How hard a node fires: importance first, the event's weight second. */
export function intensity(n: GNode | undefined, weight = 1): number {
  const imp = Math.min(1, 0.25 + 0.75 * (n?.importance ?? 0))
  return imp * Math.min(1, 0.55 + weight / 6)
}

function firstAfter(events: GEvent[], ts: number, inclusive: boolean): number {
  let lo = 0, hi = events.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (inclusive ? events[mid][1] < ts : events[mid][1] <= ts) lo = mid + 1
    else hi = mid
  }
  return lo
}

export class Replay {
  playing = false
  readonly start: number
  readonly end: number
  cursor: number
  private idx: number
  private durationMs: number

  constructor(private events: GEvent[], seconds: number, windowDays = 120) {
    const last = events.length ? events[events.length - 1][1] : 0
    this.end = last
    this.start = events.length ? Math.max(events[0][1], last - windowDays * DAY) : 0
    this.cursor = this.start
    this.idx = firstAfter(events, this.start, true)
    this.durationMs = Math.max(1, seconds) * 1000
  }

  setSeconds(seconds: number): void {
    this.durationMs = Math.max(1, seconds) * 1000
  }

  progress(): number {
    return this.end > this.start ? (this.cursor - this.start) / (this.end - this.start) : 1
  }

  seek(fraction: number): void {
    const f = Math.max(0, Math.min(1, fraction))
    this.cursor = this.start + f * (this.end - this.start)
    this.idx = firstAfter(this.events, this.cursor, true)
  }

  step(dtMs: number, cap = 40): GEvent[] {
    if (!this.playing || this.end <= this.start) return []
    this.cursor = Math.min(this.end, this.cursor + ((this.end - this.start) * dtMs) / this.durationMs)
    const out: GEvent[] = []
    while (this.idx < this.events.length && this.events[this.idx][1] <= this.cursor) out.push(this.events[this.idx++])
    if (this.cursor >= this.end) this.playing = false
    return pickBurst(out, cap)
  }
}
```

- [ ] **Step 4: Run** `cd viewer && npm test` -> all viewer tests pass
- [ ] **Step 5: Commit** `git add viewer && git commit -m "Viewer firing: chain to core, bursts, replay"`

---

### Task 5: Bridge, scene, UI, wiring

**Files:** Create `viewer/index.html`, `viewer/src/shims.d.ts`, `viewer/src/bridge.ts`, `viewer/src/scene.ts`, `viewer/src/ui.ts`, `viewer/src/main.ts`, `viewer/src/style.css`

**Interfaces:**
- Consumes: Tasks 1–4; Tauri contract from Global Constraints.
- Produces: the running app. `Scene(el, index, settings, handlers)` with `setData(visible, edges, targets)`, `applySettings(s)`, `setHighlight(ids|null)`, `setSelected(id|null)`, `setReplayCutoff(ts|null)`, `fire(id, strength, color?)`, `tick(now, dt)`, `flyTo(id)`, `pan(dx, dy)`, `baseColor(node)`. `UI(root, index, data, settings, handlers, tauri)` with `showNode(node|null, ctx)`, `setStatus(text)`, `setMode(mode)`, `updateReplay(progress, ts, playing)`, `focusSearch()`, `toast(msg)`, `areaName(id)`.

- [ ] **Step 1: Implement** (visual code; verified by typecheck, build and the browser check in Step 3)

**File:** `viewer/index.html`
```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Neurotree</title>
    <link rel="icon" href="data:," />
    <link rel="stylesheet" href="/src/style.css" />
  </head>
  <body>
    <div id="graph"></div>
    <div id="ui"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

**File:** `viewer/src/shims.d.ts`
```ts
declare module 'd3-force-3d' {
  type Accessor = (node: any) => number
  interface PositionForce { (alpha: number): void; strength(s: number | Accessor): PositionForce }
  export function forceX(x?: number | Accessor): PositionForce
  export function forceY(y?: number | Accessor): PositionForce
  export function forceZ(z?: number | Accessor): PositionForce
}
```

**File:** `viewer/src/bridge.ts`
```ts
/** Talks to the Tauri shell when running inside it; otherwise reads /graph.json from the Vite dev server. */
export interface IndexStatus { running: boolean; finished_at: number | null; error: string | null }
type Unlisten = () => void

export const isTauri = (): boolean => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
const core = () => import('@tauri-apps/api/core')
const events = () => import('@tauri-apps/api/event')

export async function loadGraphText(search = ''): Promise<string> {
  if (isTauri()) return (await core()).invoke<string>('load_graph')
  const fixture = new URLSearchParams(search).get('fixture')
  const res = await fetch(fixture ? `/graph.json?fixture=${encodeURIComponent(fixture)}` : '/graph.json')
  if (!res.ok) throw new Error(`graph.json: HTTP ${res.status}`)
  return res.text()
}

export async function openPath(path: string): Promise<void> {
  if (isTauri()) await (await core()).invoke('open_path', { path })
}

export async function revealPath(path: string): Promise<void> {
  if (isTauri()) await (await core()).invoke('reveal_path', { path })
}

export async function reindex(): Promise<boolean> {
  return isTauri() ? (await core()).invoke<boolean>('reindex') : false
}

export async function onFsChange(cb: (paths: string[], source: string) => void): Promise<Unlisten> {
  if (!isTauri()) return () => {}
  return (await events()).listen<{ paths: string[]; source: string }>('fs-change', (e) => cb(e.payload.paths, e.payload.source))
}

export async function onIndexStatus(cb: (s: IndexStatus) => void): Promise<Unlisten> {
  if (!isTauri()) return () => {}
  return (await events()).listen<IndexStatus>('index-status', (e) => cb(e.payload))
}
```

**File:** `viewer/src/scene.ts`
```ts
import ForceGraph3D from '3d-force-graph'
import { forceX, forceY, forceZ } from 'd3-force-3d'
import * as THREE from 'three'
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js'
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js'
import SpriteText from 'three-spritetext'
import type { GraphIndex } from './data'
import { chainToCore } from './firing'
import type { Vec } from './layout'
import type { Settings } from './settings'
import type { GEdge, GNode } from './types'
import { matches } from './visibility'

export interface SNode extends GNode { x?: number; y?: number; z?: number }
export interface SLink { key: string; source: string | SNode; target: string | SNode; kind: string; weight: number; burst?: number; burstColor?: string }
interface Look { group: THREE.Group; mesh: THREE.Mesh; mat: THREE.MeshBasicMaterial; label?: SpriteText; base: number; color: THREE.Color; flash: number }
export interface SceneHandlers { onHover(id: string | null): void; onClick(id: string): void; onDoubleClick(id: string): void; onBackground(): void }

const GEO = new THREE.SphereGeometry(1, 18, 14)
const WHITE = new THREE.Color('#ffffff')
const LEVEL_SIZE = [9, 5.5, 3.2, 1.5, 0.8]
const ARC: Record<string, [number, number, number, number]> = {
  co_attention: [245, 165, 36, 0.55], shared_code: [91, 141, 239, 0.5], mention: [62, 207, 142, 0.5], import: [167, 139, 250, 0.4],
}
const HOP_MS = 260
const endId = (e: string | SNode) => (typeof e === 'string' ? e : e.id)
const rgba = ([r, g, b]: number[], a: number) => `rgba(${r},${g},${b},${a})`
const hash = (s: string) => { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return (h % 1000) / 159 }

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)
}

export class Scene {
  readonly graph: any
  private looks = new Map<string, Look>()
  private nodeObjs = new Map<string, SNode>()
  private linkByKey = new Map<string, SLink>()
  private targets = new Map<string, Vec>()
  private visible = new Set<string>()
  private highlight: Set<string> | null = null
  private selected: string | null = null
  private cutoff: number | null = null
  private queue: Array<{ at: number; key: string; width: number; color: string }> = []
  private lastClick = { id: '', at: 0 }
  private bloom: UnrealBloomPass

  constructor(private el: HTMLElement, private index: GraphIndex, private settings: Settings, private h: SceneHandlers) {
    this.graph = new (ForceGraph3D as any)(el, { controlType: 'orbit' })
      .backgroundColor('#05060a')
      .showNavInfo(false)
      .nodeId('id')
      .nodeLabel((n: SNode) => `<div class="tip"><b>${escapeHtml(n.label)}</b><span>${Math.round(n.importance * 100)} %</span></div>`)
      .nodeThreeObject((n: SNode) => this.objectFor(n))
      .linkColor((l: SLink) => this.linkColor(l))
      .linkWidth((l: SLink) => this.linkWidth(l))
      .linkCurvature((l: SLink) => (l.kind === 'tree' ? 0 : 0.28))
      .linkVisibility((l: SLink) => this.linkVisible(l))
      .linkDirectionalArrowLength((l: SLink) => (this.settings.display.arrows && l.kind !== 'tree' ? 3.5 : 0))
      .linkDirectionalArrowRelPos(1)
      .linkDirectionalParticleSpeed(0.018)
      .linkDirectionalParticleWidth((l: SLink) => l.burst ?? 1.5)
      .linkDirectionalParticleColor((l: SLink) => l.burstColor ?? '#ffd27a')
      .linkDirectionalParticleResolution(8)
      .onNodeHover((n: SNode | null) => { el.style.cursor = n ? 'pointer' : 'default'; this.h.onHover(n ? n.id : null) })
      .onNodeClick((n: SNode) => this.click(n.id))
      .onBackgroundClick(() => this.h.onBackground())
      .cooldownTicks(160)
      .cooldownTime(6000)
    this.graph.d3Force('center', null)
    this.graph.scene().background = new THREE.Color('#05060a')  // color-managed; the clear color got sRGB-encoded twice
    this.graph.renderer().setPixelRatio(Math.min(window.devicePixelRatio, 1.5))
    this.bloom = new UnrealBloomPass(new THREE.Vector2(el.clientWidth / 2, el.clientHeight / 2), settings.display.bloom, 0.4, 0.18)
    this.graph.postProcessingComposer().addPass(this.bloom)
    this.graph.postProcessingComposer().addPass(new OutputPass())  // linear -> sRGB, else the background washes out
    this.graph.cameraPosition({ x: 0, y: 260, z: 1500 }, { x: 0, y: 0, z: 0 })
    window.addEventListener('resize', () => this.graph.width(el.clientWidth).height(el.clientHeight))
  }

  baseColor(n: GNode): string {
    if (n.level === 0) return '#fff1cf'
    for (const g of this.settings.groups) if (g.query && matches(n, g.query)) return g.color
    const area = n.level === 1 ? n.id : this.index.areaOf.get(n.id)
    if (area === 'area:unsorted' && !this.settings.areaColors[area]) return '#4a5163'
    return (area && (this.settings.areaColors[area] ?? this.index.byId.get(area)?.color)) || '#8b93a7'
  }

  private opacity(id: string): number {
    if (this.highlight && !this.highlight.has(id)) return 0.07
    if (this.cutoff !== null && id !== 'self') {
      const first = this.index.firstSeen.get(id)
      if (first === undefined || first > this.cutoff) return 0.06
    }
    return 0.95
  }

  private objectFor(n: SNode): THREE.Object3D {
    const mat = new THREE.MeshBasicMaterial({ transparent: true })
    const mesh = new THREE.Mesh(GEO, mat)
    const group = new THREE.Group()
    group.add(mesh)
    const look: Look = { group, mesh, mat, base: 1, color: new THREE.Color(), flash: this.looks.get(n.id)?.flash ?? 0 }
    this.looks.set(n.id, look)
    this.style(n, look)
    return group
  }

  private style(n: SNode, look: Look): void {
    const d = this.settings.display
    const imp = n.importance ?? 0
    look.base = d.nodeSize * LEVEL_SIZE[Math.min(n.level, 4)] * (n.level === 0 ? 1 : 0.55 + 1.45 * Math.pow(imp, 0.7))
    look.color.set(this.baseColor(n)).multiplyScalar(n.level === 0 ? 1 : 0.3 + 0.7 * imp)
    look.mat.color.copy(look.color)
    look.mat.opacity = this.opacity(n.id)
    look.mesh.scale.setScalar(look.base)
    const wantLabel = n.level <= 1 || (n.level === 2 && imp >= d.labelFade) || n.id === this.selected
    if (wantLabel && !look.label) {
      const t = new SpriteText(n.label, n.level === 0 ? 24 : n.level === 1 ? 16 : 9, '#e9e6dc')
      t.fontFace = 'Inter, system-ui, -apple-system, sans-serif'
      t.material.depthWrite = false
      t.position.y = look.base + (n.level <= 1 ? 16 : 8)
      look.group.add(t)
      look.label = t
    }
    if (look.label) {
      look.label.visible = wantLabel
      ;(look.label.material as THREE.SpriteMaterial).opacity = Math.min(1, look.mat.opacity + 0.05)
    }
  }

  private linkColor(l: SLink): string {
    const a = endId(l.source), b = endId(l.target)
    const dim = !!this.highlight && !(this.highlight.has(a) && this.highlight.has(b))
    if (l.kind === 'tree') return rgba([140, 150, 185], dim ? 0.03 : 0.22)
    const c = ARC[l.kind] ?? [200, 200, 200, 0.3]
    return rgba(c, dim ? 0.03 : c[3])
  }

  private linkWidth(l: SLink): number {
    return l.kind === 'tree' ? 0 : this.settings.display.linkThickness * Math.min(2.2, 0.35 + 0.35 * Math.sqrt(l.weight))
  }

  private linkVisible(l: SLink): boolean {
    if (l.kind !== 'import' || this.settings.display.showImports) return true
    return !!this.highlight && this.highlight.has(endId(l.source)) && this.highlight.has(endId(l.target))
  }

  setData(visible: Set<string>, edges: GEdge[], targets: Map<string, Vec>): void {
    this.visible = visible
    this.targets = targets
    const nodes: SNode[] = []
    for (const id of visible) {
      const base = this.index.byId.get(id)
      if (!base) continue
      let n = this.nodeObjs.get(id)
      if (!n) {
        n = { ...base }
        const t = targets.get(id)
        if (t) { n.x = t[0]; n.y = t[1]; n.z = t[2] }
        this.nodeObjs.set(id, n)
      }
      nodes.push(n)
    }
    const links: SLink[] = []
    this.linkByKey.clear()
    for (const e of edges) {
      if (!visible.has(e.source) || !visible.has(e.target)) continue
      const tree = e.kind === 'tree'
      const source = tree ? e.target : e.source  // tree links point to the core, so particles travel inward
      const target = tree ? e.source : e.target
      const l: SLink = { key: `${e.kind}:${source}>${target}`, source, target, kind: e.kind, weight: e.weight }
      links.push(l)
      this.linkByKey.set(l.key, l)
    }
    this.graph.graphData({ nodes, links })
    this.applyForces()
  }

  private applyForces(): void {
    const f = this.settings.forces
    const at = (i: number) => (n: SNode) => this.targets.get(n.id)?.[i] ?? 0
    this.graph.d3Force('x', forceX(at(0)).strength(f.corePull))
    this.graph.d3Force('y', forceY(at(1)).strength(f.corePull))
    this.graph.d3Force('z', forceZ(at(2)).strength(f.corePull))
    this.graph.d3Force('charge')?.strength(-f.repel).distanceMax(240)
    const gap = (l: SLink) => {
      const a = this.targets.get(endId(l.source)), b = this.targets.get(endId(l.target))
      return a && b ? Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) : 120
    }
    this.graph.d3Force('link')?.strength((l: SLink) => (l.kind === 'tree' ? f.linkStrength : 0)).distance(gap)
    this.graph.d3ReheatSimulation()
  }

  private restyle(): void {
    for (const [id, look] of this.looks) {
      const n = this.nodeObjs.get(id)
      if (n) this.style(n, look)
    }
    this.graph.linkColor(this.graph.linkColor()).linkVisibility(this.graph.linkVisibility())
  }

  applySettings(s: Settings): void {
    this.settings = s
    this.bloom.strength = s.display.bloom
    this.graph.linkWidth(this.graph.linkWidth()).linkDirectionalArrowLength(this.graph.linkDirectionalArrowLength())
    this.restyle()
    this.applyForces()
  }

  setHighlight(ids: Set<string> | null): void { this.highlight = ids; this.restyle() }
  setSelected(id: string | null): void { this.selected = id; this.restyle() }

  setReplayCutoff(ts: number | null): void {
    this.cutoff = ts
    for (const [id, look] of this.looks) look.mat.opacity = this.opacity(id)
  }

  fire(id: string, strength: number, color?: string): void {
    const chain = chainToCore(this.index, id).filter((x) => this.visible.has(x))
    if (!chain.length) return
    const now = performance.now()
    const tint = new THREE.Color(color ?? this.baseColor(this.index.byId.get(chain[0])!)).lerp(WHITE, 0.45)
    const burstColor = `#${tint.getHexString()}`
    chain.forEach((nid, i) => {
      const look = this.looks.get(nid)
      if (look) look.flash = Math.max(look.flash, strength * Math.pow(0.78, i))
    })
    for (let i = 0; i < chain.length - 1 && this.queue.length < 4000; i++) {
      this.queue.push({ at: now + i * HOP_MS, key: `tree:${chain[i]}>${chain[i + 1]}`, width: 0.6 + 3.4 * strength, color: burstColor })
    }
  }

  tick(now: number, dt: number): void {
    if (this.queue.length) {
      const later: typeof this.queue = []
      for (const q of this.queue) {
        if (q.at > now) { later.push(q); continue }
        const l = this.linkByKey.get(q.key)
        if (l) { l.burst = q.width; l.burstColor = q.color; this.graph.emitParticle(l) }
      }
      this.queue = later
    }
    const decay = Math.exp(-dt / 520)
    const t = now / 1000
    for (const [id, look] of this.looks) {
      const act = this.index.activity7d.get(id) ?? 0
      const breath = act > 0 ? 0.07 * Math.sin(t * (0.8 + Math.min(2.4, Math.log1p(act))) + hash(id)) : 0
      if (look.flash < 0.002 && breath === 0) continue
      look.mesh.scale.setScalar(look.base * (1 + breath + 0.9 * look.flash))
      if (look.flash >= 0.002) {
        look.mat.color.copy(look.color).lerp(WHITE, Math.min(0.75, look.flash))
        look.flash *= decay
      } else if (look.flash > 0) {
        look.flash = 0
        look.mat.color.copy(look.color)
      }
    }
  }

  flyTo(id: string): void {
    const n = this.nodeObjs.get(id)
    if (!n) return
    const p = new THREE.Vector3(n.x ?? 0, n.y ?? 0, n.z ?? 0)
    const dist = 170 + (this.looks.get(id)?.base ?? 4) * 22
    const cam = p.length() < 1 ? new THREE.Vector3(0, 120, dist * 3) : p.clone().multiplyScalar(1 + dist / p.length())
    this.graph.cameraPosition({ x: cam.x, y: cam.y, z: cam.z }, { x: p.x, y: p.y, z: p.z }, 1100)
  }

  pan(dx: number, dy: number): void {
    const cam = this.graph.camera() as THREE.Camera
    const controls = this.graph.controls() as { target: THREE.Vector3; update(): void }
    const right = new THREE.Vector3().setFromMatrixColumn(cam.matrix, 0).multiplyScalar(dx)
    const up = new THREE.Vector3().setFromMatrixColumn(cam.matrix, 1).multiplyScalar(dy)
    const delta = right.add(up)
    cam.position.add(delta)
    controls.target.add(delta)
    controls.update()
  }

  private click(id: string): void {
    const now = performance.now()
    if (this.lastClick.id === id && now - this.lastClick.at < 380) {
      this.lastClick = { id: '', at: 0 }
      this.h.onDoubleClick(id)
    } else {
      this.lastClick = { id, at: now }
      this.h.onClick(id)
    }
  }
}
```

**File:** `viewer/src/ui.ts`
```ts
import type { GraphIndex } from './data'
import type { Settings } from './settings'
import type { GNode, GraphData } from './types'

export type Mode = 'live' | 'replay'
export interface UIHandlers {
  onSearch(q: string): void
  onSearchSubmit(q: string): void
  onMode(m: Mode): void
  onPlay(): void
  onSeek(f: number): void
  onSettings(s: Settings): void
  onReindex(): void
  onOpen(id: string): void
  onReveal(id: string): void
  onExpand(id: string): void
  onLocal(id: string | null, depth: number): void
  onClose(): void
}

type Attrs = Record<string, string | number | boolean | ((e: Event) => void)>
function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...kids: Array<Node | string | null>): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag)
  for (const [k, v] of Object.entries(attrs)) {
    if (typeof v === 'function') e.addEventListener(k, v)
    else if (k === 'text') e.textContent = String(v)
    else if (typeof v === 'boolean') { if (v) e.setAttribute(k, '') }
    else e.setAttribute(k, String(v))
  }
  for (const k of kids) if (k !== null) e.append(k)
  return e
}

const SIGNALS: Array<[keyof GNode['signals'], string, string]> = [
  ['attention', 'Attention', '45 %'], ['history', 'History', '25 %'], ['structure', 'Structure', '20 %'], ['runtime', 'Runtime', '10 %'],
]
type Control = { section: string; key: string; label: string; kind: 'toggle' } | { section: string; key: string; label: string; kind: 'range'; min: number; max: number; step: number }
const CONTROLS: Control[] = [
  { section: 'Filters', key: 'filters.code', label: 'Code', kind: 'toggle' },
  { section: 'Filters', key: 'filters.docs', label: 'Documents', kind: 'toggle' },
  { section: 'Filters', key: 'filters.functions', label: 'Functions', kind: 'toggle' },
  { section: 'Filters', key: 'filters.facts', label: 'Memory facts', kind: 'toggle' },
  { section: 'Filters', key: 'filters.noise', label: 'Lockfiles and generated', kind: 'toggle' },
  { section: 'Filters', key: 'filters.orphans', label: 'Untouched files', kind: 'toggle' },
  { section: 'Filters', key: 'filters.maxFiles', label: 'Files shown', kind: 'range', min: 200, max: 6000, step: 100 },
  { section: 'Display', key: 'display.arrows', label: 'Arrows', kind: 'toggle' },
  { section: 'Display', key: 'display.showImports', label: 'All import links', kind: 'toggle' },
  { section: 'Display', key: 'display.labelFade', label: 'Label threshold', kind: 'range', min: 0, max: 1, step: 0.05 },
  { section: 'Display', key: 'display.nodeSize', label: 'Node size', kind: 'range', min: 0.4, max: 3, step: 0.1 },
  { section: 'Display', key: 'display.linkThickness', label: 'Link thickness', kind: 'range', min: 0.2, max: 3, step: 0.1 },
  { section: 'Display', key: 'display.bloom', label: 'Glow', kind: 'range', min: 0, max: 3, step: 0.05 },
  { section: 'Forces', key: 'forces.corePull', label: 'Core pull', kind: 'range', min: 0, max: 0.3, step: 0.01 },
  { section: 'Forces', key: 'forces.repel', label: 'Repel', kind: 'range', min: 0, max: 80, step: 1 },
  { section: 'Forces', key: 'forces.linkStrength', label: 'Link force', kind: 'range', min: 0, max: 1, step: 0.05 },
  { section: 'Forces', key: 'forces.linkDistance', label: 'Link distance', kind: 'range', min: 0.5, max: 2, step: 0.05 },
  { section: 'Forces', key: 'forces.cohesion', label: 'Branch width', kind: 'range', min: 0.4, max: 2, step: 0.05 },
]

const getPath = (o: unknown, path: string): unknown => path.split('.').reduce<any>((a, k) => a?.[k], o)
function setPath(o: any, path: string, v: unknown): void {
  const keys = path.split('.')
  keys.slice(0, -1).reduce((a, k) => a[k], o)[keys[keys.length - 1]] = v
}

function ago(ts: number | null): string {
  if (!ts) return 'no recorded activity'
  const d = (Date.now() / 1000 - ts) / 86400
  if (d < 1 / 24) return 'just now'
  if (d < 1) return `${Math.round(d * 24)} h ago`
  if (d < 60) return `${Math.round(d)} days ago`
  return new Date(ts * 1000).toLocaleDateString()
}

export class UI {
  private panel: HTMLElement
  private drawer: HTMLElement
  private status: HTMLElement
  private search: HTMLInputElement
  private modeBtns: Record<Mode, HTMLButtonElement>
  private replayBar: HTMLElement
  private scrub: HTMLInputElement
  private when: HTMLElement
  private playBtn: HTMLButtonElement
  private toastEl: HTMLElement

  constructor(root: HTMLElement, private index: GraphIndex, private data: GraphData, private settings: Settings, private h: UIHandlers, private tauri: boolean) {
    this.search = el('input', { type: 'search', placeholder: 'Search files, projects, notes  ( / )', spellcheck: false,
      input: () => this.h.onSearch(this.search.value),
      keydown: (e) => { if ((e as KeyboardEvent).key === 'Enter') this.h.onSearchSubmit(this.search.value) } })
    this.modeBtns = {
      live: el('button', { class: 'seg', text: 'Live', click: () => this.h.onMode('live') }),
      replay: el('button', { class: 'seg', text: 'Replay', click: () => this.h.onMode('replay') }),
    }
    this.status = el('div', { class: 'status' })
    const top = el('header', { class: 'top' },
      el('div', { class: 'brand' }, el('span', { class: 'dot' }), el('span', { text: 'Neurotree' })),
      el('div', { class: 'searchwrap' }, this.search),
      el('div', { class: 'right' },
        el('div', { class: 'segs' }, this.modeBtns.live, this.modeBtns.replay),
        tauri ? el('button', { class: 'ghost', text: 'Reindex', click: () => this.h.onReindex() }) : null,
        el('button', { class: 'ghost', text: 'Settings', click: () => this.drawer.classList.toggle('open') })),
    )
    this.playBtn = el('button', { class: 'play', text: 'Play', click: () => this.h.onPlay() })
    this.scrub = el('input', { type: 'range', min: 0, max: 1000, value: 0, input: () => this.h.onSeek(Number(this.scrub.value) / 1000) })
    this.when = el('span', { class: 'when' })
    this.replayBar = el('footer', { class: 'replay hidden' }, this.playBtn, this.scrub, this.when)
    this.panel = el('aside', { class: 'panel hidden' })
    this.drawer = el('aside', { class: 'drawer' })
    this.toastEl = el('div', { class: 'toast' })
    root.append(top, this.status, this.panel, this.drawer, this.replayBar, this.toastEl)
    this.buildDrawer()
  }

  areaName(id: string): string {
    const area = this.index.areaOf.get(id)
    return area ? this.index.byId.get(area)?.label ?? '' : ''
  }

  private buildDrawer(): void {
    this.drawer.replaceChildren(el('h3', { text: 'Graph settings' }))
    const sections = new Map<string, HTMLElement>()
    for (const c of CONTROLS) {
      let sec = sections.get(c.section)
      if (!sec) { sec = el('section', {}, el('h4', { text: c.section })); sections.set(c.section, sec); this.drawer.append(sec) }
      const value = getPath(this.settings, c.key)
      const input = c.kind === 'toggle'
        ? el('input', { type: 'checkbox', checked: Boolean(value), change: (e) => this.set(c.key, (e.target as HTMLInputElement).checked) })
        : el('input', { type: 'range', min: c.min, max: c.max, step: c.step, value: Number(value), input: (e) => this.set(c.key, Number((e.target as HTMLInputElement).value)) })
      sec.append(el('label', { class: `row ${c.kind}` }, el('span', { text: c.label }), input))
    }
    const groups = el('section', {}, el('h4', { text: 'Groups' }))
    for (const area of this.index.children.get('self') ?? []) {
      const color = this.settings.areaColors[area.id] ?? area.color ?? '#8b93a7'
      groups.append(el('label', { class: 'row' }, el('span', { text: area.label }),
        el('input', { type: 'color', value: color, input: (e) => { this.settings.areaColors[area.id] = (e.target as HTMLInputElement).value; this.h.onSettings(this.settings) } })))
    }
    this.settings.groups.forEach((g, i) => {
      groups.append(el('div', { class: 'row group' },
        el('input', { type: 'text', value: g.query, placeholder: 'search term', change: (e) => { g.query = (e.target as HTMLInputElement).value; this.h.onSettings(this.settings) } }),
        el('input', { type: 'color', value: g.color, input: (e) => { g.color = (e.target as HTMLInputElement).value; this.h.onSettings(this.settings) } }),
        el('button', { class: 'ghost small', text: 'Remove', click: () => { this.settings.groups.splice(i, 1); this.h.onSettings(this.settings); this.buildDrawer() } })))
    })
    groups.append(el('button', { class: 'ghost small', text: 'New group', click: () => { this.settings.groups.push({ query: '', color: '#f472b6' }); this.buildDrawer() } }))
    this.drawer.append(groups)
  }

  private set(key: string, value: unknown): void {
    setPath(this.settings, key, value)
    this.h.onSettings(this.settings)
  }

  setStatus(text: string): void { this.status.textContent = text }

  setMode(m: Mode): void {
    for (const [k, b] of Object.entries(this.modeBtns)) b.classList.toggle('on', k === m)
    this.replayBar.classList.toggle('hidden', m !== 'replay')
  }

  updateReplay(progress: number, ts: number, playing: boolean): void {
    this.scrub.value = String(Math.round(progress * 1000))
    this.when.textContent = ts ? new Date(ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : ''
    this.playBtn.textContent = playing ? 'Pause' : 'Play'
  }

  focusSearch(): void { this.search.focus(); this.search.select() }

  clearSearch(): void { this.search.value = '' }

  toast(msg: string): void {
    this.toastEl.textContent = msg
    this.toastEl.classList.add('show')
    window.setTimeout(() => this.toastEl.classList.remove('show'), 2600)
  }

  showNode(n: GNode | null, ctx: { expanded: boolean; local: boolean }): void {
    if (!n) { this.panel.classList.add('hidden'); return }
    const bars = el('div', { class: 'bars' })
    for (const [k, name, w] of SIGNALS) {
      const v = n.signals[k]
      bars.append(el('div', { class: 'bar' }, el('span', { text: name }),
        el('div', { class: 'track' }, el('div', { class: 'fill', style: `width:${v === null ? 0 : Math.round(v * 100)}%` })),
        el('span', { class: 'w', text: v === null ? 'n/a' : w })))
    }
    const facts = (this.index.children.get(n.id) ?? []).filter((c) => c.kind === 'fact')
    const actions = el('div', { class: 'actions' })
    if (n.path) {
      actions.append(el('button', { text: this.tauri ? 'Open' : 'Copy path', click: () => (this.tauri ? this.h.onOpen(n.id) : navigator.clipboard?.writeText(n.path!)) }))
      if (this.tauri) actions.append(el('button', { class: 'ghost', text: 'Reveal in Finder', click: () => this.h.onReveal(n.id) }))
    }
    if (n.level === 2) actions.append(el('button', { class: 'ghost', text: ctx.expanded ? 'Collapse files' : 'Show all files', click: () => this.h.onExpand(n.id) }))
    const depth = el('select', { change: (e) => this.h.onLocal(n.id, Number((e.target as HTMLSelectElement).value)) },
      ...[1, 2, 3].map((d) => el('option', { value: d, text: `depth ${d}`, selected: d === this.settings.localDepth })))
    actions.append(ctx.local
      ? el('button', { class: 'ghost', text: 'Back to full tree', click: () => this.h.onLocal(null, 0) })
      : el('button', { class: 'ghost', text: 'Local graph', click: () => this.h.onLocal(n.id, Number(depth.value)) }), depth)
    const parts: Array<Node | null> = [
      el('button', { class: 'close', text: 'x', 'aria-label': 'Close', click: () => this.h.onClose() }),
      el('div', { class: 'kicker', text: [n.kind, this.areaName(n.id)].filter(Boolean).join(' · ') }),
      el('h2', { text: n.label }),
      n.path ? el('div', { class: 'path', text: n.path }) : null,
      el('div', { class: 'imp' }, el('b', { text: `${Math.round(n.importance * 100)} %` }), el('span', { text: ' importance' })),
      bars,
      n.evidence.length ? el('ul', { class: 'evidence' }, ...n.evidence.map((x) => el('li', { text: x }))) : null,
      el('div', { class: 'meta', text: `Last activity: ${ago(n.lastActivity)}` }),
      facts.length ? el('div', { class: 'facts' }, el('h4', { text: 'From memory' }), ...facts.map((f) => el('p', { text: f.label }))) : null,
      actions,
    ]
    this.panel.replaceChildren(...parts.filter((x): x is Node => x !== null))
    this.panel.classList.remove('hidden')
  }
}
```

**File:** `viewer/src/main.ts`
```ts
import { isTauri, loadGraphText, onFsChange, onIndexStatus, openPath, reindex, revealPath } from './bridge'
import { buildIndex, resolvePath } from './data'
import { intensity, isIdle, Replay } from './firing'
import { computeTargets } from './layout'
import { Scene } from './scene'
import { loadSettings, saveSettings, type Settings } from './settings'
import type { GraphData } from './types'
import { UI, type Mode } from './ui'
import { visibleIds, type ViewState } from './visibility'

async function boot(): Promise<void> {
  const graphEl = document.getElementById('graph')!
  const uiEl = document.getElementById('ui')!
  let data: GraphData
  try {
    data = JSON.parse(await loadGraphText(location.search)) as GraphData
  } catch (e) {
    uiEl.innerHTML = '<div class="empty"><h1>No graph yet</h1><p>Run <code>python -m neurotree all</code> in the Neurotree folder, then reload.</p></div>'
    console.error(e)
    return
  }
  if (!data.nodes.length) {
    uiEl.innerHTML = '<div class="empty"><h1>The graph is empty</h1><p>The indexer found no data. Check settings.json in the Neurotree data folder.</p></div>'
    return
  }
  const index = buildIndex(data)
  let settings: Settings = loadSettings()
  const state: ViewState = { expanded: new Set(), selected: null, local: null, query: '' }
  let mode: Mode = 'live'
  const replay = new Replay(data.events, settings.replaySeconds)
  const tauri = isTauri()

  const refresh = () => {
    const visible = visibleIds(data, index, state, settings.filters)
    scene.setData(visible, data.edges, computeTargets(index, settings.forces))
    ui.setStatus(`${visible.size.toLocaleString()} of ${data.nodes.length.toLocaleString()} nodes · indexed ${new Date(data.meta.generated_at * 1000).toLocaleString()}`)
  }
  const select = (id: string | null) => {
    state.selected = id
    scene.setSelected(id)
    const n = id ? index.byId.get(id) ?? null : null
    ui.showNode(n, { expanded: !!id && state.expanded.has(id), local: !!state.local })
    if (n && n.level === 3) refresh()  // show the file's functions and memory facts
  }
  const highlightOf = (id: string) => new Set([id, ...(index.neighbors.get(id) ?? [])])

  const scene = new Scene(graphEl, index, settings, {
    onHover: (id) => scene.setHighlight(id ? highlightOf(id) : state.selected ? highlightOf(state.selected) : null),
    onClick: (id) => { select(id); scene.setHighlight(highlightOf(id)) },
    onDoubleClick: (id) => scene.flyTo(id),
    onBackground: () => { select(null); scene.setHighlight(null) },
  })
  const ui = new UI(uiEl, index, data, settings, {
    onSearch: (q) => { state.query = q.length >= 2 ? q : ''; refresh() },
    onSearchSubmit: (q) => {
      const hit = data.nodes.filter((n) => n.label.toLowerCase().includes(q.toLowerCase())).sort((a, b) => b.importance - a.importance)[0]
      if (hit) { select(hit.id); scene.flyTo(hit.id) }
    },
    onMode: (m) => { wake(); setMode(m) },
    onPlay: () => { if (replay.progress() >= 1) replay.seek(0); replay.playing = !replay.playing },
    onSeek: (f) => { replay.seek(f); scene.setReplayCutoff(replay.cursor) },
    onSettings: (s) => { settings = s; saveSettings(s); replay.setSeconds(s.replaySeconds); scene.applySettings(s); refresh() },
    onReindex: async () => ui.toast((await reindex()) ? 'Reindexing… the tree updates when it finishes' : 'An index run is already going'),
    onOpen: (id) => { const p = index.byId.get(id)?.path; if (p) void openPath(p) },
    onReveal: (id) => { const p = index.byId.get(id)?.path; if (p) void revealPath(p) },
    onExpand: (id) => { if (state.expanded.has(id)) state.expanded.delete(id); else state.expanded.add(id); refresh(); select(id) },
    onLocal: (id, depth) => { state.local = id ? { id, depth } : null; if (id) { settings.localDepth = depth; saveSettings(settings) } refresh(); if (id) select(id) },
    onClose: () => { select(null); scene.setHighlight(null) },
  }, tauri)

  function setMode(m: Mode): void {
    mode = m
    ui.setMode(m)
    if (m === 'replay') { replay.seek(0); replay.playing = true; scene.setReplayCutoff(replay.cursor) }
    else { replay.playing = false; scene.setReplayCutoff(null) }
  }

  refresh()
  setMode('live')
  if (import.meta.env.DEV) Object.assign(window, { __neurotree: { scene, data, index } })  // dev-only debugging handle

  // Live: real changes from the shell; in a plain browser, a sample of real recent events keeps the tree alive.
  const recent = data.events.slice(-400)
  let nextSample = performance.now() + 900
  let lastActive = performance.now()
  let paused = false
  const wake = () => {
    lastActive = performance.now()
    if (paused) { paused = false; scene.graph.resumeAnimation() }
  }
  for (const ev of ['pointermove', 'pointerdown', 'wheel', 'keydown']) window.addEventListener(ev, wake, { passive: true })
  if (tauri) {
    await onFsChange((paths, source) => {
      for (const p of paths.slice(0, 40)) {
        const id = resolvePath(index, p)
        if (id) { wake(); scene.fire(id, Math.max(0.45, intensity(index.byId.get(id), 3)), source === 'claude' ? '#fff1c9' : undefined) }
      }
    })
    await onIndexStatus((s) => {
      if (s.running) ui.toast('Indexing…')
      else if (s.error) ui.toast(`Index failed: ${s.error}`)
      else { ui.toast('Index updated, reloading'); window.setTimeout(() => location.reload(), 800) }
    })
  }

  window.addEventListener('keydown', (e) => {
    const typing = (e.target as HTMLElement).tagName === 'INPUT'
    if (e.key === '/' && !typing) { e.preventDefault(); ui.focusSearch(); return }
    if (e.key === 'Escape') { ui.clearSearch(); state.query = ''; state.local = null; select(null); scene.setHighlight(null); refresh(); return }
    if (typing) return
    const step = e.shiftKey ? 90 : 30
    const moves: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }
    if (moves[e.key]) { e.preventDefault(); scene.pan(...moves[e.key]) }
  })

  const fpsEl = Object.assign(document.createElement('div'), { className: 'fps' })
  fpsEl.hidden = !new URLSearchParams(location.search).has('perf')
  document.body.append(fpsEl)
  window.addEventListener('keydown', (e) => {  // F toggles the frame-rate overlay (no URL bar in the desktop app)
    if ((e.key === 'f' || e.key === 'F') && (e.target as HTMLElement).tagName !== 'INPUT') fpsEl.hidden = !fpsEl.hidden
  })
  let frames = 0, fpsAt = performance.now(), last = performance.now()
  const loop = (now: number) => {
    const dt = Math.min(100, now - last)
    last = now
    if (mode === 'replay') {
      for (const ev of replay.step(dt)) scene.fire(ev[0], intensity(index.byId.get(ev[0]), ev[3]))
      if (replay.playing) scene.setReplayCutoff(replay.cursor)
      ui.updateReplay(replay.progress(), replay.cursor, replay.playing)
    } else if (!tauri && !paused && recent.length && now >= nextSample) {
      const ev = recent[Math.floor(Math.random() * recent.length)]
      scene.fire(ev[0], intensity(index.byId.get(ev[0]), ev[3]))
      nextSample = now + 700 + Math.random() * 900
    }
    if (!paused && isIdle(now, lastActive, replay.playing)) { paused = true; scene.graph.pauseAnimation() }
    if (!paused) scene.tick(now, dt)
    if (!fpsEl.hidden && ++frames && now - fpsAt >= 1000) { fpsEl.textContent = `${frames} fps · ${scene.graph.graphData().nodes.length} nodes`; frames = 0; fpsAt = now }
    requestAnimationFrame(loop)
  }
  requestAnimationFrame(loop)
}

void boot()
```

**File:** `viewer/src/style.css`
```css
:root {
  --bg: #05060a; --panel: rgba(14, 16, 24, 0.78); --line: rgba(255, 255, 255, 0.08); --text: #e9e6dc;
  --muted: #8d93a5; --accent: #f5a524; --radius: 12px;
  color-scheme: dark;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text); overflow: hidden;
  font: 13px/1.45 Inter, system-ui, -apple-system, sans-serif; }
#graph { position: fixed; inset: 0; }
#ui { position: fixed; inset: 0; pointer-events: none; }
#ui > * { pointer-events: auto; }
button, input, select { font: inherit; color: inherit; }
button { background: var(--accent); color: #1b1203; border: 0; border-radius: 8px; padding: 6px 12px; cursor: pointer; font-weight: 600; }
button.ghost { background: rgba(255, 255, 255, 0.06); color: var(--text); font-weight: 500; border: 1px solid var(--line); }
button.small { padding: 3px 8px; font-size: 12px; }
.top { position: absolute; top: 14px; left: 16px; right: 16px; display: flex; gap: 14px; align-items: center; }
.brand { display: flex; align-items: center; gap: 8px; font-weight: 700; letter-spacing: 0.02em; font-size: 15px; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: #fff1cf; box-shadow: 0 0 14px 3px rgba(255, 210, 122, 0.8); }
.searchwrap { flex: 1; display: flex; justify-content: center; }
.searchwrap input { width: min(520px, 100%); background: var(--panel); border: 1px solid var(--line); border-radius: 999px; padding: 8px 16px; outline: none; backdrop-filter: blur(10px); }
.searchwrap input:focus { border-color: rgba(245, 165, 36, 0.5); }
.right { display: flex; gap: 8px; align-items: center; }
.segs { display: flex; background: var(--panel); border: 1px solid var(--line); border-radius: 999px; padding: 3px; }
.seg { background: transparent; color: var(--muted); border-radius: 999px; padding: 4px 12px; font-weight: 600; }
.seg.on { background: var(--accent); color: #1b1203; }
.status { position: absolute; left: 18px; bottom: 16px; color: var(--muted); font-size: 12px; pointer-events: none; }
.panel, .drawer { position: absolute; top: 64px; right: 16px; width: 340px; max-height: calc(100% - 140px); overflow: auto;
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 16px 18px; backdrop-filter: blur(14px); }
.panel h2 { margin: 2px 0 6px; font-size: 18px; line-height: 1.25; word-break: break-word; }
.kicker { text-transform: uppercase; letter-spacing: 0.08em; font-size: 11px; color: var(--accent); }
.path { font: 11px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--muted); word-break: break-all; margin-bottom: 10px; }
.imp b { font-size: 22px; }
.imp span { color: var(--muted); }
.bars { margin: 10px 0; display: grid; gap: 6px; }
.bar { display: grid; grid-template-columns: 76px 1fr 38px; gap: 8px; align-items: center; font-size: 12px; }
.track { height: 6px; background: rgba(255, 255, 255, 0.07); border-radius: 3px; overflow: hidden; }
.fill { height: 100%; background: linear-gradient(90deg, #f5a524, #fff1cf); }
.w { color: var(--muted); text-align: right; }
.evidence { margin: 8px 0; padding-left: 18px; }
.evidence li { margin: 2px 0; }
.meta { color: var(--muted); font-size: 12px; margin: 6px 0 10px; }
.facts h4, .drawer h4 { margin: 12px 0 6px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }
.facts p { margin: 4px 0; padding: 6px 8px; background: rgba(255, 255, 255, 0.04); border-radius: 6px; }
.actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.actions select { background: rgba(255, 255, 255, 0.06); border: 1px solid var(--line); border-radius: 8px; padding: 4px; }
.close { position: absolute; top: 10px; right: 10px; background: transparent; color: var(--muted); padding: 2px 8px; }
.drawer { transform: translateX(380px); opacity: 0; transition: transform 0.25s ease, opacity 0.25s ease; pointer-events: none; }
.drawer.open { transform: none; opacity: 1; pointer-events: auto; z-index: 2; }
.drawer h3 { margin: 0 0 4px; }
.row { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin: 6px 0; }
.row input[type='range'] { width: 150px; accent-color: var(--accent); }
.row.group input[type='text'] { flex: 1; background: rgba(255, 255, 255, 0.05); border: 1px solid var(--line); border-radius: 6px; padding: 4px 6px; }
.replay { position: absolute; left: 50%; bottom: 18px; transform: translateX(-50%); width: min(640px, calc(100% - 32px));
  display: flex; gap: 12px; align-items: center; background: var(--panel); border: 1px solid var(--line); border-radius: 999px; padding: 6px 14px; backdrop-filter: blur(12px); }
.replay input { flex: 1; accent-color: var(--accent); }
.when { min-width: 96px; text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
.hidden { display: none !important; }
.toast { position: absolute; top: 64px; left: 50%; transform: translate(-50%, -8px); opacity: 0; transition: all 0.2s ease;
  background: var(--panel); border: 1px solid var(--line); border-radius: 999px; padding: 6px 14px; pointer-events: none; }
.toast.show { opacity: 1; transform: translate(-50%, 0); }
.tip { background: rgba(10, 12, 18, 0.9); border: 1px solid var(--line); border-radius: 8px; padding: 4px 8px; display: flex; gap: 8px; }
.tip span { color: var(--accent); }
.empty { position: absolute; inset: 0; display: grid; place-content: center; text-align: center; color: var(--muted); }
.empty h1 { color: var(--text); }
.fps { position: fixed; left: 18px; bottom: 36px; font: 12px ui-monospace, monospace; color: #3ecf8e; }
@media (max-width: 720px) {
  .searchwrap { display: none; }
  .panel, .drawer { left: 12px; right: 12px; width: auto; }
}
```

- [ ] **Step 2: Typecheck, test, build** — `cd viewer && npm run typecheck && npm test && npm run build` -> no type errors, all tests pass, `dist/` built.
- [ ] **Step 3: Browser check** — `npm run dev`, open `http://127.0.0.1:5173/` (real graph) and `/?fixture=graph`: tree renders with glow; hover highlights neighbors; click opens the panel with breakdown and evidence; double-click flies; search + Enter; Local graph; Replay plays and dims unseen nodes; settings change live; no console errors.
- [ ] **Step 4: Commit** `git add viewer && git commit -m "Viewer scene, UI and wiring"`

---

### Task 6: Performance gate

- [ ] **Step 1:** `cd viewer && npm run fixture` (writes `fixtures/big.json`, 20,000 files; gitignored).
- [ ] **Step 2:** Open `http://127.0.0.1:5173/?fixture=big&perf=1` and set Filters so all 20k files are visible (they are `lod 0` up to 6,000 in the fixture; expand projects to exceed it). Record fps after the layout settles in Chrome; repeat inside the Tauri window (WebKit) once plan 3 is merged. Gate: >= 30 fps with 20,000 nodes in Tauri, else switch the shell to Electron.
- [ ] **Step 3:** Write the measured numbers into this section and commit.
