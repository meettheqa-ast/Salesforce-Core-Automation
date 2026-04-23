/**
 * NextAuth v5 config: Google OAuth + JWT sessions, restricted to a single
 * Google Workspace domain.
 *
 * The `hd` (hosted-domain) param tells Google to only show the account picker
 * for the configured Workspace. The `signIn` callback re-checks server-side
 * because `hd` is not a security boundary -- a malicious user can still
 * post-process URLs.
 *
 * Tokens are HS256-signed with NEXTAUTH_SECRET (same env var the FastAPI
 * backend reads to verify them in `ai_qa_portal/backend/services/auth.py`).
 */

import NextAuth from "next-auth";
import Google from "next-auth/providers/google";

const ALLOWED_DOMAIN = (process.env.ALLOWED_EMAIL_DOMAIN || "astounddigital.com")
  .trim()
  .toLowerCase();

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
          prompt: "select_account",
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
      // Capture the raw Google id_token on the initial sign-in. The backend
      // verifies this directly against Google's JWKS -- no shared secret with
      // NextAuth required. `account` is only present on the first call after
      // a successful OAuth flow; on subsequent calls we already have it on
      // the token from a previous round.
      if (account?.id_token) {
        token.googleIdToken = account.id_token;
        // `expires_at` is in seconds-since-epoch; we'll use it client-side to
        // know when a refresh is due (Google ID tokens last 1h).
        if (typeof account.expires_at === "number") {
          token.googleIdTokenExpiresAt = account.expires_at;
        }
      }
      if (profile) {
        token.email = profile.email;
        token.name = profile.name;
        token.picture = (profile as { picture?: string }).picture;
        token.sub = (profile as { sub?: string }).sub || token.sub;
      }
      return token;
    },
    async session({ session, token }) {
      // Mirror token claims into the session object so the client can read them.
      if (session.user) {
        session.user.email = (token.email as string | undefined) || session.user.email;
        session.user.name = (token.name as string | undefined) || session.user.name;
        session.user.image = (token.picture as string | undefined) || session.user.image;
      }
      return session;
    },
  },
});
