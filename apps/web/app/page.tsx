"use client";

import React, { useState, useEffect } from "react";
import {
  api,
  DatasetItem,
  SystemHealth,
  ProductDetail,
} from "@/lib/api";
import { Sidebar } from "@/components/Sidebar";
import { ChatInterface } from "@/components/ChatInterface";
import { UploadModal } from "@/components/UploadModal";
import { ProductDetailModal } from "@/components/ProductDetailModal";
import { SettingsModal } from "@/components/SettingsModal";

export default function Home() {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [datasets, setDatasets] = useState<DatasetItem[]>([]);
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null>(null);

  // Modals & details
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState<ProductDetail | null>(null);
  const [loadingDemo, setLoadingDemo] = useState(false);

  // Active Job Polling
  const [activeJob, setActiveJob] = useState<{ id: string; status: string; progress: number; step: string } | null>(null);

  // Initial load
  useEffect(() => {
    api.getHealth().then(setHealth).catch(console.error);
    loadDatasets();
  }, []);

  const loadDatasets = async () => {
    try {
      const resp = await api.getDatasets();
      setDatasets(resp.datasets);
      if (resp.datasets.length > 0 && !selectedDatasetId) {
        setSelectedDatasetId(resp.datasets[0].id);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const selectDataset = (datasetId: string) => {
    setSelectedDatasetId(datasetId);
  };

  const handleDeleteDataset = async (datasetId: string) => {
    try {
      await api.deleteDataset(datasetId);
      const remaining = datasets.filter((d) => d.id !== datasetId);
      setDatasets(remaining);
      if (selectedDatasetId === datasetId) {
        setSelectedDatasetId(remaining.length > 0 ? remaining[0].id : null);
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
          loadDatasets();
        }
      } catch (err) {
        console.error(err);
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [activeJob]);

  // Load Demo Data
  const handleLoadDemo = async () => {
    setLoadingDemo(true);
    try {
      const demo = await api.loadSampleDataset();
      await loadDatasets();
      setSelectedDatasetId(demo.id);
      // Run forecast job on demo dataset if needed
      const fJob = await api.startForecastJob(demo.id, {
        horizon: 28,
        num_folds: 2,
        service_level: 0.90,
        lead_time_days: 7,
        review_period_days: 7,
      });
      setActiveJob({
        id: fJob.job_id,
        status: "processing",
        progress: 10,
        step: "Forecasting demo catalog...",
      });
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingDemo(false);
    }
  };

  // Inspect a specific product
  const handleInspectProduct = async (productName: string) => {
    if (!selectedDatasetId) return;
    try {
      const detail = await api.getProductDetail(selectedDatasetId, productName);
      setSelectedProduct(detail);
    } catch (err) {
      console.error(err);
    }
  };

  const currentDataset = datasets.find((d) => d.id === selectedDatasetId) || null;

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-950 text-slate-100 font-sans">
      {/* Minimal Left Sidebar */}
      <Sidebar
        datasets={datasets}
        selectedDatasetId={selectedDatasetId}
        onSelectDataset={selectDataset}
        onNewUpload={() => setIsUploadOpen(true)}
        onDeleteDataset={handleDeleteDataset}
        onOpenSettings={() => setIsSettingsOpen(true)}
        health={health}
      />

      {/* Central ChatGPT-style Workspace */}
      <main className="flex-1 flex flex-col h-screen overflow-hidden">
        {/* Job Notification Banner if running */}
        {activeJob && activeJob.status === "processing" && (
          <div className="bg-blue-900/30 border-b border-blue-500/20 px-6 py-2.5 flex items-center justify-between text-xs text-blue-200">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse"></span>
              <span>{activeJob.step || "Processing..."}</span>
            </div>
            <span className="font-mono font-semibold">{activeJob.progress}%</span>
          </div>
        )}

        <ChatInterface
          dataset={currentDataset}
          onOpenUpload={() => setIsUploadOpen(true)}
          onLoadDemo={handleLoadDemo}
          isLoadingDemo={loadingDemo}
          onInspectProduct={handleInspectProduct}
          onViewAllProducts={() => {
            // Pick first product to inspect
            if (currentDataset) {
              api.getProducts(currentDataset.id, { page_size: 1 }).then((res) => {
                if (res.products && res.products.length > 0) {
                  handleInspectProduct(res.products[0].product);
                }
              });
            }
          }}
        />
      </main>

      {/* Modals */}
      <UploadModal
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
        onSuccess={(newDatasetId, jobId) => {
          setActiveJob({
            id: jobId,
            status: "processing",
            progress: 0,
            step: "Cleaning dataset...",
          });
          loadDatasets().then(() => setSelectedDatasetId(newDatasetId));
        }}
      />

      <ProductDetailModal
        product={selectedProduct}
        onClose={() => setSelectedProduct(null)}
      />

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
      />
    </div>
  );
}
