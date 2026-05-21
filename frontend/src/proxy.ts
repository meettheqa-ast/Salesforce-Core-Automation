/**
 * Auth gate. Anyone hitting any page other than /login or /api/auth/*
 * is redirected to /login if they don't have a valid NextAuth session.
 *
 * Static assets and the NextAuth handlers are excluded via the matcher below.
 *
 * Note: in Next.js 16, the `middleware.ts` file convention was renamed to
 * `proxy.ts`. Functionality is unchanged.
 */

import { auth } from "@/auth";
import { NextResponse } from "next/server";

const AUTH_DISABLED =
  (process.env.NEXT_PUBLIC_AUTH_DISABLED || "").toLowerCase() === "true";

export default auth((req) => {
  const { pathname } = req.nextUrl;

  // Always-public paths.
  if (
    pathname.startsWith("/api/auth") ||
    pathname.startsWith("/login") ||
    pathname === "/favicon.ico"
  ) {
    return NextResponse.next();
  }

  // Dev / pre-OAuth bypass: matches the backend's AUTH_DISABLED flag so the
  // pre-Google-OAuth frontend can still render against an open backend.
  if (AUTH_DISABLED) {
    return NextResponse.next();
  }

  // Two unauthenticated cases we treat identically:
  //   1. No session at all (cookie missing / expired).
  //   2. Session exists but the jwt callback in auth.ts gave up on
  //      refreshing the Google ID token and set ``error = "RefreshTokenError"``
  //      (revoked access, invalid_grant, legacy session without a refresh
  //      token). Without this branch the page renders normally and only
  //      breaks on the first API call -- which is exactly the "log me out
  //      randomly" UX we are fixing. Bouncing here makes the failure mode
  //      a clean redirect.
  const isLoggedIn = !!req.auth;
  const refreshFailed =
    (req.auth as { error?: string } | null | undefined)?.error === "RefreshTokenError";
  if (!isLoggedIn || refreshFailed) {
    const url = new URL("/login", req.nextUrl.origin);
    url.searchParams.set("from", pathname);
    if (refreshFailed) {
      url.searchParams.set("reason", "expired");
    }
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
});

export const config = {
  // Run proxy on everything EXCEPT Next.js internals and static assets.
  matcher: ["/((?!_next/static|_next/image|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)"],
};
