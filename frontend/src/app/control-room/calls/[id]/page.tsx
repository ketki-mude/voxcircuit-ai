import { CallDetailPage } from "@/components/team/call-detail-page";

// Loads its data in the browser after sign-in, so navigation is not expected to be instant.
export const instant = false;

export default function Page() {
  return <CallDetailPage />;
}
