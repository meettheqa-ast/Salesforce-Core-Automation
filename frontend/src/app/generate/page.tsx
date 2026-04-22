"use client";

import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import AIConsoleInput from "@/components/ai/AIConsoleInput";
import CredentialBar from "@/components/layout/CredentialBar";
import RobotCodeEditor from "@/components/editor/RobotCodeEditor";
import ExecutionLogStream from "@/components/execution/ExecutionLogStream";
import StoryExecutionPanel from "@/components/execution/StoryExecutionPanel";
import { api } from "@/lib/api";

const TEMPLATES = [
  { label: "Create Lead", prompt: "Create a new Lead with auto-generated data and verify it was created" },
  { label: "Account CRUD", prompt: "Create an Account named Acme Corp, verify it exists, then delete it" },
  { label: "Update Opp", prompt: "Create an Opportunity, update its Stage to Closed Won, and verify" },
  { label: "Verify Contact", prompt: "Create a Contact and verify First Name, Last Name, and Email" },
];

export default function GeneratePage() {
  const [loading, setLoading] = useState(false);
  const [robotCode, setRobotCode] = useState("");
  const [error, setError] = useState("");
  const [genMode, setGenMode] = useState<"quick" | "stepwise">("stepwise");
  const [creds, setCreds] = useState({ sandboxUrl: "", username: "", password: "" });
  const [runRequest, setRunRequest] = useState<any>(null);
  const [testPath, setTestPath] = useState("");
  const [saveStatus, setSaveStatus] = useState("");

  const handleGenerate = useCallback(async (prompt: string) => {
    setLoading(true);
    setError("");
    setRobotCode("");
    setRunRequest(null);
    setSaveStatus("");
    try {
      const payload = {
        prompt,
        sandbox_url: creds.sandboxUrl,
        username: creds.username,
        password: creds.password,
        generation_mode: genMode,
      };
      const res = genMode === "stepwise"
        ? await api.generate.stepwise(payload)
        : await api.generate.quick(payload);
      setRobotCode(res.robot_code || "");
      setTestPath(res.test_path || "");
    } catch (err: any) {
      setError(err.message || "Generation failed");
    } finally {
      setLoading(false);
    }
  }, [creds, genMode]);

  const handleRun = () => {
    if (!testPath || !creds.sandboxUrl) {
      setError("Generate a script and configure credentials first.");
      return;
    }
    setRunRequest({
      test_path: testPath,
      sandbox_url: creds.sandboxUrl,
      username: creds.username,
      password: creds.password,
      headless: true,
    });
  };

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-4xl font-bold mb-2">
          <span className="bg-gradient-to-r from-purple-400 to-cyan-400 bg-clip-text text-transparent">
            AI Test Generator
          </span>
        </h1>
        <p className="text-slate-400 mb-6">Describe your Salesforce test. The AI generates, you review, then run.</p>
      </motion.div>

      {/* Credentials */}
      <CredentialBar onCredentialsChange={setCreds} />

      {/* Generation Mode Toggle */}
      <div className="flex items-center gap-4 mb-6">
        <span className="text-xs text-slate-500 uppercase tracking-wider font-semibold">Mode</span>
        <div className="flex gap-1 glass rounded-lg p-0.5">
          {([
            { key: "stepwise" as const, label: "MCP Stepwise", icon: "✓" },
            { key: "quick" as const, label: "Quick Generate", icon: "⚡" },
          ]).map(({ key, label, icon }) => (
            <button
              key={key}
              onClick={() => setGenMode(key)}
              className={`px-4 py-1.5 rounded-md text-xs font-medium transition-all flex items-center gap-1.5 ${
                genMode === key ? "bg-purple-600/30 text-purple-300" : "text-slate-500 hover:text-slate-300"
              }`}
            >
              <span>{icon}</span> {label}
            </button>
          ))}
        </div>
      </div>

      {/* Templates */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        {TEMPLATES.map((t, i) => (
          <motion.button
            key={t.label}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.08 }}
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
            onClick={() => handleGenerate(t.prompt)}
            className="glass p-3 text-left text-sm text-slate-300 hover:text-white transition-colors"
          >
            <div className="font-semibold mb-1">{t.label}</div>
            <div className="text-xs text-slate-500 line-clamp-2">{t.prompt}</div>
          </motion.button>
        ))}
      </div>

      {/* AI Console */}
      <AIConsoleInput onSubmit={handleGenerate} loading={loading} />

      {/* Error */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mt-4 p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-red-400 text-sm"
          >
            {error}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Generated Code */}
      <AnimatePresence>
        {robotCode && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mt-6"
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-cyan-400">Generated Script</h3>
              <div className="flex gap-2">
                <motion.button
                  whileHover={{ scale: 1.05 }}
                  whileTap={{ scale: 0.95 }}
                  onClick={handleRun}
                  className="px-4 py-1.5 bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 rounded-lg text-xs font-semibold hover:bg-emerald-600/40 transition-all"
                >
                  Run
                </motion.button>
                {saveStatus ? (
                  <span className="text-xs text-emerald-400 self-center">{saveStatus}</span>
                ) : null}
              </div>
            </div>

            <RobotCodeEditor
              value={robotCode}
              onChange={setRobotCode}
              height="400px"
            />
          </motion.div>
        )}
      </AnimatePresence>

      <StoryExecutionPanel />

      {/* Execution Logs */}
      <ExecutionLogStream runRequest={runRequest} />
    </div>
  );
}
