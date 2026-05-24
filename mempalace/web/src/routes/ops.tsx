import { createFileRoute } from "@tanstack/react-router";

import { Stub } from "@/components/Stub";

export const Route = createFileRoute("/ops")({
  component: () => <Stub title="operations" legacy="/ui/operations" />,
});
