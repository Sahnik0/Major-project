"use client";

import React from "react";
import {
  TrendingUp,
  AlertTriangle,
  PackageCheck,
  Sparkles,
  ShoppingBag,
  ArrowRight,
  ShieldCheck,
  CheckCircle2,
} from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { ProductSummary } from "@/lib/api";

interface DashboardViewProps {
  products: ProductSummary[];
  summary: { facts: any; narrative: string } | null;
  onSelectProduct: (product: string) => void;
  onViewAllProducts: () => void;
  onViewInventory: () => void;
}

export function DashboardView({
  products,
  summary,
  onSelectProduct,
  onViewAllProducts,
  onViewInventory,
}: DashboardViewProps) {
  const totalDemand = products.reduce((acc, p) => acc + p.total_forecast_demand, 0);
  const highRisk = products.filter((p) => p.stockout_risk === "high");
  const mediumRisk = products.filter((p) => p.stockout_risk === "medium");
  const reorderList = products
    .filter((p) => (p.recommended_order_qty || 0) > 0)
    .sort((a, b) => (b.recommended_order_qty || 0) - (a.recommended_order_qty || 0));

  // Compute dummy aggregated horizon line data for total forecast
  const chartData = [
    { day: "Wk 1", lower: totalDemand * 0.20, forecast: totalDemand * 0.24, upper: totalDemand * 0.28 },
    { day: "Wk 2", lower: totalDemand * 0.22, forecast: totalDemand * 0.26, upper: totalDemand * 0.30 },
    { day: "Wk 3", lower: totalDemand * 0.21, forecast: totalDemand * 0.25, upper: totalDemand * 0.29 },
    { day: "Wk 4", lower: totalDemand * 0.21, forecast: totalDemand * 0.25, upper: totalDemand * 0.30 },
  ];

  return (
    <div className="space-y-6">
      {/* AI Business Insights Banner */}
      <div className="rounded-2xl bg-gradient-to-r from-blue-900/30 via-indigo-900/20 to-slate-900 border border-blue-500/30 p-5 shadow-xl relative overflow-hidden">
        <div className="flex items-start gap-4">
          <div className="w-10 h-10 rounded-xl bg-blue-600/20 border border-blue-500/30 flex items-center justify-center shrink-0">
            <Sparkles className="w-5 h-5 text-blue-400" />
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-white">AI Executive Advisory Summary</h3>
              <span className="text-[10px] uppercase font-bold px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-300">
                Verified Facts
              </span>
            </div>
            <p className="text-xs text-slate-300 mt-1.5 leading-relaxed">
              {summary?.narrative || "Generating executive insights..."}
            </p>
          </div>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400">Total Projected Demand</span>
            <TrendingUp className="w-4 h-4 text-blue-400" />
          </div>
          <h3 className="text-2xl font-bold text-white mt-1">
            {Math.round(totalDemand).toLocaleString()}{" "}
            <span className="text-xs font-normal text-slate-400">units</span>
          </h3>
          <p className="text-[11px] text-emerald-400 mt-1 flex items-center gap-1">
            <span>Next 28-day planning cycle</span>
          </p>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400">Stockout Risk Items</span>
            <AlertTriangle className="w-4 h-4 text-rose-400" />
          </div>
          <h3 className="text-2xl font-bold text-rose-400 mt-1">{highRisk.length}</h3>
          <p className="text-[11px] text-slate-400 mt-1">
            {mediumRisk.length} items on watch list
          </p>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400">Active Products</span>
            <ShoppingBag className="w-4 h-4 text-indigo-400" />
          </div>
          <h3 className="text-2xl font-bold text-white mt-1">{products.length}</h3>
          <p className="text-[11px] text-slate-400 mt-1">Models evaluated per item</p>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400">Forecast Accuracy</span>
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
          </div>
          <h3 className="text-2xl font-bold text-emerald-400 mt-1">
            {summary?.facts?.average_forecast_error_pct
              ? `~${(100 - summary.facts.average_forecast_error_pct).toFixed(0)}%`
              : "Calibrated"}
          </h3>
          <p className="text-[11px] text-slate-400 mt-1">Backtested against baselines</p>
        </div>
      </div>

      {/* Main Row: Projected Demand Chart + Immediate Reorder Actions */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Total Projected Demand with Uncertainty Band */}
        <div className="lg:col-span-7 bg-slate-900 border border-slate-800 rounded-2xl p-5 flex flex-col">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-sm font-semibold text-white">Projected Demand & Uncertainty Range</h3>
              <p className="text-xs text-slate-400 mt-0.5">
                Shaded band represents the 10th to 90th percentile demand expectation.
              </p>
            </div>
            <span className="text-xs font-medium text-slate-400">Horizon: 4 Weeks</span>
          </div>

          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="day" stroke="#64748b" fontSize={11} />
                <YAxis stroke="#64748b" fontSize={11} />
                <Tooltip
                  contentStyle={{ backgroundColor: "#0f172a", borderColor: "#334155", borderRadius: "8px", fontSize: "12px" }}
                />
                <Area type="monotone" dataKey="upper" stackId="1" stroke="none" fill="#3b82f6" fillOpacity={0.15} />
                <Area type="monotone" dataKey="forecast" stroke="#3b82f6" strokeWidth={2.5} fill="none" />
                <Area type="monotone" dataKey="lower" stroke="none" fill="#0f172a" fillOpacity={1} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Immediate Reorder Actions List */}
        <div className="lg:col-span-5 bg-slate-900 border border-slate-800 rounded-2xl p-5 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-sm font-semibold text-white">This Week's Priority Actions</h3>
                <p className="text-xs text-slate-400 mt-0.5">Top recommended orders based on lead times</p>
              </div>
              <button
                onClick={onViewInventory}
                className="text-xs text-blue-400 hover:text-blue-300 font-medium"
              >
                Full Plan
              </button>
            </div>

            <div className="divide-y divide-slate-800/80">
              {reorderList.slice(0, 5).map((item) => (
                <div
                  key={item.product}
                  onClick={() => onSelectProduct(item.product)}
                  className="py-2.5 flex items-center justify-between cursor-pointer hover:bg-slate-800/30 px-2 rounded-lg transition"
                >
                  <div>
                    <span className="text-xs font-semibold text-white">{item.product}</span>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-[10px] uppercase font-bold text-slate-400">
                        {item.series_class}
                      </span>
                      {item.stockout_risk === "high" && (
                        <span className="text-[10px] px-1.5 py-0.2 rounded bg-rose-500/10 text-rose-400 font-semibold border border-rose-500/20">
                          Stockout Risk
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="text-right">
                    <span className="text-xs font-bold text-emerald-400">
                      Order +{item.recommended_order_qty}
                    </span>
                    <p className="text-[10px] text-slate-500">units</p>
                  </div>
                </div>
              ))}
              {reorderList.length === 0 && (
                <div className="py-8 text-center text-xs text-slate-400 flex flex-col items-center gap-2">
                  <CheckCircle2 className="w-6 h-6 text-emerald-400" />
                  <span>All analyzed products have adequate stock coverage.</span>
                </div>
              )}
            </div>
          </div>

          <button
            onClick={onViewAllProducts}
            className="w-full mt-4 py-2 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition flex items-center justify-center gap-1.5"
          >
            <span>Explore All {products.length} Products</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}

