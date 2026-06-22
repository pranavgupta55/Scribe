import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  forceX,
  forceY,
} from 'https://cdn.jsdelivr.net/npm/d3-force@3/+esm';
import { quadtree } from 'https://cdn.jsdelivr.net/npm/d3-quadtree@3/+esm';

// ─── DOM refs ─────────────────────────────────────────────────────────────────

const leftEl       = document.getElementById('left');
const canvas       = document.getElementById('canvas');
const ctx          = canvas.getContext('2d');
const listCol      = document.getElementById('node-list-col');
const detailCol    = document.getElementById('node-detail-col');
const detailTitle  = document.getElementById('detail-title');
const detailMeta   = document.getElementById('detail-meta');
const detailDesc   = document.getElementById('detail-desc');
const detailChips  = document.getElementById('detail-chips');
const tooltip      = document.getElementById('tooltip');
const legendEl     = document.getElementById('legend');
const emptyState   = document.getElementById('empty-state');

// ─── State ────────────────────────────────────────────────────────────────────

let W = 0, H = 0;

let tx = 0, ty = 0, scale = 1;
let isPanning = false, panStartX = 0, panStartY = 0;
let dragNode = null, dragOffX = 0, dragOffY = 0;
let mousedownX = 0, mousedownY = 0, mousedownNode = null;
let hoveredNode = null;
let selectedNode = null;

let nodes = [];
let links = [];
// Per-plugin colour map. Now uniformly SOURCE_COLOR for every source — only
// kept so the legend swatch lookup still works. (Topic-node colour is derived
// from degree at draw time, not from this map.)
let pluginColors = {};

// ─── Data load ──────────────────────────────────────────────────────────────

// Semantic color scheme:
//   - Source nodes (videos / plugin === null) → SOURCE_COLOR (accent red).
//   - Topic nodes → warm/cool gradient where degree drives a coupled shift in
//     hue (blue → red), saturation (low → high), and value (light → dark). So
//     leaves read as pale, cool, recessive; hubs read as deep, saturated, hot
//     — visually echoing source nodes, the most "hub-like" thing in the graph.
// The legacy per-plugin palette was meaningless beyond 16 plugins (we have 200+)
// — colour now carries semantics, not provenance.
const SOURCE_COLOR = '#d9453a';
const SOURCE_RING  = '#ece6dc';   // light outline ring around every source node
const TOPIC_LO     = '#a8c5e0';   // leaf — slightly light, less saturated blue
const TOPIC_HI     = '#b22a22';   // hub  — slightly dark, more saturated red
// Kept for back-compat with the legend (renderLegend uses STANDALONE_HUB).
const STANDALONE_HUB = SOURCE_COLOR;

async function loadGraph() {
  // Default = v2 (newest + largest, additive across rebuilds). Opt back to v1
  // with ?graph=v1 in the URL for A/B comparison.
  const params = new URLSearchParams(window.location.search);
  const version = params.get('graph') === 'v1' ? 'v1' : 'v2';
  const candidates = version === 'v2'
    ? ['./graph_v2.json', './graph.json']
    : ['./graph.json'];
  for (const url of candidates) {
    try {
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        data._version = version;
        return data;
      }
    } catch {
      /* try next */
    }
  }
  return null;
}

function buildPluginColors(rawNodes) {
  const plugins = [...new Set(rawNodes.map(n => n.plugin).filter(Boolean))].sort();
  const map = {};
  plugins.forEach(p => { map[p] = SOURCE_COLOR; });
  return map;
}

