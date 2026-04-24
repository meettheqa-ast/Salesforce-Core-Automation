"use client";

import { useEffect, useState } from "react";
import { use } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import GlassSelect from "@/components/ui/GlassSelect";
import { api, type MemberOut, type InvitationRow } from "@/lib/api";
import { useMe, membershipFor, roleAtLeast } from "@/lib/useMe";

const ROLE_LABEL: Record<MemberOut["role"], string> = {
  pm: "Project Manager",
  lead: "Team Lead",
  member: "Team Member",
};

export default function ProjectMembersPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = use(params);
  const { me, refresh } = useMe();
  const [members, setMembers] = useState<MemberOut[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>("");

  // Add-member form
  const [showAdd, setShowAdd] = useState(false);
  const [addEmail, setAddEmail] = useState("");
  const [addRole, setAddRole] = useState<MemberOut["role"]>("member");
  const [adding, setAdding] = useState(false);

  const myRole = membershipFor(me, name);
  const canManageMembers = roleAtLeast(myRole, "lead"); // TL can add, PM can add+change+remove
  const canChangeOrRemove = roleAtLeast(myRole, "pm");
  const canGrantPm = roleAtLeast(myRole, "pm");
  const canSeeInvitations = roleAtLeast(myRole, "lead");

  const [invitations, setInvitations] = useState<InvitationRow[]>([]);

  const load = () => {
    setLoading(true);
    api.projects.members(name)
      .then((rows) => { setMembers(rows); setError(""); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load members"))
      .finally(() => setLoading(false));
    if (canSeeInvitations) {
      api.projects.listInvitations(name).then(setInvitations).catch(() => {});
    }
  };

  useEffect(() => {
    if (!me) return;
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me, name, canSeeInvitations]);

  const handleApprove = async (id: string) => {
    setError("");
    try {
      await api.invitations.approve(id);
      load();
      refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to approve");
    }
  };

  const handleReject = async (id: string) => {
    if (!confirm("Reject this request/invite?")) return;
    setError("");
    try {
      await api.invitations.reject(id);
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to reject");
    }
  };

  const handleRevoke = async (id: string) => {
    if (!confirm("Revoke this pending invite?")) return;
    setError("");
    try {
      await api.invitations.revoke(id);
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to revoke");
    }
  };

  const handleAdd = async () => {
    if (!addEmail.trim()) { setError("Email is required"); return; }
    setAdding(true); setError("");
    try {
      // Send an invitation. If the user already exists they get an in-app
      // notification; if not, the invite waits until they sign in for the
      // first time and they auto-accept on first login.
      await api.projects.invite(name, { email: addEmail.trim(), role: addRole });
      setAddEmail(""); setAddRole("member"); setShowAdd(false);
      load();
      refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to send invitation");
    } finally {
      setAdding(false);
    }
  };

  const handleChangeRole = async (m: MemberOut, newRole: MemberOut["role"]) => {
    if (m.role === newRole) return;
    if (newRole === "pm" && !canGrantPm) {
      setError("Only PMs can grant the PM role.");
      return;
    }
    setError("");
    try {
      await api.projects.updateMemberRole(name, m.user_id, newRole);
      load();
      refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to change role");
    }
  };

  const handleRemove = async (m: MemberOut) => {
    if (!confirm(`Remove ${m.email} from this project?`)) return;
    setError("");
    try {
      await api.projects.removeMember(name, m.user_id);
      load();
      refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to remove member");
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <div className="flex items-center gap-3 text-xs text-slate-500 mb-2">
          <Link href="/projects" className="hover:text-white">Projects</Link>
          <span>/</span>
          <Link href={`/projects/${encodeURIComponent(name)}`} className="hover:text-white">{name}</Link>
          <span>/</span>
          <span className="text-slate-300">Members</span>
        </div>
        <div className="flex items-center justify-between">
          <h1 className="text-3xl font-bold">
            <span className="bg-gradient-to-r from-purple-400 to-cyan-400 bg-clip-text text-transparent">
              Members
            </span>
          </h1>
          {canManageMembers && (
            <motion.button
              whileHover={{ scale: 1.04 }}
              whileTap={{ scale: 0.96 }}
              onClick={() => setShowAdd((v) => !v)}
              className="px-4 py-2 bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold rounded-xl"
            >
              + Add member
            </motion.button>
          )}
        </div>
        <p className="text-sm text-slate-400 mt-1">
          Your role on this project:{" "}
          <span className="text-white font-medium">{myRole ? ROLE_LABEL[myRole] : "None"}</span>
        </p>
      </motion.div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      <AnimatePresence>
        {showAdd && canManageMembers && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className="mb-6"
          >
            <AnimatedCard glow="purple">
              <h3 className="text-sm font-semibold text-white mb-3">Add member by email</h3>
              <div className="grid grid-cols-12 gap-3 items-end">
                <div className="col-span-12 sm:col-span-7">
                  <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Email</label>
                  <input
                    type="email"
                    value={addEmail}
                    onChange={(e) => setAddEmail(e.target.value)}
                    placeholder="teammate@astounddigital.com"
                    className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                  />
                </div>
                <div className="col-span-7 sm:col-span-3">
                  <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Role</label>
                  <GlassSelect
                    className="w-full"
                    value={addRole}
                    onChange={(v) => setAddRole(v as MemberOut["role"])}
                    options={[
                      { value: "member", label: "Team Member" },
                      { value: "lead", label: "Team Lead" },
                      ...(canGrantPm ? [{ value: "pm", label: "Project Manager" }] : []),
                    ]}
                  />
                </div>
                <div className="col-span-5 sm:col-span-2">
                  <button
                    type="button"
                    disabled={adding || !addEmail.trim()}
                    onClick={handleAdd}
                    className="w-full px-3 py-2 bg-emerald-600 text-white text-sm font-semibold rounded-lg disabled:opacity-50"
                  >
                    {adding ? "Adding..." : "Add"}
                  </button>
                </div>
              </div>
              <p className="text-xs text-slate-500 mt-3">
                Sends an invitation. If the invitee has already signed in, they get an in-app notification.
                Otherwise, the invite is waiting and they'll be auto-added on their first login.
              </p>
            </AnimatedCard>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Pending invitations + access requests */}
      {canSeeInvitations && invitations.length > 0 && (
        <div className="mb-6">
          <h3 className="text-xs uppercase tracking-wider text-slate-500 font-semibold mb-2">
            Pending invitations &amp; access requests
          </h3>
          <div className="space-y-2">
            {invitations.map((inv) => {
              const isRequest = inv.direction === "request";
              return (
                <AnimatedCard key={inv.id}>
                  <div className="flex items-center gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-white truncate">
                        {inv.email}
                        <span className="ml-2 text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-200">
                          {isRequest ? "Requested access" : "Invited"}
                        </span>
                        <span className="ml-2 text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-white/5 text-slate-300">
                          {ROLE_LABEL[inv.role]}
                        </span>
                      </div>
                      <div className="text-xs text-slate-500 mt-0.5">
                        Status: {inv.status}{inv.expires_at ? ` · expires ${new Date(inv.expires_at).toLocaleDateString()}` : ""}
                      </div>
                    </div>
                    <div className="flex gap-2">
                      {isRequest ? (
                        <button onClick={() => handleApprove(inv.id)} className="text-xs px-3 py-1.5 rounded bg-emerald-600 text-white hover:bg-emerald-500">Approve</button>
                      ) : (
                        <button onClick={() => handleRevoke(inv.id)} className="text-xs px-3 py-1.5 rounded glass text-slate-300 hover:text-white">Revoke</button>
                      )}
                      <button onClick={() => handleReject(inv.id)} className="text-xs px-3 py-1.5 rounded text-slate-400 hover:text-red-400">Reject</button>
                    </div>
                  </div>
                </AnimatedCard>
              );
            })}
          </div>
        </div>
      )}

      {loading && !members ? (
        <div className="flex justify-center py-16">
          <div className="flex gap-1">
            {[0, 1, 2].map((i) => (
              <motion.div key={i} className="w-3 h-3 rounded-full bg-purple-400"
                animate={{ y: [0, -10, 0] }} transition={{ duration: 0.6, repeat: Infinity, delay: i * 0.15 }} />
            ))}
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {(members || []).map((m) => {
            const isMe = me?.id === m.user_id;
            return (
              <AnimatedCard key={m.user_id}>
                <div className="flex items-center gap-4">
                  {m.picture ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={m.picture} alt="" referrerPolicy="no-referrer"
                      className="w-9 h-9 rounded-full ring-1 ring-white/20" />
                  ) : (
                    <div className="w-9 h-9 rounded-full bg-gradient-to-br from-purple-500 to-cyan-400 flex items-center justify-center text-xs font-bold text-white">
                      {(m.name || m.email).slice(0, 2).toUpperCase()}
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-white font-medium truncate">
                      {m.name || m.email}
                      {isMe && <span className="ml-2 text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-200">You</span>}
                    </div>
                    <div className="text-xs text-slate-400 truncate">{m.email}</div>
                  </div>
                  {canChangeOrRemove ? (
                    <GlassSelect
                      className="min-w-[150px]"
                      value={m.role}
                      onChange={(v) => handleChangeRole(m, v as MemberOut["role"])}
                      options={[
                        { value: "member", label: "Team Member" },
                        { value: "lead", label: "Team Lead" },
                        ...(canGrantPm ? [{ value: "pm", label: "Project Manager" }] : []),
                      ]}
                    />
                  ) : (
                    <span className="text-xs px-2.5 py-1 rounded bg-white/5 text-slate-300">
                      {ROLE_LABEL[m.role]}
                    </span>
                  )}
                  {canChangeOrRemove && (
                    <button
                      type="button"
                      onClick={() => handleRemove(m)}
                      title={isMe ? "Remove yourself (must promote another PM first if you're the only PM)" : "Remove member"}
                      className="text-slate-500 hover:text-red-400 transition text-sm px-2"
                    >
                      Remove
                    </button>
                  )}
                </div>
              </AnimatedCard>
            );
          })}
          {(members || []).length === 0 && (
            <p className="text-sm text-slate-500 text-center py-12">No members yet.</p>
          )}
        </div>
      )}
    </div>
  );
}
