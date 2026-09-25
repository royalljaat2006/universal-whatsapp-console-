import { PlatformShell } from "@/features/platform/platform-shell";
import { SessionProvider } from "@/stores/session-context";

export default function PlatformLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <PlatformShell>{children}</PlatformShell>
    </SessionProvider>
  );
}
