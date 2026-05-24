import { createFileRoute } from "@tanstack/react-router";

import { Stub } from "@/components/Stub";

export const Route = createFileRoute("/kg")({
  component: () => <Stub title="knowledge graph" legacy="/ui/kg" />,
});
