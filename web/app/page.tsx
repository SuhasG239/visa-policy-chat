import { ChatView } from "@/components/ChatView";
import { DisclaimerBar } from "@/components/DisclaimerBar";
import { SiteHeader } from "@/components/SiteHeader";

export default function Home() {
  return (
    <div className="flex min-h-dvh flex-col">
      <div className="sticky top-0 z-10">
        <SiteHeader />
        <DisclaimerBar />
      </div>
      <main className="flex flex-1 flex-col">
        <ChatView />
      </main>
    </div>
  );
}
