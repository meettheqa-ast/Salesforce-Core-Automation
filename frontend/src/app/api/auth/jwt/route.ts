/**
 * Returns the raw Google ID token captured during NextAuth sign-in, so the
 * client-side `apiFetch` helper can attach it as `Authorization: Bearer <token>`
 * when calling the FastAPI backend.
 *
 * The backend verifies the token directly against Google's JWKS endpoint --
 * no shared secret with NextAuth, no encrypted JWE handling. This avoids the
 * trap of trying to verify NextAuth's encrypted session cookies (which use
 * A256CBC-HS512 JWE, not HS256 JWT).
 *
 * 401 is returned for unauthenticated callers or when the captured Google
 * token has expired (the client should refetch after a NextAuth session
 * refresh).
 */

import { auth } from "@/auth";
import { getToken } from "next-auth/jwt";
import { NextResponse } from "next/server";

const SECRET = process.env.AUTH_SECRET || process.env.NEXTAUTH_SECRET;

export async function GET(req: Request) {
  const session = await auth();
  if (!session?.user) {
    return new NextResponse("unauthenticated", { status: 401 });
  }

  if (!SECRET) {
    return new NextResponse("server misconfigured: AUTH_SECRET missing", { status: 500 });
  }

  // Pull the decoded NextAuth session token; we stashed `googleIdToken` on it
  // during the jwt() callback in src/auth.ts.
  const token = await getToken({
    req: req as unknown as Parameters<typeof getToken>[0]["req"],
    secret: SECRET,
    raw: false,
  });

  if (!token) {
    return new NextResponse("no session token", { status: 401 });
  }

  const googleIdToken = token.googleIdToken as string | undefined;
  if (!googleIdToken) {
    // Older sessions issued before we started capturing the id_token won't
    // have one. Force the client to sign in again to refresh the session.
    return new NextResponse("session has no google id_token; sign in again", {
      status: 401,
    });
  }

  // Optional client-visible hint for when the token expires. The backend
  // re-checks expiry on every request, so this is just a UX nicety.
  const expiresAt = (token.googleIdTokenExpiresAt as number | undefined) ?? 0;
  if (expiresAt && expiresAt * 1000 < Date.now()) {
    return new NextResponse("google id_token expired; sign in again", {
      status: 401,
    });
  }

  return new NextResponse(googleIdToken, {
    status: 200,
    headers: { "Content-Type": "text/plain", "Cache-Control": "no-store" },
  });
}