function renderLegend() {
  const listEl = document.getElementById('legend-list');
  if (listEl) listEl.querySelectorAll('.legend-row').forEach(el => el.remove());
  const entries = Object.entries(pluginColors);
  if (entries.length === 0) {
    legendEl.style.display = 'none';
    return;
  }
  legendEl.style.display = 'flex';
  for (const [name, color] of entries) {
    const row = document.createElement('div');
    row.className = 'legend-row';
    // Trim .txt extension for display. Swatch uses the source-hub colour so it
    // matches the actual purple source node in the graph (not the topic colour).
    const label = name.replace(/\.txt$/, '');
    row.innerHTML = `<span class="legend-swatch" style="background:${STANDALONE_HUB}"></span>${label}`;
    if (listEl) listEl.appendChild(row);
    else legendEl.appendChild(row);
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function computeDegrees() {
  nodes.forEach(n => { n.degree = 0; });
  links.forEach(l => {
    const s = typeof l.source === 'object' ? l.source : nodes.find(n => n.id === l.source);
    const t = typeof l.target === 'object' ? l.target : nodes.find(n => n.id === l.target);
    if (s) s.degree = (s.degree || 0) + 1;
    if (t) t.degree = (t.degree || 0) + 1;
  });
}

// Topic colour by degree: log-scaled HSL blend from TOPIC_LO (leaf, pale cool
// blue) → TOPIC_HI (hub, deep warm red), travelling the LONG way around the
// wheel (blue → cyan → green → yellow → orange → red) so mid-degree topics
// pass through warm earth tones rather than purple. Saturates around degree
// ~16 (log2 = 4) — beyond that, all hub nodes look equally "hot". A strong
// bias (p = 0.4) pushes t hard away from 0.5 toward either endpoint, so
// only the narrowest middle band of degrees actually renders as green/yellow;
// almost every topic reads as clearly cool or clearly warm.
function topicColor(n) {
  const raw = Math.min(1, Math.log2(Math.max(1, n.degree || 0)) / 4);
  const t = biasToExtremes(raw, 0.4);
  return mixHsl(TOPIC_LO, TOPIC_HI, t, 'long');
}

function nodeColor(n) {
  if (!n.plugin) return SOURCE_COLOR;   // video source — always accent red
  return topicColor(n);                 // topic — blue (leaf) → red (hub) ramp
}

// Sources get a flat radius boost (+50 %) on top of the steeper degree curve,
// so they read as distinct landmarks even without their outline ring. The
// degree term uses an x^0.65 power curve (between √ and linear) so hubs get
// notably bigger than the previous √ scaling without leaves shrinking to dots.
function nodeRadius(d) {
  const base = cfg.nodeSize + Math.pow(d.degree || 0, 0.65) * 2.4;
  return d.plugin ? base : base * 1.5 + 2;
}

function screenToWorld(sx, sy) {
  // sx/sy come in as e.clientX/Y (viewport-relative); convert to canvas-local
  // first so the fixed #topbar (48px) and any other offset don't skew the hit
  // test. Earlier code assumed canvas was at (0,0) of the viewport.
  const rect = canvas.getBoundingClientRect();
  return [(sx - rect.left - W / 2 - tx) / scale, (sy - rect.top - H / 2 - ty) / scale];
}

// True iff this node's level is currently toggled visible. v1 graphs (no
// `level` field) are always visible — preserves back-compat with `?graph=v1`.
function isVisible(n) {
  if (!n) return false;
  if (n.level == null) return true;
  return cfg.visibleLevels.has(n.level);
}

function hitTest(sx, sy) {
  const [wx, wy] = screenToWorld(sx, sy);
  for (let i = nodes.length - 1; i >= 0; i--) {
    const n = nodes[i];
    if (n.x == null) continue;
    if (!isVisible(n)) continue;
    const r = nodeRadius(n) + 4;
    if ((n.x - wx) ** 2 + (n.y - wy) ** 2 <= r ** 2) return n;
  }
  return null;
}

function weightT(w) {
  return Math.max(0, Math.min(1, ((w ?? 0) - 0.25) / 0.85));
}

// ─── Canvas sizing ────────────────────────────────────────────────────────────

function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  W = leftEl.offsetWidth;
  H = leftEl.offsetHeight;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width = W + 'px';
  canvas.style.height = H + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}

new ResizeObserver(resizeCanvas).observe(leftEl);
requestAnimationFrame(resizeCanvas);

// ─── Controls ────────────────────────────────────────────────────────────────

// All sliders use a normalized 0–100 HTML range with default = 50 (visually
// centred). Each maps linearly into a [lo, hi] band picked so the user's
// preferred working values land at the midpoint. The displayed "0.50" is
// just slider.value / 100. Edge min's mapped value is divided by 100 again
// to become a 0.0–1.0 weight threshold.
const sliderRanges = {
  repulsion: [400, 1400],   // default 50 → 900
  linkDist:  [50,  250],    // default 50 → 150
  momentum:  [2,   12],     // default 50 → 7
  nodeSize:  [2,   10],     // default 50 → 6
  edgeMin:   [0,   101],    // piecewise (see updateCfgFromSlider): 0→0, 50→0.90, 100→1.01 (drops every edge)
  gravity:   [20,  180],    // default 50 → 100
  edgeRepel: [0,   240],    // default 50 → 120 (push edges apart from each other)
};

let cfg = {
  repulsion: 0, linkDist: 0, momentum: 0, nodeSize: 0, edgeMin: 0, gravity: 0, edgeRepel: 0,
  // ADR-0001 v2 viewer adaptation: per-tier visibility. Default reduces the
  // 4865-node v2 hairball to ~1089 nodes (L0 + L1 + source). Hidden levels
  // skip rendering, force-tick physics, and the sidebar TOPICS list.
  visibleLevels: new Set(['L0', 'L1']),
};

function updateCfgFromSlider(k, sliderVal) {
  const [lo, hi] = sliderRanges[k];
  const actual = lo + (sliderVal / 100) * (hi - lo);
  if (k === 'edgeMin') {
    // Piecewise so the center (50) keeps the old 0.90 threshold while the
    // endpoints reach 0 (every edge kept) and 1.01 (every edge dropped).
    cfg[k] = sliderVal <= 50
      ? (sliderVal / 50) * 0.90
      : 0.90 + ((sliderVal - 50) / 50) * 0.11;
  } else {
    cfg[k] = actual;
  }
  const valEl = document.getElementById(k + '-val');
  if (valEl) valEl.textContent = (sliderVal / 100).toFixed(2);
}

// Initialise cfg from each slider's current value (defaults to 50)
Object.keys(sliderRanges).forEach(k => {
  updateCfgFromSlider(k, +document.getElementById(k).value);
});

// Wire input listeners — every slider triggers a sim rebuild
Object.keys(sliderRanges).forEach(k => {
  document.getElementById(k).addEventListener('input', e => {
    updateCfgFromSlider(k, +e.target.value);
    rebuildSim();
  });
});

// ─── Level filter + source search wiring ─────────────────────────────────────
// Checkboxes live in #right > #node-list-col > #list-header (kept outside the
// re-rendered .node-item list so the input keeps focus across keystrokes).
// Declared here (NOT down in the buildNodeList block) so the early wiring
// can resolve the input element without hitting a temporal-dead-zone error.
let sourceSearchInput = document.getElementById('source-search');
if (sourceSearchInput) {
  sourceSearchInput.addEventListener('input', () => {
    buildNodeList();
  });
}
document.querySelectorAll('.level-filter input[type="checkbox"]').forEach(cb => {
  cb.addEventListener('change', () => {
    const lvl = cb.dataset.level;
    if (cb.checked) cfg.visibleLevels.add(lvl);
    else            cfg.visibleLevels.delete(lvl);
    rebuildSim();
    draw();
  });
});

// Set of link references that are the highest-weight edge for at least one
// of their endpoints. These are NEVER filtered out, so no node ever becomes
// completely orphaned by the edge-weight threshold.
let keystoneLinks = new Set();

function computeKeystoneLinks() {
  keystoneLinks = new Set();
  const best = new Map();   // nodeId → {weight, link}
  for (const l of links) {
    const sId = typeof l.source === 'object' && l.source !== null ? l.source.id : l.source;
    const tId = typeof l.target === 'object' && l.target !== null ? l.target.id : l.target;
    const w = l.weight ?? 0;
    const sBest = best.get(sId);
    if (!sBest || w > sBest.weight) best.set(sId, { weight: w, link: l });
    const tBest = best.get(tId);
    if (!tBest || w > tBest.weight) best.set(tId, { weight: w, link: l });
  }
  for (const { link } of best.values()) keystoneLinks.add(link);
}

function activeLinks() {
  return links.filter(l => {
    if ((l.weight ?? 0) < cfg.edgeMin && !keystoneLinks.has(l)) return false;
    // Drop edges whose endpoint level is hidden — keeps the link force from
    // tugging visible nodes toward an invisible (and now stationary) sibling.
    const s = typeof l.source === 'object' ? l.source : nodes.find(n => n.id === l.source);
    const t = typeof l.target === 'object' ? l.target : nodes.find(n => n.id === l.target);
    if (!isVisible(s) || !isVisible(t)) return false;
    return true;
  });
}

function activeNodes() {
  return nodes.filter(isVisible);
}

// ─── Simulation ──────────────────────────────────────────────────────────────

// Edge–edge repulsion: treat each visible link's midpoint as a charged particle
// indexed in a quadtree, and push pairs of nearby midpoints apart (skipping
// pairs that share a node — those are already coupled by the link force). The
// push is applied equally to both endpoints of each edge so the geometry tilts
// rather than the endpoints flying apart. Strength is per-tick * alpha; the
// effect fades naturally as the simulation cools.
function forceEdgeRepel() {
  let activeLinksList = [];
  let strength = 60;
  const maxDist = 90;    // ignore pairs farther apart than this (in world units)

  function force(alpha) {
    if (!activeLinksList.length || strength <= 0) return;
    const mids = [];
    for (const l of activeLinksList) {
      if (!l.source || !l.target || l.source.x == null) continue;
      mids.push({
        l,
        x: (l.source.x + l.target.x) / 2,
        y: (l.source.y + l.target.y) / 2,
      });
    }
    if (mids.length < 2) return;
    const tree = quadtree().x(d => d.x).y(d => d.y).addAll(mids);
    const k = strength * alpha * 0.5;   // halved because each pair visited twice
    const maxDist2 = maxDist * maxDist;

    for (const mid of mids) {
      tree.visit((node, x0, y0, x1, y1) => {
        if (!node.length) {
          do {
            const other = node.data;
            if (!other || other === mid) continue;
            const A = mid.l, B = other.l;
            // Edges sharing a node are pulled together by the link force —
            // pushing them apart here would just oscillate.
            if (A.source === B.source || A.source === B.target ||
                A.target === B.source || A.target === B.target) continue;
            const dx = mid.x - other.x;
            const dy = mid.y - other.y;
            const d2 = dx * dx + dy * dy;
            if (d2 < 1e-3 || d2 > maxDist2) continue;
            const f = k / d2;
            const fx = dx * f, fy = dy * f;
            A.source.vx += fx; A.source.vy += fy;
            A.target.vx += fx; A.target.vy += fy;
          } while ((node = node.next));
          return;
        }
        // Prune far-away subtrees.
        return (x0 > mid.x + maxDist || x1 < mid.x - maxDist ||
                y0 > mid.y + maxDist || y1 < mid.y - maxDist);
      });
    }
  }

  force.initialize = () => {};
  force.links = (l) => { activeLinksList = l; return force; };
  force.strength = (s) => { if (s == null) return strength; strength = s; return force; };
  return force;
}

let sim;

function rebuildSim() {
  if (sim) sim.stop();
  const alphaDecay = 0.0228 * (21 - cfg.momentum) / 20;
  // Link distance & strength: previously varied 0.6×–1.6× by edge weight, which
  // was the dominant reason edges looked uneven. Tightened to 0.85×–1.15× so
  // every visible edge sits in a similar length band; heavier edges still pull
  // a bit harder so the cluster structure is preserved.
  // Pass only visible nodes to the sim so hidden tiers don't participate in
  // physics. Hidden nodes keep their `x/y` (so toggling back on resumes from
  // their last position rather than springing in from (0,0)).
  sim = forceSimulation(activeNodes())
    .force('link', forceLink(activeLinks())
      .id(d => d.id)
      .distance(l => cfg.linkDist * (1.15 - 0.30 * weightT(l.weight)))
      .strength(l => 0.45 + 0.45 * weightT(l.weight)))
    .force('charge', forceManyBody().strength(-cfg.repulsion))
    .force('center', forceCenter(0, 0).strength(0.05))
    // Gravity: per-axis pull toward (0,0) so disconnected components don't
    // drift off into space. Slider 0–100 → strength 0.0–0.10.
    .force('gravityX', forceX(0).strength(cfg.gravity / 1000))
    .force('gravityY', forceY(0).strength(cfg.gravity / 1000))
    .force('collide', forceCollide().radius(d => nodeRadius(d) + 4))
    .force('edgeRepel', forceEdgeRepel().links(activeLinks()).strength(cfg.edgeRepel))
    .alphaDecay(alphaDecay)
    .velocityDecay(0.4)
    .on('tick', draw);
  buildNodeList();
}

// ─── Right panel ──────────────────────────────────────────────────────────────

function buildNodeList() {
  const scrollTop = listCol.scrollTop;

  // Remove only the .node-item children + any prior search section headers.
  // The #list-header (which now contains the level-filter and search input)
  // is preserved across re-renders so the input never loses focus.
  listCol.querySelectorAll('.node-item, .topic-section').forEach(el => el.remove());

  const query = (sourceSearchInput && sourceSearchInput.value || '').trim();
  if (query) {
    // Source-search mode: filter SOURCE nodes by tf-style match on label
    // (title) + description. Concepts / frameworks / claims / etc. are not
    // included in search results — search is scoped to the source library.
    const tokens = tokenize(query);
    const sources = nodes.filter(n => isVisible(n) && (n.level === 'source' || (n.plugin == null && /\.txt$/i.test(n.id))));
    const scored = sources.map(n => ({ n, ...scoreSource(n, tokens) }))
      .filter(s => s.score > 0);
    const title = scored.filter(s => s.titleHits > 0).sort((a, b) => b.score - a.score);
    const desc  = scored.filter(s => s.titleHits === 0).sort((a, b) => b.score - a.score);

    const appendSection = (label, list) => {
      if (!list.length) return;
      const head = document.createElement('div');
      head.className = 'topic-section';
      head.textContent = label;
      listCol.appendChild(head);
      list.forEach(({ n }) => appendNodeItem(n));
    };
    appendSection('Title matches', title);
    appendSection('All other matches', desc);
  } else {
    const sorted = [...nodes]
      .filter(isVisible)
      .sort((a, b) => (a.label || a.id).localeCompare(b.label || b.id));
    sorted.forEach(appendNodeItem);
  }

  listCol.scrollTop = scrollTop;
}

function appendNodeItem(n) {
  const item = document.createElement('div');
  item.className = 'node-item' + (n === selectedNode ? ' active' : '');
  item.innerHTML = `<span class="node-item-name">${escapeHtml(n.label || n.id)}</span><span class="node-item-degree">${n.degree ?? 0}</span>`;
  item.addEventListener('click', () => selectNode(n, true));
  n._listEl = item;
  listCol.appendChild(item);
}

// Lowercase tokens; drop punctuation + the existing source-stopword set.
function tokenize(s) {
  return (s || '')
    .toLowerCase()
    .split(/[\s\p{P}_]+/u)
    .filter(Boolean)
    .filter(t => !SOURCE_STOPWORDS.has(t));
}

// Tf-style scorer: each token hit on the source's label adds 4 (title weight),
// each token hit on description adds 1. titleHits separates the two sections.
function scoreSource(node, queryTokens) {
  if (!queryTokens.length) return { titleHits: 0, descHits: 0, score: 0 };
  const titleTokens = tokenize(node.label || node.id);
  const descTokens  = tokenize(node.description || '');
  let titleHits = 0, descHits = 0;
  for (const q of queryTokens) {
    if (titleTokens.some(t => t.includes(q))) titleHits++;
    if (descTokens.some(t => t.includes(q)))  descHits++;
  }
  return { titleHits, descHits, score: titleHits * 4 + descHits };
}

// `sourceSearchInput` is declared above (next to its event-listener wiring)
// so this section just consumes it.

function neighborsOf(n) {
  const out = [];
  links.forEach(l => {
    const s = typeof l.source === 'object' ? l.source : nodes.find(x => x.id === l.source);
    const t = typeof l.target === 'object' ? l.target : nodes.find(x => x.id === l.target);
    let neighbor = null;
    if (s === n) neighbor = t;
    else if (t === n) neighbor = s;
    if (!neighbor) return;
    out.push({ node: neighbor, weight: l.weight ?? 0 });
  });
  out.sort((a, b) => b.weight - a.weight);
  return out;
}

function selectNode(n, panTo = false) {
  selectedNode = n;

  // If the sidebar was collapsed, reopen it so the user can actually see the
  // detail they just asked for. Without this, opening the detail-col while
  // collapsed widens the sidebar element while it's still translated off-
  // screen, which leaves a blank gap on the left of the (still-hidden) panel.
  const rs = document.getElementById('right');
  if (rs && rs.classList.contains('collapsed')) {
    rs.classList.remove('collapsed');
    document.body.classList.remove('right-collapsed');
    rs.style.marginRight = '';
  }

  listCol.querySelectorAll('.node-item').forEach(el => el.classList.remove('active'));
  if (n._listEl) {
    n._listEl.classList.add('active');
    n._listEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  const deg = n.degree ?? 0;
  const home = n.plugin ? n.plugin.replace(/\.txt$/, '') : 'source';

  detailCol.classList.add('open');
  detailTitle.textContent = sourceLabels[n.id] || prettifySource(n.id);
  detailMeta.textContent = `${home} · ${deg} connection${deg !== 1 ? 's' : ''}`;
  detailDesc.innerHTML = renderMarkdown(n.description || 'No description available.');
  bindNodeLinks(detailDesc);

  detailChips.innerHTML = '';
  neighborsOf(n).forEach(({ node: neighbor, weight }) => {
    const chip = document.createElement('span');
    chip.className = 'conn-chip';
    chip.textContent = neighbor.id;
    chip.title = `${neighbor.id} · weight ${weight.toFixed(2)}`;
    chip.addEventListener('click', () => selectNode(neighbor, true));
    detailChips.appendChild(chip);
  });

  if (panTo && n.x != null) animatePanTo(n);

  draw();
}

function centerView() {
  const positioned = nodes.filter(n => n.x != null);
  if (!positioned.length) return;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  positioned.forEach(n => {
    minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
    minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
  });
  const pad = 80;
  const targetScale = Math.min(
    (W - pad * 2) / (maxX - minX || 1),
    (H - pad * 2) / (maxY - minY || 1),
    2
  );
  const targetTx = -((minX + maxX) / 2) * targetScale;
  const targetTy = -((minY + maxY) / 2) * targetScale;
  const startTx = tx, startTy = ty, startScale = scale;
  const dur = 420;
  const t0 = performance.now();
  function step(now) {
    const p = Math.min((now - t0) / dur, 1);
    const ease = 1 - Math.pow(1 - p, 3);
    tx = startTx + (targetTx - startTx) * ease;
    ty = startTy + (targetTy - startTy) * ease;
    scale = startScale + (targetScale - startScale) * ease;
    draw();
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

document.getElementById('center-btn').addEventListener('click', centerView);

function animatePanTo(n) {
  const startTx = tx, startTy = ty;
  const targetTx = -n.x * scale;
  const targetTy = -n.y * scale;
  const dur = 380;
  const t0 = performance.now();
  function step(now) {
    const p = Math.min((now - t0) / dur, 1);
    const ease = 1 - Math.pow(1 - p, 3);
    tx = startTx + (targetTx - startTx) * ease;
    ty = startTy + (targetTy - startTy) * ease;
    draw();
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// ─── Rendering ───────────────────────────────────────────────────────────────

const COLORS = {
  bg: '#0d0d0d',
  text: '#ece6dc',
  textDim: '#8a857d',
};

function draw() {
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = COLORS.bg;
  ctx.fillRect(0, 0, W, H);

  ctx.save();
  ctx.translate(W / 2 + tx, H / 2 + ty);
  ctx.scale(scale, scale);

  links.forEach(l => {
    const s = l.source, t = l.target;
    if (!s || !t || s.x == null) return;
    // Hide edges below threshold UNLESS this is the keystone (max-weight)
    // edge for at least one of its endpoints
    if ((l.weight ?? 0) < cfg.edgeMin && !keystoneLinks.has(l)) return;
    if (!isVisible(s) || !isVisible(t)) return;
    const isSelEdge = selectedNode && (s === selectedNode || t === selectedNode);
    const isHoverEdge = hoveredNode && (s === hoveredNode || t === hoveredNode);
    const ti = weightT(l.weight);
    const baseAlpha = 0.12 + 0.33 * ti;
    const baseWidth = (0.7 + 1.8 * ti);

    ctx.beginPath();
    ctx.moveTo(s.x, s.y);
    ctx.lineTo(t.x, t.y);
    if (isSelEdge) {
      ctx.strokeStyle = `rgba(217,69,58,${Math.min(0.8, baseAlpha + 0.4)})`;
      ctx.lineWidth = (baseWidth + 0.8) / scale;
    } else if (isHoverEdge) {
      ctx.strokeStyle = `rgba(217,69,58,${Math.min(0.7, baseAlpha + 0.3)})`;
      ctx.lineWidth = (baseWidth + 0.5) / scale;
    } else {
      ctx.strokeStyle = `rgba(217,69,58,${baseAlpha})`;
      ctx.lineWidth = baseWidth / scale;
    }
    ctx.stroke();
  });

  nodes.forEach(n => {
    if (n.x == null) return;
    if (!isVisible(n)) return;
    const r = nodeRadius(n);
    const isHover = n === hoveredNode;
    const isDrag = n === dragNode;
    const isSelected = n === selectedNode;
    const isSource = !n.plugin;
    const base = nodeColor(n);

    if (isSelected || isHover || isDrag || isSource || n.degree > 4) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + 7, 0, Math.PI * 2);
      const grad = ctx.createRadialGradient(n.x, n.y, r * 0.4, n.x, n.y, r + 7);
      const glowCol = isSelected ? 'rgba(239,90,77,0.45)' : hexToRgba(base, isHover ? 0.4 : 0.3);
      grad.addColorStop(0, glowCol);
      grad.addColorStop(1, 'transparent');
      ctx.fillStyle = grad;
      ctx.fill();
    }

    ctx.beginPath();
    ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
    if (isSelected) {
      ctx.fillStyle = lighten(base, 0.55);
    } else if (isHover || isDrag) {
      ctx.fillStyle = lighten(base, 0.3);
    } else {
      ctx.fillStyle = base;
    }
    ctx.fill();

    // Always outline source nodes — visual landmark for "this is a video".
    // Selection ring is drawn afterwards on top so it takes precedence.
    if (isSource) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + 1.5, 0, Math.PI * 2);
      ctx.strokeStyle = SOURCE_RING;
      ctx.lineWidth = 1.5 / scale;
      ctx.stroke();
    }

    if (isSelected) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + 3.5, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(239,90,77,0.6)';
      ctx.lineWidth = 1.5 / scale;
      ctx.stroke();
    }

    const showLabel = isSelected || isHover || isDrag || scale > 0.9;
    if (showLabel) {
      ctx.font = `${(isSelected || isHover) ? 600 : 400} ${9 / scale}px -apple-system, sans-serif`;
      ctx.fillStyle = isSelected ? '#ef5a4d' : (isHover ? COLORS.text : COLORS.textDim);
      ctx.textAlign = 'center';
      ctx.fillText(n.id, n.x, n.y - r - 5 / scale);
    }
  });

  ctx.restore();
}

function hexToRgb(hex) {
  const h = hex.replace('#', '');
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
function hexToRgba(hex, a) {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r},${g},${b},${a})`;
}
function lighten(hex, amt) {
  const [r, g, b] = hexToRgb(hex);
  const f = (c) => Math.round(c + (255 - c) * amt);
  return `rgb(${f(r)},${f(g)},${f(b)})`;
}
function mixHex(a, b, t) {
  const [ar, ag, ab] = hexToRgb(a);
  const [br, bg, bb] = hexToRgb(b);
  const f = (l, h) => Math.round(l + (h - l) * t).toString(16).padStart(2, '0');
  return `#${f(ar, br)}${f(ag, bg)}${f(ab, bb)}`;
}

