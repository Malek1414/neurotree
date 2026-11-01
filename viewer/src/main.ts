import { applyAreas, mergeAreas, moveProject, parseAreas, recolorArea, renameArea, type AreasFile } from './areas'
import { isTauri, loadAreasText, loadGraphText, onFsChange, onIndexStatus, openPath, reindex, revealPath, saveAreasText } from './bridge'
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
  // Reviewed areas edited since the last index run are applied before anything is drawn (not for fixtures).
  let areas: AreasFile | null = new URLSearchParams(location.search).has('fixture') ? null : parseAreas((await loadAreasText()) ?? '')
  if (areas) applyAreas(data, areas)
  let index = buildIndex(data)
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
    onAreaRename: (slug, name) => editAreas((a) => renameArea(a, slug, name)),
    onAreaColor: (slug, color) => editAreas((a) => recolorArea(a, slug, color)),
    onAreaMerge: (from, into) => editAreas((a) => mergeAreas(a, from, into)),
    onMoveProject: (project, slug) => editAreas((a) => moveProject(a, project, slug)),
  }, tauri)
  ui.setAreas(areas)

  let saveTimer = 0
  function editAreas(change: (a: AreasFile) => AreasFile): void {
    if (!areas) return
    try { areas = change(areas) } catch (e) { ui.toast((e as Error).message); return }
    applyAreas(data, areas)
    index = buildIndex(data)
    scene.setIndex(index)
    ui.setIndex(index)
    ui.setAreas(areas)
    refresh()
    if (state.selected) select(state.selected)
    window.clearTimeout(saveTimer)
    const text = JSON.stringify(areas, null, 1)
    saveTimer = window.setTimeout(() => {
      saveAreasText(text).then(() => ui.toast('Areas saved, every reindex keeps them')).catch((e) => ui.toast(`Could not save areas: ${e}`))
    }, 600)
  }

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
