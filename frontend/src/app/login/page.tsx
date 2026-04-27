import { redirect } from "next/navigation";
import { auth, signIn } from "@/auth";

const ALLOWED_DOMAIN = (process.env.ALLOWED_EMAIL_DOMAIN || "astounddigital.com").trim();

type SearchParams = Record<string, string | string[] | undefined>;

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const session = await auth();
  const params = await searchParams;

  // The auto-redirect away from /login when a session exists is the second
  // half of the historical refresh loop (apiFetch hits 401 -> bounces here ->
  // we'd send the user straight back to the page that just 401'd). We now
  // suppress the auto-redirect when the bouncer set ?reason=expired so the
  // user actually gets to see the sign-in button and recover.
  const reason = typeof params.reason === "string" ? params.reason : null;

  if (session?.user && reason !== "expired") {
    const from = typeof params.from === "string" ? params.from : "/";
    redirect(from && from !== "/login" ? from : "/");
  }

  const errorParam = typeof params.error === "string" ? params.error : null;
  // NextAuth surfaces auth errors as `?error=AccessDenied` (e.g. wrong domain).
  // ?reason=expired is set by lib/api.ts when /api/auth/jwt or any backend call
  // returns 401 after a token refresh attempt.
  const errorMessage =
    reason === "expired"
      ? "Your session expired. Sign in again to continue."
      : errorParam === "AccessDenied"
      ? `Sign-in is restricted to @${ALLOWED_DOMAIN} accounts.`
      : errorParam
      ? "Sign-in failed. Please try again."
      : null;

  const callbackUrl =
    typeof params.from === "string" && params.from !== "/login" ? params.from : "/";

  async function loginAction() {
    "use server";
    await signIn("google", { redirectTo: callbackUrl });
  }

  return (
    <div className="min-h-[80vh] flex items-center justify-center px-4">
      <div className="w-full max-w-md rounded-2xl border border-white/10 bg-white/5 backdrop-blur-xl p-8 shadow-2xl">
        <div className="flex flex-col items-center gap-3 mb-8">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-purple-500 to-cyan-400 animate-pulse-glow" />
          <h1 className="text-2xl font-bold text-white tracking-tight">AI QA Portal</h1>
          <p className="text-sm text-slate-400 text-center">
            Sign in with your <span className="text-white">@{ALLOWED_DOMAIN}</span> Google
            account to continue.
          </p>
        </div>

        {errorMessage && (
          <div className="mb-6 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
            {errorMessage}
          </div>
        )}

        <form action={loginAction}>
          <button
            type="submit"
            className="w-full flex items-center justify-center gap-3 rounded-xl bg-white text-slate-900 font-medium px-4 py-3 transition hover:bg-slate-100 active:scale-[0.99]"
          >
            <GoogleIcon />
            Continue with Google
          </button>
        </form>

        <p className="mt-6 text-xs text-slate-500 text-center leading-relaxed">
          By signing in you agree that your name, email, and profile picture from
          Google will be used to identify you within the portal.
        </p>
      </div>
    </div>
  );
}

function GoogleIcon() {
  return (
    <svg viewBox="0 0 48 48" width="20" height="20" aria-hidden="true">
      <path
        fill="#FFC107"
        d="M43.6 20.5H42V20H24v8h11.3C33.7 32.3 29.3 35.5 24 35.5c-6.4 0-11.5-5.1-11.5-11.5S17.6 12.5 24 12.5c2.9 0 5.6 1.1 7.6 2.9l5.7-5.7C33.7 6.4 29.1 4.5 24 4.5 13.2 4.5 4.5 13.2 4.5 24S13.2 43.5 24 43.5 43.5 34.8 43.5 24c0-1.2-.1-2.4-.4-3.5z"
      />
      <path
        fill="#FF3D00"
        d="M6.3 14.7l6.6 4.8C14.7 15.6 19 12.5 24 12.5c2.9 0 5.6 1.1 7.6 2.9l5.7-5.7C33.7 6.4 29.1 4.5 24 4.5 16.3 4.5 9.7 8.9 6.3 14.7z"
      />
      <path
        fill="#4CAF50"
        d="M24 43.5c5 0 9.6-1.9 13.1-5.1l-6-5.1c-2 1.4-4.4 2.2-7.1 2.2-5.3 0-9.7-3.2-11.3-7.6l-6.6 5.1C9.5 39 16.2 43.5 24 43.5z"
      />
      <path
        fill="#1976D2"
        d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.3 4.2-4.2 5.5l6 5.1C40.4 35.7 43.5 30.3 43.5 24c0-1.2-.1-2.4-.4-3.5z"
      />
    </svg>
  );
}
