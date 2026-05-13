import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import FloatingNavbar from "@/components/layout/FloatingNavbar";
import CommandPalette from "@/components/layout/CommandPalette";
import LeftHierarchySidebar from "@/components/layout/LeftHierarchySidebar";
import WorkspaceSubHeader from "@/components/layout/WorkspaceSubHeader";
import SessionProviderWrapper from "@/components/auth/SessionProviderWrapper";
import ToastProvider from "@/components/ui/ToastProvider";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "AI QA Portal — Test Intelligence Platform",
  description: "AI-driven Salesforce test automation. Generate, execute, and manage Robot Framework tests using natural language.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.variable} font-sans bg-gradient-animated min-h-screen antialiased`}>
        <SessionProviderWrapper>
          <ToastProvider>
            <FloatingNavbar />
            {/* Cmd+K command palette: portal-rendered, listens for the
                hotkey globally. Mounted once at root so every page gets
                the same shortcut + cache. */}
            <CommandPalette />
            {/* Unified shell: primary left rail + content. The topbar is
                `position: fixed` (outside this flow) so `pt-14` clears it.
                The sidebar
                hides itself on /login + when unauthenticated, so SSR'd
                public pages render without it. Each existing page keeps
                its own `max-w-6xl mx-auto` centering -- pages now center
                within `flex-1` rather than the viewport, which only shifts
                content right by the sidebar width on desktop. */}
            <div className="pt-14 min-h-screen">
              <WorkspaceSubHeader />
              <div className="flex min-h-[calc(100vh-3.5rem)]">
                <LeftHierarchySidebar />
                <main className="flex-1 min-w-0">{children}</main>
              </div>
            </div>
          </ToastProvider>
        </SessionProviderWrapper>
      </body>
    </html>
  );
}
