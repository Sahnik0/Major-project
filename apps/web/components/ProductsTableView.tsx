"use client";

import React, { useState } from "react";
import { Search, Filter, AlertTriangle, CheckCircle2, ChevronRight } from "lucide-react";
import { ProductSummary } from "@/lib/api";

interface ProductsTableViewProps {
  products: ProductSummary[];
  onSelectProduct: (productId: string) => void;
}

export function ProductsTableView({ products, onSelectProduct }: ProductsTableViewProps) {
  const [search, setSearch] = useState("");
  const [classFilter, setClassFilter] = useState("all");
  const [riskFilter, setRiskFilter] = useState("all");

  const filtered = products.filter((p) => {
    const matchesSearch = p.product.toLowerCase().includes(search.toLowerCase());
    const matchesClass = classFilter === "all" || p.series_class === classFilter;
    const matchesRisk = riskFilter === "all" || p.stockout_risk === riskFilter;
    return matchesSearch && matchesClass && matchesRisk;
  });

  return (
    <div className="space-y-4">
      {/* Filters & Search Header */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-slate-900 border border-slate-800 p-4 rounded-xl">
        <div className="relative w-full sm:w-72">
          <Search className="w-4 h-4 text-slate-400 absolute left-3 top-2.5" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search product SKU or name..."
            className="w-full bg-slate-950 border border-slate-700/80 rounded-lg pl-9 pr-3 py-1.5 text-xs text-white placeholder:text-slate-500 focus:outline-none focus:border-blue-500"
          />
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto">
          {/* Demand Class Filter */}
          <select
            value={classFilter}
            onChange={(e) => setClassFilter(e.target.value)}
            className="bg-slate-950 border border-slate-700/80 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none"
          >
            <option value="all">All Demand Classes</option>
            <option value="smooth">Smooth</option>
            <option value="erratic">Erratic</option>
            <option value="intermittent">Intermittent</option>
            <option value="lumpy">Lumpy</option>
            <option value="short_history">Short History</option>
            <option value="inactive">Inactive</option>
          </select>

          {/* Stockout Risk Filter */}
          <select
            value={riskFilter}
            onChange={(e) => setRiskFilter(e.target.value)}
            className="bg-slate-950 border border-slate-700/80 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none"
          >
            <option value="all">All Risk Levels</option>
            <option value="high">High Stockout Risk</option>
            <option value="medium">Medium Watch</option>
            <option value="low">Healthy / Low Risk</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="border border-slate-800 rounded-2xl overflow-hidden bg-slate-900 shadow-xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-900/90 text-slate-400 font-medium">
                <th className="py-3 px-4">Product SKU</th>
                <th className="py-3 px-4">Demand Pattern</th>
                <th className="py-3 px-4">Winning Model</th>
                <th className="py-3 px-4 text-right">Projected Demand</th>
                <th className="py-3 px-4">Stockout Risk</th>
                <th className="py-3 px-4 text-right">Reorder Qty</th>
                <th className="py-3 px-4 text-right">Error (WAPE)</th>
                <th className="py-3 px-4 text-center">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 text-slate-300">
              {filtered.map((item) => (
                <tr
                  key={item.product}
                  onClick={() => onSelectProduct(item.product)}
                  className="hover:bg-slate-800/40 cursor-pointer transition"
                >
                  <td className="py-3 px-4 font-semibold text-white">{item.product}</td>
                  <td className="py-3 px-4">
                    <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-slate-800 text-slate-300">
                      {item.series_class}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-slate-400 font-mono text-[11px]">
                    {item.chosen_model}
                  </td>
                  <td className="py-3 px-4 text-right font-mono font-bold text-white">
                    {Math.round(item.total_forecast_demand).toLocaleString()}
                  </td>
                  <td className="py-3 px-4">
                    {item.stockout_risk === "high" && (
                      <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-rose-500/10 text-rose-400 font-semibold border border-rose-500/20">
                        <AlertTriangle className="w-3 h-3" /> High Risk
                      </span>
                    )}
                    {item.stockout_risk === "medium" && (
                      <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 font-semibold border border-amber-500/20">
                        Watch List
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
                  <td className="py-3 px-4 text-right">
                    {item.recommended_order_qty && item.recommended_order_qty > 0 ? (
                      <span className="font-bold text-emerald-400">+{item.recommended_order_qty}</span>
                    ) : (
                      <span className="text-slate-500">0</span>
                    )}
                  </td>
                  <td className="py-3 px-4 text-right text-slate-400 font-mono">
                    {item.wape !== null ? `${(item.wape * 100).toFixed(1)}%` : "N/A"}
                  </td>
                  <td className="py-3 px-4 text-center">
                    <span className="text-blue-400 hover:text-blue-300 inline-flex items-center gap-0.5 font-medium">
                      Details <ChevronRight className="w-3.5 h-3.5" />
                    </span>
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={8} className="py-8 text-center text-slate-400">
                    No products matched your search or filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
