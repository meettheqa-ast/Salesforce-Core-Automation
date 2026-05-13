"use client";

/**
 * useTreeRefresh
 *
 * Tiny pub/sub used to keep the LeftHierarchySidebar (and the Cmd+K
 * palette cache) in sync with create operations that happen anywhere
 * else in the UI.
 *
 * Pattern:
 *
 *   // Producer (any page that hosts a Create*Modal):
 *   import { notifyTreeRefresh } from "@/lib/useTreeRefresh";
 *   <CreateStoryModal onCreated={(s) => {
 *     existingHandler(s);
 *     notifyTreeRefresh({ kind: "story", projectId: s.project_id });
 *   }} />
 *
 *   // Consumer (LeftHierarchySidebar / CommandPalette):
 *   useTreeRefresh((event) => {
 *     if (event.kind === "story") refetchStoriesFor(event.projectId);
 *   });
 *
 * Why an event emitter and not SWR / React Query / a context:
 *   - The codebase doesn't use SWR/RQ broadly (NotificationsBell is the
 *     only SWR consumer; everything else is `useState + useEffect +
 *     api.X.list()`). Adding either as a dep just for tree refresh would
 *     be inconsistent with established patterns.
 *   - Context would force every consumer to re-render on every notify,
 *     even ones that only care about a single event kind.
 *   - This is ~30 lines of code, tested by virtue of being trivial, and
 *     deletes cleanly when/if the codebase later adopts a real cache
 *     library.
 *
 * The emitter is module-scoped (a Set of subscriber callbacks). React
 * 18 strict-mode double-mounts are handled because subscribe returns an
 * unsubscribe; the cleanup runs synchronously on unmount.
 */

import { useEffect } from "react";

export type TreeRefreshKind =
  | "project"
  | "sprint"
  | "story"
  | "test_case"
  // Generic "blow away everything" -- used after multi-step flows where
  // the change crosses entity boundaries (e.g. assigning N backlog
  // stories to a sprint). Cheaper to refetch wide than to thread
  // per-event details through every site.
  | "all";

export interface TreeRefreshEvent {
  kind: TreeRefreshKind;
  /** Portal UUID of the project this change touches, when applicable. */
  projectId?: string;
  /** Sprint UUID, when the change is sprint-scoped. */
  sprintId?: string;
  /** Story UUID, when the change is story-scoped. */
  storyId?: string;
}

type Listener = (event: TreeRefreshEvent) => void;

const listeners = new Set<Listener>();

/** Fire a refresh event. Safe to call from anywhere (page handler,
 *  modal callback, etc.). Returns synchronously after every listener
 *  has been notified. Listener exceptions are caught + logged so one
 *  bad listener doesn't break others. */
export function notifyTreeRefresh(event: TreeRefreshEvent): void {
  for (const listener of listeners) {
    try {
      listener(event);
    } catch (err) {
      // Don't let a single listener's bug take down the whole notify.
      // Surfaces in dev console; production builds collapse to silent.
       
      console.warn("useTreeRefresh listener threw:", err);
    }
  }
}

/** Subscribe to tree-refresh events. Returns an unsubscribe function.
 *  Useful for non-React consumers (e.g. the CommandPalette's
 *  module-level cache). */
export function subscribeTreeRefresh(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** React hook flavor. Subscribes for the lifetime of the calling
 *  component; unsubscribes on unmount. The listener identity matters
 *  (re-subscribes if it changes), so wrap in `useCallback` if your
 *  component re-renders frequently. */
export function useTreeRefresh(listener: Listener): void {
  useEffect(() => {
    return subscribeTreeRefresh(listener);
  }, [listener]);
}
