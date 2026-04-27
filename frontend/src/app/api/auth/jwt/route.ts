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
 * Expiry is intentionally NOT checked here. The backend re-verifies via JWKS
 * on every request (and returns 401 with a clear "Google ID token expired"
 * message), and apiFetch handles that case by signing the user out + sending
 * them to /login. Checking expiry here would just mean two layers reporting
 * the same condition, which made the recovery flow harder to reason about.
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
