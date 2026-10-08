import type { GraphData, HealthData, NeighborsResponse, SearchResponse } from "./types";

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(payload.error || `Request failed (${response.status})`);
  return payload as T;
}

export async function getGraph(
  category?: string,
  limit = 90,
): Promise<GraphData> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (category) params.set("category", category);
  return getJson(`/api/graph/?${params}`);
}

export async function getHealth(): Promise<HealthData> {
  return getJson("/api/health/");
}

export async function searchDocuments(
  query: string,
  category?: string,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query, limit: "20" });
  if (category) params.set("category", category);
  return getJson(`/api/search/?${params}`);
}

export async function getNeighbors(id: number): Promise<NeighborsResponse> {
  return getJson(`/api/documents/${id}/neighbors/?limit=4`);
}
