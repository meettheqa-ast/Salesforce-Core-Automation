"use client";

/**
 * Peer search combobox for the Add Member form.
 *
 * Replaces the raw email textbox so PMs/TLs can find existing teammates
 * by typing part of a name -- with badges for "Already a member" and
 * "Invite pending" so duplicate invites are obvious before submit.
 *
 * Falls back to free-text email when the typed string looks like an
 * email but doesn't match any user (preserves today's "invite somebody
 * who hasn't signed in yet" path).
 *
 * Dropdown is rendered through a React Portal because the Add Member
 * form sits inside an AnimatedCard whose `overflow-hidden` would
 * otherwise clip the menu (same fix as UserMenu / NotificationsBell).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, type UserSearchHit } from "@/lib/api";

export type PeerSelection =
  | { id: string; email: string; name: string; picture: string }
  | { id: null; email: string };

type Props = {
  /** Project slug -- enables the per-row is_member / pending_invite badges. */
  projectName?: string;
  value: string;
  onChange: (next: string) => void;
  /** Fires when the user picks a row (or the free-text fallback row). */
  onSelect: (picked: PeerSelection) => void;
  placeholder?: string;
  autoFocus?: boolean;
  disabled?: boolean;
};

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const DEBOUNCE_MS = 250;

export default function PeerCombobox({
  projectName,
  value,
  onChange,
  onSelect,
  placeholder,
  autoFocus,
  disabled,
}: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const [hits, setHits] = useState<UserSearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  // -1 = no row highlighted; otherwise indexes into the visible row list
  // (which is `selectableHits` followed by the optional free-text row).
  const [activeIdx, setActiveIdx] = useState<number>(-1);
  const [coords, setCoords] = useState<{
    top: number;
    left: number;
    width: number;
  } | null>(null);

  // Debounced search. We bump a request id so a slow earlier response can't
  // overwrite a faster later one.
  const reqIdRef = useRef(0);
  useEffect(() => {
    if (!open) return;
    const q = value.trim();
    if (!q) {
      setHits([]);
      setLoading(false);
      return;
    }
    const my = ++reqIdRef.current;
    setLoading(true);
    const t = window.setTimeout(() => {
      void api.users
        .search(q, projectName ? { projectName } : undefined)
        .then((rows) => {
          if (my !== reqIdRef.current) return;
          setHits(rows);
          setActiveIdx(rows.findIndex((r) => !r.is_member && !r.pending_invite_id));
        })
        .catch(() => {
          if (my !== reqIdRef.current) return;
          setHits([]);
        })
        .finally(() => {
          if (my === reqIdRef.current) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [value, open, projectName]);

  // Anchor the portal'd popup to the input position; refresh on
  // resize / scroll so it stays glued.
  useEffect(() => {
    if (!open || !inputRef.current) return;
    const update = () => {
      const r = inputRef.current!.getBoundingClientRect();
      setCoords({ top: r.bottom + 4, left: r.left, width: r.width });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open]);

  // Outside click closes the popup. Both the input and the portal'd
  // popup are "inside" for this purpose.
  useEffect(() => {
    function onDocMouseDown(e: MouseEvent) {
      const t = e.target as Node;
      if (inputRef.current?.contains(t)) return;
      if (popupRef.current?.contains(t)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, []);

  // Build the list of selectable rows + decide if a free-text fallback
  // row should be appended. We deliberately keep disabled rows in the
  // visible list (as faded items) but not in the keyboard-nav order, so
  // ArrowDown skips straight from one selectable to the next.
  const trimmed = value.trim();
  const lowerEmails = useMemo(
    () => new Set(hits.map((h) => h.email.toLowerCase())),
    [hits],
  );
  const showFreeTextFallback =
    EMAIL_RE.test(trimmed) && !lowerEmails.has(trimmed.toLowerCase());

  // Flat list used for keyboard nav: only the selectable rows + (optional)
  // the free-text row. Disabled hits are skipped here.
  const navRows: Array<
    | { kind: "hit"; hit: UserSearchHit }
    | { kind: "freeText"; email: string }
  > = useMemo(() => {
    const out: Array<
      { kind: "hit"; hit: UserSearchHit } | { kind: "freeText"; email: string }
    > = [];
    for (const h of hits) {
      if (h.is_member || h.pending_invite_id) continue;
      out.push({ kind: "hit", hit: h });
    }
    if (showFreeTextFallback) out.push({ kind: "freeText", email: trimmed });
    return out;
  }, [hits, showFreeTextFallback, trimmed]);

  function pickRow(row: typeof navRows[number]) {
    if (row.kind === "hit") {
      onChange(row.hit.email);
      onSelect({
        id: row.hit.id,
        email: row.hit.email,
        name: row.hit.name,
        picture: row.hit.picture,
      });
    } else {
      onChange(row.email);
      onSelect({ id: null, email: row.email });
    }
    setOpen(false);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) setOpen(true);
      setActiveIdx((i) => Math.min(i + 1, navRows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      if (activeIdx >= 0 && activeIdx < navRows.length) {
        e.preventDefault();
        pickRow(navRows[activeIdx]);
      }
      // else: let the browser submit the form (no row selected).
    } else if (e.key === "Escape") {
      if (open) {
        e.preventDefault();
        setOpen(false);
      }
    }
  }

  const popup =
    open && coords && (loading || hits.length > 0 || showFreeTextFallback)
      ? createPortal(
          <div
            ref={popupRef}
            role="listbox"
            style={{
              position: "fixed",
              top: coords.top,
              left: coords.left,
              width: Math.max(coords.width, 280),
              zIndex: 1000,
            }}
            className="rounded-xl border border-white/10 bg-slate-950/95 backdrop-blur shadow-2xl overflow-hidden"
          >
            {loading && hits.length === 0 && (
              <div className="px-3 py-2 text-xs text-slate-500">Searching…</div>
            )}

            {hits.map((h) => {
              const disabledRow = h.is_member || !!h.pending_invite_id;
              // Find this row's index in navRows (only selectable rows live there).
              const navIdx = disabledRow
                ? -1
                : navRows.findIndex(
                    (r) => r.kind === "hit" && r.hit.id === h.id,
                  );
              const isActive = navIdx === activeIdx && navIdx !== -1;
              return (
                <button
                  key={h.id}
                  type="button"
                  role="option"
                  aria-selected={isActive}
                  aria-disabled={disabledRow}
                  disabled={disabledRow}
                  onMouseEnter={() => navIdx !== -1 && setActiveIdx(navIdx)}
                  onClick={() => {
                    if (disabledRow) return;
                    const row = navRows.find(
                      (r) => r.kind === "hit" && r.hit.id === h.id,
                    );
                    if (row) pickRow(row);
                  }}
                  className={[
                    "w-full text-left flex items-center gap-3 px-3 py-2 transition-colors",
                    disabledRow
                      ? "opacity-50 cursor-not-allowed"
                      : isActive
                        ? "bg-purple-500/20"
                        : "hover:bg-white/5",
                  ].join(" ")}
                >
                  {h.picture ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={h.picture}
                      alt=""
                      referrerPolicy="no-referrer"
                      className="w-7 h-7 rounded-full ring-1 ring-white/20 shrink-0"
                    />
                  ) : (
                    <div className="w-7 h-7 rounded-full bg-gradient-to-br from-purple-500 to-cyan-400 flex items-center justify-center text-[10px] font-bold text-white shrink-0">
                      {(h.name || h.email).slice(0, 2).toUpperCase()}
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-white truncate">
                      {h.name || h.email}
                    </div>
                    <div className="text-[11px] text-slate-400 truncate">
                      {h.email}
                    </div>
                  </div>
                  {h.is_member && (
                    <span className="shrink-0 text-[10px] uppercase tracking-wider px-2 py-0.5 rounded bg-white/10 text-slate-300">
                      Already a member
                    </span>
                  )}
                  {!h.is_member && h.pending_invite_id && (
                    <span className="shrink-0 text-[10px] uppercase tracking-wider px-2 py-0.5 rounded bg-amber-500/20 text-amber-200">
                      Invite pending
                    </span>
                  )}
                </button>
              );
            })}

            {showFreeTextFallback &&
              (() => {
                const navIdx = navRows.findIndex((r) => r.kind === "freeText");
                const isActive = navIdx === activeIdx;
                return (
                  <button
                    key="__freetext__"
                    type="button"
                    role="option"
                    aria-selected={isActive}
                    onMouseEnter={() => setActiveIdx(navIdx)}
                    onClick={() => pickRow({ kind: "freeText", email: trimmed })}
                    className={[
                      "w-full text-left flex items-center gap-3 px-3 py-2 border-t border-white/5 transition-colors",
                      isActive ? "bg-purple-500/20" : "hover:bg-white/5",
                    ].join(" ")}
                  >
                    <div className="w-7 h-7 rounded-full bg-white/5 flex items-center justify-center text-sm shrink-0">
                      ✉
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-white truncate">
                        Use email anyway
                      </div>
                      <div className="text-[11px] text-slate-400 truncate">
                        {trimmed}
                      </div>
                    </div>
                    <span className="shrink-0 text-[10px] uppercase tracking-wider px-2 py-0.5 rounded bg-white/5 text-slate-400">
                      Not yet signed in
                    </span>
                  </button>
                );
              })()}

            {!loading &&
              hits.length === 0 &&
              !showFreeTextFallback &&
              trimmed.length > 0 && (
                <div className="px-3 py-2 text-xs text-slate-500">
                  No matches. Type a full email to invite someone who hasn't
                  signed in yet.
                </div>
              )}
          </div>,
          document.body,
        )
      : null;

  return (
    <>
      <input
        ref={inputRef}
        type="text"
        autoComplete="off"
        spellCheck={false}
        autoFocus={autoFocus}
        disabled={disabled}
        value={value}
        placeholder={placeholder}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
        aria-autocomplete="list"
        aria-expanded={open}
      />
      {popup}
    </>
  );
}
