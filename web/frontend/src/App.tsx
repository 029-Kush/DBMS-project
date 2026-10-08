import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Activity,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  CircleHelp,
  Command,
  Compass,
  Crosshair,
  Database,
  ExternalLink,
  Filter,
  Focus,
  Layers3,
  LoaderCircle,
  Maximize2,
  Menu,
  Minus,
  MoreHorizontal,
  MousePointer2,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  X,
  Zap,
} from "lucide-react";
import { getGraph, getHealth, getNeighbors, searchDocuments } from "./api";
import NodeDetailPanel from "./NodeDetailPanel";
import type {
  DocumentItem,
  GraphData,
  HealthData,
  SearchResponse,
} from "./types";

const palette: Record<string, string> = {
  tech: "#a78bfa",
  technology: "#a78bfa",
  sports: "#f2a889",
  finance: "#e4ba71",
  health: "#8bd0b1",
  food: "#dd9ab8",
  travel: "#8cb9de",
};
const fallbackColor = "#ab9ac6";
const categoryColor = (name: string) =>
  palette[name.toLowerCase()] || fallbackColor;
const W = 1200;
const H = 800;

type Point = { x: number; y: number };
type Drag = {
  kind: "node" | "pan";
  id?: string;
  start: Point;
  origin: Point;
  followers?: Record<string, Point>;
};

function initialPositions(
  data: GraphData,
  activeCategory?: string,
): Record<string, Point> {
  const categories = data.categories
    .map((c) => c.name)
    .filter((name) => !activeCategory || name === activeCategory);
  const points: Record<string, Point> = {};
  const centerX = W / 2;
  const centerY = H / 2;
  categories.forEach((category, i) => {
    const angle =
      (i / Math.max(categories.length, 1)) * Math.PI * 2 - Math.PI / 2;
    const cx =
      categories.length === 1 ? centerX : centerX + Math.cos(angle) * 315;
    const cy =
      categories.length === 1 ? centerY : centerY + Math.sin(angle) * 225;
    points[`c:${category}`] = { x: cx, y: cy };
    const docs = data.documents.filter((doc) => doc.category === category);
    docs.forEach((doc, j) => {
      const ring = Math.floor(j / 12);
      const perRing = Math.min(12, docs.length - ring * 12);
      const index = j % 12;
      const theta =
        (index / Math.max(perRing, 1)) * Math.PI * 2 +
        angle * 0.22 +
        ring * 0.43;
      const radius = 72 + ring * 52 + (j % 3) * 6;
      points[`d:${doc.id}`] = {
        x: Math.max(38, Math.min(W - 38, cx + Math.cos(theta) * radius)),
        y: Math.max(38, Math.min(H - 38, cy + Math.sin(theta) * radius)),
      };
    });
  });
  return points;
}

