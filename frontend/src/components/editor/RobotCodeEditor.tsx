"use client";

import dynamic from "next/dynamic";
import { motion } from "framer-motion";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), { ssr: false });

interface RobotCodeEditorProps {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  height?: string;
}

export default function RobotCodeEditor({ value, onChange, readOnly = false, height = "400px" }: RobotCodeEditorProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-2xl overflow-hidden border border-white/10"
    >
      <div className="flex items-center justify-between px-4 py-2 bg-white/5 border-b border-white/5">
        <span className="text-xs text-slate-400 font-mono">Robot Framework</span>
        <div className="flex gap-2">
          <button
            onClick={() => navigator.clipboard.writeText(value)}
            className="text-xs text-slate-500 hover:text-white px-2 py-1 rounded transition-colors"
          >
            Copy
          </button>
          <button
            onClick={() => {
              const blob = new Blob([value], { type: "text/plain" });
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url;
              a.download = "generated_test.robot";
              a.click();
              URL.revokeObjectURL(url);
            }}
            className="text-xs text-slate-500 hover:text-white px-2 py-1 rounded transition-colors"
          >
            Download
          </button>
        </div>
      </div>
      <MonacoEditor
        height={height}
        language="plaintext"
        theme="vs-dark"
        value={value}
        onChange={(v) => onChange?.(v || "")}
        options={{
          readOnly,
          minimap: { enabled: false },
          fontSize: 13,
          lineNumbers: "on",
          scrollBeyondLastLine: false,
          wordWrap: "on",
          padding: { top: 12 },
          renderLineHighlight: "gutter",
          folding: true,
        }}
      />
    </motion.div>
  );
}
