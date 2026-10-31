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