// HSL helpers. The topic ramp interpolates in HSL space so hue, saturation,
// and lightness all shift smoothly between the endpoints — RGB lerp between
// a desaturated blue and a saturated red would pass through a muddy mauve.
function hexToHsl(hex) {
  const [r, g, b] = hexToRgb(hex).map(c => c / 255);
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  if (max === r)      h = ((g - b) / d) + (g < b ? 6 : 0);
  else if (max === g) h = ((b - r) / d) + 2;
  else                h = ((r - g) / d) + 4;
  return [h * 60, s, l];
}

function hslToHex(h, s, l) {
  if (s === 0) {
    const v = Math.round(l * 255).toString(16).padStart(2, '0');
    return `#${v}${v}${v}`;
  }
  const hue2rgb = (p, q, t) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const hh = h / 360;
  const r = hue2rgb(p, q, hh + 1 / 3);
  const g = hue2rgb(p, q, hh);
  const b = hue2rgb(p, q, hh - 1 / 3);
  const toHex = c => Math.round(c * 255).toString(16).padStart(2, '0');
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

// HSL interpolation. `hueDir` picks which way around the wheel to travel:
//   'short' — shorter angular path (default; e.g. blue → red via purple)
//   'long'  — longer angular path (e.g. blue → cyan → green → yellow → red)
function mixHsl(a, b, t, hueDir = 'short') {
  const [h1, s1, l1] = hexToHsl(a);
  const [h2, s2, l2] = hexToHsl(b);
  let dh = h2 - h1;
  if (hueDir === 'short') {
    if (dh >  180) dh -= 360;
    if (dh < -180) dh += 360;
  } else {
    // Force the long way: flip dh if it currently represents the short arc.
    if (dh > 0 && dh <  180) dh -= 360;
    if (dh < 0 && dh > -180) dh += 360;
  }
  let h = h1 + dh * t;
  if (h <    0) h += 360;
  if (h >= 360) h -= 360;
  return hslToHex(h, s1 + (s2 - s1) * t, l1 + (l2 - l1) * t);
}

// Push t away from 0.5 toward the endpoints (0 or 1). For p < 1, the curve
// rises steeply near the edges and flattens through the middle, so most
// values land closer to an extreme than to dead center. p = 1 is a no-op.
function biasToExtremes(t, p) {
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  return t < 0.5
    ? 0.5 - 0.5 * Math.pow(1 - 2 * t, p)
    : 0.5 + 0.5 * Math.pow(2 * t - 1, p);
}

// ─── Interaction ─────────────────────────────────────────────────────────────

canvas.addEventListener('mousemove', e => {
  if (dragNode) {
    const [wx, wy] = screenToWorld(e.clientX, e.clientY);
    dragNode.fx = wx + dragOffX;
    dragNode.fy = wy + dragOffY;
    sim.alpha(0.3).restart();
    canvas.classList.add('dragging');
    return;
  }

  if (isPanning) {
    tx += e.clientX - panStartX;
    ty += e.clientY - panStartY;
    panStartX = e.clientX;
    panStartY = e.clientY;
    draw();
    return;
  }

  const hit = hitTest(e.clientX, e.clientY);
  hoveredNode = hit;
  canvas.style.cursor = hit ? 'pointer' : 'grab';

  if (hit) {
    tooltip.style.display = 'block';
    tooltip.style.left = (e.clientX + 14) + 'px';
    tooltip.style.top = (e.clientY - 10) + 'px';
    tooltip.textContent = `${hit.id}  ·  ${hit.degree} link${hit.degree !== 1 ? 's' : ''}`;
  } else {
    tooltip.style.display = 'none';
  }
  draw();
});

canvas.addEventListener('mousedown', e => {
  if (e.button !== 0) return;
  mousedownX = e.clientX;
  mousedownY = e.clientY;
  mousedownNode = hitTest(e.clientX, e.clientY);

  if (mousedownNode) {
    dragNode = mousedownNode;
    const [wx, wy] = screenToWorld(e.clientX, e.clientY);
    dragOffX = mousedownNode.x - wx;
    dragOffY = mousedownNode.y - wy;
    mousedownNode.fx = mousedownNode.x;
    mousedownNode.fy = mousedownNode.y;
  } else {
    isPanning = true;
    panStartX = e.clientX;
    panStartY = e.clientY;
    canvas.classList.add('dragging');
  }
});

canvas.addEventListener('mouseup', e => {
  const dx = e.clientX - mousedownX;
  const dy = e.clientY - mousedownY;
  const moved = dx * dx + dy * dy > 16;

  if (dragNode) {
    if (!moved) {
      selectNode(dragNode, false);
    }
    dragNode.fx = null;
    dragNode.fy = null;
    dragNode = null;
    canvas.classList.remove('dragging');
    sim.alpha(0.2).restart();
  } else {
    if (!moved && !isPanning) {
      selectedNode = null;
      listCol.querySelectorAll('.node-item').forEach(el => el.classList.remove('active'));
      detailCol.classList.remove('open');
      draw();
    }
    isPanning = false;
    canvas.classList.remove('dragging');
  }
  mousedownNode = null;
});

canvas.addEventListener('mouseleave', () => {
  isPanning = false;
  hoveredNode = null;
  tooltip.style.display = 'none';
  if (dragNode) {
    dragNode.fx = null;
    dragNode.fy = null;
    dragNode = null;
  }
  draw();
});

canvas.addEventListener('wheel', e => {
  e.preventDefault();
  const zoomFactor = Math.exp(-e.deltaY * 0.01);
  const mx = e.clientX - W / 2;
  const my = e.clientY - H / 2;
  tx = mx - (mx - tx) * zoomFactor;
  ty = my - (my - ty) * zoomFactor;
  scale *= zoomFactor;
  scale = Math.max(0.02, Math.min(scale, 5));
  draw();
}, { passive: false });

// ─── Markdown rendering ───────────────────────────────────────────────────────

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Inline formatting. Input MUST already be HTML-escaped (we never escape twice).
// Order: code → bold → italic (asterisk only). Underscore italics are NOT
// supported on purpose — they break filenames like the_lazy_way_..._2026.txt.
function renderInline(escaped) {
  let t = escaped;
  t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
  t = t.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>');
  // single *italic* — require non-space immediately inside, and not part of **
  t = t.replace(/(^|[^*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)/g, '$1<em>$2</em>');
  t = applySourceShorthand(t);
  return t;
}

// Robust Markdown → HTML. Escapes exactly once, up front. Handles headings
// (#…######), blockquotes (>), unordered lists (- or *), horizontal rules
// (---), and paragraphs with real <br/> line breaks. General Markdown — not a
// fixed note schema (sub-agent A keeps adding ## section types).
function renderMarkdown(md) {
  if (md == null) return '';
  const escaped = escapeHtml(String(md));
  return renderBlocks(escaped.split(/\n{2,}/)).join('');
}

// `>` is &gt; after escaping; bullets are - or *; headings are #… (unescaped).
const RE_QUOTE   = /^\s*&gt;\s?/;
const RE_BULLET  = /^(\s*)[-*]\s+(.*)$/;
const RE_ORDERED = /^(\s*)\d+\.\s+(.*)$/;
const RE_HEADING = /^\s*(#{1,6})\s+(.*)$/;

// Build a <ul>/<ol> from a contiguous run of list-item lines.
//   - Top-level items: zero / minimal indent (matched by either RE_BULLET or
//     RE_ORDERED). The OUTER list type is decided by the first matching line.
//   - Sub-bullets: lines indented 2+ spaces or a tab matching `^\s+[-*]\s+`
//     are folded into a nested <ul> on the most-recently-opened top-level <li>.
//   - Order vs. unordered mixing at the SAME level keeps the outer tag; we
//     don't try to honor a numbering switch mid-list.
//
// Smoke inputs that must render correctly (kept as comments to anchor future
// regressions):
//   "1. Foo\n    * sub one\n    * sub two\n2. Bar"   → <ol> with nested <ul>
//   "- a\n- b"                                       → flat <ul>
//   "1. a\n2. b"                                     → flat <ol>
function buildListHtml(lines) {
  // Determine outer type from the first matching line.
  let outer = 'ul';
  for (const l of lines) {
    if (RE_ORDERED.test(l)) { outer = 'ol'; break; }
    if (RE_BULLET.test(l))  { outer = 'ul'; break; }
  }
  // Indent threshold: anything > 0 (i.e. 2+ spaces or a tab on a bullet) is a
  // sub-bullet. Top-level items have indent 0 (or only \s before the marker if
  // the whole list is uniformly indented — measure relative to the minimum).
  let minIndent = Infinity;
  for (const l of lines) {
    const m = l.match(RE_BULLET) || l.match(RE_ORDERED);
    if (m) minIndent = Math.min(minIndent, m[1].length);
  }
  if (!isFinite(minIndent)) minIndent = 0;

  const items = [];
  let cur = null;          // current top-level item {text, subs:[]}
  for (const l of lines) {
    let m = l.match(RE_ORDERED);
    let isOrdered = !!m;
    if (!m) m = l.match(RE_BULLET);
    if (!m) continue;
    const indent = m[1].length;
    const text = m[2];
    if (indent <= minIndent) {
      cur = { text, subs: [] };
      items.push(cur);
    } else {
      // Sub-bullet folded into the current top-level item. If none exists yet
      // (degenerate input), promote to top-level.
      if (!cur) { cur = { text, subs: [] }; items.push(cur); }
      else cur.subs.push({ text, ordered: isOrdered });
    }
  }
  const renderSubs = subs => {
    if (!subs.length) return '';
    const subOrdered = subs[0].ordered;
    const tag = subOrdered ? 'ol' : 'ul';
    return `<${tag}>${subs.map(s => `<li>${renderInline(s.text)}</li>`).join('')}</${tag}>`;
  };
  const lis = items.map(it => `<li>${renderInline(it.text)}${renderSubs(it.subs)}</li>`).join('');
  return `<${outer}>${lis}</${outer}>`;
}

function renderBlocks(blocks) {
  const out = [];
  for (const block of blocks) {
    const lines = block.split('\n');
    const nonEmpty = lines.filter(l => l.trim());
    if (!nonEmpty.length) continue;

    // Horizontal rule
    if (nonEmpty.length === 1 && /^\s*-{3,}\s*$/.test(nonEmpty[0])) {
      out.push('<hr/>');
      continue;
    }

    // Heading-led block: emit the heading, then render the remaining lines as
    // their own block (notes pack "## Claims\n- a\n- b" with single newlines).
    const h = nonEmpty[0].match(RE_HEADING);
    if (h) {
      const level = Math.min(h[1].length + 2, 6); // ## → <h4>, clamp to <h6>
      out.push(`<h${level}>${renderInline(h[2].trim())}</h${level}>`);
      const rest = lines.slice(lines.indexOf(nonEmpty[0]) + 1).join('\n').trim();
      if (rest) out.push(...renderBlocks([rest]));
      continue;
    }

    // Blockquote: every non-empty line starts with >
    if (nonEmpty.every(l => RE_QUOTE.test(l))) {
      const inner = nonEmpty
        .map(l => renderInline(l.replace(RE_QUOTE, '')))
        .join('<br/>');
      out.push(`<blockquote>${inner}</blockquote>`);
      continue;
    }

    // List block: every non-empty line is either a bullet (- / *) OR an
    // ordered item (`N. `). Mixed bullets + numbered + indented sub-bullets
    // are all routed through buildListHtml.
    if (nonEmpty.every(l => RE_BULLET.test(l) || RE_ORDERED.test(l))) {
      out.push(buildListHtml(nonEmpty));
      continue;
    }

    // Paragraph (real line breaks preserved)
    out.push(`<p>${renderInline(lines.join('\n')).replace(/\n/g, '<br/>')}</p>`);
  }
  return out;
}

// ─── Source shorthand ─────────────────────────────────────────────────────────
// Long source filenames (the_lazy_way_i_make_money_with_ai_2026.txt) are
// replaced at RENDER time with a short bold, clickable label. The pipeline
// keeps full filenames; mapping is derived deterministically from the source
// node list (nodes with plugin === null; their id IS the filename).

const SOURCE_STOPWORDS = new Set([
  'the', 'a', 'an', 'and', 'or', 'of', 'to', 'with', 'for', 'in', 'on', 'my',
  'i', 'you', 'your', 'how', 'way', 'is', 'are', 'this', 'that', 'it',
]);

let sourceLabels = {};        // filename → short label (e.g. "Lazy Money AI")
let _sourceRegex = null;      // matches any known filename in escaped text

function shortLabelFor(filename) {
  const base = filename.replace(/\.txt$/i, '').replace(/_\d{4}$/, '');
  const words = base.split(/[_\s]+/).filter(Boolean);
  const salient = words.filter(w => !SOURCE_STOPWORDS.has(w.toLowerCase()));
  const pick = (salient.length ? salient : words).slice(0, 3);
  return pick
    .map(w => w.length <= 3 ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1))
    .join(' ');
}

function buildSourceLabels(rawNodes) {
  sourceLabels = {};
  const used = new Set();
  // v2 source ids are like `source:foo.txt`; chat output cites bare `foo.txt`.
  // Key sourceLabels by the BARE filename so applySourceShorthand matches the
  // chat output. Also stash sourceLabels[full id] as a fallback for any caller
  // that resolves by full id (e.g. selectedNode title lookup).
  const bareNames = [];
  rawNodes
    .filter(n => /\.txt$/i.test(n.id) &&
                 (n.plugin == null || n.level === 'source' || n.id.startsWith('source:')))
    .forEach(n => {
      const bare = n.id.replace(/^source:/, '');
      let label = shortLabelFor(bare);
      let candidate = label, k = 2;
      while (used.has(candidate.toLowerCase())) candidate = `${label} ${k++}`;
      used.add(candidate.toLowerCase());
      sourceLabels[bare] = candidate;
      if (bare !== n.id) sourceLabels[n.id] = candidate;   // legacy resolver path
      bareNames.push(bare);
    });

  const names = bareNames
    .sort((a, b) => b.length - a.length)        // longest-first so prefixes don't shadow
    .map(n => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  _sourceRegex = names.length ? new RegExp(names.join('|'), 'g') : null;
}

// Replace any known source filename in already-escaped text with a clickable
// bold short label that opens the corresponding source node.
function applySourceShorthand(escaped) {
  if (!_sourceRegex) return escaped;
  return escaped.replace(_sourceRegex, (fn) => {
    const label = sourceLabels[fn] || fn;
    const safe = fn.replace(/"/g, '&quot;');
    return `<strong class="src-ref node-link" data-node="${safe}">${label}</strong>`;
  });
}

// ─── View toggle: Graph | Chat ────────────────────────────────────────────────

const viewToggle  = document.getElementById('view-toggle');
const btnGraph    = document.getElementById('toggle-graph');
const btnChat     = document.getElementById('toggle-chat');
const btnCopy     = document.getElementById('toggle-copy');
const chatPanel   = document.getElementById('chat-panel');
const copyPanel   = document.getElementById('copy-panel');
const controlsEl  = document.getElementById('controls');
const centerBtn   = document.getElementById('center-btn');
const hintEl      = document.getElementById('hint');

// ─── Collapsible graph controls ───────────────────────────────────────────────

const controlsHeader = document.getElementById('controls-header');
if (controlsHeader) {
  controlsHeader.addEventListener('click', () => {
    controlsEl.classList.toggle('collapsed');
  });
}

// ─── Collapsible + scrollable legend ─────────────────────────────────────────

const legendHeader = document.getElementById('legend-header');
if (legendHeader) {
  legendHeader.addEventListener('click', (e) => {
    e.stopPropagation(); // don't bubble to controls-header
    legendEl.classList.toggle('legend-collapsed');
  });
}

function setView(view) {
  const isChat = view === 'chat';
  const isCopy = view === 'copy';
  const isGraph = view === 'graph';
  // Stamp the body so #topbar's per-view CSS can hide/show groups.
  document.body.dataset.view = view;
  // Toggle pill position via class (handles 0% / 100% / 200%)
  viewToggle.classList.toggle('chat', isChat);
  viewToggle.classList.toggle('copy', isCopy);
  btnGraph.classList.toggle('active', isGraph);
  btnChat.classList.toggle('active', isChat);
  btnCopy.classList.toggle('active', isCopy);

  // Show/hide the three view-panels via opacity (so child elements can animate)
  chatPanel.classList.toggle('show', isChat);
  copyPanel.classList.toggle('show', isCopy);
  // canvas/center/hint only visible in graph mode (no need to animate these)
  [canvas, hintEl].forEach(el => {
    if (el) el.style.display = isGraph ? '' : 'none';
  });

  // Sliding side-chrome:
  //   #controls (graph controls top-left): slides up off-screen when not graph
  //   #dev-panel (left of chat): slides left off-screen when not chat
  //   #right (topics sidebar):  slides right off-screen ONLY in copy view
  controlsEl.classList.toggle('is-hidden', !isGraph);
  const devEl = document.getElementById('dev-panel');
  if (devEl) devEl.classList.toggle('is-hidden', !isChat);
  const rightEl = document.getElementById('right');
  if (rightEl) rightEl.classList.toggle('is-hidden', isCopy);
  const hkEl = document.getElementById('copy-hotkeys');
  if (hkEl) hkEl.classList.toggle('off', !isCopy);
  // The right-sidebar minimize button: only useful in graph + chat (the
  // copy view already hides the sidebar via its own .is-hidden class).
  // (right-toggle / dev-toggle visibility is CSS-driven via body[data-view])

  // Don't auto-focus the input when entering chat/copy — the user wants the
  // arrow keys to swap pages without first clicking out of the textarea.
  if (isChat) startStatusPoll(); else stopStatusPoll();
  if (isCopy) requestAnimationFrame(layoutCopyStack);
  if (isChat) requestAnimationFrame(centerChatToViewport);
}

btnGraph.addEventListener('click', () => setView('graph'));
btnChat.addEventListener('click', () => setView('chat'));
btnCopy.addEventListener('click', () => setView('copy'));

// Right-sidebar minimize / restore. Slides the sidebar off via transform AND
// applies a negative margin-right equal to its current width so the layout
// also collapses — otherwise #left wouldn't expand into the freed space.
const rightToggleBtn = document.getElementById('right-toggle');
const rightSidebar = document.getElementById('right');
if (rightToggleBtn && rightSidebar) {
  rightToggleBtn.addEventListener('click', () => {
    const collapsing = !rightSidebar.classList.contains('collapsed');
    if (collapsing) {
      const w = rightSidebar.offsetWidth;
      rightSidebar.style.marginRight = `-${w}px`;
    } else {
      rightSidebar.style.marginRight = '';
    }
    rightSidebar.classList.toggle('collapsed', collapsing);
    document.body.classList.toggle('right-collapsed', collapsing);
    centerChatToViewport();
  });
}

// Dev-panel collapse — mirrors the right-sidebar pattern.
const devToggleBtn = document.getElementById('dev-toggle');
const devPanelEl   = document.getElementById('dev-panel');
if (devToggleBtn && devPanelEl) {
  devToggleBtn.addEventListener('click', () => {
    const collapsing = !devPanelEl.classList.contains('collapsed');
    if (collapsing) {
      const w = devPanelEl.offsetWidth;
      devPanelEl.style.marginLeft = `-${w}px`;
    } else {
      devPanelEl.style.marginLeft = '';
    }
    devPanelEl.classList.toggle('collapsed', collapsing);
    document.body.classList.toggle('dev-collapsed', collapsing);
    centerChatToViewport();
  });
}

// ADR 0005 D1: chat content centers naturally inside #chat-main via
// `.chat-inner { max-width: 720px; margin: 0 auto }`. The prior
// viewport-centering shim (ADR 0002 D2, ADR 0004 D1 clamp) computed
// translateX from sidebar widths — math was off and the inner ended up
// pinned to chat-main's edge. The function survives as a no-op that
// clears any leftover inline transform; call sites stay untouched.
function centerChatToViewport() {
  document.querySelectorAll('.chat-inner, .composer-inner, .composer-meta')
    .forEach(el => { el.style.transform = ''; });
}

// ─── Chat (RAG over the knowledge base) ───────────────────────────────────────

const chatMessages = document.getElementById('chat-messages');
const chatScroll    = document.getElementById('chat-scroll');
const chatEmpty     = document.getElementById('chat-empty');
const chatInput     = document.getElementById('chat-input');
const chatSend      = document.getElementById('chat-send');

// ── Status pill ──────────────────────────────────────────────────────────────
const statusDot    = document.getElementById('status-dot');
const statusLabel  = document.getElementById('status-label');

// ── Gemini-only toggle ────────────────────────────────────────────────────────
const geminiOnlyBtn = document.getElementById('gemini-only-btn');
let geminiOnly = false;

if (geminiOnlyBtn) {
  geminiOnlyBtn.addEventListener('click', () => {
    geminiOnly = !geminiOnly;
    geminiOnlyBtn.classList.toggle('on', geminiOnly);
    // qwen-only and gemini-only are mutually exclusive
    if (geminiOnly && qwenOnly) {
      qwenOnly = false;
      qwenOnlyBtn?.classList.remove('on');
    }
  });
}

// ── Qwen-only toggle ──────────────────────────────────────────────────────────
const qwenOnlyBtn = document.getElementById('qwen-only-btn');
let qwenOnly = false;

if (qwenOnlyBtn) {
  qwenOnlyBtn.addEventListener('click', () => {
    qwenOnly = !qwenOnly;
    qwenOnlyBtn.classList.toggle('on', qwenOnly);
    // qwen-only and gemini-only are mutually exclusive
    if (qwenOnly && geminiOnly) {
      geminiOnly = false;
      geminiOnlyBtn?.classList.remove('on');
    }
  });
}

let _lastStatusData = null;
let _statusTickInterval = null;

function _startStatusTick() {
  if (_statusTickInterval) return;
  _statusTickInterval = setInterval(_tickStatus, 1000);
}
function _stopStatusTick() {
  if (_statusTickInterval) { clearInterval(_statusTickInterval); _statusTickInterval = null; }
}

function _tickStatus() {
  if (!_lastStatusData) return;
  _renderStatusPill(_lastStatusData);
}

function _renderStatusPill(status) {
  if (!statusDot || !statusLabel) return;
  const now = Date.now() / 1000;
  if (status.gemini_ok) {
    statusDot.className = 'status-dot green';
    const elapsed = status.last_success_ts ? Math.floor(now - status.last_success_ts) : null;
    const rpm14 = (status.recent_calls_60s ?? 0) >= 14;  // approaching 15 RPM limit
    let txt = 'Gemini';
    if (elapsed !== null && elapsed > 0) txt += ` · ${elapsed}s ago`;
    if (rpm14) txt += ' · near limit';
    statusLabel.textContent = txt;
  } else {
    statusDot.className = 'status-dot amber';
    let txt = 'Local (qwen)';
    if (status.in_cooldown) {
      if (status.retry_known && status.cooldown_remaining != null) {
        // Tick down the remaining seconds live
        const serverAge = Math.floor(now - (status._fetchedAt || now));
        const live = Math.max(0, status.cooldown_remaining - serverAge);
        txt += ` · Gemini in ${live}s`;
      } else {
        txt += ' · Gemini cooldown';
      }
    }
    statusLabel.textContent = txt;
  }
}

function updateStatusPill(status) {
  if (!status) {
    _stopStatusTick();
    statusDot.className = 'status-dot grey';
    statusLabel.textContent = 'server offline';
    return;
  }
  status._fetchedAt = Date.now() / 1000;
  _lastStatusData = status;
  _renderStatusPill(status);
  _startStatusTick();
}

// Also update from per-turn backend event
function updateStatusFromBackend(backend) {
  if (!statusDot || !statusLabel) return;
  if (backend === 'gemini') {
    statusDot.className = 'status-dot green';
    statusLabel.textContent = 'Gemini';
  } else if (backend === 'qwen') {
    statusDot.className = 'status-dot amber';
    if (!statusLabel.textContent.startsWith('Local')) {
      statusLabel.textContent = 'Local (qwen)';
    }
  }
}

// Poll /api/status while chat view is active
let _statusPollInterval = null;

async function pollStatus() {
  try {
    const res = await fetch('/api/status');
    if (res.ok) {
      const data = await res.json();
      updateStatusPill(data);
    } else {
      updateStatusPill(null);
    }
  } catch {
    updateStatusPill(null);
  }
}

function startStatusPoll() {
  pollStatus(); // immediate
  if (!_statusPollInterval) {
    _statusPollInterval = setInterval(pollStatus, 8000);
  }
}
function stopStatusPoll() {
  if (_statusPollInterval) {
    clearInterval(_statusPollInterval);
    _statusPollInterval = null;
  }
}

// Dev / Debug panel (left side of chat view)
const devSystem     = document.getElementById('dev-system');
const devDecomp     = document.getElementById('dev-decomp');
const devContext    = document.getElementById('dev-context');
const devPrompt     = document.getElementById('dev-prompt');
const devResponse   = document.getElementById('dev-response');

// Write text into a Dev <pre>; empty/undefined shows a placeholder.
// ─── Copy-to-clipboard (dev blocks + chat) ────────────────────────────────────

function copyText(text, btn) {
  const done = () => {
    if (!btn) return;
    const old = btn.textContent;
    btn.textContent = 'Copied';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = old; btn.classList.remove('copied'); }, 1200);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}
function fallbackCopy(text, done) {
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch {}
  document.body.removeChild(ta);
}

// Dev-block copy buttons (delegated; copy the linked <pre>'s exact text)
document.addEventListener('click', e => {
  const btn = e.target.closest('.copy-btn[data-copy]');
  if (!btn) return;
  const el = document.getElementById(btn.dataset.copy);
  if (el) copyText(el.textContent, btn);
});

// Cheap token approximation — GPT-style BPE averages ~3.8 chars/tok on
// English prose, more like ~3.3 for code/structured text. 3.5 is a fair
// midpoint. Good enough for a dev-panel indicator (we're not budgeting).
function _approxTokens(s) {
  if (!s) return 0;
  return Math.ceil(s.length / 3.5);
}
function _updateDevCount(id, text) {
  const el = document.getElementById(id);
  if (!el) return;
  if (text == null || text === '') {
    el.textContent = '—';
    el.classList.add('empty');
  } else {
    const t = _approxTokens(text);
    const c = text.length;
    el.textContent = `~${t.toLocaleString()}t · ${c.toLocaleString()}c`;
    el.classList.remove('empty');
  }
}

function setDevField(el, text) {
  if (!el) return;
  if (text == null || text === '') {
    el.innerHTML = '<span class="dev-empty">— empty —</span>';
  } else {
    el.textContent = text; // monospaced, exact, un-rendered
  }
  // Update the matching token-count badge (id = `<el.id>-count`).
  if (el.id) _updateDevCount(`${el.id}-count`, text);
}

function resetDevPanel() {
  setDevField(devSystem, '');
  setDevField(devDecomp, '');
  setDevField(devContext, '');
  setDevField(devPrompt, '');
  setDevField(devResponse, '');
}

let chatBusy = false;
// Chat history — replayed to the server on each turn so the model has
// conversational context. Each entry: {role, content, sources?:[filenames]}.
// Sources are tracked per turn so the server can dedup retrieval (sources
// already in history aren't re-injected; older turns' assistant prose has
// them already).
const chatHistory = [];

function selectNodeById(id) {
  const target = id.trim().toLowerCase();
  // v2 source ids carry a `source:` prefix; chat / shorthand strips it. Match
  // either form so a bare filename click still finds the node.
  const node = nodes.find(n => {
    const nid = n.id.toLowerCase();
    return nid === target || nid === `source:${target}` || nid.replace(/^source:/, '') === target;
  });
  if (node) selectNode(node, false);
}

function autoGrow() {
  chatInput.style.height = 'auto';
  chatInput.style.height = Math.min(chatInput.scrollHeight, 200) + 'px';
}
// Live token counter under the textarea. Approx via 3.5 chars/token.
// Two counters:
//   #chat-meta-input → tokens of the current draft (just the textarea)
//   #chat-meta-conv  → running total sent to the model on next send
//                      = system prompt + prior chat history + draft input
const _chatMeta      = document.getElementById('chat-meta');
const _chatMetaInput = document.getElementById('chat-meta-input');
const _chatMetaConv  = document.getElementById('chat-meta-conv');
// Updated on every debug event from the server (the source of truth).
let _systemTokens  = 0;   // tokens used by the system prompt
let _historyTokens = 0;   // tokens used by prior turns ONLY (no current draft)
const _approxTok = s => (s && s.length) ? Math.ceil(s.length / 3.5) : 0;
function updateChatMeta() {
  if (!_chatMeta || !_chatMetaInput || !_chatMetaConv) return;
  const txt = chatInput.value || '';
  const inputT = _approxTok(txt);
  const convT  = _systemTokens + _historyTokens + inputT;
  _chatMetaInput.textContent = `${inputT.toLocaleString()} token${inputT === 1 ? '' : 's'}`;
  _chatMetaConv.textContent  = `${convT.toLocaleString()} conv`;
  // warn / alert tier reflects the conversation total, not the draft.
  _chatMeta.classList.remove('warn', 'alert');
  if (convT > 24_000)      _chatMeta.classList.add('alert');
  else if (convT > 12_000) _chatMeta.classList.add('warn');
}
// Recompute history tokens from the locally-tracked chatHistory. Called
// after each turn settles so the conv counter advances. The server sends
// authoritative numbers via the debug event; this is the fallback used
// before the first debug event arrives (e.g., on chat-view-show with a
// fresh history loaded from a prior session).
function _recalcHistoryTokens() {
  _historyTokens = (chatHistory || []).reduce(
    (sum, m) => sum + _approxTok(m.content || ''), 0);
}
chatInput.addEventListener('input', () => { autoGrow(); updateChatMeta(); });
updateChatMeta();
chatInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!chatInput.value.trim()) {
      chatInput.blur();
    } else {
      sendChat({ refocus: false });
      chatInput.blur();
    }
  } else if (e.key === 'Escape') {
    e.preventDefault();
    chatInput.blur();
  }
});
chatSend.addEventListener('click', () => sendChat({ refocus: true }));

