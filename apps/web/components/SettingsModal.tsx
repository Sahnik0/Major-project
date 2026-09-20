"use client";

import React, { useState, useEffect } from "react";
import { X, AlertTriangle, ShieldCheck, CheckCircle2, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const [settings, setSettings] = useState<any>(null);
  const [timesfmVersion, setTimesfmVersion] = useState("3.0");
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [ollamaModel, setOllamaModel] = useState("gemma3:4b");
  const [leadTime, setLeadTime] = useState(7);
  const [reviewPeriod, setReviewPeriod] = useState(7);
  const [serviceLevel, setServiceLevel] = useState(0.90);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState(false);

  useEffect(() => {
    if (isOpen) {
      api.getSettings().then((s) => {
        setSettings(s);
        setTimesfmVersion(s.timesfm_version);
        setOllamaUrl(s.ollama_url);
        setOllamaModel(s.ollama_model);
        setLeadTime(s.default_lead_time_days);
        setReviewPeriod(s.default_review_period_days);
        setServiceLevel(s.default_service_level);
      });
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateSettings({
        timesfm_version: timesfmVersion,
        ollama_url: ollamaUrl,
        ollama_model: ollamaModel,
        default_lead_time_days: leadTime,
        default_review_period_days: reviewPeriod,
        default_service_level: serviceLevel,
      });
      setSavedMsg(true);
      setTimeout(() => setSavedMsg(false), 2500);
    } catch (e) {
      alert("Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-2xl overflow-hidden flex flex-col shadow-2xl">
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
          <h2 className="text-base font-bold text-white">System Settings & Model Configuration</h2>
          <button onClick={onClose} className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-5 overflow-y-auto max-h-[80vh] text-xs">
          {/* Foundation Model Choice & Persistent Warning */}
          <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800 space-y-3">
            <h3 className="text-xs font-bold text-slate-200 uppercase tracking-wider">
              Forecasting Foundation Model
            </h3>
            <div className="space-y-2">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="radio"
                  name="timesfm_ver"
                  checked={timesfmVersion === "3.0"}
                  onChange={() => setTimesfmVersion("3.0")}
                  className="text-blue-600 bg-slate-900 border-slate-700"
                />
                <div>
                  <span className="font-semibold text-white">TimesFM 3.0 (Latest 330M PyTorch)</span>
                  <p className="text-slate-400 text-[11px]">Supports multivariate covariates and native 9-quantile pass.</p>
                </div>
              </label>

              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="radio"
                  name="timesfm_ver"
                  checked={timesfmVersion === "2.5"}
                  onChange={() => setTimesfmVersion("2.5")}
                  className="text-blue-600 bg-slate-900 border-slate-700"
                />
                <div>
                  <span className="font-semibold text-white">TimesFM 2.5 (Commercial-Safe Fallback)</span>
                  <p className="text-slate-400 text-[11px]">Apache-2.0 licensed for commercial and production use.</p>
                </div>
              </label>
            </div>

            {/* Persistent Licensing Warning */}
            {timesfmVersion === "3.0" ? (
              <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-[11px] flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <span>
                  <strong>Licensing Notice:</strong> TimesFM 3.0 pretrained weights are licensed under the
                  `timesfm-non-commercial-license-v1.0` for non-commercial, research, and evaluation use. For commercial deployment, switch to 2.5 (Apache-2.0).
                </span>
              </div>
            ) : (
              <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-[11px] flex items-start gap-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                <span>TimesFM 2.5 is licensed under Apache-2.0 and approved for commercial production.</span>
              </div>
            )}
          </div>

          {/* Local Gemma LLM (Ollama) */}
          <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800 space-y-3">
            <h3 className="text-xs font-bold text-slate-200 uppercase tracking-wider">
              Local LLM (Gemma via Ollama)
            </h3>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Ollama HTTP URL</label>
                <input
                  type="text"
                  value={ollamaUrl}
                  onChange={(e) => setOllamaUrl(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded px-2.5 py-1.5 text-xs text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Model Tag</label>
                <input
                  type="text"
                  value={ollamaModel}
                  onChange={(e) => setOllamaModel(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded px-2.5 py-1.5 text-xs text-white"
                />
              </div>
            </div>
          </div>

          {/* Business Defaults */}
          <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800 space-y-3">
            <h3 className="text-xs font-bold text-slate-200 uppercase tracking-wider">
              Default Sizing Parameters
            </h3>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Lead Time (Days)</label>
                <input
                  type="number"
                  value={leadTime}
                  onChange={(e) => setLeadTime(parseInt(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-800 rounded px-2.5 py-1.5 text-xs text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Review Period (Days)</label>
                <input
                  type="number"
                  value={reviewPeriod}
                  onChange={(e) => setReviewPeriod(parseInt(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-800 rounded px-2.5 py-1.5 text-xs text-white"
                />
              </div>
              <div>
                <label className="block text-[11px] text-slate-400 mb-1">Service Level</label>
                <input
                  type="number"
                  step="0.01"
                  value={serviceLevel}
                  onChange={(e) => setServiceLevel(parseFloat(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-800 rounded px-2.5 py-1.5 text-xs text-white"
                />
              </div>
            </div>
          </div>

          {/* Privacy Guarantee */}
          <div className="flex items-center gap-2 text-slate-400 text-[11px]">
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
            <span>All settings and models run locally on this machine. No cloud telemetry.</span>
          </div>
        </div>

        <div className="px-6 py-4 border-t border-slate-800 flex items-center justify-between bg-slate-900/90">
          <div>
            {savedMsg && (
              <span className="text-xs text-emerald-400 font-semibold flex items-center gap-1">
                <CheckCircle2 className="w-3.5 h-3.5" /> Settings saved!
              </span>
            )}
          </div>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold shadow-md shadow-blue-600/20 transition disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save Settings"}
          </button>
        </div>
      </div>
    </div>
  );
}

