import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
} from 'https://cdn.jsdelivr.net/npm/d3-force@3/+esm';

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
const summaryWrap        = document.getElementById('detail-summary-wrap');
const detailSummary      = document.getElementById('detail-summary');
const summaryResizeHandle= document.getElementById('summary-resize-handle');
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
let pluginColors = {}; // plugin name -> hex

// ─── Data load ──────────────────────────────────────────────────────────────

const PALETTE = ['#5fa8d3', '#e0a458', '#73c990', '#d36f8f', '#c3a6e0', '#5bc8c8', '#d3b85f'];
const STANDALONE_COLOR = '#4a3f9f';
const STANDALONE_HUB = '#7c6af7';

async function loadGraph() {
  const candidates = ['./graph.json'];
  for (const url of candidates) {
    try {
      const res = await fetch(url);
      if (res.ok) return await res.json();
    } catch {
      /* try next */
    }
  }
  return null;
}

function buildPluginColors(rawNodes) {
  const plugins = [...new Set(rawNodes.map(n => n.plugin).filter(Boolean))].sort();
  const map = {};
  plugins.forEach((p, i) => { map[p] = PALETTE[i % PALETTE.length]; });
  return map;
}

function renderLegend() {
  legendEl.querySelectorAll('.legend-row').forEach(el => el.remove());
  const entries = Object.entries(pluginColors);
  if (entries.length === 0) {
    legendEl.style.display = 'none';
    return;
  }
  legendEl.style.display = 'flex';
  for (const [name, color] of entries) {
    const row = document.createElement('div');
    row.className = 'legend-row';
    // Trim .txt extension for display
    const label = name.replace(/\.txt$/, '');
    row.innerHTML = `<span class="legend-swatch" style="background:${color}"></span>${label}`;
    legendEl.appendChild(row);
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

function nodeColor(n) {
  return n.plugin ? pluginColors[n.plugin] : STANDALONE_COLOR;
}

function nodeRadius(d) {
  return cfg.nodeSize + Math.sqrt(d.degree || 0) * 1.8;
}

function screenToWorld(sx, sy) {
  return [(sx - W / 2 - tx) / scale, (sy - H / 2 - ty) / scale];
}

function hitTest(sx, sy) {
  const [wx, wy] = screenToWorld(sx, sy);
  for (let i = nodes.length - 1; i >= 0; i--) {
    const n = nodes[i];
    if (n.x == null) continue;
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

let cfg = { repulsion: 300, linkDist: 90, momentum: 8, nodeSize: 5 };

['repulsion', 'linkDist', 'momentum', 'nodeSize'].forEach(k => {
  document.getElementById(k).addEventListener('input', e => {
    cfg[k] = +e.target.value;
    rebuildSim();
  });
});

// ─── Simulation ──────────────────────────────────────────────────────────────

let sim;

function rebuildSim() {
  if (sim) sim.stop();
  const alphaDecay = 0.0228 * (21 - cfg.momentum) / 20;
  sim = forceSimulation(nodes)
    .force('link', forceLink(links)
      .id(d => d.id)
      .distance(l => cfg.linkDist * (1.6 - weightT(l.weight)))
      .strength(l => 0.2 + 0.7 * weightT(l.weight)))
    .force('charge', forceManyBody().strength(-cfg.repulsion))
    .force('center', forceCenter(0, 0).strength(0.05))
    .force('collide', forceCollide().radius(d => nodeRadius(d) + 4))
    .alphaDecay(alphaDecay)
    .velocityDecay(0.4)
    .on('tick', draw);
  buildNodeList();
}

// ─── Right panel ──────────────────────────────────────────────────────────────

function buildNodeList() {
  const sorted = [...nodes].sort((a, b) => a.id.localeCompare(b.id));
  const scrollTop = listCol.scrollTop;

  listCol.querySelectorAll('.node-item').forEach(el => el.remove());

  sorted.forEach(n => {
    const item = document.createElement('div');
    item.className = 'node-item' + (n === selectedNode ? ' active' : '');
    item.innerHTML = `<span class="node-item-name">${n.id}</span><span class="node-item-degree">${n.degree ?? 0}</span>`;
    item.addEventListener('click', () => selectNode(n, true));
    n._listEl = item;
    listCol.appendChild(item);
  });

  listCol.scrollTop = scrollTop;
}

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

  listCol.querySelectorAll('.node-item').forEach(el => el.classList.remove('active'));
  if (n._listEl) {
    n._listEl.classList.add('active');
    n._listEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  const deg = n.degree ?? 0;
  const home = n.plugin ? n.plugin.replace(/\.txt$/, '') : 'source';

  detailCol.classList.add('open');
  detailTitle.textContent = n.id;
  detailMeta.textContent = `${home} · ${deg} connection${deg !== 1 ? 's' : ''}`;
  detailDesc.textContent = n.description || 'No description available.';

  if (n.summary) {
    summaryWrap.style.display = 'block';
    detailSummary.textContent = n.summary;
    resetSummaryHeight();
  } else {
    summaryWrap.style.display = 'none';
  }

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
  text: '#c8c4f0',
  textDim: '#5a5580',
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
    const isSelEdge = selectedNode && (s === selectedNode || t === selectedNode);
    const isHoverEdge = hoveredNode && (s === hoveredNode || t === hoveredNode);
    const ti = weightT(l.weight);
    const baseAlpha = 0.12 + 0.33 * ti;
    const baseWidth = (0.7 + 1.8 * ti);

    ctx.beginPath();
    ctx.moveTo(s.x, s.y);
    ctx.lineTo(t.x, t.y);
    if (isSelEdge) {
      ctx.strokeStyle = `rgba(180,165,255,${Math.min(0.8, baseAlpha + 0.4)})`;
      ctx.lineWidth = (baseWidth + 0.8) / scale;
    } else if (isHoverEdge) {
      ctx.strokeStyle = `rgba(170,160,255,${Math.min(0.7, baseAlpha + 0.3)})`;
      ctx.lineWidth = (baseWidth + 0.5) / scale;
    } else {
      ctx.strokeStyle = `rgba(120,110,200,${baseAlpha})`;
      ctx.lineWidth = baseWidth / scale;
    }
    ctx.stroke();
  });

  nodes.forEach(n => {
    if (n.x == null) return;
    const r = nodeRadius(n);
    const isHover = n === hoveredNode;
    const isDrag = n === dragNode;
    const isSelected = n === selectedNode;
    const base = nodeColor(n);

    if (isSelected || isHover || isDrag || n.degree > 4) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + 7, 0, Math.PI * 2);
      const grad = ctx.createRadialGradient(n.x, n.y, r * 0.4, n.x, n.y, r + 7);
      const glowCol = isSelected ? 'rgba(220,210,255,0.45)' : hexToRgba(base, isHover ? 0.4 : 0.3);
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
    } else if (!n.plugin && n.degree > 4) {
      ctx.fillStyle = STANDALONE_HUB;
    } else {
      ctx.fillStyle = base;
    }
    ctx.fill();

    if (isSelected) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + 3.5, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(220,210,255,0.6)';
      ctx.lineWidth = 1.5 / scale;
      ctx.stroke();
    }

    const showLabel = isSelected || isHover || isDrag || scale > 0.9;
    if (showLabel) {
      ctx.font = `${(isSelected || isHover) ? 600 : 400} ${12 / scale}px -apple-system, sans-serif`;
      ctx.fillStyle = isSelected ? '#d8d0ff' : (isHover ? COLORS.text : COLORS.textDim);
      ctx.textAlign = 'center';
      ctx.fillText(n.id, n.x, n.y - r - 6 / scale);
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
  scale = Math.max(0.1, Math.min(scale, 5));
  draw();
}, { passive: false });

// ─── Summary resize handle ───────────────────────────────────────────────────

const SUMMARY_MIN_H = 108;

summaryResizeHandle.addEventListener('mousedown', e => {
  e.preventDefault();
  const startY = e.clientY;
  const startH = detailSummary.offsetHeight;

  function onMove(ev) {
    const h = Math.max(SUMMARY_MIN_H, startH + (ev.clientY - startY));
    detailSummary.style.maxHeight = h + 'px';
  }
  function onUp() {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
  }
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
});

function resetSummaryHeight() {
  detailSummary.style.maxHeight = SUMMARY_MIN_H + 'px';
}

// ─── Boot ──────────────────────────────────────────────────────────────────

(async function init() {
  const data = await loadGraph();
  if (!data || !Array.isArray(data.nodes) || data.nodes.length === 0) {
    emptyState.classList.add('show');
    return;
  }

  pluginColors = buildPluginColors(data.nodes);
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
  requestAnimationFrame(rebuildSim);
})();
