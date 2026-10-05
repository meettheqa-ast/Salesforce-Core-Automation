"use client";

import { MotionConfig } from "framer-motion";
import type { ReactNode } from "react";

/**
 * Global animation kill-switch, mounted once at the root layout.
 *
 * `reducedMotion="always"` forces every `motion.*` component in the tree
 * to skip transform/layout animations (slide-ins, hover lifts, tap
 * scales, drawer slides, etc.) -- regardless of whatever explicit
 * `transition` prop each individual component passes. This is a hard
 * override at the framer-motion engine level, not a "default that gets
 * overridden by explicit props," so it reaches every one of the ~70
 * files using `motion.div` / `AnimatePresence` without touching them.
 *
 * Framer Motion intentionally keeps opacity / backgroundColor animating
 * even under `reducedMotion="always"` (see
 * https://motion.dev/docs/react-motion-config) -- that's an accessibility
 * default (a visible fade still helps users track state changes), not a
 * perf escape hatch we can close from this single file. The remaining
 * per-page opacity fades were judged a minor, one-shot cost compared to
 * the stacked transform/stagger animations this removes; see
 * `AnimatedCard.tsx` for the other half of this fix (it dropped the
 * opacity fade + per-card stagger delay entirely since it's the single
 * most-reused wrapper in the app).
 */
export default function MotionConfigProvider({ children }: { children: ReactNode }) {
  return <MotionConfig reducedMotion="always">{children}</MotionConfig>;
}
