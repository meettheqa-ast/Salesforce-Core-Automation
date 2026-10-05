/**
 * Server-side proxy for the FastAPI backend's `/health` endpoint.
 *
 * Why this exists: `StatusHub.tsx` probes this path (not the backend
 * directly) because `/health` on the backend isn't behind the Next.js
 * bearer-token wrapper, and a direct cross-origin `fetch` from the browser
 * would conflate CORS failures with real backend-down states. Proxying
 * server-side gives a clean HTTP status that mirrors the backend's actual
 * health -- no CORS, no auth token needed.
 *
 * Uses the same `NEXT_PUBLIC_API_URL` env var as `lib/api.ts` (defaulting to
 * `http://localhost:8000` for local dev) since this runs on the Next.js
 * server, which sits on the same host as the backend in every deployment
 * topology we support (local dev, Docker compose, Fly + Vercel via the
 * public backend URL).
 */
import { NextResponse } from "next/server";

const BACKEND_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000")
  .trim()
  .replace(/\/+$/, "");

export async function GET() {
  try {
    const r = await fetch(`${BACKEND_URL}/health`, { cache: "no-store" });
    const text = await r.text();
    return new NextResponse(text, {
      status: r.status,
      headers: {
        "Content-Type": r.headers.get("content-type") || "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch (e) {
    return NextResponse.json(
      {
        status: "down",
        error: e instanceof Error ? e.message : "backend unreachable",
        backend_url: BACKEND_URL,
      },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
