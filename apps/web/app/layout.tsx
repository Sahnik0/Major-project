import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Demand Forecasting & Inventory Advisor",
  description: "Local-first retail sales forecasting and stock recommendation platform.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-[#212121] text-[#ececec] antialiased selection:bg-neutral-700 selection:text-white">
        {children}
      </body>
    </html>
  );
}