function formatDate(value: string) {
  return new Date(value).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function App() {
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [category, setCategory] = useState<string | undefined>();
  const [selected, setSelected] = useState<DocumentItem | null>(null);
  const [searchText, setSearchText] = useState("");
  const [searchResult, setSearchResult] = useState<SearchResponse | null>(null);
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [positions, setPositions] = useState<Record<string, Point>>({});
  const [hovered, setHovered] = useState<string | null>(null);
  const [hoverPreview, setHoverPreview] = useState<{
    doc: DocumentItem;
    x: number;
    y: number;
  } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [neighbors, setNeighbors] = useState<DocumentItem[]>([]);
  const [neighborLoading, setNeighborLoading] = useState(false);
  const [neighborError, setNeighborError] = useState("");
  const [trail, setTrail] = useState<DocumentItem[]>([]);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState<Point>({ x: 0, y: 0 });
  const [mobileNav, setMobileNav] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const svgRef = useRef<SVGSVGElement>(null);
  const graphCardRef = useRef<HTMLElement>(null);
  const dragRef = useRef<Drag | null>(null);
  const dragMovedRef = useRef(false);
  const positionsRef = useRef<Record<string, Point>>({});
  const followerTargetsRef = useRef<Record<string, Point>>({});
  const animationFrameRef = useRef<number | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async (activeCategory?: string) => {
    setLoading(true);
    setError("");
    try {
      const [newGraph, newHealth] = await Promise.all([
        getGraph(activeCategory),
        getHealth(),
      ]);
      setGraph(newGraph);
      setHealth(newHealth);
      const nextPositions = initialPositions(newGraph, activeCategory);
      positionsRef.current = nextPositions;
      followerTargetsRef.current = {};
      setPositions(nextPositions);
      setSelected((current) =>
        current && newGraph.documents.some((doc) => doc.id === current.id)
          ? current
          : null,
      );
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not connect to the project API",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(category);
  }, [category, load]);
  useEffect(() => {
    if (!selected) {
      setNeighbors([]);
      return;
    }
    let cancelled = false;
    setNeighborLoading(true);
    setNeighborError("");
    setNeighbors([]);
    getNeighbors(selected.id)
      .then((response) => {
        if (!cancelled) setNeighbors(response.neighbors);
      })
      .catch((cause) => {
        if (!cancelled)
          setNeighborError(cause instanceof Error ? cause.message : "Could not load neighbors");
      })
      .finally(() => {
        if (!cancelled) setNeighborLoading(false);
      });
    return () => { cancelled = true; };
  }, [selected?.id]);
  useEffect(() => () => {
    if (animationFrameRef.current != null) cancelAnimationFrame(animationFrameRef.current);
  }, []);
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (event.key === "/" && document.activeElement?.tagName !== "INPUT") {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape") {
        setSearchResult(null);
        searchRef.current?.blur();
      }
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, []);

  const categories = graph?.categories ?? [];
  const docs = graph?.documents ?? [];
  const searchIds = useMemo(
    () => new Set(searchResult?.results.map((item) => item.id) ?? []),
    [searchResult],
  );
  const selectedKey = selected ? `d:${selected.id}` : null;
  const latest = health?.latest;
  const status = latest?.status ?? "UNKNOWN";

  async function submitSearch(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = searchText.trim();
    if (!query) {
      setSearchResult(null);
      setSearchError("");
      return;
    }
    setSearchBusy(true);
    setSearchError("");
    try {
      const result = await searchDocuments(query, category);
      setSearchResult(result);
      if (result.results.length) selectDocument(result.results[0]);
    } catch (cause) {
      setSearchError(cause instanceof Error ? cause.message : "Search failed");
      setSearchResult(null);
    } finally {
      setSearchBusy(false);
    }
  }

  function clearSearch() {
    setSearchText("");
    setSearchResult(null);
    setSearchError("");
  }

  function selectDocument(doc: DocumentItem) {
    setSelected(doc);
    setInspectorOpen(true);
    setTrail((previous) =>
      previous.at(-1)?.id === doc.id ? previous : [...previous.slice(-4), doc],
    );
  }

  function showPreview(event: React.MouseEvent<SVGGElement>, doc: DocumentItem) {
    if (dragRef.current) return;
    const rect = graphCardRef.current?.getBoundingClientRect();
    if (!rect) return;
    setHovered(`d:${doc.id}`);
    setHoverPreview({ doc, x: event.clientX - rect.left, y: event.clientY - rect.top });
  }

  function animateFollowers() {
    if (animationFrameRef.current != null) return;
    const tick = () => {
      const current = positionsRef.current;
      const next = { ...current };
      let moving = false;
      for (const [id, target] of Object.entries(followerTargetsRef.current)) {
        const point = current[id];
        if (!point) continue;
        const x = point.x + (target.x - point.x) * 0.27;
        const y = point.y + (target.y - point.y) * 0.27;
        next[id] = { x, y };
        if (Math.abs(target.x - x) + Math.abs(target.y - y) > 0.35) moving = true;
        else next[id] = target;
      }
      positionsRef.current = next;
      setPositions(next);
      animationFrameRef.current = moving ? requestAnimationFrame(tick) : null;
    };
    animationFrameRef.current = requestAnimationFrame(tick);
  }

  function pointerPosition(event: React.PointerEvent<SVGSVGElement>): Point {
    const matrix = svgRef.current?.getScreenCTM();
    if (!matrix) return { x: 0, y: 0 };
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(
      matrix.inverse(),
    );
    return { x: point.x, y: point.y };
  }

  function startNodeDrag(event: React.PointerEvent<SVGGElement>, id: string) {
    event.stopPropagation();
    const matrix = svgRef.current?.getScreenCTM();
    if (!matrix) return;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(
      matrix.inverse(),
    );
    const p = { x: point.x, y: point.y };
    dragMovedRef.current = false;
    setDragging(true);
    setHoverPreview(null);
    if (id.startsWith("d:")) delete followerTargetsRef.current[id];
    const followers: Record<string, Point> = {};
    if (id.startsWith("c:")) {
      const cluster = id.slice(2);
      for (const doc of docs) {
        if (doc.category === cluster) followers[`d:${doc.id}`] = positionsRef.current[`d:${doc.id}`];
      }
    }
    dragRef.current = { kind: "node", id, start: p, origin: positionsRef.current[id], followers };
    svgRef.current?.setPointerCapture(event.pointerId);
  }

  function startPan(event: React.PointerEvent<SVGSVGElement>) {
    dragRef.current = {
      kind: "pan",
      start: pointerPosition(event),
      origin: offset,
    };
    svgRef.current?.setPointerCapture(event.pointerId);
  }

  function movePointer(event: React.PointerEvent<SVGSVGElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const now = pointerPosition(event);
    const dx = now.x - drag.start.x;
    const dy = now.y - drag.start.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) dragMovedRef.current = true;
    if (drag.kind === "pan")
      setOffset({ x: drag.origin.x + dx, y: drag.origin.y + dy });
    else if (drag.id) {
      const id = drag.id;
      const move = { x: dx / scale, y: dy / scale };
      const next = {
        ...positionsRef.current,
        [id]: { x: drag.origin.x + move.x, y: drag.origin.y + move.y },
      };
      positionsRef.current = next;
      setPositions(next);
      if (id.startsWith("c:") && drag.followers) {
        followerTargetsRef.current = Object.fromEntries(
          Object.entries(drag.followers).map(([child, origin]) => [
            child,
            { x: origin.x + move.x, y: origin.y + move.y },
          ]),
        );
        animateFollowers();
      }
    }
  }

  function endPointerDrag() {
    const drag = dragRef.current;
    dragRef.current = null;
    setDragging(false);
    if (!drag || drag.kind !== "node" || dragMovedRef.current || !drag.id) return;
    if (drag.id.startsWith("c:")) {
      chooseCategory(drag.id.slice(2));
      return;
    }
    const doc = docs.find((item) => `d:${item.id}` === drag.id);
    if (doc) selectDocument(doc);
  }

  function wheel(event: React.WheelEvent<SVGSVGElement>) {
    event.preventDefault();
    setScale((current) =>
      Math.max(0.55, Math.min(2.2, current * (event.deltaY > 0 ? 0.9 : 1.1))),
    );
  }

  function resetView() {
    setScale(1);
    setOffset({ x: 0, y: 0 });
    followerTargetsRef.current = {};
    if (animationFrameRef.current != null) cancelAnimationFrame(animationFrameRef.current);
    animationFrameRef.current = null;
    if (graph) {
      const next = initialPositions(graph, category);
      positionsRef.current = next;
      setPositions(next);
    }
  }

  function chooseCategory(next?: string) {
    setCategory(next);
    setMobileNav(false);
    clearSearch();
    setScale(1);
    setOffset({ x: 0, y: 0 });
    setTrail([]);
  }

  return (
    <div className="shell">
      <aside className={`sidebar ${mobileNav ? "sidebar-open" : ""}`}>
        <div className="brand">
          <div className="brand-mark">
            <Layers3 size={20} strokeWidth={2.4} />
          </div>
          <div>
            <strong>
              vectra<span>.</span>
            </strong>
            <small>KNOWLEDGE WORKSPACE</small>
          </div>
        </div>
        <div className="workspace-select">
          <div className="workspace-icon">
            <Database size={16} />
          </div>
          <div className="workspace-name">
            <strong>Research workspace</strong>
            <span>Local PostgreSQL</span>
          </div>
          <ChevronDown size={15} />
        </div>
        <div className="sidebar-section-label">WORKSPACE</div>
        <nav className="nav-main">
          <button className="nav-item active" onClick={() => chooseCategory()}>
            <Compass size={18} /> Graph explorer{" "}
            <span className="nav-active-line" />
          </button>
          <button
            className="nav-item"
            onClick={() => searchRef.current?.focus()}
          >
            <Search size={18} /> Semantic search{" "}
            <span className="shortcut">/</span>
          </button>
          <button
            className="nav-item"
            onClick={() =>
              document
                .getElementById("system-health")
                ?.scrollIntoView({ behavior: "smooth" })
            }
          >
            <Activity size={18} /> System health
          </button>
        </nav>
        <div className="sidebar-divider" />
        <div className="sidebar-heading">
          <span>COLLECTIONS</span>
          <span>{categories.length.toString().padStart(2, "0")}</span>
        </div>
        <div className="collections">
          <button
            className={`collection-item ${!category ? "selected" : ""}`}
            onClick={() => chooseCategory()}
          >
            <span className="collection-symbol all">
              <Layers3 size={15} />
            </span>
            <span>All knowledge</span>
            <small>{graph ? categories.reduce((sum, item) => sum + item.count, 0) : "—"}</small>
          </button>
          {categories.map((item) => (
            <button
              key={item.name}
              className={`collection-item ${category === item.name ? "selected" : ""}`}
              onClick={() => chooseCategory(item.name)}
            >
              <span
                className="collection-dot"
                style={{ background: categoryColor(item.name) }}
              />
              <span className="capitalize">{item.name}</span>
              <small>{item.count}</small>
            </button>
          ))}
        </div>
        <div className="sidebar-spacer" />
        <div className="sidebar-bottom">
          <div className="sync-card">
            <div className="sync-icon">
              <Zap size={17} fill="currentColor" />
            </div>
            <div>
              <strong>Self-healing engine</strong>
              <span>
                {status === "CRITICAL"
                  ? "Attention needed"
                  : "Monitoring your knowledge"}
              </span>
            </div>
            <span
              className={`live-pulse ${status === "CRITICAL" ? "critical" : ""}`}
            />
          </div>
          <div className="sidebar-foot">
            <span>
              <CircleHelp size={15} /> Help & shortcuts
            </span>
            <span>v0.1</span>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <button
            className="mobile-menu icon-button"
            onClick={() => setMobileNav(!mobileNav)}
            aria-label="Toggle menu"
          >
            <Menu size={20} />
          </button>
          <div className="breadcrumb">
            <span>Workspace</span>
            <span className="slash">/</span>
            <strong>Graph explorer</strong>
          </div>
          <div className="topbar-actions">
            <div className="connection">
              <span className="connection-dot" /> LOCAL SYSTEM
            </div>
            <button className="avatar" aria-label="Workspace profile">
              KG
            </button>
          </div>
        </header>
        <div className="content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                <span className="eyebrow-line" /> EXPLORE YOUR KNOWLEDGE
              </div>
              <h1>
                Ideas, connected<span>.</span>
              </h1>
              <p>
                See how your documents relate. Follow a thread. Find what
                matters.
              </p>
            </div>
            <div className="heading-actions">
              <button className="ghost-button" onClick={resetView}>
                <Focus size={16} /> Reset view
              </button>
              <button
                className="outline-button"
                onClick={() => setInspectorOpen(!inspectorOpen)}
              >
                <BookOpen size={16} />{" "}
                {inspectorOpen ? "Hide details" : "Show details"}
              </button>
            </div>
          </div>
          <div className="search-row">
            <form className="search-box" onSubmit={submitSearch}>
              <Search size={19} />
              <input
                ref={searchRef}
                value={searchText}
                onChange={(event) => setSearchText(event.target.value)}
                placeholder="Search by meaning, not just keywords..."
                aria-label="Semantic search"
              />
              {searchText && (
                <button
                  type="button"
                  className="clear-button"
                  onClick={clearSearch}
                  aria-label="Clear search"
                >
                  <X size={16} />
                </button>
              )}
              <span className="search-key">
                <Command size={11} /> ↵
              </span>
              <button
                type="submit"
                className="search-submit"
                aria-label="Run search"
              >
                {searchBusy ? (
                  <LoaderCircle size={17} className="spin" />
                ) : (
                  <ArrowRight size={18} />
                )}
              </button>
            </form>
            <div className="filter-chip">
              <Filter size={16} />{" "}
              {category ? (
                <span className="capitalize">{category}</span>
              ) : (
                "All categories"
              )}{" "}
              <ChevronDown size={15} />
            </div>
          </div>
          {searchError && <div className="inline-alert">{searchError}</div>}
          <div
            className={`work-area ${inspectorOpen ? "" : "inspector-hidden"}`}
          >
            <section
              ref={graphCardRef}
              className="graph-card"
              aria-label="Interactive knowledge graph"
            >
              <div className="graph-toolbar">
                <div className="graph-label">
                  <span className="graph-label-icon">
                    <Sparkles size={15} />
                  </span>
                  <strong>Knowledge graph</strong>
                  <span className="graph-label-separator" />{" "}
                  <span>{docs.length} visible nodes</span>
                </div>
                <div className="graph-toolbar-right">
                  <span className="graph-hint">
                    <MousePointer2 size={13} /> Drag to explore
                  </span>
                  <button
                    className="graph-more"
                    aria-label="Reset graph"
                    onClick={resetView}
                  >
                    <MoreHorizontal size={19} />
                  </button>
                </div>
              </div>
              {loading && (
                <div className="graph-message">
                  <LoaderCircle className="spin" size={28} />
                  <span>Mapping connections...</span>
                </div>
              )}
              {!loading && error && (
                <div className="graph-message graph-error">
                  <Database size={30} />
                  <strong>Connect the local API</strong>
                  <span>{error}</span>
                  <button onClick={() => void load(category)}>Try again</button>
                </div>
              )}
              {!loading && !error && graph && (
                <>
                  <svg
                    ref={svgRef}
                    viewBox={`0 0 ${W} ${H}`}
                    preserveAspectRatio="xMidYMid meet"
                    className="graph-svg"
                    onPointerDown={startPan}
                    onPointerMove={movePointer}
                    onPointerUp={endPointerDrag}
                    onPointerCancel={() => {
                      dragRef.current = null;
                      setDragging(false);
                    }}
                    onWheel={wheel}
                  >
                    <defs>
                      <radialGradient id="graphGlow">
                        <stop
                          offset="0%"
                          stopColor="#7c4bd1"
                          stopOpacity=".18"
                        />
                        <stop
                          offset="100%"
                          stopColor="#7c4bd1"
                          stopOpacity="0"
                        />
                      </radialGradient>
                      <filter id="softGlow">
                        <feGaussianBlur stdDeviation="9" />
                      </filter>
                    </defs>
                    <circle
                      cx="600"
                      cy="400"
                      r="420"
                      fill="url(#graphGlow)"
                      pointerEvents="none"
                    />
                    <g
                      transform={`translate(${W / 2 + offset.x} ${H / 2 + offset.y}) scale(${scale}) translate(${-W / 2} ${-H / 2})`}
                    >
                      {graph.links.map((link) => {
                        const from = positions[`d:${link.source}`];
                        const to = positions[`d:${link.target}`];
                        if (!from || !to) return null;
                        const emphasis =
                          selected?.id === link.source ||
                          selected?.id === link.target;
                        return (
                          <line
                            key={`s:${link.source}:${link.target}`}
                            x1={from.x}
                            y1={from.y}
                            x2={to.x}
                            y2={to.y}
                            stroke={emphasis ? "#c6a0ff" : "#9d7ec0"}
                            strokeWidth={emphasis ? 2 : 1.1}
                            opacity={emphasis ? 0.72 : 0.33}
                            pointerEvents="none"
                          />
                        );
                      })}
                      {docs.map((doc) => {
                        const from = positions[`c:${doc.category}`];
                        const to = positions[`d:${doc.id}`];
                        if (!from || !to) return null;
                        const emphasis =
                          selected?.id === doc.id || searchIds.has(doc.id);
                        return (
                          <line
                            key={`e:${doc.id}`}
                            x1={from.x}
                            y1={from.y}
                            x2={to.x}
                            y2={to.y}
                            stroke={
                              emphasis ? categoryColor(doc.category) : "#655c73"
                            }
                            strokeWidth={emphasis ? 1.7 : 0.9}
                            opacity={emphasis ? 0.65 : 0.22}
                            pointerEvents="none"
                          />
                        );
                      })}
                      {categories.map((item) => {
                        const point = positions[`c:${item.name}`];
                        if (!point) return null;
                        const color = categoryColor(item.name);
                        return (
                          <g
                            key={item.name}
                            className="graph-node hub-node"
                            transform={`translate(${point.x} ${point.y})`}
                            onPointerDown={(event) =>
                              startNodeDrag(event, `c:${item.name}`)
                            }
                            onMouseEnter={() => setHovered(`c:${item.name}`)}
                            onMouseLeave={() => setHovered(null)}
                          >
                            <circle r="38" fill={color} opacity=".08" />
                            <circle
                              r="23"
                              fill="#1d1927"
                              stroke={color}
                              strokeWidth="1.5"
                            />
                            <circle r="16" fill={color} opacity=".18" />
                            <circle r="5" fill={color} />
                            <text
                              y="-49"
                              textAnchor="middle"
                              className="hub-label"
                            >
                              {item.name.toUpperCase()}
                            </text>
                            <text
                              y="-34"
                              textAnchor="middle"
                              className="hub-count"
                            >
                              {item.count} documents
                            </text>
                          </g>
                        );
                      })}
                      {docs.map((doc) => {
                        const point = positions[`d:${doc.id}`];
                        if (!point) return null;
                        const key = `d:${doc.id}`;
                        const active = selectedKey === key;
                        const hoveredNow = hovered === key;
                        const matched = searchIds.has(doc.id);
                        const muted = !!searchResult && !matched;
                        const color = categoryColor(doc.category);
                        return (
                          <g
                            key={key}
                            className={`graph-node doc-node ${active ? "active-node" : ""}`}
                            transform={`translate(${point.x} ${point.y})`}
                            opacity={muted ? 0.27 : 1}
                            onPointerDown={(event) => startNodeDrag(event, key)}
                            onMouseEnter={(event) => showPreview(event, doc)}
                            onMouseMove={(event) => showPreview(event, doc)}
                            onMouseLeave={() => { setHovered(null); setHoverPreview(null); }}
                          >
                            <circle
                              r={active ? 24 : hoveredNow ? 18 : 14}
                              fill={color}
                              opacity={active ? 0.18 : 0.1}
                            />
                            <circle
                              r={active ? 10 : matched ? 8 : 6}
                              fill={active || matched ? color : "#b9abc9"}
                              stroke="#201928"
                              strokeWidth="2"
                            />
                            <circle r="24" fill="transparent" />
                          </g>
                        );
                      })}
                    </g>
                  </svg>
                  {hoverPreview && !dragging && (
                    <div
                      className="node-preview"
                      role="tooltip"
                      style={{
                        left: Math.max(14, Math.min(hoverPreview.x + 20, (graphCardRef.current?.clientWidth ?? 900) - (inspectorOpen ? 326 : 0) - 326)),
                        top: Math.max(60, Math.min(hoverPreview.y - 114, (graphCardRef.current?.clientHeight ?? 600) - 140)),
                      }}
                    >
                      <div className="node-preview-kicker"><span style={{ background: categoryColor(hoverPreview.doc.category) }} />{hoverPreview.doc.category.toUpperCase()} <i>·</i> DOC #{String(hoverPreview.doc.id).padStart(3, "0")}</div>
                      <strong>{hoverPreview.doc.body}</strong>
                      <small>Click to inspect this document</small>
                    </div>
                  )}
                  {searchResult && (
                    <div className="results-panel">
                      <div className="results-heading">
                        <div>
                          <span>SEMANTIC MATCHES</span>
                          <strong>
                            {searchResult.results.length} results <i>·</i>{" "}
                            {searchResult.latency_ms.toFixed(1)} ms
                          </strong>
                        </div>
                        <button
                          onClick={clearSearch}
                          aria-label="Close results"
                        >
                          <X size={16} />
                        </button>
                      </div>
                      <div className="results-scroll">
                        {searchResult.results.length ? (
                          searchResult.results.map((doc, index) => (
                            <button
                              key={doc.id}
                              className={`result-item ${selected?.id === doc.id ? "selected" : ""}`}
                              onClick={() => {
                                selectDocument(doc);
                              }}
                            >
                              <span className="result-rank">
                                {String(index + 1).padStart(2, "0")}
                              </span>
                              <span className="result-copy">
                                <strong>{doc.title}</strong>
                                <small>
                                  <span
                                    style={{
                                      background: categoryColor(doc.category),
                                    }}
                                  />{" "}
                                  {doc.category} ·{" "}
                                  {(1 - (doc.distance ?? 0)) * 100 < 0
                                    ? 0
                                    : Math.round(
                                        (1 - (doc.distance ?? 0)) * 100,
                                      )}
                                  % match
                                </small>
                              </span>
                              <ArrowRight size={15} />
                            </button>
                          ))
                        ) : (
                          <p className="empty-results">
                            No matching documents in this category.
                          </p>
                        )}
                      </div>
                    </div>
                  )}
                  <div className="graph-bottom">
                    <div className="graph-legend">
                      <span>
                        <i className="legend-hub" /> Category
                      </span>
                      <span>
                        <i className="legend-doc" /> Document
                      </span>
                      <span>
                        <i className="legend-line" /> Connection
                      </span>
                    </div>
                    <div className="zoom-controls">
                      <button
                        onClick={() =>
                          setScale((value) => Math.max(0.55, value - 0.15))
                        }
                        aria-label="Zoom out"
                      >
                        <Minus size={16} />
                      </button>
                      <span>{Math.round(scale * 100)}%</span>
                      <button
                        onClick={() =>
                          setScale((value) => Math.min(2.2, value + 0.15))
                        }
                        aria-label="Zoom in"
                      >
                        <Plus size={16} />
                      </button>
                      <button onClick={resetView} aria-label="Reset zoom">
                        <Maximize2 size={15} />
                      </button>
                    </div>
                  </div>
                </>
              )}
            </section>

            {inspectorOpen && (
              <NodeDetailPanel
                selected={selected}
                neighbors={neighbors}
                trail={trail}
                loading={neighborLoading}
                error={neighborError}
                onSelect={selectDocument}
                onStartOver={() => { setSelected(null); setTrail([]); }}
                onClose={() => setInspectorOpen(false)}
              />
            )}
          </div>

          <section id="system-health" className="health-section">
            <div className="section-title">
              <div>
                <div className="eyebrow">THE SYSTEM BEHIND THE SCENES</div>
                <h2>
                  Always keeping watch<span>.</span>
                </h2>
                <p>Real signals from your database, in one place.</p>
              </div>
              <button
                className="text-button"
                onClick={() => void load(category)}
              >
                Refresh status <ArrowRight size={16} />
              </button>
            </div>
            <div className="metric-grid">
              <Metric
                icon={<ShieldCheck size={20} />}
                label="SYSTEM STATUS"
                value={
                  status === "WARNING" &&
                  latest?.issues.every((issue) => issue === "INDEX_NOT_USED")
                    ? "Operating normally"
                    : status.toLowerCase()
                }
                detail={
                  latest
                    ? `Checked ${formatDate(latest.recorded_at)}`
                    : "Awaiting data"
                }
                tone={status === "CRITICAL" ? "critical" : "good"}
              />
              <Metric
                icon={<Crosshair size={20} />}
                label="SEARCH RECALL"
                value={
                  latest?.recall == null
                    ? "—"
                    : `${Math.round(latest.recall * 100)}%`
                }
                detail="Relevant results recovered"
                tone="violet"
              />
              <Metric
                icon={<Activity size={20} />}
                label="MODEL CONSISTENCY"
                value={
                  latest?.version_skew_pct == null
                    ? "—"
                    : `${Math.max(0, 100 - latest.version_skew_pct).toFixed(0)}%`
                }
                detail="Documents on current model"
                tone="blue"
              />
              <Metric
                icon={<Zap size={20} />}
                label="REPAIRS RECORDED"
                value={String(
                  health?.events.filter((event) => event.status === "SUCCEEDED")
                    .length ?? 0,
                )}
                detail="Recent successful events"
                tone="amber"
              />
            </div>
            <div className="health-lower">
              <div className="trend-card">
                <div className="card-heading">
                  <div>
                    <span className="card-icon">
                      <Activity size={18} />
                    </span>
                    <strong>Search quality over time</strong>
                  </div>
                  <span className="small-caption">
                    LAST {Math.min(health?.history.length ?? 0, 12)} CHECKS
                  </span>
                </div>
                <HealthChart history={health?.history ?? []} />
              </div>
              <div className="timeline-card">
                <div className="card-heading">
                  <div>
                    <span className="card-icon violet">
                      <Sparkles size={18} />
                    </span>
                    <strong>Healing activity</strong>
                  </div>
                  <ExternalLink size={15} />
                </div>
                {health?.events.length ? (
                  <div className="event-list">
                    {health.events.slice(0, 3).map((event) => (
                      <div key={event.id} className="event">
                        <div
                          className={`event-dot ${event.status.toLowerCase()}`}
                        >
                          <Check size={12} />
                        </div>
                        <div>
                          <strong>
                            {event.actions
                              .map((action) =>
                                action.replaceAll("_", " ").toLowerCase(),
                              )
                              .join(" + ") || event.status.toLowerCase()}
                          </strong>
                          <span>
                            {event.affected_rows} rows ·{" "}
                            {formatDate(event.started_at)}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="no-events">
                    No repairs recorded yet. The system is watching for issues.
                  </div>
                )}
              </div>
            </div>
          </section>
          <footer className="footer">
            <span>Vectra Workspace · Powered by PostgreSQL + pgvector</span>
            <span>
              Built to keep knowledge connected{" "}
              <span className="footer-heart">✦</span>
            </span>
          </footer>
        </div>
      </main>
    </div>
  );
}

function Metric({
  icon,
  label,
  value,
  detail,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  detail: string;
  tone: string;
}) {
  return (
    <div className="metric-card">
      <div className={`metric-icon ${tone}`}>{icon}</div>
      <span className="metric-label">{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function HealthChart({ history }: { history: HealthData["history"] }) {
  const points = [...history].reverse().slice(-12);
  if (points.length < 2)
    return (
      <div className="chart-empty">
        More health checks will draw your search quality trend.
      </div>
    );
  const values = points.map((item) => item.recall ?? 0);
  const line = values
    .map(
      (value, i) =>
        `${i === 0 ? "M" : "L"} ${(i / (values.length - 1)) * 650} ${110 - value * 100}`,
    )
    .join(" ");
  const fill = `${line} L 650 120 L 0 120 Z`;
  return (
    <div className="chart-wrap">
      <div className="chart-axis">
        <span>100%</span>
        <span>75%</span>
        <span>50%</span>
      </div>
      <svg
        viewBox="0 0 650 130"
        preserveAspectRatio="none"
        aria-label="Recall over time"
      >
        <defs>
          <linearGradient id="chartGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#9f72e9" stopOpacity=".32" />
            <stop offset="100%" stopColor="#9f72e9" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={fill} fill="url(#chartGradient)" />
        <path
          d={line}
          fill="none"
          stroke="#b689ff"
          strokeWidth="3"
          vectorEffect="non-scaling-stroke"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {values.map((value, i) => (
          <circle
            key={i}
            cx={(i / (values.length - 1)) * 650}
            cy={110 - value * 100}
            r="3.5"
            fill="#c49dff"
          />
        ))}
      </svg>
      <div className="chart-dates">
        <span>Earlier</span>
        <span>Latest</span>
      </div>
    </div>
  );
}

export default App;
