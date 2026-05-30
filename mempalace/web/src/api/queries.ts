/**
 * TanStack Query hooks for every /api/v1 endpoint.
 *
 * Discoverability rule: add the new hook here when you add a route or a
 * component that needs server state. Components call ``useFooQuery`` and
 * never touch ``api()`` directly.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type {
  DrawerDetail,
  DrawerListResponse,
  HealthResponse,
  KGEntityResponse,
  KGGraphResponse,
  KGStats,
  OpSeriesResponse,
  PalaceSummary,
  RecentOpsResponse,
  RoomsResponse,
  SearchResponse,
  WingsResponse,
} from "./types";

export const queryKeys = {
  palaceSummary: ["palace", "summary"] as const,
  palaceWings: ["palace", "wings"] as const,
  palaceRooms: (wing?: string) => ["palace", "rooms", wing] as const,
  drawers: (params: { wing?: string; room?: string; offset: number; limit: number }) =>
    ["drawers", params] as const,
  drawer: (id: string) => ["drawers", id] as const,
  search: (params: {
    q: string;
    wing?: string;
    room?: string;
    mode: "semantic" | "fts";
    limit: number;
  }) => ["search", params] as const,
  opsRecent: (limit: number) => ["ops", "recent", limit] as const,
  opsSeries: (days: number, bucket?: "hour" | "day") =>
    ["ops", "series", days, bucket] as const,
  kgStats: ["kg", "stats"] as const,
  kgEntity: (name: string, direction: string) => ["kg", "entity", name, direction] as const,
  kgGraph: (includeExpired: boolean, maxNodes?: number) =>
    ["kg", "graph", includeExpired, maxNodes] as const,
  health: ["health"] as const,
};

export function usePalaceSummary() {
  return useQuery({
    queryKey: queryKeys.palaceSummary,
    queryFn: () => api<PalaceSummary>("/api/v1/palace/summary"),
    staleTime: 30_000,
  });
}

export function usePalaceWings() {
  return useQuery({
    queryKey: queryKeys.palaceWings,
    queryFn: () => api<WingsResponse>("/api/v1/palace/wings"),
    staleTime: 30_000,
  });
}

export function usePalaceRooms(wing?: string) {
  return useQuery({
    queryKey: queryKeys.palaceRooms(wing),
    queryFn: () => api<RoomsResponse>("/api/v1/palace/rooms", { params: { wing } }),
    staleTime: 30_000,
    enabled: wing === undefined || !!wing,
  });
}

export function useDrawers(params: {
  wing?: string;
  room?: string;
  offset: number;
  limit: number;
}) {
  return useQuery({
    queryKey: queryKeys.drawers(params),
    queryFn: () => api<DrawerListResponse>("/api/v1/drawers", { params }),
    staleTime: 10_000,
  });
}

export function useDrawer(drawerId: string) {
  return useQuery({
    queryKey: queryKeys.drawer(drawerId),
    queryFn: () => api<DrawerDetail>(`/api/v1/drawers/${encodeURIComponent(drawerId)}`),
    enabled: !!drawerId,
  });
}

export function useSearch(params: {
  q: string;
  wing?: string;
  room?: string;
  mode: "semantic" | "fts";
  limit: number;
}) {
  return useQuery({
    queryKey: queryKeys.search(params),
    queryFn: () => api<SearchResponse>("/api/v1/search", { params }),
    enabled: !!params.q,
  });
}

export function useRecentOps(limit = 20) {
  return useQuery({
    queryKey: queryKeys.opsRecent(limit),
    queryFn: () => api<RecentOpsResponse>("/api/v1/ops", { params: { limit } }),
    refetchInterval: 15_000,
  });
}

export function useOpsSeries(days = 30, bucket?: "hour" | "day") {
  return useQuery({
    queryKey: queryKeys.opsSeries(days, bucket),
    queryFn: () => api<OpSeriesResponse>("/api/v1/ops/series", { params: { days, bucket } }),
    staleTime: 60_000,
  });
}

export function useKGStats() {
  return useQuery({
    queryKey: queryKeys.kgStats,
    queryFn: () => api<KGStats>("/api/v1/kg/stats"),
    staleTime: 30_000,
  });
}

export function useKGEntity(name: string, direction: "outgoing" | "incoming" | "both" = "both") {
  return useQuery({
    queryKey: queryKeys.kgEntity(name, direction),
    queryFn: () =>
      api<KGEntityResponse>("/api/v1/kg/entity", { params: { name, direction } }),
    enabled: !!name,
  });
}

export function useKGGraph(includeExpired = false, maxNodes?: number) {
  return useQuery({
    queryKey: queryKeys.kgGraph(includeExpired, maxNodes),
    queryFn: () =>
      api<KGGraphResponse>("/api/v1/kg/graph", {
        params: {
          include_expired: includeExpired ? "1" : undefined,
          max_nodes: maxNodes,
        },
      }),
    staleTime: 30_000,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => api<HealthResponse>("/api/v1/health"),
    refetchInterval: 30_000,
  });
}

export interface KGMergeResponse {
  success: boolean;
  from?: string;
  into?: string;
  subject_rewrites?: number;
  object_rewrites?: number;
  triples_rewritten?: number;
  error?: string;
  note?: string;
}

/**
 * Fold one KG entity into another. On success invalidates every KG
 * query so the graph view, entity pages, and stats redraw against the
 * post-merge data.
 */
export function useKGMerge() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { from: string; into: string }) =>
      api<KGMergeResponse>("/api/v1/kg/merge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(vars),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kg"] });
    },
  });
}

export interface KGSetEntityTypeResponse {
  success: boolean;
  name?: string;
  type?: string;
  previous_type?: string;
  error?: string;
}

export interface KGAddTripleResponse {
  success: boolean;
  triple_id?: string;
  fact?: string;
  error?: string;
}

export interface KGDeleteTripleResponse {
  success: boolean;
  deleted?: boolean;
  triple_id?: string;
  fact?: string;
  error?: string;
}

export interface KGDeleteEntityResponse {
  success: boolean;
  deleted?: boolean;
  name?: string;
  triples_deleted?: number;
  incident_triples?: number;
  error?: string;
}

/** Delete a KG entity. ``cascade=true`` sweeps incident triples first. */
export function useKGDeleteEntity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { name: string; cascade?: boolean }) => {
      const q = vars.cascade ? "?cascade=1" : "";
      return api<KGDeleteEntityResponse>(
        `/api/v1/kg/entity/${encodeURIComponent(vars.name)}${q}`,
        { method: "DELETE" },
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kg"] });
    },
  });
}

/** Hard-delete a triple by id. Invalidates KG caches on success. */
export function useKGDeleteTriple() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tripleId: string) =>
      api<KGDeleteTripleResponse>(
        `/api/v1/kg/triples/${encodeURIComponent(tripleId)}`,
        { method: "DELETE" },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kg"] });
    },
  });
}

/** Add a new triple to the KG. Invalidates KG caches on success. */
export function useKGAdd() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      subject: string;
      predicate: string;
      object: string;
      value_text?: string;
      valid_from?: string;
    }) =>
      api<KGAddTripleResponse>("/api/v1/kg/triples", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(vars),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kg"] });
    },
  });
}

/** PATCH an existing entity's ``type``. Invalidates KG caches on success. */
export function useKGSetEntityType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { name: string; type: string }) =>
      api<KGSetEntityTypeResponse>(
        `/api/v1/kg/entity/${encodeURIComponent(vars.name)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ type: vars.type }),
        },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kg"] });
    },
  });
}
