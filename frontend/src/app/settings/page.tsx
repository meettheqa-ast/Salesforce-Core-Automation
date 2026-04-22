"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import { api } from "@/lib/api";

export default function SettingsPage() {
  const [providers, setProviders] = useState<any[]>([]);
  const [selectedProvider, setSelectedProvider] = useState("gemini");
  const [apiKey, setApiKey] = useState("");
  const [mcpStatus, setMcpStatus] = useState<any>(null);
  const [catalogStatus, setCatalogStatus] = useState("");
  const [dxStatus, setDxStatus] = useState<any>(null);

  useEffect(() => {
    api.llm.providers().then((d) => setProviders(d.providers || [])).catch(() => {});
    api.mcp.health().then(setMcpStatus).catch(() => setMcpStatus({ running: false }));
    fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/salesforce/dx/status`)
      .then((r) => r.json()).then(setDxStatus).catch(() => setDxStatus({ available: false }));
  }, []);

  const handleMCPToggle = async () => {
    try {
      if (mcpStatus?.running) await api.mcp.stop(); else await api.mcp.start();
      setMcpStatus(await api.mcp.health());
    } catch {}
  };

  const handleRebuildCatalog = async () => {
    setCatalogStatus("Rebuilding...");
    try {
      await api.catalog.rebuild();
      setCatalogStatus("Catalog rebuilt successfully");
      setTimeout(() => setCatalogStatus(""), 3000);
    } catch (e: any) {
      setCatalogStatus(`Error: ${e.message}`);
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-4xl font-bold mb-2">
          <span className="bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">Settings</span>
        </h1>
        <p className="text-slate-400 mb-8">Configure AI providers, MCP server, and platform preferences.</p>
      </motion.div>

      <div className="grid md:grid-cols-2 gap-6">
        {/* AI Provider */}
        <AnimatedCard glow="purple" delay={0}>
          <h3 className="text-sm font-bold text-white mb-4">AI Provider</h3>
          <div className="space-y-2 mb-4">
            {providers.map((p) => (
              <button key={p.id} onClick={() => setSelectedProvider(p.id)}
                className={`w-full flex items-center justify-between p-3 rounded-xl text-sm transition-all ${
                  selectedProvider === p.id
                    ? "bg-purple-600/20 text-purple-300 border border-purple-500/30"
                    : "glass text-slate-400 hover:text-white"
                }`}>
                <span>{p.label}</span>
                {selectedProvider === p.id && <span className="text-xs text-purple-400">Active</span>}
              </button>
            ))}
          </div>
          <div className="space-y-2">
            <input type="password" placeholder="API Key (overrides .env)" value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-300 placeholder-slate-600 outline-none focus:border-purple-500" />
            <p className="text-xs text-slate-600">Leave blank to use the key from .env</p>
          </div>
        </AnimatedCard>

        {/* MCP Server */}
        <AnimatedCard glow="cyan" delay={0.1}>
          <h3 className="text-sm font-bold text-white mb-4">RF-MCP Server</h3>
          <div className="flex items-center gap-3 mb-4">
            <div className={`w-3 h-3 rounded-full ${mcpStatus?.running ? "bg-emerald-400 animate-pulse" : "bg-slate-500"}`} />
            <span className="text-sm text-slate-300">{mcpStatus?.running ? "Running" : "Stopped"}</span>
          </div>
          {mcpStatus?.url && <p className="text-xs text-slate-500 mb-4 font-mono">{mcpStatus.url}</p>}
          <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }} onClick={handleMCPToggle}
            className={`w-full py-2.5 rounded-xl font-semibold text-sm transition-all ${
              mcpStatus?.running
                ? "bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30"
                : "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/30"
            }`}>
            {mcpStatus?.running ? "Stop Server" : "Start Server"}
          </motion.button>
        </AnimatedCard>

        {/* Keyword Catalog */}
        <AnimatedCard glow="pink" delay={0.2}>
          <h3 className="text-sm font-bold text-white mb-4">Keyword Catalog</h3>
          <p className="text-xs text-slate-400 mb-4">Regenerate the keyword catalog from Robot Framework resource files.</p>
          <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }} onClick={handleRebuildCatalog}
            className="w-full py-2.5 bg-pink-500/20 text-pink-300 border border-pink-500/30 rounded-xl font-semibold text-sm hover:bg-pink-500/30 transition-all">
            Rebuild Catalog
          </motion.button>
          {catalogStatus && (
            <p className={`text-xs mt-2 ${catalogStatus.includes("Error") ? "text-red-400" : "text-emerald-400"}`}>
              {catalogStatus}
            </p>
          )}
        </AnimatedCard>

        {/* SF DX Status */}
        <AnimatedCard glow="purple" delay={0.3}>
          <h3 className="text-sm font-bold text-white mb-4">Salesforce DX</h3>
          <div className="flex items-center gap-3 mb-3">
            <div className={`w-3 h-3 rounded-full ${dxStatus?.available ? "bg-emerald-400" : "bg-slate-500"}`} />
            <span className="text-sm text-slate-300">{dxStatus?.available ? "Available" : "Not Available"}</span>
          </div>
          <p className="text-xs text-slate-500">{dxStatus?.summary || "SF DX CLI status unavailable"}</p>
        </AnimatedCard>
      </div>
    </div>
  );
}
