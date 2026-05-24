import { createFileRoute } from "@tanstack/react-router";

import { Stub } from "@/components/Stub";

export const Route = createFileRoute("/search")({
  component: () => <Stub title="search" legacy="/ui/search" />,
});