function addUserMessage(text) {
  if (chatEmpty) chatEmpty.style.display = 'none';
  const el = document.createElement('div');
  el.className = 'msg user';
  el.innerHTML = `<div class="msg-role">You</div><div class="msg-body"></div>`;
  el.querySelector('.msg-body').textContent = text;
  chatMessages.appendChild(el);
  scrollToBottom();
}

const THINKING_HTML =
  '<span class="thinking"><span class="thinking-label">Thinking</span>' +
  '<span class="thinking-dots"><i></i><i></i><i></i></span></span>';

function addAssistantShell() {
  const el = document.createElement('div');
  el.className = 'msg assistant';
  el.innerHTML = `
    <div class="msg-role"><span>Scribe</span><button class="copy-btn msg-copy">Copy</button></div>
    <div class="retrieval">
      <div class="retrieval-head searching"><span class="dot"></span>Searching knowledge base…</div>
      <div class="retrieval-nodes"></div>
    </div>
    <div class="msg-body"></div>`;
  chatMessages.appendChild(el);
  scrollToBottom();
  return {
    head:    el.querySelector('.retrieval-head'),
    nodes:   el.querySelector('.retrieval-nodes'),
    body:    el.querySelector('.msg-body'),
    retrieval: el.querySelector('.retrieval'),
    copyBtn: el.querySelector('.msg-copy'),
  };
}

