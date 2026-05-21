/**
 * NextAuth v5 config: Google OAuth + JWT sessions, restricted to a single
 * Google Workspace domain.
 *
 * The `hd` (hosted-domain) param tells Google to only show the account picker
 * for the configured Workspace. The `signIn` callback re-checks server-side
 * because `hd` is not a security boundary -- a malicious user can still
 * post-process URLs.
 *
 * Auth handoff to the backend:
 *   1. NextAuth keeps the Google `id_token` AND `refresh_token` on the JWT
 *      cookie session. The `jwt` callback below transparently exchanges the
 *      refresh token for a fresh id_token whenever the current one is
 *      within ~60s of expiry.
 *   2. The Next.js route `/api/auth/jwt` returns the (always fresh) id_token
 *      to the browser, where `frontend/src/lib/api.ts` caches it in memory.
 *   3. The FastAPI backend verifies the ID token against Google's JWKS
 *      (`verify_google_id_token` in `ai_qa_portal/backend/services/auth.py`).
 *      It does NOT issue or verify a separate portal JWT, and
 *      NEXTAUTH_SECRET is never read by the backend.
 *
 * Why the silent refresh:
 *   Google ID tokens live ~1 hour. Without refresh, locking your laptop for
 *   more than that would force a logout on the next API call. The refresh
 *   flow keeps the cookie / API layer honest indefinitely until the user
 *   either signs out manually or the NextAuth session cookie's outer 30-day
 *   bound elapses. See docs/silent-google-token-refresh details inline.
 */

import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import type { JWT } from "next-auth/jwt";

const ALLOWED_DOMAIN = (process.env.ALLOWED_EMAIL_DOMAIN || "astounddigital.com")
  .trim()
  .toLowerCase();

// Refresh ~60s before actual expiry so we never serve a token that
// dies mid-request, but also don't burn refresh quota every minute.
const REFRESH_SAFETY_BUFFER_S = 60;

interface TokenExchangeResponse {
  id_token?: string;
  access_token?: string;
  expires_in: number;
  // Google may rotate the refresh_token; persist when present.
  refresh_token?: string;
  error?: string;
  error_description?: string;
}

/**
 * Trade the stored Google refresh_token for a fresh id_token. Called from
 * the `jwt` callback whenever the existing token is expiring. Returns an
 * updated JWT on success or the same JWT with ``error = "RefreshTokenError"``
 * on any failure (network, invalid_grant, missing id_token, ...).
 *
 * Failure modes that land here are all expected and benign:
 *   - User revoked portal access from https://myaccount.google.com/permissions.
 *   - Refresh token was rotated and the previous one was reused.
 *   - Google service blip / timeout.
 * In every case the route handler at /api/auth/jwt returns 401 and the
 * existing client-side recovery in api.ts bounces the user to /login.
 */
async function refreshGoogleAccessToken(token: JWT): Promise<JWT> {
  const refreshToken = token.googleRefreshToken as string | undefined;
  if (!refreshToken) {
    return { ...token, error: "RefreshTokenError" };
  }
  try {
    const res = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: process.env.GOOGLE_CLIENT_ID || "",
        client_secret: process.env.GOOGLE_CLIENT_SECRET || "",
        grant_type: "refresh_token",
        refresh_token: refreshToken,
      }),
    });
    const body = (await res.json()) as TokenExchangeResponse;
    if (!res.ok || body.error || !body.id_token) {
      console.error(
        "Google refresh failed:",
        res.status,
        body.error || body.error_description || "no id_token in response",
      );
      return { ...token, error: "RefreshTokenError" };
    }
    return {
      ...token,
      googleIdToken: body.id_token,
      googleIdTokenExpiresAt: Math.floor(Date.now() / 1000) + body.expires_in,
      // Google sometimes rotates the refresh_token; persist when it does.
      googleRefreshToken: body.refresh_token ?? refreshToken,
      error: undefined,
    };
  } catch (err) {
    console.error("Google refresh threw:", err);
    return { ...token, error: "RefreshTokenError" };
  }
}

export const { handlers, signIn, signOut, auth } = NextAuth({
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
      authorization: {
        params: {
          // Google's account picker will pre-filter to this Workspace.
          // Empty string disables the filter (any Google account).
          hd: ALLOWED_DOMAIN || undefined,
          // ``offline`` is what tells Google to return a refresh_token in
          // the OAuth response. Without it, the user has to re-OAuth every
          // hour (the bug we're fixing). ``consent`` is required because
          // Google only emits refresh_token on EXPLICIT consent; users who
          // already signed in pre-refresh will see one extra consent screen
          // on their next sign-in and never again.
          access_type: "offline",
          prompt: "consent",
        },
      },
    }),
  ],
  session: { strategy: "jwt" },
  pages: {
    signIn: "/login",
    error: "/login",
  },
  callbacks: {
    async signIn({ profile }) {
      if (!ALLOWED_DOMAIN) return true;
      const email = (profile?.email || "").toLowerCase();
      const okDomain =
        email.endsWith("@" + ALLOWED_DOMAIN) ||
        // Some Workspace setups expose `hd` directly on the profile.
        (profile as { hd?: string })?.hd === ALLOWED_DOMAIN;
      return okDomain;
    },
    async jwt({ token, account, profile }) {
      // Three-branch lifecycle:
      //   1. Initial sign-in: `account` is present, capture the OAuth
      //      response (id_token + refresh_token + expires_at).
      //   2. Subsequent invocations + token still fresh: return as-is.
      //   3. Subsequent invocations + token expiring: silently refresh
      //      against Google's /token endpoint.
      if (account?.id_token) {
        token.googleIdToken = account.id_token;
        // `expires_at` is in seconds-since-epoch; we'll use it on every
        // subsequent jwt() call to decide whether to refresh.
        if (typeof account.expires_at === "number") {
          token.googleIdTokenExpiresAt = account.expires_at;
        }
        // refresh_token is only returned when access_type=offline AND
        // prompt=consent are both set on the authorization URL (above).
        // Existing users who signed in before this change won't have it
        // and will be force-rerouted to consent on their next request;
        // see comments in /api/auth/jwt/route.ts.
        if (account.refresh_token) {
          token.googleRefreshToken = account.refresh_token;
        }
        if (profile) {
          token.email = profile.email;
          token.name = profile.name;
          token.picture = (profile as { picture?: string }).picture;
          token.sub = (profile as { sub?: string }).sub || token.sub;
        }
        return token;
      }

      // Branches 2 + 3: no `account` -- this is a re-evaluation of the
      // existing session (every getToken / auth() call). Decide whether
      // to refresh based on the stored expiry.
      const expSeconds = (token.googleIdTokenExpiresAt as number | undefined) ?? 0;
      const nowSeconds = Math.floor(Date.now() / 1000);
      if (expSeconds - REFRESH_SAFETY_BUFFER_S > nowSeconds) {
        // Still good for at least another minute.
        return token;
      }
      return refreshGoogleAccessToken(token);
    },
    async session({ session, token }) {
      // Mirror token claims into the session object so the client can read them.
      if (session.user) {
        session.user.email = (token.email as string | undefined) || session.user.email;
        session.user.name = (token.name as string | undefined) || session.user.name;
        session.user.image = (token.picture as string | undefined) || session.user.image;
      }
      // Surface the refresh-error flag on the session so the proxy
      // (frontend/src/proxy.ts) can treat it as unauthenticated and
      // bounce to /login proactively, instead of letting the user load
      // a page that will 401 on its first API call.
      if (token?.error) {
        (session as { error?: string }).error = token.error as string;
      }
      return session;
    },
  },
});
