"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api, type AuditLogRow } from "@/lib/api";
import { useMe } from "@/lib/useMe";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";

export default function AdminAuditPage() {
  const { me } = useMe();
  const [rows, setRows] = useState<AuditLogRow[]>([]);
  const [filter, setFilter] = useState({ action: "", target_type: "" });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = () => {
    setLoading(true);
    api.admin.audit({
      action: filter.action || undefined,
      target_type: filter.target_type || undefined,
      limit: 200,
    })
      .then(setRows)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load audit log"))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load();   }, [filter.action, filter.target_type]);

  if (me && !me.is_admin) {
    return (
      <PageScaffold>
        <div className="py-12 text-center text-slate-400">Admin only</div>
      </PageScaffold>
    );
  }

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="space-y-4">
        <PageHeader
          eyebrow="Admin"
          title="Audit log"
          description="Last 200 events. Filter by action or target type."
          actions={
            <Link href="/admin" className="text-xs text-slate-400 hover:text-white">
              Back to admin
            </Link>
          }
        />

      <div className="flex gap-3 mb-4">
        <input
          type="text" value={filter.action}
          onChange={(e) => setFilter({ ...filter, action: e.target.value })}
          placeholder="Filter by action (e.g. run_started)"
          className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500 flex-1"
        />
        <input
          type="text" value={filter.target_type}
          onChange={(e) => setFilter({ ...filter, target_type: e.target.value })}
          placeholder="Filter by target type (run, user, persona...)"
          className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500 flex-1"
        />
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-slate-500">Loading...</p>
      ) : rows.length === 0 ? (
        <p className="text-slate-500">No audit entries match the current filters.</p>
      ) : (
        <div className="space-y-1">
          {rows.map((r) => (
            <div key={r.id} className="rounded-lg bg-white/[0.02] border border-white/5 px-4 py-2 hover:bg-white/[0.04] transition">
              <div className="flex items-baseline gap-3 text-xs">
                <span className="text-slate-500 font-mono">{r.timestamp ? new Date(r.timestamp).toLocaleString() : ""}</span>
                <span className="text-purple-300 font-semibold">{r.action}</span>
                <span className="text-slate-400">{r.target_type}/{r.target_id.slice(0, 12)}{r.target_id.length > 12 ? "..." : ""}</span>
                <span className="text-slate-500 ml-auto">{r.user_email || r.user_name || (r.user_id ? `user ${r.user_id.slice(0, 8)}` : "system")}</span>
              </div>
              {r.metadata_json && (
                <pre className="text-[10px] text-slate-500 mt-1 overflow-x-auto whitespace-pre-wrap break-all font-mono">{r.metadata_json}</pre>
              )}
            </div>
          ))}
        </div>
      )}
      </motion.div>
    </PageScaffold>
  );
}
