"use client";

import React, { useState, useEffect, useRef } from "react";
import {
  ArrowUp,
  Paperclip,
  Download,
  CheckCircle2,
  AlertCircle,
  FileSpreadsheet,
  ArrowRight,
  ShieldCheck,
  Package,
} from "lucide-react";
import { api, DatasetItem } from "@/lib/api";

interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  card_type?: string;
  payload?: any;
  created_at: string;
}

interface ChatInterfaceProps {
  dataset: DatasetItem | null;
  onOpenUpload: () => void;
  onLoadDemo: () => void;
  isLoadingDemo: boolean;
  onInspectProduct: (productName: string) => void;
  onViewAllProducts: () => void;
}

export function ChatInterface({
  dataset,
  onOpenUpload,
  onLoadDemo,
  isLoadingDemo,
  onInspectProduct,
  onViewAllProducts,
}: ChatInterfaceProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Load conversation when dataset changes
  useEffect(() => {
    if (!dataset) {
      setMessages([]);
      return;
    }
    loadMessages(dataset.id);
  }, [dataset?.id]);

  const loadMessages = async (datasetId: string) => {
    try {
      const msgs = await api.getChatMessages(datasetId);
      setMessages(msgs);
    } catch (err) {
      console.error(err);
    } finally {
      scrollToBottom();
    }
  };

  const scrollToBottom = () => {
    setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, 100);
  };

  const handleSend = async (textToSend?: string) => {
    const query = textToSend || input.trim();
    if (!query || !dataset || isSending) return;

    setInput("");
    setIsSending(true);

    const tempUserMsg: ChatMessage = {
      id: "temp-" + Date.now(),
      role: "user",
      content: query,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempUserMsg]);
    scrollToBottom();

    try {
      const assistantMsg = await api.sendChatMessage(dataset.id, query);
      setMessages((prev) => [...prev.filter((m) => m.id !== tempUserMsg.id), tempUserMsg, assistantMsg]);
    } catch (err) {
      console.error(err);
      setMessages((prev) => [
        ...prev,
        {
          id: "err-" + Date.now(),
          role: "assistant",
          content: "Sorry, I could not query the local engine. Please verify that the API is running locally.",
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setIsSending(false);
      scrollToBottom();
    }
  };

  // Zero-State: Clean ChatGPT minimal layout, no slop logos
  if (!dataset) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-6 max-w-xl mx-auto w-full text-center space-y-6 bg-[#212121]">
        <div className="space-y-2.5">
          <h1 className="text-2xl font-medium text-[#ececec] tracking-tight">
            What should I stock, and how much?
          </h1>
          <p className="text-xs text-[#b4b4b4] leading-relaxed max-w-md mx-auto">
            Upload your sales history. We clean your data, test TimesFM against simple baselines honestly, and turn forecasts into exact reorder quantities — 100% on your machine.
          </p>
        </div>

        {/* Upload Dropzone */}
        <div
          onClick={onOpenUpload}
          className="w-full border border-dashed border-[#383838] hover:border-[#555555] rounded-2xl p-8 bg-[#262626]/50 hover:bg-[#262626] transition cursor-pointer flex flex-col items-center space-y-2 group"
        >
          <div className="p-3 rounded-full bg-[#2f2f2f] text-[#ececec] group-hover:scale-105 transition">
            <FileSpreadsheet className="w-5 h-5 text-[#ececec]" />
          </div>
          <div className="text-xs">
            <span className="font-medium text-[#ececec]">Click to select or drop your sales CSV / Excel</span>
            <p className="text-[11px] text-[#8e8e8e] mt-0.5">
              Requires columns: Date, Product SKU, Units Sold
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 w-full justify-center pt-1">
          <span className="text-[#676767] text-xs">or</span>
          <button
            onClick={onLoadDemo}
            disabled={isLoadingDemo}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium bg-[#2a2a2a] hover:bg-[#333333] border border-[#383838] text-[#ececec] transition"
          >
            <span>{isLoadingDemo ? "Loading Demo..." : "Try with 40-product demo dataset"}</span>
          </button>
        </div>

        <div className="pt-6 flex items-center gap-6 text-[11px] text-[#8e8e8e] justify-center">
          <span className="flex items-center gap-1.5">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" /> Real data cleaning
          </span>
          <span className="flex items-center gap-1.5">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" /> Honest baseline testing
          </span>
          <span className="flex items-center gap-1.5">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" /> 100% Offline & Private
          </span>
        </div>
      </div>
    );
  }

  // Active Dataset Chat View (ChatGPT Dark Theme: #212121)
  return (
    <div className="flex-1 flex flex-col h-screen overflow-hidden bg-[#212121] text-[#ececec]">
      {/* Top Header bar */}
      <header className="px-6 py-2.5 border-b border-[#2c2c2c] bg-[#212121] flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2.5">
          <span className="text-xs font-medium text-[#ececec]">{dataset.name}</span>
          <span className="text-[10px] text-[#8e8e8e] font-mono">
            {dataset.product_count} items • {dataset.row_count?.toLocaleString()} rows
          </span>
        </div>

        <div className="flex items-center gap-2">
          <a
            href={api.getInventoryCsvUrl(dataset.id)}
            download
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#2a2a2a] hover:bg-[#333333] border border-[#383838] text-[#ececec] text-xs font-medium transition"
          >
            <Download className="w-3.5 h-3.5 text-[#b4b4b4]" />
            <span>Export CSV</span>
          </a>
          <button
            onClick={onViewAllProducts}
            className="px-3 py-1.5 rounded-lg bg-[#2a2a2a] hover:bg-[#333333] border border-[#383838] text-[#b4b4b4] hover:text-[#ececec] text-xs font-medium transition"
          >
            Catalog
          </button>
        </div>
      </header>

      {/* Messages Stream */}
      <div className="flex-1 overflow-y-auto px-4 md:px-8 py-6 space-y-6">
        <div className="max-w-2xl mx-auto space-y-6">
          {messages.map((m) => (
            <div
              key={m.id}
              className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`${
                  m.role === "user"
                    ? "bg-[#2f2f2f] text-[#ececec] rounded-3xl px-4 py-2.5 max-w-lg text-xs leading-relaxed"
                    : "w-full text-xs leading-relaxed space-y-3 text-[#ececec]"
                }`}
              >
                {/* Assistant Message Header (Minimal monochrome) */}
                {m.role === "assistant" && (
                  <div className="flex items-center gap-2 text-[11px] text-[#8e8e8e] mb-1">
                    <span className="font-semibold text-[#ececec]">Stock Advisor</span>
                  </div>
                )}

                {/* Plain Text Body */}
                <div className="whitespace-pre-line text-[#ececec] leading-relaxed">
                  {m.content}
                </div>

                {/* Step 1: Clean & Check Data Card */}
                {m.card_type === "health_summary" && m.payload && (
                  <div className="p-3.5 rounded-xl bg-[#262626] border border-[#333333] space-y-2.5 mt-2">
                    <div className="flex items-center justify-between text-[11px] text-[#8e8e8e]">
                      <span className="font-medium text-[#ececec]">Syntetos-Boylan Demand Quadrant</span>
                      <span>{m.payload.total_products} products</span>
                    </div>

                    <div className="grid grid-cols-3 gap-1.5 text-[11px]">
                      {Object.entries(m.payload.class_distribution || {}).map(([cls, count]) => (
                        <div
                          key={cls}
                          className="bg-[#212121] px-2.5 py-1.5 rounded-lg border border-[#2e2e2e] flex items-center justify-between"
                        >
                          <span className="capitalize text-[#b4b4b4] text-[10px]">{cls}</span>
                          <span className="font-semibold text-[#ececec]">{count as number}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Step 2: Forecast Proof Card */}
                {m.card_type === "forecast_proof" && m.payload && (
                  <div className="p-3.5 rounded-xl bg-[#262626] border border-[#333333] space-y-2.5 mt-2">
                    <div className="flex items-center justify-between text-[11px] text-[#8e8e8e]">
                      <span className="font-medium text-[#ececec]">Honest Backtest Scoreboard</span>
                      <span className="font-mono text-[10px]">WAPE ~{m.payload.average_error_pct}%</span>
                    </div>

                    <div className="grid grid-cols-2 md:grid-cols-4 gap-1.5 text-[11px]">
                      {Object.entries(m.payload.model_counts || {}).map(([model, count]) => (
                        <div
                          key={model}
                          className="bg-[#212121] px-2.5 py-2 rounded-lg border border-[#2e2e2e]"
                        >
                          <span className="text-[10px] text-[#8e8e8e] block truncate">{model}</span>
                          <span className="text-xs font-semibold text-emerald-400 mt-0.5 block">{count as number} items</span>
                        </div>
                      ))}
                    </div>

                    <p className="text-[10px] text-[#8e8e8e] italic">
                      Every model was chosen based on rolling backtests. Simpler baselines were selected whenever TimesFM did not outperform them.
                    </p>
                  </div>
                )}

                {/* Step 3: Inventory Decision Card */}
                {m.card_type === "inventory_decision" && m.payload && (
                  <div className="p-3.5 rounded-xl bg-[#262626] border border-[#333333] space-y-2.5 mt-2">
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="font-medium text-[#ececec]">Reorder Recommendations</span>
                      <span className="text-rose-400 font-medium">
                        {m.payload.reorder_actions_count} items need restock
                      </span>
                    </div>

                    {m.payload.top_reorders?.length > 0 ? (
                      <div className="space-y-1">
                        {m.payload.top_reorders.map((r: any) => (
                          <div
                            key={r.product}
                            onClick={() => onInspectProduct(r.product)}
                            className="flex items-center justify-between p-2 rounded-lg bg-[#212121] border border-[#2e2e2e] hover:border-[#444444] cursor-pointer transition text-xs"
                          >
                            <span className="font-medium text-[#ececec]">{r.product}</span>
                            <div className="flex items-center gap-3">
                              <span className="text-[#8e8e8e] text-[11px]">ROP: {Math.round(r.reorder_point)}</span>
                              <span className="px-2 py-0.5 rounded bg-white/10 text-white font-medium text-[11px]">
                                Order {r.recommended_qty}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-emerald-400 flex items-center gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5" /> All items currently maintain stock above reorder points.
                      </p>
                    )}

                    <div className="pt-1">
                      <a
                        href={m.payload.csv_url}
                        download
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white text-black hover:bg-neutral-200 text-xs font-medium transition"
                      >
                        <Download className="w-3.5 h-3.5" />
                        <span>Download Purchase Order CSV</span>
                      </a>
                    </div>
                  </div>
                )}

                {/* Focused Product Card */}
                {m.card_type === "product_focus" && m.payload && (
                  <div className="p-3.5 rounded-xl bg-[#262626] border border-[#333333] space-y-2 mt-2">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-[#ececec] text-xs">{m.payload.product}</span>
                      <button
                        onClick={() => onInspectProduct(m.payload.product)}
                        className="text-[11px] text-[#b4b4b4] hover:text-white font-medium flex items-center gap-1"
                      >
                        <span>What-If Sliders</span>
                        <ArrowRight className="w-3 h-3" />
                      </button>
                    </div>

                    <div className="grid grid-cols-3 gap-1.5 text-[11px] pt-1">
                      <div className="bg-[#212121] p-2 rounded-lg border border-[#2e2e2e]">
                        <span className="text-[#8e8e8e] block text-[10px]">Model</span>
                        <span className="font-medium text-[#ececec]">{m.payload.chosen_model}</span>
                      </div>
                      <div className="bg-[#212121] p-2 rounded-lg border border-[#2e2e2e]">
                        <span className="text-[#8e8e8e] block text-[10px]">Projected Demand</span>
                        <span className="font-medium text-[#ececec]">{m.payload.total_units} units</span>
                      </div>
                      <div className="bg-[#212121] p-2 rounded-lg border border-[#2e2e2e]">
                        <span className="text-[#8e8e8e] block text-[10px]">Reorder Point</span>
                        <span className="font-medium text-[#ececec]">{m.payload.reorder_point} units</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}

          {isSending && (
            <div className="flex items-center gap-2 text-xs text-[#8e8e8e] py-1">
              <span className="w-2 h-2 rounded-full bg-[#8e8e8e] animate-pulse"></span>
              <span>Checking facts and formulating answer...</span>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Suggestion Chips & Bottom Chat Input (ChatGPT Style) */}
      <div className="p-4 bg-[#212121] shrink-0">
        <div className="max-w-2xl mx-auto space-y-3">
          {/* Suggestion Chips */}
          <div className="flex items-center gap-2 overflow-x-auto pb-1 text-[11px]">
            {[
              "What should I reorder this week?",
              "Which products have high stockout risk?",
              "Why was AutoETS chosen for PROD_01?",
              "What data cleaning was done?",
            ].map((chip) => (
              <button
                key={chip}
                onClick={() => handleSend(chip)}
                disabled={isSending}
                className="whitespace-nowrap px-3 py-1.5 rounded-full bg-[#2a2a2a] hover:bg-[#333333] border border-[#383838] text-[#b4b4b4] hover:text-white transition disabled:opacity-50"
              >
                {chip}
              </button>
            ))}
          </div>

          {/* Input Box: Authentic ChatGPT pill shape */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSend();
            }}
            className="flex items-center gap-2 bg-[#2f2f2f] border border-[#383838] focus-within:border-[#555555] rounded-3xl px-3 py-2 transition"
          >
            <button
              type="button"
              onClick={onOpenUpload}
              className="p-1.5 rounded-full text-[#8e8e8e] hover:text-[#ececec] hover:bg-[#383838] transition"
              title="Upload new file"
            >
              <Paperclip className="w-4 h-4" />
            </button>

            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask anything about your stock, forecasts, or reorder needs..."
              className="flex-1 bg-transparent px-2 text-xs text-[#ececec] placeholder:text-[#8e8e8e] focus:outline-none"
              disabled={isSending}
            />

            <button
              type="submit"
              disabled={!input.trim() || isSending}
              className="w-7 h-7 rounded-full flex items-center justify-center bg-white text-black hover:bg-neutral-200 disabled:opacity-20 disabled:hover:bg-white transition shrink-0"
            >
              <ArrowUp className="w-4 h-4 stroke-[2.5]" />
            </button>
          </form>

          <p className="text-[10px] text-[#8e8e8e] text-center">
            Gemma explains results, but never computes numbers. All forecasts and reorder math are strictly deterministic.
          </p>
        </div>
      </div>
    </div>
  );
}
