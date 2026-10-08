import { ArrowRight, Compass, CornerDownRight, FileText, RotateCcw, Sparkles, X } from "lucide-react";
import type { DocumentItem } from "./types";

type Props = {
  selected: DocumentItem | null;
  neighbors: DocumentItem[];
  trail: DocumentItem[];
  loading: boolean;
  error: string;
  onSelect: (document: DocumentItem) => void;
  onStartOver: () => void;
  onClose: () => void;
};

function similarity(distance?: number) {
  return distance == null ? null : Math.max(0, Math.min(100, Math.round((1 - distance) * 100)));
}

export default function NodeDetailPanel({
  selected,
  neighbors,
  trail,
  loading,
  error,
  onSelect,
  onStartOver,
  onClose,
}: Props) {
  if (!selected) {
    return (
      <aside className="node-detail-panel">
        <div className="node-detail-top"><span>NODE INTELLIGENCE</span><button onClick={onClose} aria-label="Close details"><X size={16} /></button></div>
        <div className="node-detail-empty">
          <div className="node-detail-orbit"><Compass size={26} /></div>
          <span className="detail-overline">EXPLORE THE GRAPH</span>
          <h2>Follow a connection.</h2>
          <p>Choose any document node to see its content, route through the graph, and nearest neighbors measured from the full vector database.</p>
        </div>
      </aside>
    );
  }

  const match = similarity(selected.distance);
  const dimensions = selected.model_version.startsWith("tfidf") ? 64 : 384;

  return (
    <aside className="node-detail-panel" aria-label="Selected document details">
      <div className="node-detail-top">
        <span>{match == null ? "GRAPH NODE" : `${match}% SEARCH MATCH`} <i>·</i> DOC #{String(selected.id).padStart(3, "0")}</span>
        <button onClick={onClose} aria-label="Close details"><X size={16} /></button>
      </div>
      <div className="node-detail-scroll">
        <div className="node-detail-title"><div className="node-detail-symbol"><FileText size={19} /></div><h2>{selected.title}</h2></div>
        <p className="node-detail-description">{selected.body}</p>

        <div className="detail-section">
          <div className="detail-section-label">DOCUMENT PROFILE</div>
          <div className="profile-grid">
            <div><span>ID</span><strong>#{String(selected.id).padStart(3, "0")}</strong></div>
            <div><span>CATEGORY</span><strong className="profile-category">{selected.category}</strong></div>
            <div><span>VECTOR</span><strong>{dimensions}D</strong></div>
          </div>
          <div className="model-caption">{selected.model_version} · HNSW cosine search</div>
        </div>

        <div className="detail-section">
          <div className="detail-section-label">PATH TAKEN <span>{trail.length} {trail.length === 1 ? "step" : "steps"}</span></div>
          <div className="path-list">
            <div className="path-row"><span className="path-marker category-marker" /><strong className="capitalize">{selected.category}</strong><small>Category</small></div>
            {trail.slice(-4).map((doc, index) => (
              <button key={`${doc.id}:${index}`} className={`path-row ${doc.id === selected.id ? "current" : ""}`} onClick={() => onSelect(doc)}>
                <span className="path-marker" /><strong>{doc.title}</strong><small>{doc.id === selected.id ? "Now" : `0${index + 1}`}</small>
              </button>
            ))}
          </div>
        </div>

        <div className="detail-section nearest-section">
          <div className="detail-section-label">NEAREST NEIGHBORS <span>TOP 4 · FULL CORPUS</span></div>
          {loading && <p className="neighbor-state">Measuring vector distances…</p>}
          {error && <p className="neighbor-state error">{error}</p>}
          {!loading && !error && <div className="neighbors-list">
            {neighbors.map((doc) => <button key={doc.id} onClick={() => onSelect(doc)}>
              <span className="neighbor-dot" /><span className="neighbor-title">{doc.title}</span><strong>{similarity(doc.distance)}%</strong><ArrowRight size={13} />
            </button>)}
          </div>}
        </div>
      </div>
      <div className="node-detail-actions">
        <button onClick={onStartOver}><RotateCcw size={14} /> Start over</button>
        <button className="explore-action" disabled={!neighbors.length} onClick={() => neighbors[0] && onSelect(neighbors[0])}><Sparkles size={15} /> Explore nearest <CornerDownRight size={13} /></button>
      </div>
    </aside>
  );
}
