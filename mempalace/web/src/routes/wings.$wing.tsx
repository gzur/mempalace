import { createFileRoute } from "@tanstack/react-router";

import { Stub } from "@/components/Stub";

export const Route = createFileRoute("/wings/$wing")({
  component: WingPage,
});

function WingPage() {
  const { wing } = Route.useParams();
  return <Stub title={`wing · ${wing}`} legacy={`/ui/wing/${wing}`} />;
}
