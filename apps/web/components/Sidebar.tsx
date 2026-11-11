"use client";

import React from "react";
import {
  Plus,
  FileSpreadsheet,
  Trash2,
  Cpu,
  ShieldCheck,
  Settings as SettingsIcon,
} from "lucide-react";
import { DatasetItem, SystemHealth } from "@/lib/api";

interface SidebarProps {
  datasets: DatasetItem[];
  selectedDatasetId: string | null;
  onSelectDataset: (id: string) => void;
  onNewUpload: () => void;
  onDirectUpload?: (file: File) => void;
  onDeleteDataset: (id: string) => void;
  onOpenSettings: () => void;
  health: SystemHealth | null;
}

export function Sidebar({
  datasets,
  selectedDatasetId,
  onSelectDataset,
  onNewUpload,
  onDirectUpload,
  onDeleteDataset,
  onOpenSettings,
  health,
}: SidebarProps) {
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  return (
    <aside className="w-64 bg-[#171717] border-r border-[#262626] flex flex-col h-screen shrink-0 select-none text-[#ececec]">
      {/* App Header: Clean minimalist typography */}
      <div className="p-3.5 border-b border-[#262626] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-full border border-white/20 flex items-center justify-center text-[10px] font-mono text-white/80">
            DA
          </div>
          <span className="text-xs font-semibold tracking-tight text-[#ececec]">AI Data Analyst</span>
        </div>

        <button
          onClick={onOpenSettings}
          className="p-1.5 rounded-md text-[#b4b4b4] hover:text-[#ececec] hover:bg-[#212121] transition"
          title="Settings"
        >
          <SettingsIcon className="w-4 h-4" />
        </button>
      </div>

      {/* New Upload Button (ChatGPT "+ New Chat" style) */}
      <div className="p-3">
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,.tsv,.txt,.xlsx,.xls,.json,.jsonl,.parquet"
          className="hidden"
          onChange={(e) => {
            if (e.target.files?.[0]) {
              onDirectUpload?.(e.target.files[0]);
              e.target.value = "";
            }
          }}
        />
        <button
          onClick={() => {
            if (onDirectUpload) {
              fileInputRef.current?.click();
            } else {
              onNewUpload();
            }
          }}
          className="w-full flex items-center justify-between px-3 py-2 rounded-lg border border-[#333333] hover:bg-[#212121] text-[#ececec] text-xs font-medium transition group"
        >
          <span className="flex items-center gap-2">
            <Plus className="w-4 h-4 text-[#b4b4b4] group-hover:text-white" />
            <span>Upload dataset</span>
          </span>
          <span className="text-[10px] text-[#8e8e8e] font-mono">CSV/XLSX/JSON</span>
        </button>
      </div>

      {/* Datasets List */}
      <div className="flex-1 overflow-y-auto px-2.5 py-1 space-y-0.5">
        <div className="px-2 py-1.5 text-[11px] font-medium text-[#8e8e8e]">
          History
        </div>

        {datasets.length === 0 ? (
          <div className="px-3 py-4 text-xs text-[#8e8e8e]">
            No sales files uploaded yet.
          </div>
        ) : (
          datasets.map((d) => {
            const isSelected = d.id === selectedDatasetId;
            return (
              <div
                key={d.id}
                onClick={() => onSelectDataset(d.id)}
                className={`group flex items-center justify-between px-2.5 py-2 rounded-lg text-xs cursor-pointer transition ${
                  isSelected
                    ? "bg-[#212121] text-white font-medium"
                    : "text-[#b4b4b4] hover:bg-[#212121]/60 hover:text-white"
                }`}
              >
                <div className="flex items-center gap-2 truncate pr-2">
                  <FileSpreadsheet className="w-3.5 h-3.5 shrink-0 text-[#8e8e8e]" />
                  <span className="truncate">{d.name}</span>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (confirm(`Delete "${d.name}"?`)) {
                      onDeleteDataset(d.id);
                    }
                  }}
                  className={`p-1.5 rounded hover:bg-[#2f2f2f] text-[#8e8e8e] hover:text-rose-400 transition shrink-0 ${
                    isSelected
                      ? "opacity-70 hover:opacity-100"
                      : "opacity-0 group-hover:opacity-100"
                  }`}
                  title={`Delete ${d.name}`}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            );
          })
        )}
      </div>

      {/* Hardware & Privacy Status Footer: Minimal, clean, monochrome */}
      <div className="p-3 border-t border-[#262626] bg-[#171717] space-y-1.5 text-[11px] text-[#8e8e8e]">
        <div className="flex items-center justify-between px-1">
          <span className="flex items-center gap-1.5">
            <Cpu className="w-3.5 h-3.5 text-[#b4b4b4]" />
            <span className="font-mono text-[11px] text-[#ececec]">
              {health?.device_name ? "RTX 4050 (CUDA)" : "Local CPU"}
            </span>
          </span>
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
        </div>

        <div className="flex items-center justify-between px-1 text-[11px]">
          <span className="text-[#8e8e8e]">Local Gemma 3</span>
          <span className="text-[#ececec] font-mono text-[10px]">Verified Math</span>
        </div>

        <div className="flex items-center gap-1.5 pt-1 px-1 text-[10px] text-[#8e8e8e]">
          <ShieldCheck className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
          <span>100% Offline • Data stays on device</span>
        </div>
      </div>
    </aside>
  );
}
