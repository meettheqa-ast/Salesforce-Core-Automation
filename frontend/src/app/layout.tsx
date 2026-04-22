import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import FloatingNavbar from "@/components/layout/FloatingNavbar";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "AI QA Portal — Test Intelligence Platform",
  description: "AI-driven Salesforce test automation. Generate, execute, and manage Robot Framework tests using natural language.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.variable} font-sans bg-gradient-animated min-h-screen antialiased`}>
        <FloatingNavbar />
        <main className="pt-20">{children}</main>
      </body>
    </html>
  );
}
