"use client";

import { useState, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";

interface AIConsoleInputProps {
  onSubmit: (prompt: string) => void;
  loading?: boolean;
  placeholder?: string;
}

export default function AIConsoleInput({ onSubmit, loading, placeholder }: AIConsoleInputProps) {
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSubmit = () => {
    if (value.trim() && !loading) {
      onSubmit(value.trim());
      setValue("");
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="relative">
      <motion.div
        animate={{
          boxShadow: focused
            ? "0 0 30px rgba(139, 92, 246, 0.3), 0 0 60px rgba(6, 182, 212, 0.1)"
            : "0 0 0px rgba(139, 92, 246, 0)",
        }}
        className="relative rounded-2xl overflow-hidden"
      >
        <div className="gradient-border">
          <div className="bg-[#0a0e1a] p-1 rounded-2xl">
            <textarea
              ref={textareaRef}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              onKeyDown={handleKeyDown}
              placeholder={placeholder || "Describe your test in plain English..."}
              rows={4}
              className="w-full bg-transparent text-slate-200 placeholder-slate-500 text-sm resize-none outline-none p-4 font-mono"
            />
            <div className="flex items-center justify-between px-4 pb-3">
              <div className="flex items-center gap-2">
                <AnimatePresence>
                  {loading && (
                    <motion.div
                      initial={{ opacity: 0, scale: 0 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={{ opacity: 0, scale: 0 }}
                      className="flex items-center gap-2 text-xs text-purple-400"
                    >
                      <div className="flex gap-1">
                        {[0, 1, 2].map((i) => (
                          <motion.div
                            key={i}
                            className="w-1.5 h-1.5 rounded-full bg-purple-400"
                            animate={{ y: [0, -6, 0] }}
                            transition={{ duration: 0.6, repeat: Infinity, delay: i * 0.15 }}
                          />
                        ))}
                      </div>
                      AI is thinking...
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
              <motion.button
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                onClick={handleSubmit}
                disabled={loading || !value.trim()}
                className="px-5 py-2 bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold rounded-xl disabled:opacity-40 transition-all"
              >
                {loading ? "Generating..." : "Generate"}
              </motion.button>
            </div>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
