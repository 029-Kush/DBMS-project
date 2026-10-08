export type DocumentItem = {
  id: number;
  category: string;
  body: string;
  title: string;
  model_version: string;
  created_at: string;
  distance?: number;
};

export type GraphData = {
  documents: DocumentItem[];
  links: { source: number; target: number; distance: number }[];
  total: number;
  categories: { name: string; count: number }[];
};

export type HealthSnapshot = {
  id: number;
  status: string;
  issues: string[];
  recall: number | null;
  ann_recall: number | null;
  distance_shift_pct: number | null;
  version_skew_pct: number | null;
  sentinel_mismatch_pct: number | null;
  recorded_at: string;
};

export type MaintenanceEvent = {
  id: number;
  status: string;
  diagnosis: string;
  actions: string[];
  affected_rows: number;
  started_at: string;
};

export type HealthData = {
  latest: HealthSnapshot | null;
  history: HealthSnapshot[];
  events: MaintenanceEvent[];
};

export type SearchResponse = {
  query: string;
  latency_ms: number;
  results: DocumentItem[];
};

export type NeighborsResponse = {
  document: DocumentItem;
  neighbors: DocumentItem[];
};
