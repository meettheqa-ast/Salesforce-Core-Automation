"use client";

import useSWR from "swr";
import { useSession } from "next-auth/react";
import { api, type MeResponse, type MembershipRow } from "@/lib/api";

const AUTH_DISABLED =
  (process.env.NEXT_PUBLIC_AUTH_DISABLED || "").toLowerCase() === "true";

/** Cached fetch of /api/me. Returns the current user + memberships.
 *
 *  Gated on NextAuth session status to avoid firing api.me() (and the
 *  apiFetch -> 401 -> recovery cascade) on pages where we're not signed in
 *  yet (most importantly /login, which mounts the navbar). */
export function useMe() {
  const { status } = useSession();

  // SWR `key=null` short-circuits the fetcher entirely.
  const key = AUTH_DISABLED
    ? "me-auth-disabled"
    : status === "authenticated"
      ? "me"
      : null;

  const { data, error, isLoading, mutate } = useSWR<MeResponse>(
    key,
    () => api.me(),
    {
      revalidateOnFocus: false,
      // Refresh every 5 min so role/membership changes propagate without a reload.
      refreshInterval: 5 * 60 * 1000,
      shouldRetryOnError: false,
    },
  );
  return { me: data ?? null, error, isLoading, refresh: mutate };
}

/** Helper: what role does this user have on this project? Admin overrides to PM. */
export function membershipFor(me: MeResponse | null, projectSlug: string): MembershipRow["role"] | null {
  if (!me) return null;
  if (me.is_admin || me.global_role === "admin") return "pm";
  const m = (me.memberships || []).find((x) => x.project_slug === projectSlug);
  return m ? m.role : null;
}

const RANK = { member: 1, lead: 2, pm: 3 } as const;

export function roleAtLeast(
  actual: MembershipRow["role"] | null | undefined,
  required: MembershipRow["role"],
): boolean {
  if (!actual) return false;
  return RANK[actual] >= RANK[required];
}
