"use client";

import { motion } from "framer-motion";
import { ReactNode } from "react";

interface AnimatedCardProps {
  children: ReactNode;
  className?: string;
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

export default function AnimatedCard({
  children,
  className = "",
  delay = 0,
  glow = "purple",
  hover = true,
}: AnimatedCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay, ease: "easeOut" }}
      whileHover={hover ? { y: -4, scale: 1.01 } : undefined}
      className={`glass p-6 transition-all duration-300 ${glowMap[glow]} ${className}`}
    >
      {children}
    </motion.div>
  );
}
