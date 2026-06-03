"use client";

/**
 * Single "is everything green?" panel for the Settings page.
 *
 * Closes the IA gap flagged in the audit: status signals were
 * scattered across Settings cards, the Dashboard, the per-project
 * Integrations page, and a couple of admin-only endpoints. This card
 * pulls them all into one place + adds a Refresh button.
 *
 * Each signal is checked independently; one timeout doesn't take
 * down the whole panel. Statuses are pessimistic: anything we can't
 * positively confirm renders as "unknown" rather than "ok".
 */

import { useCallback, useEffect, useState } from "react";
import AnimatedCard from "@/components/cards/AnimatedCard";
import StatusPill from "@/components/data/StatusPill";
import LoadingState from "@/components/feedback/LoadingState";
import { api } from "@/lib/api";

type SignalStatus = "ok" | "degraded" | "down" | "unknown";

interface Signal {
  key: string;
  label: string;
  status: SignalStatus;
  detail?: string;
}

const TONE_FOR_STATUS = {
  ok: "success",
  degraded: "warning",
  down: "danger",
  unknown: "muted",
} as const;

export default function StatusHub() {
  const [signals, setSignals] = useState<Signal[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);

  const refresh = useCallback(async () => {
    setBusy(true);
    // Kick off every check in parallel; gather independently so one
    // slow probe doesn't block the rest.
    const probes = await Promise.all([
      probeHealth(),
      probeMcp(),
      probeLlmProviders(),
      probeSfDx(),
    ]);
    setSignals(probes);
    setLastChecked(new Date());
    setBusy(false);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <AnimatedCard glow="cyan" delay={0.1}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-bold text-white">Platform status</h3>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={busy}
          className="text-xs px-2.5 py-1 rounded-lg glass text-slate-300 hover:text-white disabled:opacity-40"
        >
          {busy ? "Checking…" : "Refresh"}
        </button>
      </div>
      {!signals ? (
        <LoadingState variant="block" label="Probing services…" />
      ) : (
        <ul className="divide-y divide-white/5">
          {signals.map((s) => (
            <li
              key={s.key}
              className="flex items-center justify-between gap-3 py-2"
            >
              <div className="min-w-0">
                <p className="text-sm text-slate-200">{s.label}</p>
                {s.detail && (
                  <p className="text-[11px] text-slate-500 truncate">{s.detail}</p>
                )}
              </div>
              <StatusPill
                tone={TONE_FOR_STATUS[s.status]}
                label={s.status.toUpperCase()}
              />
            </li>
          ))}
        </ul>
      )}
      {lastChecked && (
        <p className="mt-3 text-[11px] text-slate-600">
          Last checked {lastChecked.toLocaleTimeString()}
        </p>
      )}
    </AnimatedCard>
  );
}

// ---------- probes ----------------------------------------------

async function probeHealth(): Promise<Signal> {
  try {
    const r = await fetch("/api/health-proxy", { cache: "no-store" });
    if (r.ok) return { key: "api", label: "Backend API", status: "ok" };
    return {
      key: "api",
      label: "Backend API",
      status: "down",
      detail: `HTTP ${r.status}`,
    };
  } catch (e) {
    // We use the api helper as a fallback because the /health route
    // isn't behind the Next.js bearer-token wrapper; instead probe a
    // trivial authenticated endpoint we know works post-login.
    try {
      await api.me();
      return { key: "api", label: "Backend API", status: "ok" };
    } catch {
      return {
        key: "api",
        label: "Backend API",
        status: "down",
        detail: e instanceof Error ? e.message : "unreachable",
      };
    }
  }
}

async function probeMcp(): Promise<Signal> {
  try {
    const r = await api.mcp.health();
    if (r.running) {
      return {
        key: "mcp",
        label: "RF-MCP server",
        status: "ok",
        detail: r.url || undefined,
      };
    }
    return {
      key: "mcp",
      label: "RF-MCP server",
      status: "degraded",
      detail: "Stopped — start it from the RF-MCP card.",
    };
  } catch (e) {
    return {
      key: "mcp",
      label: "RF-MCP server",
      status: "unknown",
      detail: e instanceof Error ? e.message : "probe failed",
    };
  }
}

async function probeLlmProviders(): Promise<Signal> {
  try {
    const r = await api.llm.providers();
    const list = r.providers || [];
    if (list.length === 0) {
      return {
        key: "llm",
        label: "LLM providers",
        status: "down",
        detail: "No providers configured — set env vars in backend.",
      };
    }
    return {
      key: "llm",
      label: "LLM providers",
      status: "ok",
      detail: `${list.length} configured`,
    };
  } catch (e) {
    return {
      key: "llm",
      label: "LLM providers",
      status: "unknown",
      detail: e instanceof Error ? e.message : "probe failed",
    };
  }
}

async function probeSfDx(): Promise<Signal> {
  // Salesforce DX is optional; the endpoint exists in the backend
  // (api.salesforce.dxStatus) but the card was historically hidden in
  // Settings. We surface it here as informational rather than required.
  try {
    const r = await api.salesforce.dxStatus();
    if (r.available) {
      return {
        key: "sfdx",
        label: "Salesforce DX CLI",
        status: "ok",
        detail: r.summary || undefined,
      };
    }
    return {
      key: "sfdx",
      label: "Salesforce DX CLI",
      status: "degraded",
      detail: r.summary || "Not installed — only needed for SOQL features.",
    };
  } catch {
    return {
      key: "sfdx",
      label: "Salesforce DX CLI",
      status: "unknown",
      detail: "Probe endpoint unavailable.",
    };
  }
}
