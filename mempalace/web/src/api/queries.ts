/**
 * TanStack Query hooks for every /api/v1 endpoint.
 *
 * Discoverability rule: add the new hook here when you add a route or a
 * component that needs server state. Components call ``useFooQuery`` and
 * never touch ``api()`` directly.
 */
import { useQuery } from "@tanstack/react-query";

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