function renderNodeChips(container, names) {
  container.innerHTML = '';
  if (!names.length) {
    container.innerHTML = `<span style="font-size:11px;color:#4a4641">No matching topics</span>`;
    return;
  }
  names.forEach(name => {
    const chip = document.createElement('span');
    chip.className = 'retrieval-chip';
    chip.textContent = name;
    chip.addEventListener('click', () => selectNodeById(name));
    container.appendChild(chip);
  });
}

// Replace [[Node Title]] references in streamed text with clickable links —
// but ONLY when the name matches a real graph node id (case-insensitive).
// Gemini wraps plenty of non-topics in [[...]] (e.g. "AI agency"); those are
// rendered as plain text with the brackets stripped (no link, no bolding).
function linkifyNodes(html) {
  return html.replace(/\[\[([^\]]+)\]\]/g, (_, name) => {
    const trimmed = name.trim();
    if (nodeExists(trimmed)) {
      return `<a class="node-link" data-node="${trimmed.replace(/"/g, '&quot;')}">${trimmed}</a>`;
    }
    return trimmed; // unknown topic → plain text, brackets stripped
  });
}

function nodeExists(name) {
  const target = name.trim().toLowerCase();
  return nodes.some(n => n.id.toLowerCase() === target);
}

function bindNodeLinks(bodyEl) {
  bodyEl.querySelectorAll('.node-link').forEach(a => {
    if (a._bound) return;
    a._bound = true;
    a.addEventListener('click', () => {
      // Update the #right detail sidebar (visible in both views) WITHOUT
      // switching away from Chat — the user stays where they are.
      selectNodeById(a.dataset.node);
    });
  });
}

function scrollToBottom() {
  chatScroll.scrollTop = chatScroll.scrollHeight;
}

