"use client";

import React from "react";
import {
  AlertTriangle,
  Info,
  CheckCircle2,
  Calendar,
  Layers,
  Package,
  ArrowRight,
  TrendingUp,
} from "lucide-react";
import { DataHealthReport } from "@/lib/api";

interface DataHealthViewProps {
  report: DataHealthReport;
  onStartForecast: () => void;
  isForecasting: boolean;
}

export function DataHealthView({ report, onStartForecast, isForecasting }: DataHealthViewProps) {
  const classColors: Record<string, string> = {
    smooth: "bg-emerald-500",
    erratic: "bg-amber-500",
    intermittent: "bg-blue-500",
    lumpy: "bg-purple-500",
    short_history: "bg-indigo-400",
    inactive: "bg-rose-500",
  };

  const classLabels: Record<string, { label: string; desc: string }> = {
    smooth: { label: "Smooth", desc: "Regular daily sales with stable order sizes (easiest to forecast)." },
    erratic: { label: "Erratic", desc: "Frequent sales with large variation in order volumes." },
    intermittent: { label: "Intermittent", desc: "Occasional sales spaced apart, but predictable quantities." },
    lumpy: { label: "Lumpy", desc: "Infrequent sales with highly unpredictable batch sizes." },
    short_history: { label: "Short History", desc: "Newly launched items with limited history." },
    inactive: { label: "Inactive", desc: "No recorded sales in recent periods (possibly discontinued)." },
  };

  return (
    <div className="space-y-6">
      {/* Top Metric Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex items-center gap-3.5">
          <div className="p-3 rounded-lg bg-blue-500/10 text-blue-400">
            <Package className="w-5 h-5" />
          </div>
          <div>
            <p className="text-xs text-slate-400">Total Products</p>
            <h4 className="text-xl font-bold text-white mt-0.5">{report.total_products}</h4>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex items-center gap-3.5">
          <div className="p-3 rounded-lg bg-emerald-500/10 text-emerald-400">
            <Layers className="w-5 h-5" />
          </div>
          <div>
            <p className="text-xs text-slate-400">Regularized Records</p>
            <h4 className="text-xl font-bold text-white mt-0.5">{report.total_rows_cleaned.toLocaleString()}</h4>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex items-center gap-3.5">
          <div className="p-3 rounded-lg bg-indigo-500/10 text-indigo-400">
            <Calendar className="w-5 h-5" />
          </div>
          <div>
            <p className="text-xs text-slate-400">Calendar Span</p>
            <h4 className="text-xl font-bold text-white mt-0.5">{report.total_days} Days</h4>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex items-center justify-between">
          <div>
            <p className="text-xs text-slate-400">Ready to Forecast</p>
            <h4 className="text-sm font-semibold text-emerald-400 mt-0.5 flex items-center gap-1.5">
              <CheckCircle2 className="w-4 h-4" /> Healthy
            </h4>
          </div>
          <button
            onClick={onStartForecast}
            disabled={isForecasting}
            className="flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold rounded-lg bg-blue-600 hover:bg-blue-500 text-white shadow-md shadow-blue-600/20 transition disabled:opacity-50"
          >
            {isForecasting ? "Processing..." : "Run Forecast"}
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Demand Classification Breakdown */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-sm font-semibold text-white">Demand Pattern Classification (Syntetos-Boylan)</h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Each product is classified to select the optimal mathematical forecasting model.
            </p>
          </div>
        </div>

        {/* Stacked bar visualization */}
        <div className="w-full h-3 rounded-full bg-slate-800 overflow-hidden flex mb-5">
          {Object.entries(report.class_distribution).map(([cls, count]) => {
            if (count === 0) return null;
            const pct = (count / report.total_products) * 100;
            return (
              <div
                key={cls}
                style={{ width: `${pct}%` }}
                className={`${classColors[cls] || "bg-slate-600"} h-full`}
                title={`${classLabels[cls]?.label || cls}: ${count} products (${pct.toFixed(1)}%)`}
              />
            );
          })}
        </div>

        {/* Legend grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {Object.entries(report.class_distribution).map(([cls, count]) => (
            <div key={cls} className="p-3 rounded-xl bg-slate-950/60 border border-slate-800/80 flex items-start gap-3">
              <div className={`w-3 h-3 rounded-full ${classColors[cls] || "bg-slate-600"} mt-0.5 shrink-0`} />
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-white">{classLabels[cls]?.label || cls}</span>
                  <span className="text-[11px] font-mono px-1.5 py-0.2 rounded bg-slate-800 text-slate-300">
                    {count} items
                  </span>
                </div>
                <p className="text-[11px] text-slate-400 mt-0.5 leading-relaxed">
                  {classLabels[cls]?.desc || ""}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Quality Issues & Actions List */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6">
        <h3 className="text-sm font-semibold text-white mb-1">Data Health & Automatic Cleanups</h3>
        <p className="text-xs text-slate-400 mb-4">
          Plain-English audit of anomalies detected and how the pipeline handled them.
        </p>

        <div className="divide-y divide-slate-800">
          {report.issues.map((issue, idx) => (
            <div key={idx} className="py-3.5 flex items-start gap-3">
              <div className="mt-0.5">
                {issue.severity === "warning" ? (
                  <AlertTriangle className="w-4 h-4 text-amber-400" />
                ) : (
                  <Info className="w-4 h-4 text-blue-400" />
                )}
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-slate-200">{issue.title}</span>
                  <span className="text-[10px] uppercase font-bold px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">
                    {issue.issue_type}
                  </span>
                </div>
                <p className="text-xs text-slate-400 mt-0.5">{issue.description}</p>
                <div className="mt-1 text-[11px] text-emerald-400/90 flex items-center gap-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  <span>Action taken: {issue.action_taken}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
