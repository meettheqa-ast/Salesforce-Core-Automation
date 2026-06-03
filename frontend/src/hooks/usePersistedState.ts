"use client";

/**
 * Tiny `useState` + `localStorage` mash-up. Behaves identically to
 * `useState` except the value is hydrated from localStorage on mount
 * and written back on every change.
 *
 * SSR-safe: on the server (or before hydration) it returns the initial
 * value; the first client render reads localStorage and re-renders if
 * the persisted value differed. Errors from a quota-full / disabled
 * storage are swallowed -- the state still works, just doesn't persist.
 *
 * Use sparingly: this is for cheap user-preference toggles (Show
 * archived, filter chips, sidebar collapse). Heavy / server-side data
 * still belongs in a fetch + cache layer.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export type Serializer<T> = {
  parse: (raw: string) => T;
  stringify: (value: T) => string;
};

const JSON_SERIALIZER: Serializer<unknown> = {
  parse: (raw) => JSON.parse(raw),
  stringify: (v) => JSON.stringify(v),
};

export function usePersistedState<T>(
  key: string,
  initialValue: T,
  serializer: Serializer<T> = JSON_SERIALIZER as Serializer<T>,
): [T, (value: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(initialValue);
  // Track whether we've already rehydrated from storage so the first
  // post-mount effect doesn't write the initial value back over an
  // existing entry.
  const hydrated = useRef(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = window.localStorage.getItem(key);
      if (raw !== null) {
        setValue(serializer.parse(raw));
      }
    } catch {
      // Ignore -- treat as no stored value.
    } finally {
      hydrated.current = true;
    }
    // We intentionally rehydrate only on key change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!hydrated.current) return;
    try {
      window.localStorage.setItem(key, serializer.stringify(value));
    } catch {
      // Storage full / disabled -- state still works in memory.
    }
  }, [key, value, serializer]);

  const update = useCallback((next: T | ((prev: T) => T)) => {
    setValue(next);
  }, []);

  return [value, update];
}
