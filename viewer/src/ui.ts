import { UNSORTED, type AreasFile } from './areas'
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
  onAreaRename(slug: string, name: string): void
  onAreaColor(slug: string, color: string): void
  onAreaMerge(from: string, into: string): void
  onMoveProject(project: string, slug: string): void
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
  private areas: AreasFile | null = null

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

  setIndex(index: GraphIndex): void { this.index = index; this.buildDrawer() }
  setAreas(areas: AreasFile | null): void { this.areas = areas; this.buildDrawer() }

  private areaList(): Array<{ slug: string; node: GNode }> {
    return (this.index.children.get('self') ?? [])
      .map((node) => ({ slug: node.id.replace(/^area:/, ''), node }))
      .sort((a, b) => Number(a.slug === UNSORTED) - Number(b.slug === UNSORTED) || b.node.importance - a.node.importance)
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
    const areaSec = el('section', {}, el('h4', { text: 'Areas' }))
    if (!this.areas) areaSec.append(el('p', { class: 'note', text: 'Areas can be edited once the indexer has written areas.json.' }))
    else {
      areaSec.append(el('p', { class: 'note', text: this.areas.reviewed ? 'Reviewed: every reindex keeps this placement.' : 'Proposal from your attention. Any edit marks it reviewed.' }))
      const list = this.areaList()
      for (const { slug, node } of list) {
        const count = (this.index.children.get(node.id) ?? []).length
        const merge = slug === UNSORTED ? null : el('select', { title: 'Merge into', change: (e) => { const v = (e.target as HTMLSelectElement).value; if (v) this.h.onAreaMerge(slug, v) } },
          el('option', { value: '', text: 'Merge…' }), ...list.filter((o) => o.slug !== slug).map((o) => el('option', { value: o.slug, text: o.node.label })))
        areaSec.append(el('div', { class: 'row area' },
          el('input', { type: 'text', value: node.label, title: `${count} projects`, change: (e) => this.h.onAreaRename(slug, (e.target as HTMLInputElement).value) }),
          el('span', { class: 'count', text: String(count) }),
          el('input', { type: 'color', value: node.color ?? '#8b93a7', change: (e) => this.h.onAreaColor(slug, (e.target as HTMLInputElement).value) }),
          merge))
      }
    }
    this.drawer.append(areaSec)
    const groups = el('section', {}, el('h4', { text: 'Groups' }))
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
    const move = n.level === 2 && this.areas
      ? el('label', { class: 'row move' }, el('span', { text: 'Area' }), el('select', { change: (e) => this.h.onMoveProject(n.id, (e.target as HTMLSelectElement).value) },
          ...this.areaList().map(({ slug, node }) => el('option', { value: slug, text: node.label, selected: n.parent === node.id }))))
      : null
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
      move,
      actions,
    ]
    this.panel.replaceChildren(...parts.filter((x): x is Node => x !== null))
    this.panel.classList.remove('hidden')
  }
}
