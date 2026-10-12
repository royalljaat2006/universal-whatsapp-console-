import { AppShell } from "@/components/layout/app-shell";
import { SessionProvider } from "@/stores/session-context";
import { UiModeProvider } from "@/stores/ui-mode";

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <UiModeProvider>
        <AppShell>{children}</AppShell>
      </UiModeProvider>
    </SessionProvider>
  );
}
