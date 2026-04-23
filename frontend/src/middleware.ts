/**
 * Auth gate. Anyone hitting any page other than /login or /api/auth/*
 * is redirected to /login if they don't have a valid NextAuth session.
 *
 * Static assets and the NextAuth handlers are excluded via the matcher below.
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

  const isLoggedIn = !!req.auth;
  if (!isLoggedIn) {
    const url = new URL("/login", req.nextUrl.origin);
    url.searchParams.set("from", pathname);
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
});

export const config = {
  // Run middleware on everything EXCEPT Next.js internals and static assets.
  matcher: ["/((?!_next/static|_next/image|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)"],
};
