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
    async jwt({ token, profile }) {
      // Persist Google sub + display fields into the JWT we hand to the backend.
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
