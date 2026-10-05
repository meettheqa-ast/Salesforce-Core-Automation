import { ReactNode } from "react";

interface AnimatedCardProps {
  children: ReactNode;
  className?: string;
  /** @deprecated no-op -- entrance stagger removed for perf. Kept so call
   *  sites across ~30 pages don't need a mechanical prop-removal pass. */
  delay?: number;
  glow?: "purple" | "cyan" | "pink" | "none";
  hover?: boolean;
}

const glowMap = {
  purple: "hover:shadow-[0_0_30px_rgba(139,92,246,0.3)]",
  cyan: "hover:shadow-[0_0_30px_rgba(6,182,212,0.3)]",
  pink: "hover:shadow-[0_0_30px_rgba(236,72,153,0.3)]",
  none: "",
};

/**
 * Static glass card -- used to be a `motion.div` with an opacity/y fade-in
 * (staggered via `delay`) plus a `whileHover` lift/scale. Removed entirely:
 * this is the single most-reused wrapper in the app (~30 pages, often 4-8
 * instances per page with increasing `delay`), so the stagger was adding a
 * real, compounding "page feels like it's still loading" cost on every
 * navigation. The hover glow is still CSS (`hover:shadow-*`, cheap,
 * compositor-only) -- only the JS-driven entrance/lift animation is gone.
 */
export default function AnimatedCard({
  children,
  className = "",
  glow = "purple",
}: AnimatedCardProps) {
  return (
    <div
      className={`glass p-6 transition-shadow duration-200 ${glowMap[glow]} ${className}`}
    >
      {children}
    </div>
  );
}
