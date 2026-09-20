"use client";

import React from "react";
import {
  Plus,
  FileSpreadsheet,
  Trash2,
  Cpu,
  Sparkles,
  ShieldCheck,
  Settings as SettingsIcon,
  HardDrive,
} from "lucide-react";
import { DatasetItem, SystemHealth } from "@/lib/api";

interface SidebarProps {
  datasets: DatasetItem[];
  selectedDatasetId: string | null;
  onSelectDataset: (id: string) => void;
  onNewUpload: () => void;
  onDeleteDataset: (id: string) => void;
  onOpenSettings: () => void;
  health: SystemHealth | null;
}

export function Sidebar({
  datasets,
  selectedDatasetId,
  onSelectDataset,
  onNewUpload,
  onDeleteDataset,
  onOpenSettings,
  health,
}: SidebarProps) {
  return (
    <aside className="w-64 bg-slate-950 border-r border-slate-800/80 flex flex-col h-screen shrink-0 select-none">
      {/* App Header */}
      <div className="p-4 border-b border-slate-800/60 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-blue-600 to-indigo-600 flex items-center justify-center shadow-md shadow-blue-500/20">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          <div>
            <h1 className="text-sm font-bold text-white tracking-tight leading-none">Stock Advisor</h1>
            <span className="text-[10px] text-slate-400 font-mono">100% Local AI</span>
          </div>
        </div>

        <button
          onClick={onOpenSettings}
          className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-900 transition"
          title="Settings & Defaults"
        >
          <SettingsIcon className="w-4 h-4" />
        </button>
      </div>

      {/* New Upload Button */}
      <div className="p-3">
        <button
          onClick={onNewUpload}
          className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold shadow-md shadow-blue-600/20 transition"
        >
          <Plus className="w-4 h-4" />
          <span>Upload Sales File</span>
        </button>
      </div>

      {/* Datasets List */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1">
        <div className="px-2 py-1 text-[11px] font-semibold tracking-wider text-slate-500 uppercase">
          Sales Histories
        </div>

        {datasets.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-slate-500">
            No files uploaded yet.
          </div>
        ) : (
          datasets.map((d) => {
            const isSelected = d.id === selectedDatasetId;
            return (
              <div
                key={d.id}
                onClick={() => onSelectDataset(d.id)}
                className={`group flex items-center justify-between px-2.5 py-2 rounded-xl text-xs cursor-pointer transition ${
                  isSelected
                    ? "bg-slate-800/90 text-white font-medium shadow-sm"
                    : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"
                }`}
              >
                <div className="flex items-center gap-2 truncate pr-2">
                  <FileSpreadsheet className={`w-4 h-4 shrink-0 ${isSelected ? "text-blue-400" : "text-slate-500"}`} />
                  <span className="truncate">{d.name}</span>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (confirm(`Delete "${d.name}"?`)) onDeleteDataset(d.id);
                  }}
                  className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-slate-700/60 text-slate-400 hover:text-rose-400 transition"
                  title="Delete file"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            );
          })
        )}
      </div>

      {/* Privacy & Hardware Status Footer */}
      <div className="p-3 border-t border-slate-800/80 bg-slate-950/60 space-y-2 text-[11px]">
        <div className="flex items-center justify-between text-slate-400 px-1">
          <span className="flex items-center gap-1.5">
            <Cpu className="w-3.5 h-3.5 text-emerald-400" />
            <span className="font-mono">{health?.device_name ? "NVIDIA RTX 4050" : "Local CPU"}</span>
          </span>
          <span className="flex items-center gap-1 text-[10px] text-emerald-400">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
            CUDA
          </span>
        </div>

        <div className="flex items-center justify-between text-slate-400 px-1">
          <span className="flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5 text-blue-400" />
            <span>Gemma 3</span>
          </span>
          <span className="text-[10px] text-blue-300 font-mono">Verified Facts</span>
        </div>

        <div className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-[10px] font-medium">
          <ShieldCheck className="w-3.5 h-3.5 shrink-0" />
          <span>100% Offline • Zero Telemetry</span>
        </div>
      </div>
    </aside>
  );
}
