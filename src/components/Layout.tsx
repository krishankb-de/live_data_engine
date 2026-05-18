import { useState } from "react";
import { Outlet } from "react-router";
import { Sidebar } from "./Sidebar";

export function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="flex h-full">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-30 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar: hidden on mobile unless open, always visible md+ */}
      <div
        className={[
          "fixed inset-y-0 left-0 z-40 md:static md:flex md:z-auto transition-transform duration-200",
          sidebarOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
        ].join(" ")}
      >
        <Sidebar />
      </div>

      <div className="flex-1 overflow-y-auto bg-bg p-4 md:p-8">
        {/* Hamburger button — mobile only */}
        <button
          type="button"
          className="mb-4 md:hidden p-2 rounded bg-surface-dark text-text-inverse text-lg leading-none"
          aria-label="Open navigation"
          onClick={() => setSidebarOpen(true)}
        >
          ☰
        </button>
        <Outlet />
      </div>
    </div>
  );
}
