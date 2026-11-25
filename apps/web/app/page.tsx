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

  // Active Job Polling
  const [activeJob, setActiveJob] = useState<{ id: string; status: string; progress: number; step: string } | null>(null);
  const [chatRefreshKey, setChatRefreshKey] = useState<number>(0);

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
          const updated = await api.getDatasets();
          setDatasets(updated.datasets);
          setChatRefreshKey((k) => k + 1);

          if (j.job_type === "cleaning_profiling" && j.dataset_id) {
            const targetDs = updated.datasets.find((d) => d.id === j.dataset_id);
            const dsType = targetDs?.column_mapping?._dataset_type;
            if (dsType !== "tabular") {
              try {
                const fJob = await api.startForecastJob(j.dataset_id, {
                  horizon: 28,
                  num_folds: 2,
                  service_level: 0.90,
                  lead_time_days: 7,
                  review_period_days: 7,
                  include_timesfm: true,
                });
                setActiveJob({
                  id: fJob.job_id,
                  status: "processing",
                  progress: 10,
                  step: "Dataset ready! Running background forecasting & reorder math...",
                });
                return;
              } catch (fcErr) {
                console.error(fcErr);
              }
            }
          }
          setActiveJob(null);
        } else if (j.status === "failed") {
          await loadDatasets();
          setChatRefreshKey((k) => k + 1);
          setActiveJob(null);
        }
      } catch (err) {
        console.error(err);
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [activeJob]);

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

  // Direct zero-friction file upload
  const handleDirectFileUpload = async (file: File) => {
    setActiveJob({
      id: "uploading",
      status: "processing",
      progress: 5,
      step: `Analyzing columns of ${file.name} with AI...`,
    });
    try {
      const resp = await api.uploadDataset(file);
      await loadDatasets();
      setSelectedDatasetId(resp.id);

      if (resp.job_id) {
        setActiveJob({
          id: resp.job_id,
          status: "processing",
          progress: 15,
          step: "Columns understood by AI. Regularizing records & running TimesFM...",
        });
      }
    } catch (err: any) {
      alert(err.message || "Failed to upload file");
      setActiveJob(null);
    }
  };

  const currentDataset = datasets.find((d) => d.id === selectedDatasetId) || null;

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#212121] text-[#ececec] font-sans">
      {/* Minimal Left Sidebar */}
      <Sidebar
        datasets={datasets}
        selectedDatasetId={selectedDatasetId}
        onSelectDataset={selectDataset}
        onNewUpload={() => setIsUploadOpen(true)}
        onDirectUpload={handleDirectFileUpload}
        onDeleteDataset={handleDeleteDataset}
        onOpenSettings={() => setIsSettingsOpen(true)}
        health={health}
      />

      {/* Central ChatGPT-style Workspace */}
      <main className="flex-1 flex flex-col h-screen overflow-hidden">
        {/* Job Notification Banner if running */}
        {activeJob && activeJob.status === "processing" && (
          <div className="bg-[#2a2a2a] border-b border-[#333333] px-6 py-2 flex items-center justify-between text-xs text-[#ececec]">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
              <span>{activeJob.step || "Processing..."}</span>
            </div>
            <span className="font-mono font-medium text-[#b4b4b4]">{activeJob.progress}%</span>
          </div>
        )}

        <ChatInterface
          dataset={currentDataset}
          refreshTrigger={chatRefreshKey}
          onOpenUpload={() => setIsUploadOpen(true)}
          onDirectUpload={handleDirectFileUpload}
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