async function sendChat(opts = {}) {
  const { refocus = true } = opts;
  const text = chatInput.value.trim();
  if (!text || chatBusy) return;
  chatBusy = true;
  chatSend.disabled = true;
  chatInput.value = '';
  autoGrow();
  updateChatMeta();

  addUserMessage(text);
  const ui = addAssistantShell();
  // Copy button yields the raw markdown answer accumulated so far
  if (ui.copyBtn) ui.copyBtn.addEventListener('click', () => copyText(answer, ui.copyBtn));
  // Phase 1 → 2 → 3: searching · consulted N topics · generating.
  // Until the first token arrives, show the animated "Thinking…" indicator.
  ui.body.innerHTML = THINKING_HTML;
  // Dev panel reflects THIS turn's round-trip; clear the previous one.
  resetDevPanel();
  let answer = '';        // raw model markdown (un-rendered) — also feeds Dev
  let gotToken = false;
  let reader = null;
  let turnSources = [];   // filenames attached to this turn's context

  const finishBody = () => {
    ui.body.innerHTML = linkifyNodes(renderMarkdown(answer || '*No response.*'));
    bindNodeLinks(ui.body);
    setDevField(devResponse, answer); // final raw markdown
    scrollToBottom();
  };

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: text,
        gemini_only: geminiOnly,
        qwen_only: qwenOnly,
        history: chatHistory,
      }),
    });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

    reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finished = false;

    while (!finished) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep;
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const line = raw.split('\n').find(l => l.startsWith('data:'));
        if (!line) continue;
        let evt;
        try { evt = JSON.parse(line.slice(5).trim()); } catch { continue; }

        if (evt.type === 'sources') {
          // Filenames attached to THIS turn's context — store so the next
          // turn's request can list them in history and the server can dedup.
          turnSources = evt.sources || [];
        } else if (evt.type === 'nodes') {
          ui.head.classList.remove('searching');
          ui.head.innerHTML = `<span class="dot generating"></span>Consulted ${evt.nodes.length} topic${evt.nodes.length !== 1 ? 's' : ''}`;
          renderNodeChips(ui.nodes, evt.nodes);
          // Phase 3: waiting on the model — keep the Thinking indicator visible.
          if (!gotToken) ui.body.innerHTML = THINKING_HTML;
          scrollToBottom();
        } else if (evt.type === 'debug') {
          // Exact round-trip sent to the model (after retrieval, pre-generation)
          setDevField(devSystem, evt.system);
          if (evt.sub_queries && evt.sub_queries.length > 1) {
            setDevField(devDecomp, evt.sub_queries.map((q, i) => `${i + 1}. ${q}`).join('\n'));
          } else {
            setDevField(devDecomp, '(atomic — no split)');
          }
          setDevField(devContext, evt.context);
          setDevField(devPrompt, evt.prompt);  // FULL wire payload (ADR follow-up)
          // Pin the system-prompt token count from the server (authoritative)
          // so the conv-total counter under the chat input is exact.
          if (typeof evt.system_tokens === 'number') {
            _systemTokens = evt.system_tokens;
            updateChatMeta();
          }
        } else if (evt.type === 'backend') {
          // Which engine is answering this turn
          updateStatusFromBackend(evt.backend);
          // Schedule a poll shortly after the turn ends to sync cooldown state
          setTimeout(pollStatus, 1500);
        } else if (evt.type === 'notice') {
          // Non-fatal status (e.g. Gemini rate-limited → local qwen fallback)
          ui.head.classList.remove('searching');
          ui.head.innerHTML = `<span class="dot generating"></span>${evt.text}`;
          if (!gotToken) ui.body.innerHTML = THINKING_HTML;
          scrollToBottom();
        } else if (evt.type === 'token') {
          gotToken = true;
          answer += evt.text;
          ui.head.querySelector('.dot')?.classList.remove('generating');
          ui.body.innerHTML = linkifyNodes(renderMarkdown(answer)) +
            '<span class="typing-cursor"></span>';
          bindNodeLinks(ui.body);
          setDevField(devResponse, answer); // raw markdown, live
          scrollToBottom();
        } else if (evt.type === 'error') {
          answer = answer || `⚠️ ${evt.message}`;
          ui.head.classList.remove('searching');
          ui.head.innerHTML = `<span class="dot" style="background:#d36f8f"></span>Error`;
        } else if (evt.type === 'done') {
          // Authoritative end-of-turn: stop reading immediately so a hung
          // keep-alive socket can never wedge the UI (the "stuck after one
          // question" bug). The server also sends Connection: close.
          finished = true;
          break;
        }
      }
    }
  } catch (err) {
    answer = answer || `⚠️ Could not reach the knowledge server. Is \`serve.sh\` running with \`server.py\`?\n\n_${err.message}_`;
    ui.head.classList.remove('searching');
    ui.head.innerHTML = `<span class="dot" style="background:#d36f8f"></span>Connection error`;
  } finally {
    if (reader) { try { await reader.cancel(); } catch { /* ignore */ } }
    finishBody();
    // Commit this turn to chat history — user msg + final assistant msg.
    // Sources are attached to the user turn (they were retrieved FOR it).
    if (text) {
      chatHistory.push({ role: 'user',      content: text,           sources: turnSources });
    }
    if (answer) {
      chatHistory.push({ role: 'assistant', content: answer });
    }
    // History grew → recompute the conv-total token counter so the
    // baseline reflects what'll be sent on the NEXT turn.
    _recalcHistoryTokens();
    updateChatMeta();
    chatBusy = false;
    chatSend.disabled = false;
    if (refocus) chatInput.focus();
  }
}

// ─── Copy-paste RAG view ──────────────────────────────────────────────────────

const copyStage    = document.getElementById('copy-stage');
const copyEmpty    = document.getElementById('copy-empty');
const copyInput    = document.getElementById('copy-input');
const copySend     = document.getElementById('copy-send');
const copyUp       = document.getElementById('copy-up');
const copyDown     = document.getElementById('copy-down');
const copyPos      = document.getElementById('copy-pos');
const copyLoading  = document.getElementById('copy-loading');

let copyTurns = [];          // [{el, query, sources, newSources, allText, newText}]
let copyFocus = 0;           // index of focused turn (last = newest)
let copyBusy  = false;
let copySeen  = new Set();   // source names included in any prior turn THIS session
                              // (in-memory only — resets on page reload, i.e. new chat)

function fitTextToBox(box, startFs = 12, minFs = 4) {
  // Shrink font-size until content fits without scrolling.
  let fs = startFs;
  box.style.fontSize = fs + 'px';
  box.style.lineHeight = fs < 9 ? '1.25' : '1.4';
  // Bail out if box is hidden (zero size) — try again later
  if (box.clientHeight === 0 || box.clientWidth === 0) return;
  while ((box.scrollHeight > box.clientHeight + 1 ||
          box.scrollWidth  > box.clientWidth  + 1) && fs > minFs) {
    fs -= 0.25;
    box.style.fontSize = fs + 'px';
    box.style.lineHeight = fs < 9 ? '1.2' : '1.4';
  }
}

// ── Bento packing: exact tiling of an H×W grid with 1×1 + 1×2 cells ──
//   1. Choose a (W, H, k) such that 2·k + (N − k) = W·H and k ≤ H·⌊W/2⌋
//      (= the max horizontal 1×2's that fit in a W×H grid)
//   2. Place k 1×2 dominos at random valid positions, then fill the rest with
//      1×1 monominos. Random source-to-cell assignment.
function shuffled(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// Aim for SQUARE-ish cells. Optionally reserve a top-left rectangular block
// (e.g. 3×3 user-prompt cell) before placing N other items as 1×1 / 1×2 dominos.
function bentoPackSquare(N, itemsLen, containerW, containerH, reserved) {
  if (N === 0 && !reserved) return { placements: [], W: 1, H: 1 };
  const reservedCells = reserved ? reserved.w * reserved.h : 0;
  const aspect = Math.max(0.3, containerW / Math.max(containerH, 1));
  const idxByLen = [...itemsLen.keys()].sort((a, b) => itemsLen[b] - itemsLen[a]);

  // Score (W, H, k) by how SQUARE the resulting cells will be.
  // cellW = containerW / W,  cellH = containerH / H,  square ⇒ cellW = cellH.
  // Use the cell-aspect score to pick the best grid.
  const candidates = [];
  const minW = reserved ? Math.max(3, reserved.c + reserved.w) : 2;
  const minH = reserved ? Math.max(3, reserved.r + reserved.h) : 1;
  for (let W = minW; W <= Math.min(16, N + reservedCells); W++) {
    for (let H = minH; H <= Math.min(16, N + reservedCells); H++) {
      const total = W * H;
      const free = total - reservedCells;
      if (free < N) continue;       // need ≥ N cells for sources
      const k = free - N;
      if (k > N) continue;          // need each wide to come from an item
      const cellW = containerW / W;
      const cellH = containerH / H;
      const cellAspect = cellW / cellH;
      const squareScore = Math.abs(Math.log(cellAspect));   // 0 = perfect square
      const sizeScore   = -Math.min(cellW, cellH);          // prefer big cells
      candidates.push({ W, H, k, score: squareScore * 4 + sizeScore * 0.01 });
    }
  }
  candidates.sort((a, b) => a.score - b.score);

  for (const c of candidates.slice(0, 30)) {
    const result = tryPlaceSquare(N, itemsLen.length ? idxByLen : [], c.W, c.H, c.k, reserved);
    if (result) return { placements: result, W: c.W, H: c.H };
  }
  // Fallback — best-effort 1×1 only
  const W = Math.max(2, Math.round(Math.sqrt((N + reservedCells) * aspect)));
  const H = Math.ceil((N + reservedCells) / W);
  return {
    placements: itemsLen.map((_, i) => ({ r: Math.floor(i / W), c: i % W, w: 1, h: 1 })),
    W, H
  };
}

function tryPlaceSquare(N, idxByLen, W, H, k, reserved) {
  for (let attempt = 0; attempt < 25; attempt++) {
    const grid = Array.from({ length: H }, () => Array(W).fill(false));
    // Block reserved cells
    if (reserved) {
      for (let r = reserved.r; r < reserved.r + reserved.h; r++)
        for (let c = reserved.c; c < reserved.c + reserved.w; c++)
          grid[r][c] = true;
    }

    // Place k wides (1×2 horizontal) at random valid positions in remaining area
    const wideStarts = [];
    for (let r = 0; r < H; r++)
      for (let c = 0; c < W - 1; c++)
        if (!grid[r][c] && !grid[r][c + 1]) wideStarts.push([r, c]);
    const order = shuffled(wideStarts);
    const widePos = [];
    for (const [r, c] of order) {
      if (widePos.length >= k) break;
      if (grid[r][c] || grid[r][c + 1]) continue;
      grid[r][c] = grid[r][c + 1] = true;
      widePos.push({ r, c });
    }
    if (widePos.length < k) continue;

    // Fill remaining cells with 1×1
    const smallCells = [];
    for (let r = 0; r < H; r++)
      for (let c = 0; c < W; c++)
        if (!grid[r][c]) smallCells.push({ r, c });

    // Sanity: small cells + 2*k should equal N
    if (smallCells.length + k !== N) continue;

    const widePosShuf = shuffled(widePos);
    const smallPosShuf = shuffled(smallCells);
    const wideIds = shuffled(idxByLen.slice(0, k));
    const smallIds = shuffled(idxByLen.slice(k));
    const placements = new Array(N);
    wideIds.forEach((id, i) => {
      const p = widePosShuf[i];
      placements[id] = { r: p.r, c: p.c, w: 2, h: 1 };
    });
    smallIds.forEach((id, i) => {
      const p = smallPosShuf[i];
      placements[id] = { r: p.r, c: p.c, w: 1, h: 1 };
    });
    return placements;
  }
  return null;
}

const copyPanelEl = document.getElementById('copy-panel');
const copyComposerEl = document.getElementById('copy-composer');

function copyAutoGrow() {
  copyInput.style.height = 'auto';
  copyInput.style.height = Math.min(copyInput.scrollHeight, 120) + 'px';
  // Slide the stage upward as the composer grows, so the focused panel stays
  // visually centered without ever overlapping the input bar.
  requestAnimationFrame(() => {
    const composerH = copyComposerEl.offsetHeight;
    const stageBottom = 30 + composerH + 20;   // composer bottom-offset + height + clearance
    copyPanelEl.style.setProperty('--stage-bottom', `${stageBottom}px`);
  });
}
copyInput.addEventListener('input', copyAutoGrow);
copyInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!copyInput.value.trim()) {
      // Empty input + Enter → just blur (toggle off the input).
      copyInput.blur();
    } else {
      sendCopy({ refocus: false });
      copyInput.blur();
    }
  } else if (e.key === 'Escape') {
    e.preventDefault();
    copyInput.blur();
  }
});
copySend.addEventListener('click', () => sendCopy({ refocus: true }));

copyUp.addEventListener('click', () => {
  if (copyFocus > 0) { copyFocus--; layoutCopyStack(); }
});
copyDown.addEventListener('click', () => {
  if (copyFocus < copyTurns.length - 1) { copyFocus++; layoutCopyStack(); }
});

function flashCopied(el) {
  if (!el) return;
  el.classList.add('copied');
  setTimeout(() => el.classList.remove('copied'), 1200);
}

// Unified document-level hotkeys. Behaviour depends on current view:
//   Copy view:
//     ↑/↓  switch focused turn
//     ←    copy the new-sources panel
//     →    copy the user prompt
//     Enter / /  focus composer
//   Graph + Chat view:
//     ←/→  cycle pages (graph ↔ chat ↔ copy)
//     Enter / /  focus the chat composer (in chat view only)
// When typing in any text input, hotkeys are ignored except Escape/Enter which
// the input's own handler manages.
function currentView() {
  if (copyPanel.classList.contains('show')) return 'copy';
  if (chatPanel.classList.contains('show')) return 'chat';
  return 'graph';
}
function cycleView(dir) {
  const order = ['graph', 'chat', 'copy'];
  const cur = currentView();
  const i = order.indexOf(cur);
  const next = order[(i + dir + order.length) % order.length];
  setView(next);
}

