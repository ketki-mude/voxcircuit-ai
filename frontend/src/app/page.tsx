import { AssistantStatus } from "@/components/assistant-status";
import { CallConsole } from "@/components/call/call-console";
import { SiteHeader } from "@/components/site-header";

export default function Home() {
  return (
    <>
      <SiteHeader status={<AssistantStatus />} />
      <CallConsole />
    </>
  );
}
