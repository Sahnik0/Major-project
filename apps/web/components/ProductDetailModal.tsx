"use client";

import React, { useState } from "react";
import {
  X,
  Sliders,
  ShieldCheck,
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
      <div className="bg-[#212121] border border-[#333333] rounded-2xl w-full max-w-4xl max-h-[92vh] overflow-hidden flex flex-col shadow-2xl text-[#ececec]">
        {/* Header */}
        <div className="px-6 py-4 border-b border-[#2e2e2e] flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-semibold text-[#ececec]">{product.product}</h2>
                <span className="text-[11px] uppercase font-mono px-2 py-0.5 rounded bg-[#2a2a2a] text-[#b4b4b4] border border-[#383838]">
                  {product.series_class}
                </span>
                <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-[#2a2a2a] text-emerald-400 border border-[#383838]">
                  Winner: {product.chosen_model}
                </span>
              </div>
              <p className="text-xs text-[#8e8e8e] mt-0.5">
                Demand dynamics, prediction intervals, and deterministic inventory sizing
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 text-[#8e8e8e] hover:text-[#ececec] rounded-lg hover:bg-[#2f2f2f]">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-6 overflow-y-auto space-y-6">
          {/* Main Chart */}
          <div className="bg-[#262626] border border-[#333333] rounded-xl p-4">
            <div className="flex items-center justify-between mb-3 text-xs">
              <span className="font-medium text-[#ececec]">Historical Sales & Forecast Range</span>
              <div className="flex items-center gap-4 text-[#8e8e8e] text-[11px]">
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-0.5 bg-[#8e8e8e]" /> History
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-0.5 bg-white" /> Forecast
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-2 bg-emerald-500/20" /> 10-90% Band
                </span>
              </div>
            </div>

            <div className="h-60 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2e2e2e" />
                  <XAxis dataKey="date" stroke="#676767" fontSize={10} />
                  <YAxis stroke="#676767" fontSize={10} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#1a1a1a",
                      borderColor: "#383838",
                      borderRadius: "8px",
                      fontSize: "11px",
                      color: "#ececec",
                    }}
                  />
                  <Line type="monotone" dataKey="history" stroke="#8e8e8e" strokeWidth={1.5} dot={false} />
                  <Area type="monotone" dataKey="upper" stroke="none" fill="#10a37e" fillOpacity={0.15} />
                  <Line type="monotone" dataKey="forecast" stroke="#ffffff" strokeWidth={2} dot={false} />
                  <Area type="monotone" dataKey="lower" stroke="none" fill="#212121" fillOpacity={1} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Dual Column: Interactive Stock Simulator & Accuracy Proof */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Interactive Inventory Sizing */}
            <div className="bg-[#262626] border border-[#333333] rounded-xl p-4 space-y-4">
              <div className="flex items-center gap-2">
                <Sliders className="w-4 h-4 text-[#ececec]" />
                <h4 className="text-xs font-semibold text-[#ececec] uppercase tracking-wider">
                  Interactive What-If Sizing
                </h4>
              </div>

              {/* Sliders */}
              <div className="space-y-3">
                <div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-[#8e8e8e]">Supplier Lead Time:</span>
                    <span className="font-semibold text-white">{leadTime} days</span>
                  </div>
                  <input
                    type="range"
                    min={1}
                    max={30}
                    value={leadTime}
                    onChange={(e) => setLeadTime(parseInt(e.target.value))}
                    className="w-full h-1.5 bg-[#1a1a1a] rounded-lg appearance-none cursor-pointer accent-white"
                  />
                </div>

                <div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-[#8e8e8e]">Target Service Level:</span>
                    <span className="font-semibold text-white">{(serviceLevel * 100).toFixed(0)}%</span>
                  </div>
                  <input
                    type="range"
                    min={0.80}
                    max={0.99}
                    step={0.01}
                    value={serviceLevel}
                    onChange={(e) => setServiceLevel(parseFloat(e.target.value))}
                    className="w-full h-1.5 bg-[#1a1a1a] rounded-lg appearance-none cursor-pointer accent-white"
                  />
                </div>

                <div>
                  <label className="block text-xs text-[#8e8e8e] mb-1">Current Stock on Hand (Optional):</label>
                  <input
                    type="number"
                    value={stockInput}
                    onChange={(e) => setStockInput(e.target.value)}
                    placeholder="Enter units on warehouse shelves..."
                    className="w-full bg-[#1a1a1a] border border-[#383838] rounded-lg px-3 py-1.5 text-xs text-[#ececec] focus:outline-none focus:border-[#555555]"
                  />
                </div>
              </div>

              {/* Calculated Outputs */}
              <div className="grid grid-cols-2 gap-2 pt-2 border-t border-[#333333]">
                <div className="p-2.5 rounded-lg bg-[#1e1e1e] border border-[#2e2e2e]">
                  <span className="text-[10px] text-[#8e8e8e]">Reorder Point (ROP)</span>
                  <p className="text-base font-semibold text-white">{calcROP} <span className="text-xs font-normal text-[#8e8e8e]">units</span></p>
                </div>
                <div className="p-2.5 rounded-lg bg-[#1e1e1e] border border-[#2e2e2e]">
                  <span className="text-[10px] text-[#8e8e8e]">Buffer Safety Stock</span>
                  <p className="text-base font-semibold text-emerald-400">+{calcSS} <span className="text-xs font-normal text-[#8e8e8e]">units</span></p>
                </div>
              </div>

              <div className="p-3 rounded-lg bg-[#1e1e1e] border border-[#2e2e2e] text-xs text-[#ececec]">
                {currentStockVal !== null ? (
                  recommendedQty && recommendedQty > 0 ? (
                    <span>
                      <strong>Action Needed:</strong> Reorder <strong>{recommendedQty} units</strong> immediately.
                    </span>
                  ) : (
                    <span className="text-emerald-400">
                      <strong>Stock Healthy:</strong> Inventory is above reorder point. No order needed.
                    </span>
                  )
                ) : (
                  <span className="text-[#b4b4b4]">
                    <strong>Stocking Rule:</strong> Place order for <strong>{Math.round(avgDaily * 7)} units</strong> whenever stock dips below <strong>{calcROP} units</strong>.
                  </span>
                )}
              </div>
            </div>

            {/* Accuracy & Backtest Proof */}
            <div className="bg-[#262626] border border-[#333333] rounded-xl p-4 space-y-3">
              <div className="flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-emerald-400" />
                <h4 className="text-xs font-semibold text-[#ececec] uppercase tracking-wider">
                  Honest Backtest Competition
                </h4>
              </div>

              <p className="text-xs text-[#8e8e8e] leading-relaxed">
                Candidate models were evaluated across rolling origins of this product's actual sales history. The winning model produced the lowest historical error.
              </p>

              <div className="border border-[#333333] rounded-lg overflow-hidden">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="bg-[#1e1e1e] border-b border-[#333333] text-[#8e8e8e] text-[11px]">
                      <th className="py-2 px-3">Model</th>
                      <th className="py-2 px-3 text-right">Error (WAPE)</th>
                      <th className="py-2 px-3 text-right">Coverage</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#2e2e2e]">
                    {(product.backtest_table || []).map((m: any) => (
                      <tr
                        key={m.model_name}
                        className={m.model_name === product.chosen_model ? "bg-[#1f2923] font-medium text-white" : "text-[#b4b4b4]"}
                      >
                        <td className="py-2 px-3 flex items-center gap-1.5">
                          {m.model_name}
                          {m.model_name === product.chosen_model && (
                            <span className="text-[10px] px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-400">
                              Winner
                            </span>
                          )}
                        </td>
                        <td className="py-2 px-3 text-right font-mono">
                          {(m.wape * 100).toFixed(1)}%
                        </td>
                        <td className="py-2 px-3 text-right text-[#8e8e8e] font-mono">
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
