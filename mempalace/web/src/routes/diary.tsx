import { createFileRoute } from "@tanstack/react-router";

import { Stub } from "@/components/Stub";

export const Route = createFileRoute("/diary")({
  component: () => <Stub title="diary" />,
});
