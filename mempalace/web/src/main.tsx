import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";

import { routeTree } from "./routeTree.gen";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Aggressive retry is wasteful against a local daemon; if loopback
      // fails we'd rather surface the error immediately.
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

const router = createRouter({
  routeTree,
  // The SPA is served under /app/ by the daemon (see mempalace/http_server.py
  // _SPAStaticFiles mount). Without this basepath, history pushes synthesize
  // hrefs like ``/wings/foo`` instead of ``/app/wings/foo``; reloading that
  // URL hits the daemon's root router which has no /wings/* route, so the
  // user gets a 404 on what should be a deep link.
  basepath: "/app",
  defaultPreload: "intent",
  context: { queryClient },
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("missing #root element");

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
