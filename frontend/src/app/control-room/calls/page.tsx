import { CallsPage } from "@/components/team/calls-page";

// Loads its data in the browser after sign-in, so navigation is not expected to be instant.
export const instant = false;

export default function Page() {
  return <CallsPage />;
}
