"use client";

/**
 * Reads the user's persisted theme preference (set on /settings/profile)
 * and writes it to `document.documentElement` as both a class and a
 * data attribute so future light-theme stylesheets can pick it up.
 *
 * Phase 3 of the IA audit: ship the infrastructure even though the
 * actual light-theme stylesheet is a separate design pass. With this
 * applier mounted, when the design team ships light styles they only
 * need to add `:root[data-theme="light"] { ... }` rules -- no JS
 * wiring required.
 *
 * Mounted in app/layout.tsx so it runs on every page. Reads
 * `ui.themePref` from localStorage (the same key the profile page
 * writes). Honours `system` via `prefers-color-scheme`.
 */

import { useEffect } from "react";

type Pref = "dark" | "light" | "system";

function readPref(): Pref {
  if (typeof window === "undefined") return "dark";
  try {
    const raw = window.localStorage.getItem("ui.themePref");
    if (raw === "light" || raw === "system") return raw;
  } catch {
    // localStorage disabled -- fall through to default.
  }
  return "dark";
}

function effectiveTheme(pref: Pref): "dark" | "light" {
  if (pref !== "system") return pref;
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function apply(pref: Pref) {
  if (typeof document === "undefined") return;
  const effective = effectiveTheme(pref);
  const root = document.documentElement;
  root.setAttribute("data-theme", effective);
  root.classList.toggle("dark", effective === "dark");
  root.classList.toggle("light", effective === "light");
}

export default function ThemeApplier() {
  useEffect(() => {
    let pref = readPref();
    apply(pref);

    // Re-apply when the user changes the preference on /settings/profile
    // in this tab. Storage events only fire across tabs, so we also
    // poll once on focus.
    function onStorage(e: StorageEvent) {
      if (e.key !== "ui.themePref") return;
      pref = readPref();
      apply(pref);
    }
    function onFocus() {
      const next = readPref();
      if (next !== pref) {
        pref = next;
        apply(pref);
      }
    }
    function onSystemChange() {
      if (pref === "system") apply(pref);
    }

    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", onFocus);
    const mq = window.matchMedia?.("(prefers-color-scheme: light)");
    mq?.addEventListener?.("change", onSystemChange);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", onFocus);
      mq?.removeEventListener?.("change", onSystemChange);
    };
  }, []);
  return null;
}
