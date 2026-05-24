import { createFileRoute } from "@tanstack/react-router";

import { Stub } from "@/components/Stub";

export const Route = createFileRoute("/drawers/$id")({
  component: DrawerPage,
});

function DrawerPage() {
  const { id } = Route.useParams();
  return <Stub title={`drawer · ${id.slice(0, 32)}…`} legacy={`/ui/drawer/${id}`} />;
}
