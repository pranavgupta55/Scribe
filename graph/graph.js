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

const PALETTE = [
  '#5fa8d3', '#e0a458', '#73c990', '#d36f8f', '#c3a6e0', '#5bc8c8', '#d3b85f',
  '#e07070', '#7ec8a0', '#d4a0d8', '#63b8e8', '#e8b85a', '#88d8b0', '#e898b0',
  '#8ab4f8', '#f0c070',
];
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

function nodeColor(n) {
  return n.plugin ? pluginColors[n.plugin] : STANDALONE_COLOR;
}

function nodeRadius(d) {
  return cfg.nodeSize + Math.sqrt(d.degree || 0) * 0.7;
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

let cfg = { repulsion: 400, linkDist: 120, momentum: 8, nodeSize: 4 };

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
      ctx.font = `${(isSelected || isHover) ? 600 : 400} ${9 / scale}px -apple-system, sans-serif`;
      ctx.fillStyle = isSelected ? '#d8d0ff' : (isHover ? COLORS.text : COLORS.textDim);
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
const RE_BULLET  = /^\s*[-*]\s+/;
const RE_HEADING = /^\s*(#{1,6})\s+(.*)$/;

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

    // Unordered list: every non-empty line is a bullet
    if (nonEmpty.every(l => RE_BULLET.test(l))) {
      const items = nonEmpty
        .map(l => `<li>${renderInline(l.replace(RE_BULLET, ''))}</li>`)
        .join('');
      out.push(`<ul>${items}</ul>`);
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
  rawNodes
    .filter(n => n.plugin == null && /\.txt$/i.test(n.id))
    .forEach(n => {
      let label = shortLabelFor(n.id);
      let candidate = label, k = 2;
      while (used.has(candidate.toLowerCase())) candidate = `${label} ${k++}`;
      used.add(candidate.toLowerCase());
      sourceLabels[n.id] = candidate;
    });

  const names = Object.keys(sourceLabels)
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
const chatPanel   = document.getElementById('chat-panel');
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
  const chat = view === 'chat';
  viewToggle.classList.toggle('chat', chat);
  btnGraph.classList.toggle('active', !chat);
  btnChat.classList.toggle('active', chat);
  chatPanel.classList.toggle('show', chat);
  // Graph-only chrome hides in chat mode
  [canvas, controlsEl, centerBtn, hintEl].forEach(el => {
    if (el) el.style.display = chat ? 'none' : '';
  });
  if (chat) {
    chatInput.focus();
    startStatusPoll();
  } else {
    stopStatusPoll();
  }
}

btnGraph.addEventListener('click', () => setView('graph'));
btnChat.addEventListener('click', () => setView('chat'));

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
  });
}

function updateStatusPill(status) {
  if (!statusDot || !statusLabel) return;
  if (!status) {
    statusDot.className = 'status-dot grey';
    statusLabel.textContent = 'server offline';
    return;
  }
  if (status.gemini_ok) {
    statusDot.className = 'status-dot green';
    statusLabel.textContent = 'Gemini';
  } else {
    statusDot.className = 'status-dot amber';
    let txt = 'Local (qwen)';
    if (status.in_cooldown) {
      if (status.retry_known && status.cooldown_remaining != null) {
        txt += ` · retry in ${status.cooldown_remaining}s`;
      } else {
        txt += ' · retry time unknown';
      }
    }
    statusLabel.textContent = txt;
  }
}

// Also update from per-turn backend event
function updateStatusFromBackend(backend) {
  if (!statusDot || !statusLabel) return;
  if (backend === 'gemini') {
    statusDot.className = 'status-dot green';
    statusLabel.textContent = 'Gemini';
  } else if (backend === 'qwen') {
    statusDot.className = 'status-dot amber';
    // Don't overwrite cooldown info — re-poll will fix it shortly
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

function setDevField(el, text) {
  if (!el) return;
  if (text == null || text === '') {
    el.innerHTML = '<span class="dev-empty">— empty —</span>';
  } else {
    el.textContent = text; // monospaced, exact, un-rendered
  }
}

function resetDevPanel() {
  setDevField(devSystem, '');
  setDevField(devContext, '');
  setDevField(devPrompt, '');
  setDevField(devResponse, '');
}

let chatBusy = false;

function selectNodeById(id) {
  const target = id.trim().toLowerCase();
  const node = nodes.find(n => n.id.toLowerCase() === target);
  if (node) selectNode(node, false);
}

function autoGrow() {
  chatInput.style.height = 'auto';
  chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + 'px';
}
chatInput.addEventListener('input', autoGrow);
chatInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
});
chatSend.addEventListener('click', sendChat);

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
    container.innerHTML = `<span style="font-size:11px;color:#4a4560">No matching topics</span>`;
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

async function sendChat() {
  const text = chatInput.value.trim();
  if (!text || chatBusy) return;
  chatBusy = true;
  chatSend.disabled = true;
  chatInput.value = '';
  autoGrow();

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
      body: JSON.stringify({ query: text, gemini_only: geminiOnly }),
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

        if (evt.type === 'nodes') {
          ui.head.classList.remove('searching');
          ui.head.innerHTML = `<span class="dot generating"></span>Consulted ${evt.nodes.length} topic${evt.nodes.length !== 1 ? 's' : ''}`;
          renderNodeChips(ui.nodes, evt.nodes);
          // Phase 3: waiting on the model — keep the Thinking indicator visible.
          if (!gotToken) ui.body.innerHTML = THINKING_HTML;
          scrollToBottom();
        } else if (evt.type === 'debug') {
          // Exact round-trip sent to the model (after retrieval, pre-generation)
          setDevField(devSystem, evt.system);
          setDevField(devContext, evt.context);
          setDevField(devPrompt, evt.prompt);
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
    chatBusy = false;
    chatSend.disabled = false;
    chatInput.focus();
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
  requestAnimationFrame(rebuildSim);
})();
