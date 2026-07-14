#!/usr/bin/env python3
"""Arrow Lake architecture diagrams — 'Midnight Blueprint' design language.

Dark premium theme: deep navy gradient bg, color-coded tier accents, glass node
cards, bold typography. Only gradients + solid fills + bold strokes (no blur/glow
filters) so cairosvg renders faithfully. English labels; Chinese prose lives in
the .md doc.  Run: python3 gen_midnight.py  (then cairosvg to PNG).
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# --- Midnight Blueprint tokens ---
BG_TOP, BG_BOT = "#0b1220", "#0f172a"
PANEL_FILL = "#1e293b"
PANEL_FILL_HI = "#243349"
INK = "#f8fafc"
MUTED = "#94a3b8"
DIM = "#64748b"
GRID = "#1c2740"

CYAN, EMER, AMBER, VIOLET, ROSE, SLATE = (
    "#22d3ee", "#34d399", "#fbbf24", "#a78bfa", "#fb7185", "#60a5fa")
COLORS = {"cyan": CYAN, "emer": EMER, "amber": AMBER,
          "violet": VIOLET, "rose": ROSE, "slate": SLATE}


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def svg_open(W, H):
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
         f'font-family="DejaVu Sans, Arial, sans-serif"><defs>']
    o.append('<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
             f'<stop offset="0" stop-color="{BG_TOP}"/>'
             f'<stop offset="1" stop-color="{BG_BOT}"/></linearGradient>')
    for n, c in COLORS.items():
        o.append(f'<marker id="m-{n}" viewBox="0 0 10 10" refX="9" refY="5" '
                 f'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
                 f'<path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>')
    o.append('</defs>')
    o.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="url(#bg)"/>')
    return '\n'.join(o)


def header(title, sub, accent=CYAN, x=60, yt=60, sub_y=98, rule_w=110):
    o = [f'<text x="{x}" y="{yt}" font-size="26" font-weight="800" '
         f'letter-spacing="1.5" fill="{INK}">{esc(title)}</text>']
    o.append(f'<rect x="{x}" y="{yt+10}" width="{rule_w}" height="3" fill="{accent}"/>')
    o.append(f'<text x="{x}" y="{sub_y}" font-size="12.5" fill="{MUTED}">{esc(sub)}</text>')
    return '\n'.join(o)


def card(x, y, w, h, type_label, title, sub, accent):
    o = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" '
         f'fill="{PANEL_FILL}" stroke="{accent}" stroke-width="1.6"/>']
    o.append(f'<rect x="{x}" y="{y+12}" width="3" height="{h-24}" rx="1.5" fill="{accent}"/>')
    tx = x + 16
    o.append(f'<text x="{tx}" y="{y+22}" font-size="9.5" font-weight="700" '
             f'letter-spacing="1.3" fill="{accent}">{esc(type_label)}</text>')
    o.append(f'<text x="{tx}" y="{y+44}" font-size="14" font-weight="700" '
             f'fill="{INK}">{esc(title)}</text>')
    o.append(f'<text x="{tx}" y="{y+62}" font-size="10.5" fill="{MUTED}">{esc(sub)}</text>')
    return '\n'.join(o)


def arrow(x1, y1, x2, y2, color="cyan", label="", dashed=False, width=2.0,
          lo=8, lw=10):
    d = ' stroke-dasharray="5,3"' if dashed else ''
    o = [f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{COLORS[color]}" '
         f'stroke-width="{width}" stroke-linecap="round"{d} marker-end="url(#m-{color})"/>']
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2 - lo
        o.append(f'<text x="{mx}" y="{my}" font-size="{lw-0}" font-weight="600" '
                 f'fill="{COLORS[color]}" text-anchor="middle">{esc(label)}</text>')
    return '\n'.join(o)


def ortho_path(points, color="slate", dashed=True, width=1.6):
    d = ' stroke-dasharray="5,3"' if dashed else ''
    pts = " L ".join(f"{p[0]},{p[1]}" for p in points)
    return (f'<path d="M {pts}" fill="none" stroke="{COLORS[color]}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"'
            f'{d} marker-end="url(#m-{color})"/>')


def zone(x, y, w, h, label, accent):
    o = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" '
         f'fill="{PANEL_FILL_HI}" fill-opacity="0.22" stroke="{accent}" '
         f'stroke-width="1.2" stroke-dasharray="5,4"/>']
    o.append(f'<rect x="{x}" y="{y}" width="4" height="{h}" fill="{accent}"/>')
    o.append(f'<text x="{x+16}" y="{y+24}" font-size="10" font-weight="700" '
             f'letter-spacing="1.2" fill="{accent}">{esc(label)}</text>')
    return '\n'.join(o)


# ---------------------------------------------------------------------------
# D1 — Layered Architecture (hero)
# ---------------------------------------------------------------------------
def d1_layered():
    W, H = 1280, 820
    L = [svg_open(W, H)]
    # hero header
    L.append(f'<text x="60" y="62" font-size="30" font-weight="800" letter-spacing="2" fill="{INK}">ARROW LAKE</text>')
    L.append(f'<rect x="60" y="74" width="120" height="3" fill="{CYAN}"/>')
    L.append(f'<text x="60" y="100" font-size="13" fill="{CYAN}">v1.8.6 · Multimodal Data Lakehouse</text>')
    L.append(f'<text x="60" y="120" font-size="12" fill="{MUTED}">Layered Architecture — request flow descends five tiers; governance spans all.</text>')

    panel_x, panel_w = 100, 940
    card_xs = [panel_x + 130, panel_x + 410, panel_x + 690]
    card_w, card_h = 240, 78
    y0, lh, gap = 150, 118, 10
    layers = [
        ("01", "ENTRY", CYAN, [("SDK", "Python · Lake facade", "9 mixin"),
                                ("REST", "FastAPI", "106 routes · RBAC"),
                                ("CLI", "arrow-lake", "16 groups")]),
        ("02", "CAPABILITIES", EMER, [("INGEST", "write-in path", "parse · chunk · embed"),
                                       ("QUERY", "read path", "8 bridges · ANN/FTS"),
                                       ("INTEL", "RAG · GraphRAG", "knowledge graph")]),
        ("03", "COMPUTE", AMBER, [("DAFT", "lazy DataFrame", "AI fn · multimodal"),
                                   ("RAY", "distributed", "head · worker · GPU"),
                                   ("EMBED", "vectorize", "bge-m3 · CLIP")]),
        ("04", "ENGINES", VIOLET, [("LANCEDB", "vector · scalar · FTS", "tags · branches"),
                                    ("DUCKDB", "query engine", "lance_scan · vector_search"),
                                    ("DUCKLAKE", "materialized view", "TTL · ART index")]),
        ("05", "PERSISTENCE", ROSE, [("MINIO/S3", "blob raw", "image · video · backup"),
                                      ("REDIS", "hot state", "session · task · JWT"),
                                      ("HUGEGRAPH", "graph store", "Gremlin · Vermeer")]),
    ]
    geom = []
    for i, (num, name, accent, cards) in enumerate(layers):
        py = y0 + i * (lh + gap)
        geom.append((py, accent))
        L.append(f'<rect x="{panel_x}" y="{py}" width="{panel_w}" height="{lh}" rx="12" '
                 f'fill="{PANEL_FILL_HI}" fill-opacity="0.32" stroke="{GRID}" stroke-width="1"/>')
        L.append(f'<rect x="{panel_x}" y="{py}" width="4" height="{lh}" fill="{accent}"/>')
        L.append(f'<text x="{panel_x+22}" y="{py+40}" font-size="22" font-weight="800" fill="{accent}">{num}</text>')
        L.append(f'<text x="{panel_x+22}" y="{py+62}" font-size="11" font-weight="700" letter-spacing="1.2" fill="{INK}">{name}</text>')
        cy = py + (lh - card_h) // 2
        for cx, (t, ti, su) in zip(card_xs, cards):
            L.append(card(cx, cy, card_w, card_h, t, ti, su, accent))

    # request-flow spine
    sx, top_y = 78, y0 + 30
    bot_y = y0 + 5 * (lh + gap) - 30
    L.append(f'<line x1="{sx}" y1="{top_y}" x2="{sx}" y2="{bot_y}" stroke="{CYAN}" stroke-width="2.5" marker-end="url(#m-cyan)"/>')
    for (py, accent) in geom:
        mid = py + lh // 2
        L.append(f'<line x1="{sx}" y1="{mid}" x2="{panel_x-6}" y2="{mid}" stroke="{accent}" stroke-width="1.4" stroke-opacity="0.65"/>')

    # cross-cutting rail
    rx, rw = 1060, 160
    ry0 = y0
    ry1 = y0 + 5 * (lh + gap) - gap
    L.append(f'<rect x="{rx}" y="{ry0}" width="{rw}" height="{ry1-ry0}" rx="12" fill="{PANEL_FILL_HI}" fill-opacity="0.28" stroke="{SLATE}" stroke-width="1" stroke-dasharray="4,4"/>')
    L.append(f'<text x="{rx+rw/2}" y="{ry0+22}" font-size="10" font-weight="700" letter-spacing="1.3" fill="{SLATE}" text-anchor="middle">CROSS-CUTTING</text>')
    cc = [("GRAVITINO", "catalog · tag->ACL"), ("OBSERVABILITY", "logs · metrics"), ("SECURITY", "auth · RBAC · audit")]
    ch, inner = 92, (ry1 - ry0) - 40
    sp = (inner - 3 * ch) / 2
    for i, (t, s) in enumerate(cc):
        cyy = ry0 + 40 + i * (ch + sp)
        L.append(f'<rect x="{rx+12}" y="{cyy}" width="{rw-24}" height="{ch}" rx="9" fill="{PANEL_FILL}" stroke="{SLATE}" stroke-width="1.3"/>')
        L.append(f'<text x="{rx+rw/2}" y="{cyy+36}" font-size="11" font-weight="700" fill="{INK}" text-anchor="middle">{esc(t)}</text>')
        L.append(f'<text x="{rx+rw/2}" y="{cyy+60}" font-size="9.5" fill="{MUTED}" text-anchor="middle">{esc(s)}</text>')
    for idx in (1, 3):
        mid = geom[idx][0] + lh // 2
        L.append(f'<line x1="{rx}" y1="{mid}" x2="{panel_x+panel_w+6}" y2="{mid}" stroke="{SLATE}" stroke-width="1.2" stroke-dasharray="3,3" stroke-opacity="0.6"/>')

    L.append(f'<text x="60" y="{H-24}" font-size="11" fill="{DIM}">spine = main call chain  ·  dashed = cross-cutting spans  ·  each tier color-coded</text>')
    L.append('</svg>')
    return '\n'.join(L)


# ---------------------------------------------------------------------------
# D2 — Ingestion Pipeline
# ---------------------------------------------------------------------------
def d2_ingest():
    W, H = 1320, 430
    L = [svg_open(W, H),
         header("INGESTION",
                "Source -> Parse -> Chunk -> Embed -> Quality -> Lance + Blob", AMBER)]
    stages = [("SOURCES", "Documents / Media", "PDF · IMG · AV · Kafka", EMER),
              ("PARSE", "Docling / OCR", "layout · tables", AMBER),
              ("CHUNK", "7 strategies", "semantic · hybrid", AMBER),
              ("EMBED", "bge-m3 / CLIP", "Daft batch", VIOLET),
              ("QUALITY", "3-stage gate", "filter · dedup", ROSE),
              ("STORAGE", "Lance + MinIO", "vectors · blob", SLATE)]
    cw, ch, gap, y = 180, 110, 26, 185
    xs = [40 + i * (cw + gap) for i in range(6)]
    for x, (t, ti, su, ac) in zip(xs, stages):
        L.append(card(x, y, cw, ch, t, ti, su, ac))
    for i, lab in enumerate(["raw", "tokens", "chunks", "vectors", "clean"]):
        L.append(arrow(xs[i] + cw, y + ch // 2, xs[i + 1] - 4, y + ch // 2,
                       color="cyan", label=lab, width=2.4))
    bar_y = y + ch + 38
    L.append(f'<rect x="40" y="{bar_y}" width="{xs[-1]+cw-40}" height="50" rx="10" '
             f'fill="{PANEL_FILL}" stroke="{AMBER}" stroke-width="1.2" stroke-dasharray="4,3"/>')
    L.append(f'<text x="60" y="{bar_y+31}" font-size="12" font-weight="600" fill="{INK}">'
             f'Orchestration:  Metaflow (checkpoint/retry)  ·  Ray (batch/parallel)  ·  async task (Redis-shared)</text>')
    L.append('</svg>')
    return '\n'.join(L)


# ---------------------------------------------------------------------------
# D3 — RAG + GraphRAG Query Flow
# ---------------------------------------------------------------------------
def d3_rag():
    W, H = 1240, 560
    L = [svg_open(W, H),
         header("RAG + GRAPHRAG",
                "question -> embed -> dual retrieval (vector + graph) -> rerank -> augment -> LLM", VIOLET)]
    cy = 320
    L.append(card(40, cy - 40, 130, 80, "INPUT", "Question", "user query", EMER))
    L.append(card(210, cy - 40, 130, 80, "EMBED", "Embed", "bge-m3 / CLIP", AMBER))
    L.append(card(410, cy - 130, 190, 75, "VECTOR", "Vector Search", "DuckDB · ANN", VIOLET))
    L.append(card(410, cy + 55, 190, 75, "GRAPH", "Graph Retrieval", "Gremlin · neighbors", VIOLET))
    L.append(card(670, cy - 40, 140, 80, "RERANK", "Rerank", "cross-encoder", AMBER))
    L.append(card(860, cy - 40, 140, 80, "AUGMENT", "Augment", "context + cite", ROSE))
    L.append(card(1050, cy - 40, 140, 80, "LLM", "LLM", "5 providers", AMBER))
    L.append(arrow(170, cy, 208, cy, "cyan", width=2.4))
    L.append(arrow(340, cy - 12, 500, cy - 55, "violet", label="vector", width=2.0))
    L.append(arrow(340, cy + 12, 500, cy + 55, "violet", label="graph q", width=2.0))
    L.append(arrow(600, cy - 90, 668, cy - 20, "cyan", width=2.2))
    L.append(arrow(600, cy + 90, 668, cy + 20, "cyan", width=2.2))
    L.append(arrow(810, cy, 858, cy, "cyan", label="context", width=2.4))
    L.append(arrow(1000, cy, 1048, cy, "cyan", width=2.4))
    # cited answer feedback loop (top)
    L.append(ortho_path([(1120, cy - 40), (1180, cy - 40), (1180, 120),
                          (105, 120), (105, cy - 40)], color="slate", dashed=True, width=1.8))
    L.append(f'<text x="640" y="112" font-size="11" font-weight="600" fill="{SLATE}" text-anchor="middle">cited answer + sources  (async stream)</text>')
    L.append(f'<text x="60" y="{H-20}" font-size="11" fill="{DIM}">cyan = primary flow  ·  violet = graph branch  ·  slate dashed = response loop</text>')
    L.append('</svg>')
    return '\n'.join(L)


# ---------------------------------------------------------------------------
# D4 — KG Build Pipeline (async)
# ---------------------------------------------------------------------------
def d4_kgbuild():
    W, H = 1180, 500
    L = [svg_open(W, H),
         header("KG BUILD  ·  ASYNC",
                "kg_build() returns task_id immediately; Ray worker builds via Vermeer -> HugeGraph", ROSE)]
    ty = 150
    L.append(card(40, ty, 150, 80, "CLIENT", "Client", "kg_build(ds)", EMER))
    L.append(card(240, ty, 170, 80, "FACADE", "Lake.kg_build", "async entry", CYAN))
    L.append(card(470, ty, 190, 80, "TASK", "TaskManager", "Redis HASH", SLATE))
    by = 330
    L.append(card(150, by, 170, 80, "SCAN", "Scan Dataset", "Lance rows", AMBER))
    L.append(card(380, by, 200, 80, "EXTRACT", "Entity / Relation", "LLM qwen2.5", AMBER))
    L.append(card(640, by, 170, 80, "BUILD", "Vermeer", "parallel graph", VIOLET))
    L.append(card(870, by, 170, 80, "STORE", "HugeGraph", "Gremlin", ROSE))
    # sync chain + immediate return
    L.append(arrow(190, ty + 40, 238, ty + 40, "cyan", label="kg_build(ds)", width=2.2))
    L.append(arrow(410, ty + 40, 468, ty + 40, "cyan", width=2.2))
    L.append(ortho_path([(565, ty), (565, 80), (115, 80), (115, ty)], color="slate", dashed=True, width=1.8))
    L.append(f'<text x="340" y="74" font-size="11" font-weight="600" fill="{SLATE}" text-anchor="middle">task_id (immediate, non-blocking)</text>')
    # dispatch down + build chain + status loop
    L.append(ortho_path([(565, ty + 80), (565, 270), (235, 270), (235, by)], color="amber", dashed=False, width=1.8))
    L.append(f'<text x="380" y="262" font-size="10.5" font-weight="600" fill="{AMBER}">dispatch</text>')
    L.append(arrow(320, by + 40, 378, by + 40, "cyan", width=2.2))
    L.append(arrow(580, by + 40, 638, by + 40, "cyan", width=2.2))
    L.append(arrow(810, by + 40, 868, by + 40, "rose", label="write", dashed=True, width=2.0))
    L.append(ortho_path([(955, by), (955, 290), (565, 290), (565, ty + 80)], color="slate", dashed=True, width=1.6))
    L.append(f'<text x="760" y="282" font-size="10.5" font-weight="600" fill="{SLATE}">status: DONE / FAILED</text>')
    L.append('</svg>')
    return '\n'.join(L)


# ---------------------------------------------------------------------------
# D5 — Deployment Topology
# ---------------------------------------------------------------------------
def d5_deploy():
    W, H = 1300, 680
    L = [svg_open(W, H),
         header("DEPLOYMENT",
                "Docker Compose · image arrow-lake:1.8.6  —  edge -> app -> compute/storage | KG+LLM | observability", SLATE)]
    zones = [(40, 130, 280, 260, "EDGE", CYAN),
             (350, 130, 280, 260, "APPLICATION", EMER),
             (660, 130, 280, 260, "COMPUTE", AMBER),
             (970, 130, 290, 260, "STORAGE", ROSE),
             (40, 420, 620, 210, "KNOWLEDGE GRAPH + LLM", VIOLET),
             (700, 420, 560, 210, "OBSERVABILITY", SLATE)]
    for z in zones:
        L.append(zone(*z))
    # EDGE
    L.append(card(60, 180, 240, 58, "PROXY", "nginx", "TLS · 127.0.0.1", CYAN))
    L.append(card(60, 252, 240, 58, "EGRESS", "proxy-forwarder", "WSL2 outbound", CYAN))
    # APP
    L.append(card(370, 165, 240, 58, "API", "api (FastAPI)", ":8000 · RBAC", EMER))
    L.append(card(370, 235, 240, 58, "CACHE", "redis", "session · task · JWT", EMER))
    L.append(card(370, 305, 240, 58, "EXPORT", "redis-exporter", "metrics", EMER))
    # COMPUTE
    L.append(card(680, 165, 240, 58, "HEAD", "ray-head", "coordinator", AMBER))
    L.append(card(680, 235, 240, 58, "WORKER", "ray-worker", "KG build · embed", AMBER))
    L.append(card(680, 305, 240, 58, "GPU", "ray-worker-gpu", "CLIP · heavy", AMBER))
    # STORAGE
    L.append(card(990, 180, 250, 58, "BLOB", "minio", "S3 raw + backup", ROSE))
    L.append(card(990, 252, 250, 58, "INIT", "minio-init", "buckets · volumes", ROSE))
    L.append(card(990, 324, 250, 58, "BKUP", "minio-backup", "scheduled", ROSE))
    # KG + LLM
    L.append(card(70, 470, 270, 76, "GRAPH", "hugegraph (PD)", "Gremlin · REST fallback", VIOLET))
    L.append(card(380, 470, 270, 76, "LLM", "ollama-relay", "qwen2.5 + bge-m3", VIOLET))
    # OBS
    L.append(card(720, 460, 245, 58, "METRIC", "prometheus", "scrape · alert", SLATE))
    L.append(card(985, 460, 245, 58, "DASH", "grafana", "dashboards", SLATE))
    L.append(card(720, 535, 245, 58, "TRACE", "jaeger", "OTel traces", SLATE))
    L.append(card(985, 535, 245, 58, "LOG", "loki + alertmgr", "logs · alerts", SLATE))
    # inter-zone arrows
    L.append(arrow(300, 209, 368, 194, "cyan", width=2.2))                       # nginx -> api
    L.append(arrow(610, 194, 678, 194, "amber", label="ray tasks", width=2.0))    # api -> ray-head
    L.append(arrow(920, 264, 988, 209, "rose", label="blob write", dashed=True, width=1.8))  # ray-worker -> minio
    L.append(ortho_path([(490, 345), (490, 400), (205, 400), (205, 470)], color="violet", dashed=False, width=1.8))  # api -> hugegraph
    L.append(f'<text x="350" y="394" font-size="10" font-weight="600" fill="{VIOLET}">graph query</text>')
    L.append(ortho_path([(490, 345), (490, 405), (515, 405), (515, 470)], color="violet", dashed=False, width=1.8))  # api -> ollama
    L.append(f'<text x="540" y="400" font-size="10" font-weight="600" fill="{VIOLET}">LLM call</text>')
    L.append(f'<text x="60" y="{H-22}" font-size="11" fill="{DIM}">all zones emit metrics / logs / traces -> observability  ·  read-only containers · cap_drop · health-gated rolling update</text>')
    L.append('</svg>')
    return '\n'.join(L)


# ---------------------------------------------------------------------------
# Sequence diagram helper
# ---------------------------------------------------------------------------
def sequence(W, H, title, sub, participants, messages, accent=SLATE):
    """participants: [(label, color_name)]. messages: [(fi, ti, label, color, dashed)]."""
    L = [svg_open(W, H), header(title, sub, accent)]
    n = len(participants)
    margin = 95
    gap = (W - 2 * margin) / (n - 1)
    xs = [margin + i * gap for i in range(n)]
    top_y = 150
    bot_y = H - 50
    for (lbl, col), x in zip(participants, xs):
        c = COLORS[col]
        L.append(f'<rect x="{x-60}" y="{top_y-30}" width="120" height="32" rx="6" '
                 f'fill="{PANEL_FILL}" stroke="{c}" stroke-width="1.6"/>')
        L.append(f'<text x="{x}" y="{top_y-10}" font-size="11" font-weight="700" '
                 f'fill="{c}" text-anchor="middle">{esc(lbl)}</text>')
    for x in xs:
        L.append(f'<line x1="{x}" y1="{top_y+6}" x2="{x}" y2="{bot_y}" stroke="{GRID}" '
                 f'stroke-width="1" stroke-dasharray="3,4"/>')
    y = top_y + 40
    for m in messages:
        fi, ti, lbl, col = m[0], m[1], m[2], m[3]
        dashed = m[4] if len(m) > 4 else False
        if fi == ti:
            x = xs[fi]
            c = COLORS[col]
            L.append(f'<path d="M {x},{y} L {x+36},{y} L {x+36},{y+16} L {x+5},{y+16}" '
                     f'fill="none" stroke="{c}" stroke-width="1.8" marker-end="url(#m-{col})"/>')
            L.append(f'<text x="{x+44}" y="{y+11}" font-size="10" font-weight="600" '
                     f'fill="{c}">{esc(lbl)}</text>')
            y += 38
        else:
            L.append(arrow(xs[fi], y, xs[ti], y, color=col, label=lbl,
                           dashed=dashed, width=1.9, lo=8, lw=9.5))
            y += 40
    return '\n'.join(L) + '</svg>'


# ---------------------------------------------------------------------------
# D6 — RAG + GraphRAG query sequence
# ---------------------------------------------------------------------------
def d6_rag_sequence():
    W, H = 1320, 720
    parts = [("Client", "emer"), ("Lake", "cyan"), ("Embedder", "amber"),
             ("DuckDB", "violet"), ("HugeGraph", "rose"),
             ("Reranker", "amber"), ("LLM", "amber")]
    msgs = [
        (0, 1, "1. rag_query(question)", "cyan"),
        (1, 2, "2. embed(question)", "amber"),
        (2, 1, "3. query_vector", "amber", True),
        (1, 3, "4. vector_search (lance_scan + ANN)", "violet"),
        (1, 4, "5. gremlin neighbors(subgraph)", "rose"),
        (3, 1, "6. chunks[]", "violet", True),
        (4, 1, "7. subgraph context", "rose", True),
        (1, 5, "8. rerank(chunks + graph)", "amber"),
        (5, 1, "9. ranked context", "amber", True),
        (1, 6, "10. generate(ctx + question)", "amber"),
        (6, 1, "11. answer + citations", "amber", True),
        (1, 0, "12. cited response", "cyan", True),
    ]
    return sequence(W, H, "RAG + GraphRAG — Query Sequence",
                    "dual retrieval (vector + graph) -> rerank -> LLM -> cited answer",
                    parts, msgs, VIOLET)


# ---------------------------------------------------------------------------
# D7 — KG build async sequence
# ---------------------------------------------------------------------------
def d7_kgbuild_sequence():
    W, H = 1240, 720
    parts = [("Client", "emer"), ("Lake", "cyan"), ("TaskManager", "slate"),
             ("RayWorker", "amber"), ("Vermeer", "violet"), ("HugeGraph", "rose")]
    msgs = [
        (0, 1, "1. kg_build(dataset)", "cyan"),
        (1, 2, "2. create_task (Redis HASH)", "slate"),
        (2, 1, "3. task_id", "slate", True),
        (1, 0, "4. return task_id (non-blocking)", "cyan", True),
        (1, 3, "5. dispatch (async)", "amber"),
        (3, 3, "6. scan dataset", "amber"),
        (3, 4, "7. extract + build(entities, rel)", "violet"),
        (4, 5, "8. bulk write graph", "rose", True),
        (4, 3, "9. done", "violet", True),
        (3, 2, "10. status = DONE", "amber", True),
        (0, 2, "11. poll kg_build_status(id)", "slate"),
        (2, 0, "12. DONE", "slate", True),
    ]
    return sequence(W, H, "Knowledge-Graph Build — Async Sequence",
                    "fire-and-forget: task_id returns at step 4; build continues on Ray worker",
                    parts, msgs, ROSE)


# ---------------------------------------------------------------------------
# D8 — Lake Facade + 9 mixins
# ---------------------------------------------------------------------------
def d8_facade():
    W, H = 1120, 760
    cname = {v: k for k, v in COLORS.items()}
    L = [svg_open(W, H),
         header("LAKE FACADE", "one class · nine mixins · lazy components + RLock", CYAN)]
    # Lake box (left)
    L.append(f'<rect x="40" y="130" width="300" height="560" rx="14" fill="{PANEL_FILL}" stroke="{CYAN}" stroke-width="2"/>')
    L.append(f'<rect x="40" y="130" width="300" height="46" rx="14" fill="{CYAN}" fill-opacity="0.18"/>')
    L.append(f'<text x="58" y="160" font-size="17" font-weight="800" fill="{CYAN}">class Lake</text>')
    for i, h in enumerate(["_base_uri", "_config : ArrowLakeConfig",
                           "_storage : StorageProtocol", "_components : dict",
                           "_component_lock : RLock"]):
        L.append(f'<text x="60" y="{206+i*26}" font-size="12" fill="{MUTED}">· {esc(h)}</text>')
    L.append(f'<line x1="60" y1="350" x2="320" y2="350" stroke="{GRID}"/>')
    L.append(f'<text x="58" y="376" font-size="12" font-weight="700" fill="{INK}">entry points</text>')
    for i, e in enumerate(['Lake(uri)', 'Lake.from_yaml(path)', 'health() · version() · shutdown()']):
        L.append(f'<text x="60" y="{402+i*26}" font-size="12" fill="{INK}">{esc(e)}</text>')
    # 9 mixins single column (right)
    mixins = [
        ("_LakeBaseMixin", "lazy _get_component · shared httpx · shutdown", CYAN),
        ("_LakeIngestMixin", "create_dataset · ingest_* · upsert · quality", EMER),
        ("_LakeSearchMixin", "search · text_search · hybrid · create_*_index", EMER),
        ("_LakeQueryMixin", "olap_query · materialize · export · daft_query", AMBER),
        ("_LakeAdminMixin", "list/open/delete · tags · schema evolve · backup", AMBER),
        ("_LakeLineageMixin", "record lineage · trace query", VIOLET),
        ("_LakeAuditMixin", "HMAC-SHA256 audit · tamper-evident log", VIOLET),
        ("_LakeRAGMixin", "await rag_query · stream · batch · extract", ROSE),
        ("_LakeKGMixin", "await kg_build(fire-forget) · kg_query · paths", ROSE),
    ]
    mx_x, mw, mh, gap, my0 = 460, 600, 54, 9, 130
    for i, (name, methods, ac) in enumerate(mixins):
        by = my0 + i * (mh + gap)
        L.append(f'<rect x="{mx_x}" y="{by}" width="{mw}" height="{mh}" rx="9" '
                 f'fill="{PANEL_FILL}" stroke="{ac}" stroke-width="1.6"/>')
        L.append(f'<text x="{mx_x+16}" y="{by+22}" font-size="12.5" font-weight="700" '
                 f'fill="{ac}">{esc(name)}</text>')
        L.append(f'<text x="{mx_x+16}" y="{by+42}" font-size="10.5" fill="{MUTED}">{esc(methods)}</text>')
        mid = by + mh // 2
        L.append(f'<line x1="{mx_x}" y1="{mid}" x2="342" y2="{mid}" stroke="{ac}" '
                 f'stroke-width="1.3" stroke-dasharray="4,3" stroke-opacity="0.55" '
                 f'marker-end="url(#m-{cname[ac]})"/>')
    L.append(f'<text x="60" y="{H-22}" font-size="11" fill="{DIM}">dashed = inherits into Lake  ·  RAG / KG are async (await)  ·  RLock allows reentrant nested component loading</text>')
    return '\n'.join(L) + '</svg>'


DIAGRAMS = [
    ("01-layered-architecture", d1_layered),
    ("02-ingestion-pipeline", d2_ingest),
    ("03-rag-kg-query-flow", d3_rag),
    ("04-kg-build-pipeline", d4_kgbuild),
    ("05-deployment-topology", d5_deploy),
    ("06-rag-query-sequence", d6_rag_sequence),
    ("07-kgbuild-sequence", d7_kgbuild_sequence),
    ("08-lake-facade-mixins", d8_facade),
]


def main():
    for name, fn in DIAGRAMS:
        with open(os.path.join(HERE, name + ".svg"), "w") as f:
            f.write(fn())
        print("wrote", name)


if __name__ == "__main__":
    main()
