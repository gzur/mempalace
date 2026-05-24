import { createFileRoute } from "@tanstack/react-router";

import { Stub } from "@/components/Stub";

export const Route = createFileRoute("/wings")({
  component: () => <Stub title="wings" legacy="/ui/" />,
});
