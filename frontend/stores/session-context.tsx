"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { setActiveBusiness, setActiveOrganization } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { authService } from "@/services/auth";
import { businessService } from "@/services/businesses";
import { organizationService } from "@/services/organizations";
import type { Business, MyMembership, User } from "@/types/api";

const ORG_STORAGE_KEY = "wp.activeOrganization";
const businessKey = (orgId: string) => `wp.activeBusiness.${orgId}`;

interface SessionState {
  user: User | null;
  memberships: MyMembership[];
  active: MyMembership | null;
  businesses: Business[];
  business: Business | null;
  loading: boolean;
  selectOrganization: (organizationId: string) => void;
  selectBusiness: (businessId: string) => void;
  /** Re-fetches the business list (e.g. after creating one) and, once loaded,
   * makes it the active business. */
  refreshBusinesses: (selectId?: string) => void;
  hasPermission: (code: string) => boolean;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionState | null>(null);

function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage unavailable (private mode): selection lasts for this tab only.
  }
}

/** Authenticated session + active organization + active business. Everything
 * here only shapes the UI; the API enforces every check server-side. */
export function SessionProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [memberships, setMemberships] = useState<MyMembership[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [businesses, setBusinesses] = useState<{ orgId: string; items: Business[] } | null>(null);
  const [businessId, setBusinessId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [me, mine] = await Promise.all([authService.me(), organizationService.myMemberships()]);
        if (cancelled) return;
        setUser(me);
        setMemberships(mine);
        const stored = readStored(ORG_STORAGE_KEY);
        const initial = mine.find((m) => m.organization.id === stored) ?? mine[0] ?? null;
        setActiveId(initial?.organization.id ?? null);
      } catch (cause) {
        if (cause instanceof ApiError && cause.isAuthError) router.replace("/login");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  const active = useMemo(
    () => memberships.find((m) => m.organization.id === activeId) ?? null,
    [memberships, activeId],
  );

  // Keep the API client's context headers in sync with the selection. Set
  // during render so children's first fetch already carries them.
  setActiveOrganization(active?.organization.id ?? null);

  // Load the businesses visible to this member whenever the organization changes.
  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    setActiveOrganization(activeId);
    businessService
      .list()
      .then((items) => {
        if (cancelled) return;
        setBusinesses({ orgId: activeId, items });
        const stored = readStored(businessKey(activeId));
        setBusinessId((items.find((b) => b.id === stored) ?? items[0])?.id ?? null);
      })
      .catch(() => {
        if (!cancelled) setBusinesses({ orgId: activeId, items: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  const refreshBusinesses = useCallback(
    (selectId?: string) => {
      if (!activeId) return;
      businessService
        .list()
        .then((items) => {
          setBusinesses({ orgId: activeId, items });
          const preferred = selectId ?? readStored(businessKey(activeId));
          const next = items.find((b) => b.id === preferred) ?? items[0];
          if (next) {
            store(businessKey(activeId), next.id);
            setBusinessId(next.id);
          }
        })
        .catch(() => undefined);
    },
    [activeId],
  );

  const businessItems = useMemo(
    () => (businesses && businesses.orgId === activeId ? businesses.items : []),
    [businesses, activeId],
  );
  const business = useMemo(
    () => businessItems.find((b) => b.id === businessId) ?? null,
    [businessItems, businessId],
  );
  setActiveBusiness(business?.id ?? null);

  const selectOrganization = useCallback((id: string) => {
    store(ORG_STORAGE_KEY, id);
    setActiveId(id);
  }, []);

  const selectBusiness = useCallback(
    (id: string) => {
      if (activeId) store(businessKey(activeId), id);
      setBusinessId(id);
    },
    [activeId],
  );

  const hasPermission = useCallback((code: string) => Boolean(active?.permissions.includes(code)), [active]);

  const logout = useCallback(async () => {
    await authService.logout().catch(() => undefined);
    router.replace("/login");
  }, [router]);

  const value = useMemo(
    () => ({
      user,
      memberships,
      active,
      businesses: businessItems,
      business,
      loading: loading || (Boolean(activeId) && businesses?.orgId !== activeId),
      selectOrganization,
      selectBusiness,
      refreshBusinesses,
      hasPermission,
      logout,
    }),
    [
      user,
      memberships,
      active,
      businessItems,
      business,
      loading,
      activeId,
      businesses,
      selectOrganization,
      selectBusiness,
      refreshBusinesses,
      hasPermission,
      logout,
    ],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession must be used inside SessionProvider");
  return context;
}
