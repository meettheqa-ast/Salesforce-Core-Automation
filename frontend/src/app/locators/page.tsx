"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import MetricCard from "@/components/cards/MetricCard";
import CredentialBar from "@/components/layout/CredentialBar";
import { api } from "@/lib/api";

export default function LocatorsPage() {
  const [creds, setCreds] = useState({ sandboxUrl: "", username: "", password: "" });
  const [scanning, setScanning] = useState(false);
  const [results, setResults] = useState<any>(null);
  const [error, setError] = useState("");

  const handleScan = async () => {
    if (!creds.sandboxUrl || !creds.username || !creds.password) {
      setError("Configure Salesforce credentials first.");
      return;
    }
    setScanning(true); setError(""); setResults(null);
    try {
      const res = await api.locators.scan({
        sandbox_url: creds.sandboxUrl,
        username: creds.username,
        password: creds.password,
      });
      setResults(res);
    } catch (e: any) { setError(e.message); }
    finally { setScanning(false); }
  };

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-4xl font-bold mb-2">
          <span className="bg-gradient-to-r from-pink-400 to-cyan-400 bg-clip-text text-transparent">Locator Scanner</span>
        </h1>
        <p className="text-slate-400 mb-6">Test your GlobalLocators.robot selectors against the live Salesforce DOM.</p>
      </motion.div>

      <CredentialBar onCredentialsChange={setCreds} />

      <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.95 }} onClick={handleScan} disabled={scanning}
        className="px-6 py-3 bg-gradient-to-r from-pink-600 to-purple-500 text-white font-semibold rounded-xl text-sm disabled:opacity-50 mb-6">
        {scanning ? "Scanning..." : "Start Scan"}
      </motion.button>

      {error && <p className="text-red-400 text-sm mb-4">{error}</p>}

      <AnimatePresence>
        {results && (
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
            <div className="grid grid-cols-3 gap-4 mb-6">
              <MetricCard icon="✅" label="Healthy" value={results.healthy} color="green" />
              <MetricCard icon="⚠️" label="Stale" value={results.stale} color="pink" />
              <MetricCard icon="⏭️" label="Skipped" value={results.skipped} color="cyan" />
            </div>

            {results.results?.filter((r: any) => r.status === "NOT_FOUND").length > 0 && (
              <AnimatedCard glow="pink" className="mb-4">
                <h3 className="text-sm font-bold text-red-400 mb-3">Stale Locators</h3>
                <div className="space-y-2">
                  {results.results.filter((r: any) => r.status === "NOT_FOUND").map((r: any) => (
                    <div key={r.name} className="flex items-start gap-3 p-2 bg-red-500/5 rounded-lg">
                      <span className="text-red-400 text-xs mt-0.5">✗</span>
                      <div>
                        <div className="text-sm font-mono text-white">{r.name}</div>
                        <div className="text-xs text-slate-500 font-mono truncate max-w-lg">{r.locator}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </AnimatedCard>
            )}

            {results.results?.filter((r: any) => r.status === "FOUND").length > 0 && (
              <AnimatedCard glow="cyan">
                <h3 className="text-sm font-bold text-emerald-400 mb-3">Healthy ({results.healthy})</h3>
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {results.results.filter((r: any) => r.status === "FOUND").map((r: any) => (
                    <div key={r.name} className="text-xs text-slate-500 font-mono">✓ {r.name}</div>
                  ))}
                </div>
              </AnimatedCard>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
