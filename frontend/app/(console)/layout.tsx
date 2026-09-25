import { AppShell } from "@/components/layout/app-shell";
import { SessionProvider } from "@/stores/session-context";

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <AppShell>{children}</AppShell>
    </SessionProvider>
  );
}
