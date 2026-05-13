"use client";

/**
 * Minimal global toast provider.
 *
 * Why this exists: a recent audit found 13+ pages catching API errors with
 * `.catch(() => {})` or `.catch(() => setX([]))`, leaving the user with
 * empty UI and no explanation of what went wrong. Centralising errors in
 * a toast layer means future code can always surface failures without
 * coupling to a global `setError(string)` per page.
 *
 * Usage anywhere under <RootLayout>:
 *
 *   const toast = useToast();
 *   toast.error("Failed to load runs");
 *   toast.success("Persona created");
 *
 * Or imperatively from non-React code via the `pushToast` helper.
 *
 * The provider is intentionally tiny -- no third-party deps, no animation
 * library overlap with the rest of the app. Auto-dismiss after 5s; user can
 * click to dismiss earlier.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

type ToastKind = "error" | "success" | "info";

interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastContextValue {
  push: (message: string, kind?: ToastKind) => void;
  error: (message: string) => void;
  success: (message: string) => void;
  info: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

// Module-scoped emitter so non-React callers (e.g. api.ts on a global
// 401 / network error) can still surface a toast without prop-drilling.
type Listener = (t: Toast) => void;
const listeners = new Set<Listener>();
let nextId = 1;

export function pushToast(message: string, kind: ToastKind = "info"): void {
  const t: Toast = { id: nextId++, kind, message };
  listeners.forEach((l) => l(t));
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    // Don't crash apps that render outside the provider (SSR, tests).
    // Fall back to no-ops so callers can rely on the API existing.
    return {
      push: () => undefined,
      error: () => undefined,
      success: () => undefined,
      info: () => undefined,
    };
  }
  return ctx;
}

export default function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id));
    const handle = timers.current.get(id);
    if (handle) {
      clearTimeout(handle);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback((message: string, kind: ToastKind = "info") => {
    const t: Toast = { id: nextId++, kind, message };
    setToasts((current) => [...current, t]);
    const handle = setTimeout(() => dismiss(t.id), 5000);
    timers.current.set(t.id, handle);
  }, [dismiss]);

  // Subscribe to module-level emissions from non-React code.
  useEffect(() => {
    const listener: Listener = (t) => {
      setToasts((current) => [...current, t]);
      const handle = setTimeout(() => dismiss(t.id), 5000);
      timers.current.set(t.id, handle);
    };
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, [dismiss]);

  // Cleanup stale timers on unmount.
  useEffect(() => {
    const map = timers.current;
    return () => {
      map.forEach((handle) => clearTimeout(handle));
      map.clear();
    };
  }, []);

  const value: ToastContextValue = {
    push,
    error: (m) => push(m, "error"),
    success: (m) => push(m, "success"),
    info: (m) => push(m, "info"),
  };

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="fixed bottom-6 right-6 z-[100] flex flex-col gap-2 max-w-sm pointer-events-none">
        {toasts.map((t) => (
          <button
            key={t.id}
            onClick={() => dismiss(t.id)}
            className={`pointer-events-auto text-left px-4 py-3 rounded-lg shadow-lg border text-sm leading-snug backdrop-blur-md ${
              t.kind === "error"
                ? "bg-red-950/90 border-red-700 text-red-100"
                : t.kind === "success"
                ? "bg-emerald-950/90 border-emerald-700 text-emerald-100"
                : "bg-slate-900/90 border-slate-700 text-slate-100"
            }`}
          >
            <div className="flex items-start justify-between gap-3">
              <span className="break-words">{t.message}</span>
              <span className="text-xs opacity-50 shrink-0">×</span>
            </div>
          </button>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
