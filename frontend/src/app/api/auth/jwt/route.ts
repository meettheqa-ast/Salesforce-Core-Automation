/**
 * Returns the Google ID token captured (and silently refreshed) by NextAuth,
 * so the client-side `apiFetch` helper can attach it as
 * ``Authorization: Bearer <token>`` when calling the FastAPI backend.
 *
 * The backend verifies the token directly against Google's JWKS endpoint --
 * no shared secret with NextAuth, no encrypted JWE handling. This avoids the
 * trap of trying to verify NextAuth's encrypted session cookies (which use
 * A256CBC-HS512 JWE, not HS256 JWT).
 *
 * Freshness contract:
 *   - ``auth.ts``'s ``jwt`` callback is invoked on every ``getToken()`` and
 *     transparently refreshes the Google ID token whenever the stored one is
 *     within 60s of expiry. By the time we read ``token.googleIdToken`` here
 *     it is always either fresh OR the callback already gave up and set
 *     ``token.error = "RefreshTokenError"`` (in which case we 401 here so
 *     the frontend's existing 401 recovery in lib/api.ts signs the user
 *     out cleanly instead of looping on a stale token).
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

  const token = await getToken({
    req: req as unknown as Parameters<typeof getToken>[0]["req"],
    secret: SECRET,
    raw: false,
  });

  if (!token) {
    return new NextResponse("no session token", { status: 401 });
  }

  // The jwt callback in auth.ts sets ``error = "RefreshTokenError"`` when
  // it cannot refresh (revoked access, invalid_grant, network failure,
  // legacy session without a refresh_token). In every case the right
  // recovery is to sign out + re-authenticate, NOT serve the stale token.
  if (token.error === "RefreshTokenError") {
    return new NextResponse("refresh failed; sign in again", { status: 401 });
  }

  const googleIdToken = token.googleIdToken as string | undefined;
  if (!googleIdToken) {
    return new NextResponse("session has no google id_token; sign in again", {
      status: 401,
    });
  }

  return new NextResponse(googleIdToken, {
    status: 200,
    headers: { "Content-Type": "text/plain", "Cache-Control": "no-store" },
  });
}
