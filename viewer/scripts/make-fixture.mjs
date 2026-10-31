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
