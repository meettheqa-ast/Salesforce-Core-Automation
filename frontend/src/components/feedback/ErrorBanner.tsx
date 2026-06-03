"use client";

/**
 * Shared error banner. Drop-in for the per-page red text blocks every
 * list/detail file currently rolls itself. Supports an optional retry
 * button so async error handling doesn't need to wire its own UI.
 *
 * Why a banner (not a toast): a toast disappears. When a fetch fails
 * the user needs the explanation persistently visible so they can
 * retry or copy the message into a bug report.
 */

interface Props {
  /** The message to render. Accepts an Error, string, or null/undefined
   *  (renders nothing) so callers can pass `err` straight from state
   *  without an `if` wrapper. */
  error?: unknown;
  title?: string;
  onRetry?: () => void;
  retryLabel?: string;
  onDismiss?: () => void;
  className?: string;
}

export default function ErrorBanner({
  error,
  title,
  onRetry,
  retryLabel = "Retry",
  onDismiss,
  className = "",
}: Props) {
  const message = formatError(error);
  if (!message) return null;

  return (
    <div
      role="alert"
      className={`rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200 flex flex-wrap items-start justify-between gap-3 ${className}`}
    >
      <div className="min-w-0 flex-1">
        {title && <p className="font-semibold text-red-100 mb-0.5">{title}</p>}
        <p className="break-words">{message}</p>
      </div>
      <div className="flex gap-2 flex-shrink-0">
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="px-2.5 py-1 text-xs rounded border border-red-400/40 text-red-100 hover:bg-red-500/20"
          >
            {retryLabel}
          </button>
        )}
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Dismiss error"
            className="px-2 py-1 text-xs text-red-300 hover:text-white"
          >
            ×
          </button>
        )}
      </div>
    </div>
  );
}

function formatError(err: unknown): string | null {
  if (err == null) return null;
  if (err === false || err === "") return null;
  if (err instanceof Error) return err.message || "Unknown error";
  if (typeof err === "string") return err;
  try {
    return JSON.stringify(err);
  } catch {
    return String(err);
  }
}
