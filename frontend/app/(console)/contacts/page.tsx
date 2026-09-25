"use client";

import { ContactsView } from "@/features/contacts/contacts-view";
import { useSession } from "@/stores/session-context";

export default function ContactsPage() {
  const { business } = useSession();
  // Remount per business so no state from another business survives a switch.
  return <ContactsView key={business?.id ?? "none"} />;
}
