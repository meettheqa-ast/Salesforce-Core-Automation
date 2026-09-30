"use client";

/**
 * Org Inspector -- canonical replacement for the old `/sfdx` Dev Tools
 * page. Renamed from "SF DX Tools" so the page reads as something a
 * QA lead would look for ("inspect my org") rather than something a
 * Salesforce DX-CLI user would look for.
 *
 * Tabs:
 *   - SOQL       : run an ad-hoc SOQL against the connected org
 *   - Org Schema : pull field metadata for a standard SObject
 *
 * The legacy "Apex Tests" tab was dropped because it never had a
 * backend handler -- it was UI scaffolding only and clicking "Run
 * Tests" did nothing. See safe_dev_tools_refactor plan, Phase 4.
 *
 * The old route `/sfdx` is preserved as a `redirect('/org-inspector')`
 * shim so any bookmarks, README links, or stored deep links keep
 * working.
 */

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import { api } from "@/lib/api";
import GlassSelect from "@/components/ui/GlassSelect";

const TABS = ["SOQL", "Org Schema"] as const;
const OBJECTS = ["Lead", "Account", "Contact", "Opportunity", "Case"];

export default function OrgInspectorPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("SOQL");
  const [soqlQuery, setSoqlQuery] = useState(
    "SELECT Id, Name FROM Lead ORDER BY CreatedDate DESC LIMIT 5",
  );
  const [schemaObj, setSchemaObj] = useState("Lead");
  const [result, setResult] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const runAction = async () => {
    setLoading(true);
    setError("");
    setResult(null);
    try {
      if (tab === "SOQL") {
        setResult(await api.salesforce.soql(soqlQuery));
      } else if (tab === "Org Schema") {
        setResult(await api.salesforce.describeFields(schemaObj));
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-4xl font-bold mb-2">
          <span className="bg-gradient-to-r from-cyan-400 to-purple-400 bg-clip-text text-transparent">
            Org Inspector
          </span>
        </h1>
        <p className="text-slate-400 mb-6">
          Query your Salesforce org and inspect object schemas. Use this when
          you want to verify data, double-check field API names, or sanity
          check what the generated test cases will reference.
        </p>
      </motion.div>

      {/* Tabs */}
      <div className="flex gap-1 glass rounded-lg p-0.5 mb-6 w-fit">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => {
              setTab(t);
              setResult(null);
              setError("");
            }}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
              tab === t
                ? "bg-purple-600/30 text-purple-300"
                : "text-slate-500 hover:text-slate-300"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <AnimatedCard glow="cyan">
        {tab === "SOQL" && (
          <div>
            <textarea
              value={soqlQuery}
              onChange={(e) => setSoqlQuery(e.target.value)}
              rows={4}
              className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-sm text-slate-300 font-mono outline-none focus:border-purple-500 mb-3"
              placeholder="SELECT Id, Name FROM Lead LIMIT 10"
            />
            <motion.button
              whileTap={{ scale: 0.95 }}
              onClick={runAction}
              disabled={loading}
              className="px-5 py-2 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm disabled:opacity-50"
            >
              {loading ? "Running..." : "Run Query"}
            </motion.button>
          </div>
        )}

        {tab === "Org Schema" && (
          <div className="flex items-end gap-3">
            <div className="flex-1">
              <label className="text-xs text-slate-500 mb-1 block">
                Salesforce Object
              </label>
              <GlassSelect
                className="w-full"
                value={schemaObj}
                onChange={setSchemaObj}
                placeholder="Object"
                options={OBJECTS.map((o) => ({ value: o, label: o }))}
              />
            </div>
            <motion.button
              whileTap={{ scale: 0.95 }}
              onClick={runAction}
              disabled={loading}
              className="px-5 py-3 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm disabled:opacity-50"
            >
              {loading ? "Fetching..." : "Fetch Fields"}
            </motion.button>
          </div>
        )}

        {error && <p className="text-red-400 text-sm mt-3">{error}</p>}

        <AnimatePresence>
          {result !== null && result !== undefined ? (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="mt-4"
            >
              <pre className="bg-black/30 rounded-xl p-4 text-xs text-slate-300 font-mono overflow-auto max-h-96">
                {JSON.stringify(result, null, 2)}
              </pre>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </AnimatedCard>
    </div>
  );
}
