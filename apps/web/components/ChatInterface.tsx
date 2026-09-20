"use client";

import React, { useState, useEffect, useRef } from "react";
import {
  Sparkles,
  Send,
  UploadCloud,
  FileSpreadsheet,
  Download,
  AlertTriangle,
  CheckCircle2,
  TrendingUp,
  ShieldCheck,
  RefreshCw,
  Layers,
  ArrowRight,
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
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
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
    setIsLoadingMessages(true);
    try {
      const msgs = await api.getChatMessages(datasetId);
      setMessages(msgs);
    } catch (err) {
      console.error(err);
    } finally {
      setIsLoadingMessages(false);
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

    // Optimistic user message
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
          content: "Sorry, I encountered an issue analyzing your local facts. Please check that Ollama is active on your machine.",
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setIsSending(false);
      scrollToBottom();
    }
  };

  // Zero-State: When no dataset is loaded
  if (!dataset) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-6 max-w-2xl mx-auto w-full text-center space-y-6">
        <div className="w-14 h-14 rounded-2xl bg-gradient-to-tr from-blue-600 to-indigo-600 flex items-center justify-center shadow-xl shadow-blue-500/20">
          <Sparkles className="w-7 h-7 text-white" />
        </div>

        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">
            What should I stock, and how much?
          </h2>
          <p className="text-xs text-slate-400 mt-2.5 max-w-md mx-auto leading-relaxed">
            Upload your sales history. We clean your data, test TimesFM against simple baselines honestly, and turn forecasts into exact reorder quantities — 100% locally.
          </p>
        </div>

        {/* Upload Dropzone */}
        <div
          onClick={onOpenUpload}
          className="w-full border-2 border-dashed border-slate-800 hover:border-blue-500/60 rounded-2xl p-8 bg-slate-900/40 hover:bg-slate-900/70 transition cursor-pointer flex flex-col items-center space-y-3 group"
        >
          <div className="p-3.5 rounded-full bg-blue-500/10 text-blue-400 group-hover:scale-110 transition">
            <UploadCloud className="w-6 h-6" />
          </div>
          <div className="text-xs">
            <span className="font-semibold text-white">Click to upload your sales CSV / Excel</span>
            <p className="text-[11px] text-slate-400 mt-1">
              Supports Date, Product SKU, Units Sold, and Revenue columns
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 w-full justify-center pt-2">
          <span className="text-slate-600 text-xs">— or —</span>
          <button
            onClick={onLoadDemo}
            disabled={isLoadingDemo}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-300 transition"
          >
            <FileSpreadsheet className="w-3.5 h-3.5 text-indigo-400" />
            <span>{isLoadingDemo ? "Loading Demo..." : "Try with 40-Product Retail Demo"}</span>
          </button>
        </div>

        <div className="pt-8 flex items-center gap-6 text-[11px] text-slate-500 justify-center">
          <span className="flex items-center gap-1.5">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> Real sales cleaning
          </span>
          <span className="flex items-center gap-1.5">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> Honest model competition
          </span>
          <span className="flex items-center gap-1.5">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" /> 100% Offline & Private
          </span>
        </div>
      </div>
    );
  }

  // Active Dataset Chat View
  return (
    <div className="flex-1 flex flex-col h-screen overflow-hidden bg-slate-950">
      {/* Top Header */}
      <header className="px-6 py-3 border-b border-slate-800/80 bg-slate-950/80 backdrop-blur-sm flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <FileSpreadsheet className="w-4 h-4 text-blue-400" />
          <div>
            <h3 className="text-xs font-semibold text-white">{dataset.name}</h3>
            <p className="text-[10px] text-slate-400 font-mono">
              {dataset.product_count} products • {dataset.row_count?.toLocaleString()} records
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <a
            href={api.getInventoryCsvUrl(dataset.id)}
            download
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-200 text-xs font-semibold transition"
          >
            <Download className="w-3.5 h-3.5 text-blue-400" />
            <span>Download Purchase Order CSV</span>
          </a>
          <button
            onClick={onViewAllProducts}
            className="px-3 py-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-300 text-xs font-semibold transition"
          >
            Browse Products
          </button>
        </div>
      </header>

      {/* Messages Stream */}
      <div className="flex-1 overflow-y-auto px-4 md:px-8 py-6 space-y-6">
        <div className="max-w-3xl mx-auto space-y-6">
          {messages.map((m) => (
            <div
              key={m.id}
              className={`flex gap-3.5 ${m.role === "user" ? "justify-end" : "justify-start"}`}
            >
              {m.role === "assistant" && (
                <div className="w-7 h-7 rounded-lg bg-gradient-to-tr from-blue-600 to-indigo-600 flex items-center justify-center shrink-0 shadow-md shadow-blue-500/20 mt-0.5">
                  <Sparkles className="w-3.5 h-3.5 text-white" />
                </div>
              )}

              <div
                className={`max-w-2xl rounded-2xl p-4 text-xs leading-relaxed space-y-3 ${
                  m.role === "user"
                    ? "bg-blue-600 text-white shadow-md shadow-blue-600/20"
                    : "bg-slate-900/90 border border-slate-800 text-slate-200 shadow-lg"
                }`}
              >
                {/* Text Content */}
                <div className="whitespace-pre-line font-normal">{m.content}</div>

                {/* Structured Step 1 Card: Health */}
                {m.card_type === "health_summary" && m.payload && (
                  <div className="p-3.5 rounded-xl bg-slate-950/60 border border-slate-800 space-y-3 mt-2">
                    <div className="flex items-center justify-between text-[11px] text-slate-400 font-semibold uppercase tracking-wider">
                      <span>Demand Classification (Syntetos-Boylan)</span>
                      <span>{m.payload.total_products} Products</span>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-[11px]">
                      {Object.entries(m.payload.class_distribution || {}).map(([cls, count]) => (
                        <div
                          key={cls}
                          className="bg-slate-900/80 px-2.5 py-1.5 rounded-lg border border-slate-800 flex items-center justify-between"
                        >
                          <span className="capitalize text-slate-400">{cls}</span>
                          <span className="font-bold text-white">{count as number}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Structured Step 2 Card: Forecast Proof */}
                {m.card_type === "forecast_proof" && m.payload && (
                  <div className="p-3.5 rounded-xl bg-slate-950/60 border border-slate-800 space-y-3 mt-2">
                    <div className="flex items-center justify-between text-[11px] text-slate-400 font-semibold uppercase tracking-wider">
                      <span>Honest Model Scoreboard</span>
                      <span>WAPE Error ~{m.payload.average_error_pct}%</span>
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
                      {Object.entries(m.payload.model_counts || {}).map(([model, count]) => (
                        <div
                          key={model}
                          className="bg-slate-900/80 px-2.5 py-2 rounded-lg border border-slate-800 flex flex-col justify-between"
                        >
                          <span className="text-[10px] text-slate-400 truncate">{model}</span>
                          <span className="text-sm font-bold text-emerald-400 mt-1">{count as number} items</span>
                        </div>
                      ))}
                    </div>
                    <p className="text-[10px] text-slate-400 italic">
                      Every product selected its winner based on rolling backtests. Simple baselines were chosen wherever TimesFM didn't beat them.
                    </p>
                  </div>
                )}

                {/* Structured Step 3 Card: Inventory Decisions */}
                {m.card_type === "inventory_decision" && m.payload && (
                  <div className="p-3.5 rounded-xl bg-slate-950/60 border border-slate-800 space-y-3 mt-2">
                    <div className="flex items-center justify-between text-[11px] text-slate-400 font-semibold uppercase tracking-wider">
                      <span>Top Restock Priorities</span>
                      <span className="text-rose-400 font-bold">{m.payload.reorder_actions_count} Need Orders</span>
                    </div>

                    {m.payload.top_reorders?.length > 0 ? (
                      <div className="space-y-1.5">
                        {m.payload.top_reorders.map((r: any) => (
                          <div
                            key={r.product}
                            onClick={() => onInspectProduct(r.product)}
                            className="flex items-center justify-between p-2 rounded-lg bg-slate-900/80 border border-slate-800 hover:border-blue-500/50 cursor-pointer transition text-xs"
                          >
                            <span className="font-semibold text-white">{r.product}</span>
                            <div className="flex items-center gap-3">
                              <span className="text-slate-400">ROP: {Math.round(r.reorder_point)}</span>
                              <span className="px-2 py-0.5 rounded bg-blue-500/20 text-blue-300 font-bold">
                                Order {r.recommended_qty} units
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-emerald-400 flex items-center gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5" /> All stock levels are currently above reorder points.
                      </p>
                    )}

                    <div className="pt-1 flex items-center justify-between">
                      <a
                        href={m.payload.csv_url}
                        download
                        className="flex items-center gap-1.5 text-xs text-blue-400 hover:text-blue-300 font-semibold"
                      >
                        <Download className="w-3.5 h-3.5" />
                        <span>Download Complete Purchase Order CSV</span>
                      </a>
                    </div>
                  </div>
                )}

                {/* Focused Product Card */}
                {m.card_type === "product_focus" && m.payload && (
                  <div className="p-3.5 rounded-xl bg-slate-950/60 border border-slate-800 space-y-2 mt-2">
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-white text-xs">{m.payload.product}</span>
                      <button
                        onClick={() => onInspectProduct(m.payload.product)}
                        className="text-[11px] text-blue-400 hover:text-blue-300 font-semibold flex items-center gap-1"
                      >
                        <span>Open What-If Sliders</span>
                        <ArrowRight className="w-3 h-3" />
                      </button>
                    </div>

                    <div className="grid grid-cols-3 gap-2 text-[11px] pt-1">
                      <div className="bg-slate-900 p-2 rounded-lg border border-slate-800">
                        <span className="text-slate-400 block text-[10px]">Model</span>
                        <span className="font-semibold text-white">{m.payload.chosen_model}</span>
                      </div>
                      <div className="bg-slate-900 p-2 rounded-lg border border-slate-800">
                        <span className="text-slate-400 block text-[10px]">Forecast Demand</span>
                        <span className="font-semibold text-white">{m.payload.total_units} units</span>
                      </div>
                      <div className="bg-slate-900 p-2 rounded-lg border border-slate-800">
                        <span className="text-slate-400 block text-[10px]">Reorder Point</span>
                        <span className="font-semibold text-white">{m.payload.reorder_point} units</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}

          {isSending && (
            <div className="flex gap-3.5 items-center text-xs text-slate-400">
              <div className="w-7 h-7 rounded-lg bg-gradient-to-tr from-blue-600 to-indigo-600 flex items-center justify-center shrink-0 shadow-md shadow-blue-500/20">
                <Sparkles className="w-3.5 h-3.5 text-white animate-spin" />
              </div>
              <div className="p-3 rounded-2xl bg-slate-900 border border-slate-800">
                Verifying deterministic facts and formulating answer with Gemma...
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Suggestion Chips & Bottom Chat Input */}
      <div className="p-4 border-t border-slate-800/80 bg-slate-950 shrink-0">
        <div className="max-w-3xl mx-auto space-y-3">
          {/* Quick Prompts */}
          <div className="flex items-center gap-2 overflow-x-auto pb-1 text-[11px]">
            {[
              "What should I reorder this week?",
              "Which products have the highest stockout risk?",
              "Why was AutoETS chosen for PROD_01?",
              "What data cleaning steps were performed?",
            ].map((chip) => (
              <button
                key={chip}
                onClick={() => handleSend(chip)}
                disabled={isSending}
                className="whitespace-nowrap px-3 py-1.5 rounded-full bg-slate-900 hover:bg-slate-850 border border-slate-800 text-slate-300 hover:text-white transition disabled:opacity-50"
              >
                {chip}
              </button>
            ))}
          </div>

          {/* Input Box */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSend();
            }}
            className="flex items-center gap-2 bg-slate-900 border border-slate-800 rounded-2xl p-1.5 focus-within:border-blue-500/80 transition shadow-xl"
          >
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask anything about your stock, demand forecasts, or reorder needs..."
              className="flex-1 bg-transparent px-3 py-2 text-xs text-white placeholder:text-slate-500 focus:outline-none"
              disabled={isSending}
            />

            <button
              type="submit"
              disabled={!input.trim() || isSending}
              className="p-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:hover:bg-blue-600 text-white transition shadow-md shadow-blue-600/20"
            >
              <Send className="w-3.5 h-3.5" />
            </button>
          </form>

          <p className="text-[10px] text-slate-500 text-center">
            Local AI verification: Gemma explains results, but never does math. All forecasts and reorder metrics are computed deterministically.
          </p>
        </div>
      </div>
    </div>
  );
}
