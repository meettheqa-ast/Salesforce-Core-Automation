"use client";

/**
 * Generic multi-row selection hook used by every list that supports
 * bulk actions (delete, archive, assign-to-sprint, etc.).
 *
 * Returned API is intentionally tiny -- no derived "selectedRows" so
 * callers don't pass stale row arrays back in; they compute that from
 * `selected` themselves where they own the canonical row list. This
 * keeps the hook independent of the row shape, which lets it work for
 * sprints / stories / test cases / jira sprints / jira issues with a
 * single file.
 */
import { useCallback, useMemo, useState } from "react";

export interface RowSelection<Id extends string> {
  /** Current selection set (kept as a Set for O(1) membership checks). */
  selected: Set<Id>;
  /** Number of selected ids. */
  count: number;
  /** True when at least one row is selected. */
  hasAny: boolean;
  /** True when all `ids` are present in the selection. */
  isAllSelected: (ids: Id[]) => boolean;
  /** True when the specific id is in the selection. */
  isSelected: (id: Id) => boolean;
  /** Toggle one row. */
  toggle: (id: Id) => void;
  /** If every id in `ids` is already selected, clear them; otherwise add
   *  all of them. Used for the "select all" header checkbox. */
  toggleAll: (ids: Id[]) => void;
  /** Replace the entire selection. */
  setAll: (ids: Id[]) => void;
  /** Clear selection. */
  clear: () => void;
}

export function useRowSelection<Id extends string = string>(): RowSelection<Id> {
  const [selected, setSelected] = useState<Set<Id>>(() => new Set<Id>());

  const toggle = useCallback((id: Id) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAll = useCallback((ids: Id[]) => {
    setSelected((current) => {
      const allPresent = ids.length > 0 && ids.every((id) => current.has(id));
      if (allPresent) {
        const next = new Set(current);
        ids.forEach((id) => next.delete(id));
        return next;
      }
      const next = new Set(current);
      ids.forEach((id) => next.add(id));
      return next;
    });
  }, []);

  const setAll = useCallback((ids: Id[]) => {
    setSelected(new Set(ids));
  }, []);

  const clear = useCallback(() => {
    setSelected(new Set());
  }, []);

  const isSelected = useCallback((id: Id) => selected.has(id), [selected]);
  const isAllSelected = useCallback(
    (ids: Id[]) => ids.length > 0 && ids.every((id) => selected.has(id)),
    [selected],
  );

  return useMemo(
    () => ({
      selected,
      count: selected.size,
      hasAny: selected.size > 0,
      isAllSelected,
      isSelected,
      toggle,
      toggleAll,
      setAll,
      clear,
    }),
    [selected, isAllSelected, isSelected, toggle, toggleAll, setAll, clear],
  );
}
