"use client";

import React, { useState } from "react";
import {
  X,
  TrendingUp,
  Sliders,
  ShieldCheck,
  CheckCircle2,
  Calendar,
  AlertTriangle,
} from "lucide-react";
import {
  AreaChart,
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { ProductDetail } from "@/lib/api";

interface ProductDetailModalProps {
  product: ProductDetail | null;
  onClose: () => void;
}

export function ProductDetailModal({ product, onClose }: ProductDetailModalProps) {
  if (!product) return null;

  const inv = product.inventory_advice || {};
  // Interactive what-if sliders
  const [leadTime, setLeadTime] = useState(inv.lead_time_days || 7);
  const [serviceLevel, setServiceLevel] = useState(inv.service_level || 0.90);
  const [stockInput, setStockInput] = useState<string>("");

  // Prepare chart series: combine history and forecast
  const chartData = [];
  // Recent 30 history points
  const histLen = (product.history_dates || []).length;
  const recentHistStart = Math.max(0, histLen - 30);
  for (let i = recentHistStart; i < histLen; i++) {
    chartData.push({
      date: product.history_dates[i]?.slice(5),
      history: product.history_quantities[i],
      forecast: null,
      lower: null,
      upper: null,
    });
  }

  // Append forecast points
  const q10 = (product.quantiles && product.quantiles["0.1"]) || [];
  const q90 = (product.quantiles && product.quantiles["0.9"]) || [];
  const fcDates = product.forecast_dates || [];
  for (let i = 0; i < fcDates.length; i++) {
    chartData.push({
      date: fcDates[i]?.slice(5),
      history: null,
      forecast: product.point_forecast[i],
      lower: q10[i] ?? product.point_forecast[i] * 0.8,
      upper: q90[i] ?? product.point_forecast[i] * 1.2,
    });
  }

  // Interactive local recalculation
  const avgDaily = inv.average_daily_demand || 10;
  const calcLTD = avgDaily * leadTime;
  // Safety factor z based on service level
  const z = serviceLevel >= 0.95 ? 1.645 : serviceLevel >= 0.90 ? 1.282 : 1.0;
  const calcSS = Math.round(z * Math.sqrt(leadTime) * (avgDaily * 0.25));
  const calcROP = Math.round(calcLTD + calcSS);
  const currentStockVal = stockInput !== "" ? parseFloat(stockInput) : null;
  const recommendedQty =
    currentStockVal !== null && currentStockVal <= calcROP
      ? Math.round(calcROP + avgDaily * 7 - currentStockVal)
      : null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-5xl max-h-[92vh] overflow-hidden flex flex-col shadow-2xl">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-bold text-white tracking-tight">{product.product}</h2>
                <span className="text-xs uppercase font-bold px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20">
                  {product.series_class} Pattern
                </span>
                <span className="text-xs font-mono px-2 py-0.5 rounded-full bg-slate-800 text-slate-300">
                  Best Model: {product.chosen_model}
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Detailed demand dynamics, forecast intervals, and inventory sizing
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-6 overflow-y-auto space-y-6">
          {/* Main Chart: History + Forecast + Uncertainty Band */}
          <div className="bg-slate-950/60 border border-slate-800 rounded-xl p-4">
            <div className="flex items-center justify-between mb-3 text-xs">
              <span className="font-semibold text-white">Historical Sales & Forecast Range</span>
              <div className="flex items-center gap-4 text-slate-400">
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-0.5 bg-slate-400" /> Historical Sales
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-0.5 bg-blue-500" /> Point Forecast
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-2 bg-blue-500/20" /> 10-90% Range
                </span>
              </div>
            </div>

            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="date" stroke="#64748b" fontSize={10} />
                  <YAxis stroke="#64748b" fontSize={10} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#0f172a",
                      borderColor: "#334155",
                      borderRadius: "8px",
                      fontSize: "11px",
                    }}
                  />
                  <Line type="monotone" dataKey="history" stroke="#94a3b8" strokeWidth={1.5} dot={false} />
                  <Area type="monotone" dataKey="upper" stroke="none" fill="#3b82f6" fillOpacity={0.15} />
                  <Line type="monotone" dataKey="forecast" stroke="#3b82f6" strokeWidth={2.5} dot={false} />
                  <Area type="monotone" dataKey="lower" stroke="none" fill="#020617" fillOpacity={1} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Dual Column: Interactive Stock Simulator & Accuracy Proof */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Interactive Inventory Sizing */}
            <div className="bg-slate-950/60 border border-slate-800 rounded-xl p-4 space-y-4">
              <div className="flex items-center gap-2">
                <Sliders className="w-4 h-4 text-blue-400" />
                <h4 className="text-xs font-bold text-white uppercase tracking-wider">
                  Interactive Inventory Calculator
                </h4>
              </div>

              {/* Sliders */}
              <div className="space-y-3">
                <div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-slate-400">Supplier Lead Time:</span>
                    <span className="font-bold text-white">{leadTime} days</span>
                  </div>
                  <input
                    type="range"
                    min={1}
                    max={30}
                    value={leadTime}
                    onChange={(e) => setLeadTime(parseInt(e.target.value))}
                    className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-blue-500"
                  />
                </div>

                <div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-slate-400">Service Level Protection:</span>
                    <span className="font-bold text-white">{(serviceLevel * 100).toFixed(0)}%</span>
                  </div>
                  <input
                    type="range"
                    min={0.80}
                    max={0.99}
                    step={0.01}
                    value={serviceLevel}
                    onChange={(e) => setServiceLevel(parseFloat(e.target.value))}
                    className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-blue-500"
                  />
                </div>

                <div>
                  <label className="block text-xs text-slate-400 mb-1">Current Stock on Hand (Optional):</label>
                  <input
                    type="number"
                    value={stockInput}
                    onChange={(e) => setStockInput(e.target.value)}
                    placeholder="Enter units in warehouse..."
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500"
                  />
                </div>
              </div>

              {/* Calculated Outputs */}
              <div className="grid grid-cols-2 gap-2 pt-2 border-t border-slate-800">
                <div className="p-2.5 rounded-lg bg-slate-900 border border-slate-800">
                  <span className="text-[10px] text-slate-400">Reorder Point (ROP)</span>
                  <p className="text-lg font-bold text-white">{calcROP} <span className="text-xs font-normal">units</span></p>
                </div>
                <div className="p-2.5 rounded-lg bg-slate-900 border border-slate-800">
                  <span className="text-[10px] text-slate-400">Buffer Safety Stock</span>
                  <p className="text-lg font-bold text-indigo-400">+{calcSS} <span className="text-xs font-normal">units</span></p>
                </div>
              </div>

              <div className="p-3 rounded-lg bg-blue-500/10 border border-blue-500/20 text-xs text-blue-300">
                {currentStockVal !== null ? (
                  recommendedQty && recommendedQty > 0 ? (
                    <span>
                      <strong>Action Needed:</strong> Reorder <strong>{recommendedQty} units</strong> immediately.
                    </span>
                  ) : (
                    <span>
                      <strong>Stock Healthy:</strong> Inventory is above reorder point. No order needed.
                    </span>
                  )
                ) : (
                  <span>
                    <strong>Rule:</strong> Place order for <strong>{Math.round(avgDaily * 7)} units</strong> whenever stock dips below <strong>{calcROP} units</strong>.
                  </span>
                )}
              </div>
            </div>

            {/* Accuracy & Backtest Proof */}
            <div className="bg-slate-950/60 border border-slate-800 rounded-xl p-4 space-y-3">
              <div className="flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-emerald-400" />
                <h4 className="text-xs font-bold text-white uppercase tracking-wider">
                  How Accurate Has This Been?
                </h4>
              </div>

              <p className="text-xs text-slate-400 leading-relaxed">
                Candidate models were backtested on historical folds of this product's actual sales. The winning model was selected for producing the lowest historical error.
              </p>

              <div className="border border-slate-800 rounded-lg overflow-hidden">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="bg-slate-900 border-b border-slate-800 text-slate-400 text-[11px]">
                      <th className="py-2 px-3">Model</th>
                      <th className="py-2 px-3 text-right">Avg Error (WAPE)</th>
                      <th className="py-2 px-3 text-right">Coverage</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {product.backtest_table.map((m) => (
                      <tr
                        key={m.model_name}
                        className={m.model_name === product.chosen_model ? "bg-emerald-500/10 font-bold text-white" : "text-slate-300"}
                      >
                        <td className="py-2 px-3 flex items-center gap-1.5">
                          {m.model_name}
                          {m.model_name === product.chosen_model && (
                            <span className="text-[10px] px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-400">
                              Selected
                            </span>
                          )}
                        </td>
                        <td className="py-2 px-3 text-right">
                          {(m.wape * 100).toFixed(1)}%
                        </td>
                        <td className="py-2 px-3 text-right text-slate-400">
                          {(m.interval_coverage_80 * 100).toFixed(0)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
