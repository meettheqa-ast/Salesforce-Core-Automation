"use client";

/**
 * Settings -> Infrastructure -> Locator Health card.
 *
 * Summary surface for the `GlobalLocators.robot` scan. Reads from
 * `GET /api/locators/status` so opening the Settings page does NOT
 * trigger a fresh scan; the card renders whatever the last completed
 * scan reported (or NEVER_RUN). Run Scan deep-links to the full
 * scanner at /locators, which already has the credentials bar +
 * detailed per-locator drill-down.
 *
 * Why this card exists: the IA audit flagged that locator health
 * lived in Admin > Dev Tools, surfacing it only to admins and only
 * as a one-off run. Lifting it into Settings Infrastructure puts it
 * alongside RF-MCP + Keyword Catalog where QA leads + project leads
 * naturally check the platform's operational state after a Salesforce
 * release.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api, type LocatorScanStatus, type LocatorStatusResponse } from "@/lib/api";
import AnimatedCard from "@/components/cards/AnimatedCard";
import StatusPill from "@/components/data/StatusPill";
import LoadingState from "@/components/feedback/LoadingState";
import ErrorBanner from "@/components/feedback/ErrorBanner";

const STATUS_TONE: Record<LocatorScanStatus, "success" | "warning" | "danger" | "muted"> = {
  PASS: "success",
  STALE: "warning",
  FAIL: "danger",
  NEVER_RUN: "muted",
};

const STATUS_LABEL: Record<LocatorScanStatus, string> = {
  PASS: "Healthy",
  STALE: "Stale locators",
  FAIL: "Last scan failed",
  NEVER_RUN: "Never scanned",
};

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

export default function LocatorHealthCard() {
  const [status, setStatus] = useState<LocatorStatusResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.locators.status();
      setStatus(r);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <AnimatedCard glow="pink" delay={0.2}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-bold text-white">Locator Health</h3>
        {status && (
          <StatusPill
            tone={STATUS_TONE[status.last_scan_status]}
            label={STATUS_LABEL[status.last_scan_status]}
          />
        )}
      </div>

      <p className="text-xs text-slate-400 mb-4">
        Scans <code className="text-slate-300">GlobalLocators.robot</code>
        {" "}against the live Salesforce Lightning DOM. Run after each
        Salesforce release so stale selectors are caught before a sprint
        of tests blows up.
      </p>

      <ErrorBanner error={err} onDismiss={() => setErr(null)} />

      {status === null ? (
        <LoadingState variant="block" label="Loading last scan…" />
      ) : status.last_scan_status === "NEVER_RUN" ? (
        <p className="text-xs text-slate-500 italic mb-4">
          No scan recorded yet. Open the full scanner to run the first one.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2 mb-4 text-center">
            <Tile label="Healthy" value={status.healthy_count} tone="emerald" />
            <Tile label="Stale" value={status.stale_count} tone="amber" />
            <Tile label="Failed" value={status.failed_count} tone="red" />
          </div>
          <p className="text-[11px] text-slate-500 mb-4">
            Last scan {timeAgo(status.last_scan_at)}
            {status.sandbox_url && (
              <>
                {" "}·{" "}
                <span className="font-mono truncate inline-block max-w-[20rem] align-bottom">
                  {status.sandbox_url}
                </span>
              </>
            )}
          </p>
          {status.scan_error && (
            <p className="text-[11px] text-red-300 mb-4 break-words">
              {status.scan_error}
            </p>
          )}
        </>
      )}

      <div className="flex gap-2">
        {/* Run scan deep-links to /locators which already has the
            credentials bar + detailed drill-down. We don't duplicate
            that flow inside Settings -- one canonical place for the
            actual scan runner keeps mental load low. */}
        <Link
          href="/locators"
          className="flex-1 text-center py-2 rounded-lg bg-pink-500/20 text-pink-300 border border-pink-500/30 text-sm font-semibold hover:bg-pink-500/30 transition"
        >
          Open full scanner
        </Link>
        <motion.button
          type="button"
          whileTap={{ scale: 0.97 }}
          onClick={() => void load()}
          disabled={busy}
          className="px-3 py-2 rounded-lg glass text-xs text-slate-300 hover:text-white disabled:opacity-50"
          title="Re-read the last scan summary (does not trigger a new scan)"
        >
          {busy ? "…" : "Refresh"}
        </motion.button>
      </div>
    </AnimatedCard>
  );
}

function Tile({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "emerald" | "amber" | "red";
}) {
  const cls =
    tone === "emerald"
      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-100"
      : tone === "amber"
        ? "border-amber-500/30 bg-amber-500/10 text-amber-100"
        : "border-red-500/30 bg-red-500/10 text-red-100";
  return (
    <div className={`rounded-lg border ${cls} py-2`}>
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-[10px] uppercase tracking-wider opacity-80">{label}</div>
    </div>
  );
}
