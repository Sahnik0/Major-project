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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col shadow-2xl">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-white">
              {uploadData ? "Confirm Column Mapping" : "Upload Sales History"}
            </h2>
            <p className="text-xs text-slate-400">
              {uploadData
                ? "Verify how each column in your spreadsheet maps to sales fields."
                : "Select or drop your CSV or Excel file (max 100 MB)."}
            </p>
          </div>
          <button onClick={onClose} className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 overflow-y-auto flex-1">
          {error && (
            <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-xs flex items-center gap-2">
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
              className="border-2 border-dashed border-slate-700 hover:border-blue-500 hover:bg-blue-500/5 rounded-2xl p-12 text-center cursor-pointer transition flex flex-col items-center justify-center gap-4 group"
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.xlsx,.xls"
                className="hidden"
                onChange={(e) => e.target.files?.[0] && handleFileSelected(e.target.files[0])}
              />
              <div className="w-16 h-16 rounded-2xl bg-slate-800 group-hover:bg-blue-600/20 flex items-center justify-center transition">
                <Upload className="w-8 h-8 text-slate-400 group-hover:text-blue-400" />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-white">
                  {uploading ? "Analyzing File & Sniffing Delimiters..." : "Click or drag sales file here"}
                </h3>
                <p className="text-xs text-slate-400 mt-1">Supports standard .csv and .xlsx spreadsheets</p>
              </div>
            </div>
          ) : (
            /* Step 2: Mapping & Preview */
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              {/* Left: Sample Preview Table */}
              <div className="lg:col-span-7 flex flex-col gap-3">
                <div className="flex items-center gap-2 text-xs font-semibold text-slate-300">
                  <Table className="w-4 h-4 text-blue-400" />
                  <span>Preview ({uploadData.sample_rows.length} sample rows)</span>
                </div>
                <div className="border border-slate-800 rounded-xl overflow-x-auto bg-slate-950/50 max-h-72">
                  <table className="w-full text-left text-xs border-collapse">
                    <thead>
                      <tr className="border-b border-slate-800 bg-slate-900/90 text-slate-400">
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
              <div className="lg:col-span-5 flex flex-col gap-4 bg-slate-950/40 p-4 rounded-xl border border-slate-800">
                <span className="text-xs font-semibold text-slate-300">Logical Field Mapping</span>

                {/* Required: Date */}
                <div>
                  <label className="block text-[11px] font-medium text-slate-300 mb-1">
                    Date Column <span className="text-rose-400">*</span>
                  </label>
                  <select
                    value={mapping.date}
                    onChange={(e) => setMapping({ ...mapping, date: e.target.value })}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500"
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
                  <label className="block text-[11px] font-medium text-slate-300 mb-1">
                    Product / SKU Column <span className="text-rose-400">*</span>
                  </label>
                  <select
                    value={mapping.product}
                    onChange={(e) => setMapping({ ...mapping, product: e.target.value })}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500"
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
                  <label className="block text-[11px] font-medium text-slate-300 mb-1">
                    Sales Quantity Column <span className="text-rose-400">*</span>
                  </label>
                  <select
                    value={mapping.quantity}
                    onChange={(e) => setMapping({ ...mapping, quantity: e.target.value })}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-blue-500"
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
                <div className="grid grid-cols-2 gap-2 pt-2 border-t border-slate-800">
                  <div>
                    <label className="block text-[10px] text-slate-400 mb-0.5">Revenue (Optional)</label>
                    <select
                      value={mapping.revenue}
                      onChange={(e) => setMapping({ ...mapping, revenue: e.target.value })}
                      className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs text-slate-300"
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
                    <label className="block text-[10px] text-slate-400 mb-0.5">Promo Flag (Optional)</label>
                    <select
                      value={mapping.promo_flag}
                      onChange={(e) => setMapping({ ...mapping, promo_flag: e.target.value })}
                      className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs text-slate-300"
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
                <div className="pt-2 border-t border-slate-800 flex flex-col gap-2 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Date Format</span>
                    <select
                      value={dateFormat}
                      onChange={(e) => setDateFormat(e.target.value)}
                      className="bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs text-slate-300"
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
                      className="rounded border-slate-700 bg-slate-900 text-blue-600 focus:ring-0"
                    />
                    <span className="text-slate-400 text-[11px]">Drop duplicate rows (default: aggregate)</span>
                  </label>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        {uploadData && (
          <div className="px-6 py-4 border-t border-slate-800 flex items-center justify-end gap-3 bg-slate-900/90">
            <button
              onClick={() => {
                setUploadData(null);
                setFile(null);
              }}
              className="px-4 py-2 text-xs font-medium rounded-lg text-slate-400 hover:text-white"
            >
              Back
            </button>
            <button
              onClick={handleConfirmMapping}
              disabled={confirming}
              className="flex items-center gap-2 px-5 py-2 text-xs font-semibold rounded-lg bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-600/20 transition disabled:opacity-50"
            >
              {confirming ? "Regularizing Data..." : "Looks right, continue"}
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
