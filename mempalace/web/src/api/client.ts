/**
 * Tiny fetch wrapper. All UI requests go through here so retry / logging /
 * future authentication has one chokepoint.
 *
 * The daemon is loopback-only on 127.0.0.1:9876. In dev (``npm run dev``)
 * Vite proxies ``/api/*`` to it; in prod the bundle is served from the
 * daemon itself so requests are same-origin.
 */
export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export async function api<T>(
  path: string,
  init?: RequestInit & { params?: Record<string, string | number | undefined> },
): Promise<T> {
  let url = path.startsWith("/") ? path : `/${path}`;
  if (init?.params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(init.params)) {
      if (v === undefined || v === "") continue;
      qs.set(k, String(v));
    }
    const s = qs.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }
  const res = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
  });
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // Non-JSON response — leave body null.
  }
  if (!res.ok) {
    throw new ApiError(res.status, `HTTP ${res.status} on ${path}`, body);
  }
  return body as T;
}
