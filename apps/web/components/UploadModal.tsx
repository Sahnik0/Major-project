"use client";

import React, { useState, useRef } from "react";
import { X, Upload, CheckCircle2, ArrowRight, Table, AlertCircle } from "lucide-react";
import { api, UploadResponse } from "@/lib/api";

interface UploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (datasetId: string, jobId: string) => void;
}

export function UploadModal({ isOpen, onClose, onSuccess }: UploadModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadData, setUploadData] = useState<UploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Mapping state
  const [mapping, setMapping] = useState<{ [key: string]: string }>({
    date: "",
    product: "",
    quantity: "",
    revenue: "",
    store: "",
    promo_flag: "",
  });
  const [dateFormat, setDateFormat] = useState<string>("auto");
  const [dropDuplicates, setDropDuplicates] = useState(false);
  const [granularity, setGranularity] = useState("daily");
  const [confirming, setConfirming] = useState(false);

  if (!isOpen) return null;

  const handleFileDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileSelected(e.dataTransfer.files[0]);
    }
  };

  const handleFileSelected = async (selectedFile: File) => {
    setFile(selectedFile);
    setError(null);
    setUploading(true);

    try {
      const resp = await api.uploadDataset(selectedFile);
      setUploadData(resp);
      setMapping({
        date: resp.suggested_mapping.date || "",
        product: resp.suggested_mapping.product || "",
        quantity: resp.suggested_mapping.quantity || "",
        revenue: resp.suggested_mapping.revenue || "",
        store: resp.suggested_mapping.store || "",
        promo_flag: resp.suggested_mapping.promo_flag || "",
      });
    } catch (err: any) {
      setError(err.message || "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleConfirmMapping = async () => {
    if (!uploadData) return;
    if (!mapping.date || !mapping.product || !mapping.quantity) {
      setError("Please map all required fields: Date, Product, and Quantity.");
      return;
    }

    setConfirming(true);
    setError(null);
    try {
      const activeMapping: Record<string, string> = {
        date: mapping.date,
        product: mapping.product,
        quantity: mapping.quantity,
      };
      if (mapping.revenue) activeMapping.revenue = mapping.revenue;
      if (mapping.store) activeMapping.store = mapping.store;
      if (mapping.promo_flag) activeMapping.promo_flag = mapping.promo_flag;

      const res = await api.confirmMapping(uploadData.id, {
        mapping: activeMapping,
        date_format: dateFormat === "auto" ? null : dateFormat,
        drop_duplicates: dropDuplicates,
        returns_handling: "net",
        aggregation_granularity: granularity,
      });

      onSuccess(uploadData.id, res.job_id);
      onClose();
    } catch (err: any) {
      setError(err.message || "Failed to confirm column mapping");
    } finally {
      setConfirming(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
      <div className="bg-[#212121] border border-[#333333] rounded-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col shadow-2xl text-[#ececec]">
        {/* Header */}
        <div className="px-6 py-4 border-b border-[#2e2e2e] flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-[#ececec]">
              {uploadData ? "Confirm Column Mapping" : "Upload Sales History"}
            </h2>
            <p className="text-xs text-[#b4b4b4]">
              {uploadData
                ? "Verify how each column in your spreadsheet maps to sales fields."
                : "Select or drop your CSV or Excel file (max 100 MB)."}
            </p>
          </div>
          <button onClick={onClose} className="p-1.5 text-[#8e8e8e] hover:text-[#ececec] rounded-lg hover:bg-[#2f2f2f]">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 overflow-y-auto flex-1">
          {error && (
            <div className="mb-4 p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {!uploadData ? (
            /* Step 1: Upload Dropzone */
            <div
              onDragOver={(e) => e.preventDefault()}
              onDrop={handleFileDrop}
              onClick={() => fileInputRef.current?.click()}
              className="border border-dashed border-[#383838] hover:border-[#555555] hover:bg-[#262626] rounded-2xl p-12 text-center cursor-pointer transition flex flex-col items-center justify-center gap-4 group"
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.xlsx,.xls"
                className="hidden"
                onChange={(e) => e.target.files?.[0] && handleFileSelected(e.target.files[0])}
              />
              <div className="w-12 h-12 rounded-xl bg-[#2a2a2a] flex items-center justify-center transition">
                <Upload className="w-6 h-6 text-[#b4b4b4]" />
              </div>
              <div>
                <h3 className="text-sm font-medium text-[#ececec]">
                  {uploading ? "Analyzing file & sniffing headers..." : "Click or drag sales file here"}
                </h3>
                <p className="text-xs text-[#8e8e8e] mt-1">Supports standard .csv and .xlsx spreadsheets</p>
              </div>
            </div>
          ) : (
            /* Step 2: Mapping & Preview */
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              {/* Left: Sample Preview Table */}
              <div className="lg:col-span-7 flex flex-col gap-3">
                <div className="flex items-center gap-2 text-xs font-semibold text-[#ececec]">
                  <Table className="w-4 h-4 text-[#b4b4b4]" />
                  <span>Preview ({uploadData.sample_rows.length} sample rows)</span>
                </div>
                <div className="border border-[#333333] rounded-xl overflow-x-auto bg-[#1a1a1a] max-h-72">
                  <table className="w-full text-left text-xs border-collapse">
                    <thead>
                      <tr className="border-b border-[#333333] bg-[#242424] text-[#8e8e8e]">
                        {uploadData.detected_columns.map((col) => (
                          <th key={col} className="px-3 py-2 font-medium">
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/60 text-slate-300 font-mono">
                      {uploadData.sample_rows.map((row, rIdx) => (
                        <tr key={rIdx} className="hover:bg-slate-800/30">
                          {uploadData.detected_columns.map((col) => (
                            <td key={col} className="px-3 py-1.5 whitespace-nowrap">
                              {String(row[col] ?? "")}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="text-[11px] text-slate-500">
                  File: {uploadData.original_filename} ({uploadData.detected_columns.length} columns detected)
                </p>
              </div>

              {/* Right: Mapping Controls */}
              <div className="lg:col-span-5 flex flex-col gap-4 bg-[#1e1e1e] p-4 rounded-xl border border-[#333333]">
                <span className="text-xs font-semibold text-[#ececec]">Logical Field Mapping</span>

                {/* Required: Date */}
                <div>
                  <label className="block text-[11px] font-medium text-[#b4b4b4] mb-1">
                    Date Column <span className="text-rose-400">*</span>
                  </label>
                  <select
                    value={mapping.date}
                    onChange={(e) => setMapping({ ...mapping, date: e.target.value })}
                    className="w-full bg-[#262626] border border-[#383838] rounded-lg px-2.5 py-1.5 text-xs text-[#ececec] focus:outline-none focus:border-[#555555]"
                  >
                    <option value="">-- Select column --</option>
                    {uploadData.detected_columns.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Required: Product */}
                <div>
                  <label className="block text-[11px] font-medium text-[#b4b4b4] mb-1">
                    Product / SKU Column <span className="text-rose-400">*</span>
                  </label>
                  <select
                    value={mapping.product}
                    onChange={(e) => setMapping({ ...mapping, product: e.target.value })}
                    className="w-full bg-[#262626] border border-[#383838] rounded-lg px-2.5 py-1.5 text-xs text-[#ececec] focus:outline-none focus:border-[#555555]"
                  >
                    <option value="">-- Select column --</option>
                    {uploadData.detected_columns.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Required: Quantity */}
                <div>
                  <label className="block text-[11px] font-medium text-[#b4b4b4] mb-1">
                    Sales Quantity Column <span className="text-rose-400">*</span>
                  </label>
                  <select
                    value={mapping.quantity}
                    onChange={(e) => setMapping({ ...mapping, quantity: e.target.value })}
                    className="w-full bg-[#262626] border border-[#383838] rounded-lg px-2.5 py-1.5 text-xs text-[#ececec] focus:outline-none focus:border-[#555555]"
                  >
                    <option value="">-- Select column --</option>
                    {uploadData.detected_columns.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Optional fields */}
                <div className="grid grid-cols-2 gap-2 pt-2 border-t border-[#333333]">
                  <div>
                    <label className="block text-[10px] text-[#8e8e8e] mb-0.5">Revenue (Optional)</label>
                    <select
                      value={mapping.revenue}
                      onChange={(e) => setMapping({ ...mapping, revenue: e.target.value })}
                      className="w-full bg-[#262626] border border-[#383838] rounded px-2 py-1 text-xs text-[#ececec]"
                    >
                      <option value="">None</option>
                      {uploadData.detected_columns.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-[10px] text-[#8e8e8e] mb-0.5">Promo Flag (Optional)</label>
                    <select
                      value={mapping.promo_flag}
                      onChange={(e) => setMapping({ ...mapping, promo_flag: e.target.value })}
                      className="w-full bg-[#262626] border border-[#383838] rounded px-2 py-1 text-xs text-[#ececec]"
                    >
                      <option value="">None</option>
                      {uploadData.detected_columns.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                {/* Options */}
                <div className="pt-2 border-t border-[#333333] flex flex-col gap-2 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="text-[#8e8e8e]">Date Format</span>
                    <select
                      value={dateFormat}
                      onChange={(e) => setDateFormat(e.target.value)}
                      className="bg-[#262626] border border-[#383838] rounded px-2 py-1 text-xs text-[#ececec]"
                    >
                      <option value="auto">Auto Detect</option>
                      <option value="dayfirst">Day First (DD/MM/YYYY)</option>
                      <option value="monthfirst">Month First (MM/DD/YYYY)</option>
                    </select>
                  </div>

                  <label className="flex items-center gap-2 cursor-pointer mt-1">
                    <input
                      type="checkbox"
                      checked={dropDuplicates}
                      onChange={(e) => setDropDuplicates(e.target.checked)}
                      className="rounded border-[#383838] bg-[#262626] text-white focus:ring-0"
                    />
                    <span className="text-[#8e8e8e] text-[11px]">Drop duplicate rows (default: aggregate)</span>
                  </label>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        {uploadData && (
          <div className="px-6 py-4 border-t border-[#2e2e2e] flex items-center justify-end gap-3 bg-[#1e1e1e]">
            <button
              onClick={() => {
                setUploadData(null);
                setFile(null);
              }}
              className="px-4 py-2 text-xs font-medium rounded-lg text-[#8e8e8e] hover:text-[#ececec]"
            >
              Back
            </button>
            <button
              onClick={handleConfirmMapping}
              disabled={confirming}
              className="flex items-center gap-2 px-5 py-2 text-xs font-semibold rounded-lg bg-white text-black hover:bg-neutral-200 transition disabled:opacity-50"
            >
              {confirming ? "Regularizing Data..." : "Continue"}
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

