/**
 * TypeScript shapes for the /api/v1/* endpoints exposed by mempalace.
 *
 * These mirror the dicts returned by ``mempalace.mcp_server.tool_*`` and
 * the wrappers in ``mempalace/http_api.py``. When a Python tool's return
 * shape changes, update this file in lockstep — TS strict mode will
 * surface the call sites that need to follow.
 */

export interface PalaceSummary {
  total_drawers: number;
  wings: Record<string, number>;
  rooms: Record<string, number>;
  protocol?: string;
  aaak_dialect?: string;
  error?: string;
  partial?: boolean;
}

export interface WingsResponse {
  wings: Record<string, number>;
  error?: string;
}

export interface RoomsResponse {
  rooms: Record<string, number>;
  error?: string;
}

export interface Drawer {
  drawer_id: string;
  wing: string;
  room: string;
  content_preview: string;
}

export interface DrawerListResponse {
  drawers: Drawer[];
  count: number;
  total?: number;
  error?: string;
}

export interface DrawerDetail {
  drawer_id: string;
  content: string;
  wing: string;
  room: string;
  metadata: Record<string, string | number | boolean>;
  error?: string;
}

export interface SearchHit {
  id?: string;
  drawer_id?: string;
  score?: number;
  content?: string;
  snippet?: string;
  text?: string;
  wing: string;
  room: string;
}

export interface KGEntityHit {
  id: string;
  name: string;
  type: string;
  degree: number;
}

export interface SearchResponse {
  mode: "semantic" | "fts";
  query?: string;
  count?: number;
  results: SearchHit[];
  /** Populated by the server-side KG fallback when drawer search returned
   *  nothing — entity names that match by substring even though no drawer
   *  mentions them verbatim. */
  kg_entities?: KGEntityHit[];
  kg_fallback?: boolean;
  error?: string;
}

export interface OpRecord {
  timestamp: string;
  operation: string;
  params: Record<string, unknown>;
  result?: unknown;
}

export interface RecentOpsResponse {
  operations: OpRecord[];
  total_in_log?: number;
  note?: string;
  error?: string;
}

export interface OpSeriesBucket {
  key: string;
  counts: Record<string, number>;
}

export interface OpSeriesResponse {
  days: number;
  bucket: "hour" | "day";
  total_ops: number;
  op_types: string[];
  palette: Record<string, string>;
  buckets: OpSeriesBucket[];
}

export interface KGStats {
  entities: number;
  triples: number;
  current_facts: number;
  expired_facts: number;
  relationship_types: string[];
}

export interface KGFact {
  /** Triple id, populated by query_entity. Required to call delete_triple. */
  id?: string;
  subject: string;
  predicate: string;
  object: string;
  valid_from: string | null;
  valid_to: string | null;
  current?: boolean;
  direction?: "outgoing" | "incoming";
  /** ``entity`` (subject→object is two entities) or ``text`` (object is
   *  inline prose stored in the triple's value_text column). */
  object_kind?: "entity" | "text";
}

export interface KGEntityResponse {
  entity: string;
  facts: KGFact[];
  count: number;
  error?: string;
}

export interface KGGraphNode {
  id: string;
  name: string;
  type: string;
  degree: number;
}

export interface KGGraphEdge {
  s: string;
  p: string;
  o: string;
  valid_from: string | null;
  valid_to: string | null;
  current: boolean;
}

export interface KGGraphResponse {
  nodes: KGGraphNode[];
  edges: KGGraphEdge[];
  truncated: boolean;
}

export interface HealthCheck {
  name: string;
  status: "ok" | "warn" | "fail";
  detail: string;
  value?: unknown;
  signature?: string;
  extra?: Record<string, unknown>;
}

export interface HealthResponse {
  overall: "ok" | "warn" | "fail";
  checks: HealthCheck[];
}
