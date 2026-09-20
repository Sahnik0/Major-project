"use client";

import React from "react";
import { Cpu, HardDrive, Sparkles, Settings as SettingsIcon, Database, ShieldCheck } from "lucide-react";
import { SystemHealth } from "@/lib/api";

interface NavbarProps {
  health: SystemHealth | null;
  onOpenSettings: () => void;
  onOpenUpload: () => void;
  onLoadDemo: () => void;
  loadingDemo: boolean;
}

export function Navbar({ health, onOpenSettings, onOpenUpload, onLoadDemo, loadingDemo }: NavbarProps) {
  return (
    <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-40 px-6 py-3.5">
      <div className="max-w-7xl mx-auto flex items-center justify-between">
        {/* Brand */}
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center shadow-lg shadow-blue-500/20">
            <Sparkles className="w-5 h-5 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold text-lg text-white tracking-tight">Demand Advisor</span>
              <span className="text-[10px] uppercase font-semibold px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                100% Local
              </span>
            </div>
            <p className="text-xs text-slate-400">Retail Demand Forecasting & Stock Reorder Engine</p>
          </div>
        </div>

        {/* System Status Indicators */}
        <div className="hidden md:flex items-center gap-4 text-xs">
          {/* GPU */}
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800/80 border border-slate-700/60">
            <Cpu className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-slate-300 font-medium">
              {health?.cuda_available ? (health.device_name || "CUDA GPU") : "CPU"}
            </span>
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          </div>

          {/* Ollama Gemma */}
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800/80 border border-slate-700/60">
            <Sparkles className="w-3.5 h-3.5 text-blue-400" />
            <span className="text-slate-300 font-medium">
              {health?.ollama_ready ? `Gemma (${health.ollama_model})` : "Local Fallback"}
            </span>
            <span className={`w-2 h-2 rounded-full ${health?.ollama_ready ? "bg-blue-500 animate-pulse" : "bg-amber-500"}`} />
          </div>

          {/* Disk */}
          {health && (
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800/80 border border-slate-700/60 text-slate-300">
              <HardDrive className="w-3.5 h-3.5 text-indigo-400" />
              <span>{health.disk_free_gb} GB Free</span>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-3">
          <button
            onClick={onLoadDemo}
            disabled={loadingDemo}
            className="flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/30 transition disabled:opacity-50"
          >
            <Database className="w-3.5 h-3.5" />
            {loadingDemo ? "Loading Demo..." : "Try Demo Data"}
          </button>

          <button
            onClick={onOpenUpload}
            className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold rounded-lg bg-blue-600 hover:bg-blue-500 text-white shadow-md shadow-blue-600/20 transition"
          >
            Upload Sales File
          </button>

          <button
            onClick={onOpenSettings}
            className="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition"
            title="Settings & Models"
          >
            <SettingsIcon className="w-4 h-4" />
          </button>
        </div>
      </div>
    </header>
  );
}
