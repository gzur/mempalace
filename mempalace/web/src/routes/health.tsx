import { createFileRoute } from "@tanstack/react-router";

import { Stub } from "@/components/Stub";

export const Route = createFileRoute("/health")({
  component: () => <Stub title="health" legacy="/ui/health" />,
});