document.addEventListener('keydown', (e) => {
  // Don't hijack input handlers
  if (e.target === copyInput || e.target === chatInput) return;
  const view = currentView();
  if (view === 'copy') {
    if (e.key === 'Enter' || e.key === '/') {
      e.preventDefault();
      copyInput.focus();
      return;
    }
    if (e.key === 'ArrowUp')   { e.preventDefault(); copyUp.click(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); copyDown.click(); return; }
    const f = copyTurns[copyFocus];
    if (!f) return;
    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      const btn = f.el.querySelector('.new-copy-all');
      if (btn) btn.click();
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      copyText(f.query);
      flashCopied(f.promptCard && f.promptCard.card);
    }
    return;
  }
  // Graph view: Escape closes the right-sidebar description sub-panel.
  // Scoped to graph so chat/copy retain their own Escape semantics.
  if (view === 'graph' && e.key === 'Escape' && detailCol.classList.contains('open')) {
    e.preventDefault();
    detailCol.classList.remove('open');
    selectedNode = null;
    listCol.querySelectorAll('.node-item').forEach(el => el.classList.remove('active'));
    draw();
    return;
  }
  // Graph + Chat views
  if (e.key === 'ArrowLeft')  { e.preventDefault(); cycleView(-1); return; }
  if (e.key === 'ArrowRight') { e.preventDefault(); cycleView(+1); return; }
  if (view === 'chat' && (e.key === 'Enter' || e.key === '/')) {
    e.preventDefault();
    chatInput.focus();
  }
});

function formatTurnText(query, sources) {
  // Plain-text format suitable for pasting into an external model.
  if (!sources.length) return `Question: ${query}\n\n(no sources retrieved)`;
  const blocks = sources.map((s, i) => {
    const head = `===== SOURCE ${i + 1}: ${s.name} =====`;
    const passages = (s.passages || []).map(p => {
      const hdr = p.section_title ? `[§ ${p.section_title}] ` : '';
      return hdr + (p.text || '');
    }).join('\n\n');
    const facts = (s.facts && s.facts.length)
      ? '\n\nKey facts from this source:\n' + s.facts.map(f => `- ${f}`).join('\n')
      : '';
    return `${head}\n${passages}${facts}`;
  });
  return `Question: ${query}\n\n${blocks.join('\n\n')}`;
}

// "Copy all new" format — sources first, prompt last, with clear splitters.
function formatNewBulkText(query, sources) {
  const SEP_END = '\n\n================ END OF SOURCES ================\n';
  const SEP_PR  = '\n================ USER PROMPT BELOW ================\n\n';
  const promptBlock = `${SEP_END}${SEP_PR}${query}\n`;
  if (!sources.length) return `(no new sources retrieved)${promptBlock}`;
  const blocks = sources.map((s, i) => {
    const head = `===== SOURCE ${i + 1}: ${s.name} =====`;
    const passages = (s.passages || []).map(p => {
      const hdr = p.section_title ? `[§ ${p.section_title}] ` : '';
      return hdr + (p.text || '');
    }).join('\n\n');
    const facts = (s.facts && s.facts.length)
      ? '\n\nKey facts from this source:\n' + s.facts.map(f => `- ${f}`).join('\n')
      : '';
    return `${head}\n${passages}${facts}`;
  });
  return `${blocks.join('\n\n')}${promptBlock}`;
}

function escapeHTML(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function formatOneSource(query, src) {
  const passages = (src.passages || []).map(p => {
    const hdr = p.section_title ? `[§ ${p.section_title}] ` : '';
    return hdr + (p.text || '');
  }).join('\n\n');
  const facts = (src.facts && src.facts.length)
    ? '\n\nKey facts from this source:\n' + src.facts.map(f => `- ${f}`).join('\n')
    : '';
  return `Question: ${query}\n\n===== SOURCE: ${src.name} =====\n${passages}${facts}`;
}

// Cell *body* (visible in the bento card) = source-level video_summary or first
// passage snippet if no summary exists. The full source block (passages + facts)
// is what lands on the clipboard when the user clicks the card. This separation
// was the user's explicit ask: cells should be readable at a glance, not walls
// of transcript text.
function summaryForCell(src) {
  if (src.video_summary && src.video_summary.trim()) return src.video_summary.trim();
  // Fallback: first passage's first ~200 chars
  const firstPassage = (src.passages && src.passages[0] && src.passages[0].text) || '';
  if (firstPassage) return firstPassage.slice(0, 220) + (firstPassage.length > 220 ? '…' : '');
  // Last resort: a fact
  const firstFact = (src.facts && src.facts[0]) || '';
  return firstFact || '(no summary)';
}

// Clipboard payload for a single source card. Drops the "Question: ..." prefix
// from formatOneSource (the question is its own cell). Includes passages + facts
// + the video_summary header.
function clipboardForSource(query, src) {
  const summaryHeader = src.video_summary
    ? `# ${src.title || src.name}\n\n${src.video_summary}\n\n`
    : `# ${src.title || src.name}\n\n`;
  const passages = (src.passages || []).map(p => {
    const hdr = p.section_title ? `[§ ${p.section_title}] ` : '';
    return hdr + (p.text || '');
  }).join('\n\n');
  const facts = (src.facts && src.facts.length)
    ? '\n\nKey facts from this source:\n' + src.facts.map(f => `- ${f}`).join('\n')
    : '';
  const url = src.url ? `\n\n[Source: ${src.url}]` : '';
  return `${summaryHeader}===== SOURCE: ${src.name} =====\n${passages}${facts}${url}`;
}

function prettifySource(name) {
  return name
    .replace(/\.txt$/i, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase());
}

function shortenSource(name) {
  return prettifySource(name).slice(0, 36);
}

// Warm palette — red → orange → amber → sand → rose. Same hue family as the
// app accent, with stepped value/saturation so adjacent groups remain
// distinguishable without fighting the theme.
const QUERY_COLORS = ['#e16352', '#e8943a', '#d4b34a', '#c08560', '#a55b48'];

// Color each source card by its PRIMARY sub-query — a sub-pixel outline that
// doesn't affect layout (box-shadow inset). When a source matched multiple
// sub-queries, we just use the primary (largest-contribution) color. Cards
// from a single-query result get no outline (no useful signal to convey).
function applyCardColors(cards, sources, subQueries) {
  const multi = subQueries && subQueries.length > 1;
  cards.forEach(({ card }, i) => {
    const src = sources[i];
    if (!multi || !src) {
      card.style.boxShadow = '';
      return;
    }
    const qi = src.primary_query_idx ?? 0;
    const color = QUERY_COLORS[qi % QUERY_COLORS.length];
    // 1.5px inset border — no layout impact.
    card.style.boxShadow = `inset 0 0 0 1.5px ${color}aa`;
  });
}

function buildCopyTurn(query, data) {
  const sources = data.sources || [];
  const subQueries = data.sub_queries || [];
  const newSources = sources.filter(s => !copySeen.has(s.name));
  newSources.forEach(s => copySeen.add(s.name));

  const turn = document.createElement('div');
  turn.className = 'copy-turn';
  // Layout: 3:2 row → new panel (larger) | all panel. Each has a count
  // header, a bento grid, and a "Copy all" button. A translucent backdrop sits
  // behind the entire pair.
  turn.innerHTML = `
    <div class="copy-backdrop"></div>
    <div class="copy-col new-panel">
      <div class="copy-count">
        New sources <span class="count-num">${newSources.length}</span>
      </div>
      <div class="bento-grid new-grid"></div>
      <button class="copy-all-btn new-copy-all">Copy all new</button>
    </div>
    <div class="query-strip"></div>
    <div class="copy-col all-panel">
      <div class="copy-count">
        All sources <span class="count-num">${sources.length}</span>
      </div>
      <div class="bento-grid all-grid"></div>
      <button class="copy-all-btn all-copy-all">Copy all</button>
    </div>
  `;
  const newGrid = turn.querySelector('.new-grid');
  const allGrid = turn.querySelector('.all-grid');
  const queryStrip = turn.querySelector('.query-strip');
  const newCopyAll = turn.querySelector('.new-copy-all');
  const allCopyAll = turn.querySelector('.all-copy-all');

  // Build the sub-query strip — DECOMP header + one colored card per
  // sub-query. Hidden completely when there's only one sub-query.
  if (subQueries.length > 1) {
    const header = document.createElement('div');
    header.className = 'qs-header';
    header.textContent = 'DECOMP';
    queryStrip.appendChild(header);
    subQueries.forEach((sq, qi) => {
      const color = QUERY_COLORS[qi % QUERY_COLORS.length];
      const card = document.createElement('div');
      card.className = 'qs-card';
      card.style.borderColor = `${color}55`;
      card.innerHTML = `
        <div class="qs-tag" style="background:${color}cc;color:#0d0d0d">${qi + 1}</div>
        <div class="qs-text" style="color:${color}d0"></div>
      `;
      card.querySelector('.qs-text').textContent = sq;
      queryStrip.appendChild(card);
    });
  } else {
    queryStrip.style.display = 'none';
  }

  // Bulk text for the copy-all buttons. Left button: sources first, then a
  // separator, then the user prompt (helps a downstream model treat the user
  // turn as the latest message rather than top-of-system context).
  const newBulkText = formatNewBulkText(query, newSources);
  const allBulkText = formatTurnText(query, sources);
  const wireCopyAll = (btn, text) => btn.addEventListener('click', () => {
    copyText(text);
    btn.classList.add('copied');
    const orig = btn.textContent;
    btn.textContent = 'Copied ✓';
    setTimeout(() => { btn.classList.remove('copied'); btn.textContent = orig; }, 1400);
  });
  wireCopyAll(newCopyAll, newBulkText);
  wireCopyAll(allCopyAll, allBulkText);

  // Build cards for each panel. The ALL grid prepends a 3×3 user-prompt card.
  const makeCard = (title, body, fullCopy, extraClass = '') => {
    const card = document.createElement('div');
    card.className = 'bento-card' + (extraClass ? ' ' + extraClass : '');
    card.innerHTML = `
      <div class="bc-head" title="${escapeHTML(title)}">${escapeHTML(title)}</div>
      <div class="bc-body"></div>`;
    const bodyEl = card.querySelector('.bc-body');
    bodyEl.textContent = body;
    card.addEventListener('click', () => {
      copyText(fullCopy);
      card.classList.add('copied');
      setTimeout(() => card.classList.remove('copied'), 1200);
    });
    return { card, body: bodyEl };
  };

  // New panel: just source cards (no prompt cell)
  const newCards = [];
  if (newSources.length === 0) {
    newGrid.innerHTML = '<div class="bc-empty">— no new sources —</div>';
    newGrid.style.gridTemplateColumns = '1fr';
    newGrid.style.gridTemplateRows = '1fr';
    // ADR 0004 D4a: nothing to copy → hide the bulk button entirely so it
    // doesn't read as an actionable affordance on the empty state.
    newCopyAll.style.display = 'none';
  } else {
    newSources.forEach(s => {
      const c = makeCard(shortenSource(s.name), summaryForCell(s),
                         clipboardForSource(query, s));
      newGrid.appendChild(c.card);
      newCards.push(c);
    });
  }

  // All panel: 3×3 user-prompt cell + source cards
  const allCards = [];
  const promptCard = makeCard('question', query, query, 'prompt-cell');
  allGrid.appendChild(promptCard.card);
  sources.forEach(s => {
    const c = makeCard(shortenSource(s.name), formatOneSource(query, s),
                       formatOneSource(query, s));
    allGrid.appendChild(c.card);
    allCards.push(c);
  });

  copyStage.appendChild(turn);
  copyEmpty.classList.add('hidden');

  copyTurns.push({
    el: turn, query, sources, newSources,
    newGrid, allGrid, queryStrip, newCards, allCards, promptCard, subQueries,
  });
  // Strip text refits when the column resizes. Overlays don't need this —
  // they're grid children and resize natively.
  if (subQueries.length > 1 && 'ResizeObserver' in window) {
    const ro = new ResizeObserver(() => fitQueryStrip(queryStrip));
    ro.observe(queryStrip);
  }

  copyFocus = copyTurns.length - 1;
  layoutCopyStack();
}

// Dynamic font-fit for the query-strip text — defensive shrink for very
// long sub-queries that would still overflow the (now 140 px) strip card.
// ADR 0004 D2: floor raised from 7 → 9 px since the strip is wide enough
// that normal queries shouldn't trip the shrink at all.
function fitQueryStrip(strip) {
  if (!strip || strip.style.display === 'none') return;
  const cards = strip.querySelectorAll('.qs-card .qs-text');
  cards.forEach(t => {
    let fs = 12;
    t.style.fontSize = fs + 'px';
    while (fs > 9 && t.scrollWidth > t.clientWidth) {
      fs -= 0.5;
      t.style.fontSize = fs + 'px';
    }
  });
}

function layoutBentoFor(turn) {
  // Compute the bento's available area from the COLUMN (not the grid), since
  // the grid is no longer flex:1. We subtract the source-count header and the
  // copy-all button heights plus a small min-gap allowance so space-evenly on
  // the col gives breathing room without huge empty bands.
  const MIN_GAP = 20;   // minimum visible space between header / bento / button
  const PAD_V   = 12;   // .copy-col vertical padding (6 + 6)
  const PAD_H   = 16;   // .copy-col horizontal padding (8 + 8)
  const GAP     = 6;    // gap inside the bento

  function applyPanel(col, grid, cards, sources, reserved) {
    if (!col || !grid) return;
    const hasPrompt = !!reserved;
    if (!sources.length && !hasPrompt) {
      // Empty new-panel — render a placeholder cell that fits the col
      grid.style.width  = '';
      grid.style.height = '';
      grid.style.gridTemplateColumns = '1fr';
      grid.style.gridTemplateRows = '1fr';
      col.style.overflowY = '';
      return;
    }
    // ADR 0005 D3: read offsetWidth/offsetHeight, NOT
    // getBoundingClientRect(). offset* returns the layout box and is
    // immune to the .copy-turn carousel's transform; the prior code
    // sampled rect mid-transition and produced shrunken cells until a
    // browser zoom refired the ResizeObserver.
    const headerEl = col.querySelector('.copy-count');
    const btnEl    = col.querySelector('.copy-all-btn');
    const headerH  = headerEl ? headerEl.offsetHeight : 0;
    const btnH     = btnEl    ? btnEl.offsetHeight    : 0;

    const availW = col.offsetWidth  - PAD_H;
    // 4 gaps with space-evenly: above-header / header-bento / bento-button /
    // below-button — reserve MIN_GAP for each so the bento doesn't crowd them.
    const availH = col.offsetHeight - PAD_V - headerH - btnH - 4 * MIN_GAP;
    if (availW < 50 || availH < 50) return;

    const lens = sources.map(s =>
      (s.passages || []).reduce((a, p) => a + (p.text || '').length, 0) +
      (s.facts || []).reduce((a, f) => a + (f || '').length, 0));
    const spec = bentoPackSquare(lens.length, lens, availW, availH, reserved);
    if (!spec) return;

    let cellSize = Math.floor(Math.min(
      (availW - GAP * (spec.W - 1)) / spec.W,
      (availH - GAP * (spec.H - 1)) / spec.H
    ));
    // ADR 0005 D7: when the source count would force unreadable cells,
    // floor cellSize at 60 px and let the col scroll vertically instead
    // of spilling out the bottom of the panel.
    const MIN_CELL = 60;
    const overflowing = cellSize < MIN_CELL;
    if (overflowing) cellSize = MIN_CELL;
    col.style.overflowY = overflowing ? 'auto' : '';
    grid.style.gridTemplateColumns = `repeat(${spec.W}, ${cellSize}px)`;
    grid.style.gridTemplateRows    = `repeat(${spec.H}, ${cellSize}px)`;
    grid.style.width  = `${spec.W * cellSize + GAP * (spec.W - 1)}px`;
    grid.style.height = `${spec.H * cellSize + GAP * (spec.H - 1)}px`;

    if (hasPrompt) {
      turn.promptCard.card.style.display = '';
      turn.promptCard.card.style.gridColumn = `1 / span ${reserved.w}`;
      turn.promptCard.card.style.gridRow    = `1 / span ${reserved.h}`;
    }
    cards.forEach(({ card }, i) => {
      const p = spec.placements[i];
      if (!p) { card.style.display = 'none'; return; }
      card.style.display = '';
      card.style.gridColumn = `${p.c + 1} / span ${p.w}`;
      card.style.gridRow    = `${p.r + 1} / span ${p.h}`;
    });
    // Stash the placements on the grid so overlay draws can re-use them
    // without re-running the bento packer.
    grid._lastPlacements = spec.placements;
    requestAnimationFrame(() => {
      // ADR 0005 D6: bento body text shrinks one step; floor lowered to
      // match the smaller 7 px starting size.
      cards.forEach(({ body }) => fitTextToBox(body, 8, 3));
      if (hasPrompt) fitTextToBox(turn.promptCard.body, 13, 6);
    });
  }

  const newCol = turn.el.querySelector('.copy-col.new-panel');
  const allCol = turn.el.querySelector('.copy-col.all-panel');
  applyPanel(newCol, turn.newGrid, turn.newCards, turn.newSources, null);
  applyPanel(allCol, turn.allGrid, turn.allCards, turn.sources, { r: 0, c: 0, w: 2, h: 2 });

  // Color cards by primary sub-query (no overlay layer needed).
  applyCardColors(turn.newCards, turn.newSources, turn.subQueries);
  applyCardColors(turn.allCards, turn.sources,    turn.subQueries);
  fitQueryStrip(turn.queryStrip);
}

function layoutCopyStack() {
  // Position each turn relative to the focused one.
  // Older turns (lower index) are stacked ABOVE the focused one visually,
  // newer turns (higher index) BELOW — matches "new messages push old upwards".
  // Arrows: ▲ moves focus toward newer (down the index, visually up the stack
  // = bring the older-but-still-visible one down), ▼ toward older. Wait —
  // per the user, ▲ shows the panel above (older); ▼ shows the one below.
  copyTurns.forEach((t, i) => {
    const d = i - copyFocus;       // -N (older, above) .. +N (newer, below)
    const isFocused = d === 0;
    t.el.classList.toggle('off', !isFocused);
    if (isFocused) {
      t.el.style.transform = 'translate(-50%, -50%)';
      t.el.style.opacity = '1';
      t.el.style.zIndex = '5';
    } else {
      // d<0 (older) → ABOVE: yPct negative
      // d>0 (newer) → BELOW: yPct positive
      const offset = Math.min(Math.abs(d), 4);
      const dir = d < 0 ? -1 : 1;
      const yPct = -50 + dir * (32 + (offset - 1) * 14);
      const scale = Math.max(0.4, 1 - 0.15 * offset);
      t.el.style.transform = `translate(-50%, ${yPct}%) scale(${scale})`;
      t.el.style.opacity = String(Math.max(0, 0.55 - 0.12 * (offset - 1)));
      t.el.style.zIndex  = String(2 - offset);
    }
  });
  // Re-layout the focused turn's bento (cards + text-fit). Double rAF so the
  // grid's clientRect is settled after a freshly-appended turn.
  const f = copyTurns[copyFocus];
  if (f) {
    requestAnimationFrame(() => requestAnimationFrame(() => layoutBentoFor(f)));
  }
  // Arrow state + position label.
  //   ▲ goes to OLDER (lower index)  — disabled when at index 0
  //   ▼ goes to NEWER (higher index) — disabled at last index
  // The previous wiring had ▲ = focus-- (older) which the user said is "flipped".
  // Now: ▲ moves up the visual stack → toward the OLDER panels = focus--, which
  // makes the panel above slide down into focus. This matches "arrows reflect
  // direction the focused panel travels" when you press them.
  copyUp.disabled   = copyFocus <= 0;
  copyDown.disabled = copyFocus >= copyTurns.length - 1;
  copyPos.textContent = copyTurns.length
    ? `${copyFocus + 1} / ${copyTurns.length}` : '—';
}

window.addEventListener('resize', () => {
  if (copyPanel.classList.contains('show')) layoutCopyStack();
  if (chatPanel.classList.contains('show')) centerChatToViewport();
});

async function sendCopy(opts = {}) {
  const { refocus = true } = opts;
  const q = copyInput.value.trim();
  if (!q || copyBusy) return;
  copyBusy = true;
  copySend.disabled = true;
  copyInput.value = '';
  copyAutoGrow();
  copyLoading.classList.add('show');
  try {
    const res = await fetch('/api/rag', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q }),
    });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({error: `HTTP ${res.status}`}));
      buildCopyTurn(q, { sources: [{ name: 'error', passages: [{section_title:'', text: errBody.error || 'failed'}], facts: [] }] });
    } else {
      const data = await res.json();
      buildCopyTurn(q, data);
    }
  } catch (err) {
    buildCopyTurn(q, { sources: [{ name: 'error', passages: [{section_title:'', text: 'Could not reach the knowledge server. Is `serve.sh` running?'}], facts: [] }] });
  } finally {
    copyLoading.classList.remove('show');
    copyBusy = false;
    copySend.disabled = false;
    if (refocus) copyInput.focus();
  }
}

