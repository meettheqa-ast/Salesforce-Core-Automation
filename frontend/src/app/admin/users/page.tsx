"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import GlassSelect from "@/components/ui/GlassSelect";
import { api, type AdminUser } from "@/lib/api";
import { useMe } from "@/lib/useMe";

const ROLE_OPTIONS = [
  { value: "user", label: "User" },
  { value: "tl", label: "Team Lead" },
  { value: "pm", label: "Project Manager" },
  { value: "admin", label: "Admin" },
];

export default function AdminUsersPage() {
  const { me } = useMe();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = (search: string = "") => {
    setLoading(true);
    api.admin.listUsers(search || undefined)
      .then(setUsers).catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load users"))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleRoleChange = async (u: AdminUser, role: string) => {
    setBusyId(u.id); setError("");
    try {
      await api.admin.patchUser(u.id, { global_role: role });
      load(q);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to change role");
    } finally {
      setBusyId(null);
    }
  };

  const handleToggleActive = async (u: AdminUser) => {
    if (!u.is_active && u.id === me?.id) {
      // No-op safety; backend will block too.
    }
    if (!confirm(u.is_active ? `Deactivate ${u.email}?` : `Reactivate ${u.email}?`)) return;
    setBusyId(u.id); setError("");
    try {
      await api.admin.patchUser(u.id, { is_active: !u.is_active });
      load(q);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusyId(null);
    }
  };

  const handleRevoke = async (u: AdminUser) => {
    if (!confirm(`Force ${u.email} to sign in again? Their current session will stop working.`)) return;
    setBusyId(u.id); setError("");
    try {
      await api.admin.revokeSession(u.id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusyId(null);
    }
  };

  if (me && !me.is_admin) {
    return <div className="max-w-2xl mx-auto px-6 py-16 text-center text-slate-400">Admin only</div>;
  }

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <div className="flex items-center gap-3 text-xs text-slate-500 mb-2">
        <Link href="/admin" className="hover:text-white">Admin</Link>
        <span>/</span>
        <span className="text-slate-300">Users</span>
      </div>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="mb-6 flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-3xl font-bold">
            <span className="bg-gradient-to-r from-purple-400 to-cyan-400 bg-clip-text text-transparent">
              Users
            </span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">{users.length} user{users.length === 1 ? "" : "s"}</p>
        </div>
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load(q)}
          placeholder="Search by email or name"
          className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500 min-w-[260px]"
        />
      </motion.div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-slate-500">Loading...</p>
      ) : (
        <div className="space-y-2">
          {users.map((u) => {
            const isMe = u.id === me?.id;
            return (
              <AnimatedCard key={u.id}>
                <div className="flex items-center gap-4 flex-wrap">
                  {u.picture ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={u.picture} alt="" referrerPolicy="no-referrer" className="w-9 h-9 rounded-full ring-1 ring-white/20" />
                  ) : (
                    <div className="w-9 h-9 rounded-full bg-gradient-to-br from-purple-500 to-cyan-400 flex items-center justify-center text-xs font-bold text-white">
                      {(u.name || u.email).slice(0, 2).toUpperCase()}
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-white font-medium truncate">
                      {u.name || u.email}
                      {isMe && <span className="ml-2 text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-200">You</span>}
                      {!u.is_active && <span className="ml-2 text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-red-500/20 text-red-200">Deactivated</span>}
                    </div>
                    <div className="text-xs text-slate-400 truncate">{u.email}</div>
                    <div className="text-[10px] text-slate-600 mt-0.5">
                      {u.membership_count} project{u.membership_count === 1 ? "" : "s"}
                      {u.last_login_at ? ` · last login ${new Date(u.last_login_at).toLocaleDateString()}` : " · never logged in"}
                    </div>
                  </div>
                  <GlassSelect
                    className="min-w-[160px]"
                    value={u.global_role}
                    onChange={(v) => handleRoleChange(u, v)}
                    options={ROLE_OPTIONS}
                  />
                  <button
                    onClick={() => handleToggleActive(u)}
                    disabled={busyId === u.id}
                    className="text-xs px-3 py-1.5 rounded glass text-slate-300 hover:text-white disabled:opacity-50"
                  >
                    {u.is_active ? "Deactivate" : "Reactivate"}
                  </button>
                  <button
                    onClick={() => handleRevoke(u)}
                    disabled={busyId === u.id}
                    title="Force this user to sign in again"
                    className="text-xs px-3 py-1.5 rounded text-amber-300 hover:text-amber-200 disabled:opacity-50"
                  >
                    Revoke session
                  </button>
                </div>
              </AnimatedCard>
            );
          })}
        </div>
      )}
    </div>
  );
}
