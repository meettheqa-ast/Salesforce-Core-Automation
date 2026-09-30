"use client";

/**
 * Contextual SOQL drawer rendered next to the project Credentials
 * card. Brings Org Inspector's SOQL Run capability right next to
 * where the user is configuring the org connection -- so they can
 * sanity-check "can I actually query this sandbox?" without
 * leaving the project home.
 *
 * Calls `POST /api/salesforce/soql` (the same endpoint /sfdx and
 * /org-inspector use). Backend auth is provided by the sf_dx_bridge
 * CLI login on the server, NOT by the credentials shown on this
 * page -- the sandbox URL on the card is for the test RUNNER, while
 * SOQL queries from this drawer go through the backend's SFDX CLI
 * session. That matches the existing Org Inspector behaviour and
 * is called out in the drawer copy.
 */

import { useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import Drawer from "@/components/layout/Drawer";
import LoadingState from "@/components/feedback/LoadingState";
import ErrorBanner from "@/components/feedback/ErrorBanner";

interface Props {
  /** Sandbox URL surfaced in the drawer header so the user knows
   *  which org the saved credentials point at. The actual SOQL call
   *  still goes through sf_dx_bridge on the backend. */
  sandboxUrl?: string;
}

const SAMPLE = "SELECT Id, Name FROM Account ORDER BY CreatedDate DESC LIMIT 5";

export default function SoqlInspectorDrawer({ sandboxUrl }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(SAMPLE);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [result, setResult] = useState<unknown>(null);

  const run = async () => {
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      const r = await api.salesforce.soql(query);
      setResult(r);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-[11px] px-2.5 py-1 rounded border border-cyan-400/30 bg-cyan-500/10 text-cyan-200 hover:bg-cyan-500/20 transition-colors"
        title="Run an ad-hoc SOQL query against the org's SFDX CLI session"
      >
        🛢 Test SOQL
      </button>

      <Drawer
        open={open}
        onClose={() => setOpen(false)}
        title="Test SOQL"
        width={520}
      >
        <div className="p-4 space-y-4 text-sm">
          <div className="rounded-lg border border-amber-400/20 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-100/90">
            Queries route through the backend SFDX CLI session
            (<code className="text-amber-200">sf_dx_bridge</code>), not the
            credentials saved on this card. The credentials card stores the
            login the test <em>runner</em> uses inside Robot. For a richer
            schema browser open{" "}
            <Link
              href="/org-inspector"
              className="underline hover:text-amber-50"
            >
              Org Inspector
            </Link>
            .
          </div>

          {sandboxUrl && (
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">
                Card org
              </span>
              <span className="text-[11px] font-mono text-slate-400 truncate max-w-[20rem]">
                {sandboxUrl}
              </span>
            </div>
          )}

          <div>
            <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">
              SOQL
            </label>
            <textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              rows={5}
              className="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-2 text-xs text-slate-200 font-mono outline-none focus:border-purple-500"
              placeholder={SAMPLE}
            />
          </div>

          <motion.button
            type="button"
            whileTap={{ scale: 0.97 }}
            onClick={() => void run()}
            disabled={busy || !query.trim()}
            className="w-full py-2 rounded-lg bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold text-sm disabled:opacity-50"
          >
            {busy ? "Running…" : "Run query"}
          </motion.button>

          <ErrorBanner error={err} onDismiss={() => setErr(null)} />

          {busy && <LoadingState variant="block" label="Querying org…" />}

          {!busy && result !== null && result !== undefined && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
                Result
              </p>
              <pre className="text-[11px] font-mono text-slate-300 bg-black/40 border border-white/10 rounded-lg p-3 overflow-auto max-h-[55vh] whitespace-pre-wrap">
                {JSON.stringify(result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      </Drawer>
    </>
  );
}
