"use client";

/**
 * Contextual schema inspector that opens as a side drawer from the
 * /generate Prompt section. Brings Org Inspector's "what objects /
 * fields will this script touch?" capability into the place where
 * the user is actually writing the prompt -- instead of forcing
 * them to navigate to /org-inspector, run a query, and tab back.
 *
 * Calls `POST /api/salesforce/schema/context` with:
 *   - the prompt the user is composing
 *   - the credentials already selected in WorkspaceBar
 *
 * The backend returns the SObjects it detected in the prompt and a
 * formatted schema block (field API names + labels). Both are
 * rendered so the user can copy them into their prompt as
 * additional context if needed.
 */

import { useCallback, useState } from "react";
import { motion } from "framer-motion";
import { api, type SchemaContextResponse } from "@/lib/api";
import Drawer from "@/components/layout/Drawer";
import LoadingState from "@/components/feedback/LoadingState";
import ErrorBanner from "@/components/feedback/ErrorBanner";

interface Props {
  /** Current prompt textarea contents. We send this to the backend
   *  so the Salesforce object detector can find SObject mentions. */
  prompt: string;
  /** Sandbox credentials from WorkspaceBar. Drawer is gated on these
   *  being present (you cannot describe objects without an org). */
  creds: {
    sandboxUrl: string;
    username: string;
    password: string;
  } | null;
}

export default function SchemaInspectorDrawer({ prompt, creds }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [result, setResult] = useState<SchemaContextResponse | null>(null);

  const credsReady = !!(creds?.sandboxUrl && creds.username && creds.password);
  const promptReady = prompt.trim().length > 0;

  const run = useCallback(async () => {
    if (!creds) return;
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      const r = await api.salesforce.schemaContext({
        prompt,
        sandbox_url: creds.sandboxUrl,
        username: creds.username,
        password: creds.password,
      });
      setResult(r);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }, [creds, prompt]);

  const openDrawer = () => {
    setOpen(true);
    // Auto-run on open so the user doesn't need to click twice.
    if (credsReady && promptReady) {
      void run();
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={openDrawer}
        disabled={!credsReady || !promptReady}
        title={
          !credsReady
            ? "Pick a workspace login above first"
            : !promptReady
              ? "Type a prompt first"
              : "Inspect the Salesforce objects mentioned in this prompt"
        }
        className="text-[11px] px-2.5 py-1 rounded border border-cyan-400/30 bg-cyan-500/10 text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        🛢 Inspect objects in prompt
      </button>

      <Drawer
        open={open}
        onClose={() => setOpen(false)}
        title="Inspect schema"
        width={460}
      >
        <div className="p-4 space-y-4 text-sm">
          <p className="text-xs text-slate-400">
            Sends the current prompt + workspace credentials to{" "}
            <code className="text-slate-300">
              POST /api/salesforce/schema/context
            </code>
            . Use this to confirm field API names + picklists before
            running the generator.
          </p>

          <div className="flex items-center justify-between gap-2">
            <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">
              Org
            </span>
            <span className="text-[11px] font-mono text-slate-400 truncate max-w-[16rem]">
              {creds?.sandboxUrl || "—"}
            </span>
          </div>

          <motion.button
            type="button"
            whileTap={{ scale: 0.97 }}
            onClick={() => void run()}
            disabled={busy || !credsReady || !promptReady}
            className="w-full py-2 rounded-lg bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold text-sm disabled:opacity-50"
          >
            {busy ? "Inspecting…" : "Re-run inspection"}
          </motion.button>

          <ErrorBanner error={err} onDismiss={() => setErr(null)} />

          {busy && <LoadingState variant="block" label="Reading org metadata…" />}

          {!busy && result && (
            <>
              <div>
                <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
                  Detected SObjects
                </p>
                {result.objects.length === 0 ? (
                  <p className="text-xs text-slate-500 italic">
                    No standard SObjects detected in the prompt yet. Mention
                    objects by name (Lead, Account, Opportunity, …) for
                    schema injection.
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {result.objects.map((o) => (
                      <span
                        key={o}
                        className="text-[11px] px-2 py-0.5 rounded-full bg-cyan-500/15 text-cyan-200 border border-cyan-400/20 font-mono"
                      >
                        {o}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
                  Schema context
                </p>
                {result.context.trim() ? (
                  <pre className="text-[11px] font-mono text-slate-300 bg-black/40 border border-white/10 rounded-lg p-3 overflow-auto max-h-[55vh] whitespace-pre-wrap">
                    {result.context}
                  </pre>
                ) : (
                  <p className="text-xs text-slate-500 italic">
                    No schema context returned. The generator will run
                    without injected field metadata.
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      </Drawer>
    </>
  );
}