// ─── Boot ──────────────────────────────────────────────────────────────────

(async function init() {
  const data = await loadGraph();
  if (!data || !Array.isArray(data.nodes) || data.nodes.length === 0) {
    emptyState.classList.add('show');
    return;
  }

  pluginColors = buildPluginColors(data.nodes);
  buildSourceLabels(data.nodes);
  renderLegend();

  nodes = data.nodes.map(n => {
    const node = { ...n };
    if (Array.isArray(n.pos)) {
      node.x = n.pos[0];
      node.y = n.pos[1];
    }
    return node;
  });
  links = data.links.map(l => ({ ...l }));

  computeDegrees();
  computeKeystoneLinks();
  // Honor ?view= for quick deep-linking (and for headless screenshot testing).
  // ?cq=<query> auto-submits a copy turn on load (also for screenshot tests).
  const _params = new URLSearchParams(location.search);
  const _initialView = _params.get('view');
  setView(_initialView === 'chat' || _initialView === 'copy' ? _initialView : 'graph');
  const _cq = _params.get('cq');
  if (_cq && _initialView === 'copy') {
    setTimeout(() => {
      copyInput.value = _cq;
      sendCopy({ refocus: false });
    }, 300);
  }
  requestAnimationFrame(() => {
    // ── Intro animation ────────────────────────────────────────────────
    // Boot with EXTREME repulsion + edge-spread and zero gravity so the
    // graph fans out from its PCA seeds, then ease all three sliders back
    // to their resting 0.50 over 3 s on a smoothstep curve. Two centering
    // passes — one immediately so the PCA layout is fit to the viewport,
    // and one at the end so the relaxed layout fills it too.
    const INTRO_DURATION = 3000;
    const INTRO_START = { repulsion: 100, edgeRepel: 100, gravity:  0 };
    const INTRO_END   = { repulsion:  50, edgeRepel:  50, gravity: 50 };

    const setSliderUI = (k, sliderVal) => {
      const el = document.getElementById(k);
      if (el) el.value = String(Math.round(sliderVal));
      updateCfgFromSlider(k, sliderVal);  // smooth (un-rounded) cfg
    };
    const applyForces = () => {
      if (!sim) return;
      const ch = sim.force('charge');    if (ch) ch.strength(-cfg.repulsion);
      const gx = sim.force('gravityX');  if (gx) gx.strength(cfg.gravity / 1000);
      const gy = sim.force('gravityY');  if (gy) gy.strength(cfg.gravity / 1000);
      const er = sim.force('edgeRepel'); if (er) er.strength(cfg.edgeRepel);
    };

    // Seed sliders to the intro-start values BEFORE the sim is built so
    // the initial force config is the "extreme" one — no restart needed.
    for (const k in INTRO_START) setSliderUI(k, INTRO_START[k]);
    rebuildSim();
    // First center: a short delay lets the high-repulsion sim fan the
    // tightly packed PCA seeds apart before we fit the viewport, so we
    // zoom to the expanded layout instead of the original seed cluster.
    setTimeout(centerView, 350);

    const smoothstep = p => p * p * (3 - 2 * p);
    const t0 = performance.now();
    function step(now) {
      const p = Math.min((now - t0) / INTRO_DURATION, 1);
      const e = smoothstep(p);
      for (const k in INTRO_START) {
        const v = INTRO_START[k] + (INTRO_END[k] - INTRO_START[k]) * e;
        setSliderUI(k, v);
      }
      applyForces();
      if (p < 1) {
        requestAnimationFrame(step);
      } else {
        // Snap to exact end values so the slider thumbs land at a clean 50.
        for (const k in INTRO_END) setSliderUI(k, INTRO_END[k]);
        applyForces();
        centerView();   // second center: fits the relaxed layout
      }
    }
    requestAnimationFrame(step);
  });
})();
