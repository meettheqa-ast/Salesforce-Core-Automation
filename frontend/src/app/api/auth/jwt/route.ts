/**
 * Returns the raw NextAuth session JWT for the current user, so the client-side
 * `apiFetch` helper can attach it as `Authorization: Bearer <token>` when
 * calling the FastAPI backend.
 *
 * The JWT is signed with NEXTAUTH_SECRET (HS256) and the backend verifies the
 * same signature. We hand it back as plain text rather than JSON to keep the
 * client read trivial.
 *
 * 401 is returned for unauthenticated callers so the client can react cleanly.
 */

import { auth } from "@/auth";
import { encode, getToken } from "next-auth/jwt";
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

  // Pull the same token NextAuth issued (cookie -> decoded payload) and re-encode
  // it as a fresh HS256 JWT for the backend. This guarantees the backend sees
  // the canonical claims (email, name, picture) NextAuth wrote during signIn.
  const token = await getToken({
    req: req as unknown as Parameters<typeof getToken>[0]["req"],
    secret: SECRET,
    raw: false,
  });

  if (!token) {
    return new NextResponse("no session token", { status: 401 });
  }

  const reissued = await encode({
    token: {
      sub: token.sub,
      email: token.email,
      name: token.name,
      picture: token.picture,
    },
    secret: SECRET,
    maxAge: 60 * 60, // 1 hour; client refetches when 401 fires.
    salt: "",
  });

  return new NextResponse(reissued, {
    status: 200,
    headers: { "Content-Type": "text/plain", "Cache-Control": "no-store" },
  });
}
