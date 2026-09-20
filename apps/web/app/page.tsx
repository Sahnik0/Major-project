"use client";

import React, { useState, useEffect } from "react";
import {
  Upload,
  Database,
  Activity,
  BarChart3,
  Package,
  ClipboardList,
  Sparkles,
  Loader2,
  Layers,
  ArrowUpRight,
} from "lucide-react";
import { api, DatasetItem, SystemHealth, ProductSummary, ProductDetail } from "@/lib/api";
import { Navbar } from "@/components/Navbar";
import { UploadModal } from "@/components/UploadModal";
import { DataHealthView } from "@/components/DataHealthView";
import { DashboardView } from "@/components/DashboardView";
import { ProductsTableView } from "@/components/ProductsTableView";
import { ProductDetailModal } from "@/components/ProductDetailModal";
import { InventoryView } from "@/components/InventoryView";
import { SettingsModal } from "@/components/SettingsModal";

export default function Home() {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [datasets, setDatasets] = useState<DatasetItem[]>([]);
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null>(null);

  // Active view tab: 'dashboard' | 'products' | 'health' | 'inventory'
  const [activeTab, setActiveTab] = useState<"dashboard" | "products" | "health" | "inventory">("dashboard");

  // Data for active dataset
  const [healthReport, setHealthReport] = useState<any>(null);
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [inventoryItems, setInventoryItems] = useState<any[]>([]);
  const [summary, setSummary] = useState<{ facts: any; narrative: string } | null>(null);

  // Modals & details
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState<ProductDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Background Job tracking
  const [activeJob, setActiveJob] = useState<{ id: string; status: string; progress: number; step: string } | null>(null);
  const [loadingDemo, setLoadingDemo] = useState(false);
  const [isForecasting, setIsForecasting] = useState(false);

  // Initial load: System health & Datasets
  useEffect(() => {
    api.getHealth().then(setHealth).catch(console.error);
    loadDatasets();
  }, []);

  const loadDatasets = async () => {
    try {
      const resp = await api.getDatasets();
      setDatasets(resp.datasets);
      if (resp.datasets.length > 0 && !selectedDatasetId) {
        selectDataset(resp.datasets[0].id);
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Switch active dataset
  const selectDataset = async (datasetId: string) => {
    setSelectedDatasetId(datasetId);
    try {
      // 1. Fetch Health Report
      const hr = await api.getHealthReport(datasetId).catch(() => null);
      setHealthReport(hr);

      // 2. Fetch Products
      const prods = await api.getProducts(datasetId).catch(() => ({ products: [] }));
      setProducts(prods.products || []);

      // 3. Fetch Summary
      const sum = await api.getBusinessSummary(datasetId).catch(() => null);
      setSummary(sum);

      // 4. Fetch Inventory
      const inv = await api.getInventory(datasetId).catch(() => ({ items: [] }));
      setInventoryItems(inv.items || []);

      // Default to Dashboard if forecasts exist, else Health Report
      if (prods.products && prods.products.length > 0) {
        setActiveTab("dashboard");
      } else {
        setActiveTab("health");
      }
    } catch (err) {
      console.error(err);
    }
  };

  // Job Polling
  useEffect(() => {
    if (!activeJob || activeJob.status === "completed" || activeJob.status === "failed") {
      return;
    }
    const interval = setInterval(async () => {
      try {
        const j = await api.getJob(activeJob.id);
        setActiveJob({
          id: j.id,
          status: j.status,
          progress: j.progress,
          step: j.current_step || "",
        });
        if (j.status === "completed") {
          setIsForecasting(false);
          if (selectedDatasetId) selectDataset(selectedDatasetId);
        } else if (j.status === "failed") {
          setIsForecasting(false);
        }
      } catch (err) {
        console.error(err);
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [activeJob, selectedDatasetId]);

  // Handle successful upload & mapping
  const handleUploadSuccess = (datasetId: string, jobId: string) => {
    setActiveJob({ id: jobId, status: "queued", progress: 0, step: "Queued for processing" });
    loadDatasets().then(() => selectDataset(datasetId));
  };

  // Load Demo Data
  const handleLoadDemo = async () => {
    setLoadingDemo(true);
    try {
      const demoDataset = await api.loadSampleDataset();
      await loadDatasets();
      await selectDataset(demoDataset.id);
    } catch (err) {
      alert("Failed to load demo dataset");
    } finally {
      setLoadingDemo(false);
    }
  };

  // Start Forecast Job
  const handleStartForecast = async () => {
    if (!selectedDatasetId) return;
    setIsForecasting(true);
    try {
      const res = await api.startForecastJob(selectedDatasetId, {
        horizon: 28,
        num_folds: 2,
        service_level: 0.90,
        lead_time_days: 7,
        review_period_days: 7,
      });
      setActiveJob({ id: res.job_id, status: "queued", progress: 0, step: "Starting forecasting models..." });
    } catch (err: any) {
      alert(err.message || "Failed to start forecast job");
      setIsForecasting(false);
    }
  };

  // View Product Detail
  const handleSelectProduct = async (productName: string) => {
    if (!selectedDatasetId) return;
    setLoadingDetail(true);
    try {
      const detail = await api.getProductDetail(selectedDatasetId, productName);
      setSelectedProduct(detail);
    } catch (err) {
      alert("Could not load product details");
    } finally {
      setLoadingDetail(false);
    }
  };

  const currentDataset = datasets.find((d) => d.id === selectedDatasetId);

  return (
    <div className="min-h-screen bg-slate-950 flex flex-col">
      {/* Top Navbar */}
      <Navbar
        health={health}
        onOpenSettings={() => setIsSettingsOpen(true)}
        onOpenUpload={() => setIsUploadOpen(true)}
        onLoadDemo={handleLoadDemo}
        loadingDemo={loadingDemo}
      />

      {/* Main Container */}
      <main className="max-w-7xl w-full mx-auto px-6 py-6 flex-1 flex flex-col space-y-6">
        {/* Dataset Header & View Selector */}
        {datasets.length > 0 ? (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-5">
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800 text-slate-300">
                <Layers className="w-5 h-5 text-blue-400" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <select
                    value={selectedDatasetId || ""}
                    onChange={(e) => selectDataset(e.target.value)}
                    className="bg-transparent font-bold text-white text-base focus:outline-none cursor-pointer"
                  >
                    {datasets.map((d) => (
                      <option key={d.id} value={d.id} className="bg-slate-900 text-white">
                        {d.name}
                      </option>
                    ))}
                  </select>
                  <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    Ready
                  </span>
                </div>
                <p className="text-xs text-slate-400 mt-0.5">
                  {currentDataset?.product_count || 0} products • {currentDataset?.row_count?.toLocaleString() || 0} sales records
                </p>
              </div>
            </div>

            {/* View Tabs */}
            <div className="flex items-center gap-1 bg-slate-900 border border-slate-800 p-1 rounded-xl">
              <button
                onClick={() => setActiveTab("dashboard")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                  activeTab === "dashboard"
                    ? "bg-blue-600 text-white shadow"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                <BarChart3 className="w-3.5 h-3.5" />
                <span>Dashboard</span>
              </button>

              <button
                onClick={() => setActiveTab("products")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                  activeTab === "products"
                    ? "bg-blue-600 text-white shadow"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                <Package className="w-3.5 h-3.5" />
                <span>Products ({products.length})</span>
              </button>

              <button
                onClick={() => setActiveTab("inventory")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                  activeTab === "inventory"
                    ? "bg-blue-600 text-white shadow"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                <ClipboardList className="w-3.5 h-3.5" />
                <span>Inventory Plan</span>
              </button>

              <button
                onClick={() => setActiveTab("health")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                  activeTab === "health"
                    ? "bg-blue-600 text-white shadow"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                <Activity className="w-3.5 h-3.5" />
                <span>Data Health</span>
              </button>
            </div>
          </div>
        ) : (
          /* Empty State / Welcome Screen */
          <div className="py-20 text-center flex flex-col items-center justify-center max-w-xl mx-auto space-y-5">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-tr from-blue-600 to-indigo-600 flex items-center justify-center shadow-xl shadow-blue-500/20">
              <Sparkles className="w-8 h-8 text-white" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white tracking-tight">
                Welcome to Demand Advisor
              </h2>
              <p className="text-xs text-slate-400 mt-2 leading-relaxed">
                Upload your transaction records to get instant demand forecasting, backtested baseline comparisons, and pure deterministic stock advice — 100% locally on your machine.
              </p>
            </div>
            <div className="flex items-center gap-3 pt-2">
              <button
                onClick={handleLoadDemo}
                disabled={loadingDemo}
                className="flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-xl bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/30 transition disabled:opacity-50"
              >
                <Database className="w-4 h-4" />
                {loadingDemo ? "Generating 40 Products..." : "Try with 40-Product Demo"}
              </button>

              <button
                onClick={() => setIsUploadOpen(true)}
                className="flex items-center gap-2 px-5 py-2 text-xs font-semibold rounded-xl bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-600/20 transition"
              >
                <Upload className="w-4 h-4" />
                Upload Your Sales File
              </button>
            </div>
          </div>
        )}

        {/* Persistent Background Job Banner */}
        {activeJob && activeJob.status === "processing" && (
          <div className="p-3.5 rounded-xl bg-slate-900 border border-blue-500/30 flex items-center justify-between shadow-lg text-xs animate-pulse">
            <div className="flex items-center gap-2.5">
              <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />
              <span className="font-semibold text-white">{activeJob.step}</span>
            </div>
            <span className="font-mono text-blue-400 font-bold">{activeJob.progress}%</span>
          </div>
        )}

        {/* Tab Views */}
        {selectedDatasetId && (
          <div className="flex-1">
            {activeTab === "dashboard" && (
              <DashboardView
                products={products}
                summary={summary}
                onSelectProduct={handleSelectProduct}
                onViewAllProducts={() => setActiveTab("products")}
                onViewInventory={() => setActiveTab("inventory")}
              />
            )}

            {activeTab === "products" && (
              <ProductsTableView
                products={products}
                onSelectProduct={handleSelectProduct}
              />
            )}

            {activeTab === "inventory" && (
              <InventoryView
                datasetId={selectedDatasetId}
                inventoryItems={inventoryItems}
              />
            )}

            {activeTab === "health" && healthReport && (
              <DataHealthView
                report={healthReport}
                onStartForecast={handleStartForecast}
                isForecasting={isForecasting}
              />
            )}
          </div>
        )}
      </main>

      {/* Modals */}
      <UploadModal
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
        onSuccess={handleUploadSuccess}
      />

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
      />

      <ProductDetailModal
        product={selectedProduct}
        onClose={() => setSelectedProduct(null)}
      />
    </div>
  );
}
