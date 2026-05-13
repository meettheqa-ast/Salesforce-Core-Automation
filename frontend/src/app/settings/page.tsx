"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import { api } from "@/lib/api";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";

const PROVIDER_PREF_KEY = "ai_qa_portal.preferred_llm_provider";

export default function SettingsPage() {
  const [providers, setProviders] = useState<any[]>([]);
  const [selectedProvider, setSelectedProvider] = useState("gemini");
  const [savedProvider, setSavedProvider] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [providerStatus, setProviderStatus] = useState<string | null>(null);
  const [mcpStatus, setMcpStatus] = useState<any>(null);
  const [mcpError, setMcpError] = useState<string | null>(null);
  const [catalogStatus, setCatalogStatus] = useState("");

  useEffect(() => {
    api.llm.providers().then((d) => setProviders(d.providers || [])).catch((e) => {
      setProviderStatus(`Could not load providers: ${e instanceof Error ? e.message : String(e)}`);
    });
    api.mcp.health().then(setMcpStatus).catch(() => setMcpStatus({ running: false }));
    if (typeof window !== "undefined") {
      const stored = localStorage.getItem(PROVIDER_PREF_KEY);
      if (stored) {
        setSelectedProvider(stored);
        setSavedProvider(stored);
      }
    }
  }, []);

  const handleApplyProvider = async () => {
    setProviderStatus("Verifying...");
    try {
      // Round-trip through /api/llm/chat (provider override) so we surface
      // a real success / failure rather than just toggling local state.
      await api.llm.chat({
        provider: selectedProvider,
        system_prompt: "Reply with the single word OK and nothing else.",
        user_message: "ping",
      });
      if (typeof window !== "undefined") {
        localStorage.setItem(PROVIDER_PREF_KEY, selectedProvider);
      }
      setSavedProvider(selectedProvider);
      setProviderStatus(`Active provider for new generations: ${selectedProvider}`);
    } catch (e) {
      setProviderStatus(`Provider check failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleMCPToggle = async () => {
    setMcpError(null);
    try {
      if (mcpStatus?.running) await api.mcp.stop(); else await api.mcp.start();
      setMcpStatus(await api.mcp.health());
    } catch (e) {
      setMcpError(e instanceof Error ? e.message : String(e));
    }
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
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Platform"
          title="Settings"
          description="Configure providers, runtime services, and platform maintenance tools."
        />
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
            <input type="password" placeholder="API Key (server-side override; not yet wired)" value={apiKey}
              onChange={(e) => setApiKey(e.target.value)} disabled
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-300 placeholder-slate-600 outline-none focus:border-purple-500 disabled:opacity-40 disabled:cursor-not-allowed" />
            <p className="text-xs text-slate-600">API keys live in the backend .env. The selector below only changes which provider this browser session prefers.</p>
          </div>
          <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }} onClick={handleApplyProvider}
            className="mt-3 w-full py-2 bg-purple-500/20 text-purple-300 border border-purple-500/30 rounded-xl font-semibold text-sm hover:bg-purple-500/30 transition-all">
            {savedProvider === selectedProvider ? "Re-verify provider" : "Apply provider"}
          </motion.button>
          {providerStatus && (
            <p className={`text-xs mt-2 ${providerStatus.startsWith("Provider check failed") || providerStatus.startsWith("Could not") ? "text-red-400" : "text-emerald-400"}`}>
              {providerStatus}
            </p>
          )}
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
          {mcpError && <p className="text-xs mt-2 text-red-400">{mcpError}</p>}
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

        {/* SF DX Status -- hidden along with the SF DX nav tab. Re-enable when ready.
        <AnimatedCard glow="purple" delay={0.3}>
          <h3 className="text-sm font-bold text-white mb-4">Salesforce DX</h3>
          <div className="flex items-center gap-3 mb-3">
            <div className={`w-3 h-3 rounded-full ${dxStatus?.available ? "bg-emerald-400" : "bg-slate-500"}`} />
            <span className="text-sm text-slate-300">{dxStatus?.available ? "Available" : "Not Available"}</span>
          </div>
          <p className="text-xs text-slate-500">{dxStatus?.summary || "SF DX CLI status unavailable"}</p>
        </AnimatedCard>
        */}
      </div>
    </PageScaffold>
  );
}
