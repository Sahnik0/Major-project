"use client";

import React, { useState } from "react";
import { Download, AlertTriangle, CheckCircle2 } from "lucide-react";
import { api } from "@/lib/api";

interface InventoryViewProps {
  datasetId: string;
  inventoryItems: any[];
}

export function InventoryView({ datasetId, inventoryItems }: InventoryViewProps) {
  const [filterRisk, setFilterRisk] = useState("all");

  const filtered = inventoryItems.filter((item) => {
    if (filterRisk === "all") return true;
    return item.stockout_risk === filterRisk;
  });

  return (
    <div className="space-y-4">
      {/* Header & Export button */}
      <div className="flex items-center justify-between bg-slate-900 border border-slate-800 p-4 rounded-xl">
        <div>
          <h3 className="text-sm font-semibold text-white">Consolidated Inventory & Reorder Plan</h3>
          <p className="text-xs text-slate-400 mt-0.5">
            Operational recommendations across all {inventoryItems.length} catalog items.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <select
            value={filterRisk}
            onChange={(e) => setFilterRisk(e.target.value)}
            className="bg-slate-950 border border-slate-700/80 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none"
          >
            <option value="all">All Risk Levels</option>
            <option value="high">High Risk Only</option>
            <option value="medium">Watch List</option>
            <option value="low">Healthy Stock</option>
          </select>

          <a
            href={api.getInventoryCsvUrl(datasetId)}
            download
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold shadow-md shadow-blue-600/20 transition"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Export CSV</span>
          </a>
        </div>
      </div>

      {/* Plan Table */}
      <div className="border border-slate-800 rounded-2xl overflow-hidden bg-slate-900 shadow-xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-900/90 text-slate-400 font-medium">
                <th className="py-3 px-4">Product SKU</th>
                <th className="py-3 px-4">Demand Pattern</th>
                <th className="py-3 px-4 text-right">Reorder Point (ROP)</th>
                <th className="py-3 px-4 text-right">Recommended Order</th>
                <th className="py-3 px-4">Stockout Risk</th>
                <th className="py-3 px-4">Operational Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 text-slate-300">
              {filtered.map((item) => (
                <tr key={item.product} className="hover:bg-slate-800/30 transition">
                  <td className="py-3 px-4 font-semibold text-white">{item.product}</td>
                  <td className="py-3 px-4">
                    <span className="text-[10px] uppercase font-bold px-2 py-0.5 rounded bg-slate-800 text-slate-400">
                      {item.series_class}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-right font-mono font-bold text-white">
                    {Math.round(item.reorder_point)}
                  </td>
                  <td className="py-3 px-4 text-right font-mono">
                    {item.recommended_order_qty && item.recommended_order_qty > 0 ? (
                      <span className="font-bold text-emerald-400">+{item.recommended_order_qty}</span>
                    ) : (
                      <span className="text-slate-500">0</span>
                    )}
                  </td>
                  <td className="py-3 px-4">
                    {item.stockout_risk === "high" && (
                      <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-rose-500/10 text-rose-400 font-semibold border border-rose-500/20">
                        <AlertTriangle className="w-3 h-3" /> High Risk
                      </span>
                    )}
                    {item.stockout_risk === "medium" && (
                      <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 font-semibold border border-amber-500/20">
                        Medium Watch
                      </span>
                    )}
                    {item.stockout_risk === "low" && (
                      <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 font-semibold border border-emerald-500/20">
                        <CheckCircle2 className="w-3 h-3" /> Healthy
                      </span>
                    )}
                    {item.stockout_risk === "unknown" && (
                      <span className="text-[11px] text-slate-500">Unspecified Stock</span>
                    )}
                  </td>
                  <td className="py-3 px-4 text-slate-300 text-[11px]">
                    {item.advice_text}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

