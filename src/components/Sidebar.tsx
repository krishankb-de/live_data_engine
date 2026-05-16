interface SidebarProps {
  activeRoute: "overview";
}

const NAV_ITEMS = [
  { label: "Overview", route: "overview", icon: "▦" },
  { label: "Pipeline Status", route: "pipeline-status", icon: "↻" },
  { label: "Email Log", route: "email-log", icon: "✉" },
  { label: "Manual Review", route: "manual-review", icon: "✎" },
  { label: "Settings", route: "settings", icon: "⚙" },
] as const;

export function Sidebar({ activeRoute }: SidebarProps) {
  return (
    <aside className="w-60 shrink-0 bg-surface-dark flex flex-col h-full">
      <div className="px-6 py-6">
        <span className="text-accent font-bold text-lg tracking-tight">
          Live-Data Engine
        </span>
      </div>

      <nav className="flex-1 px-3">
        <ul className="flex flex-col gap-1">
          {NAV_ITEMS.map(({ label, route, icon }) => {
            const isActive = route === activeRoute;
            const isStub = route !== "overview";
            return (
              <li
                key={route}
                aria-current={isActive ? "page" : undefined}
                className={[
                  "px-3 py-2.5 text-sm font-medium border-l-2 flex items-center gap-2.5 rounded-sm",
                  isActive
                    ? "text-accent border-accent"
                    : "text-text-inverse-muted border-transparent hover:text-text-inverse",
                  isStub ? "pointer-events-none opacity-50" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
              >
                <span className="text-base leading-none">{icon}</span>
                {label}
              </li>
            );
          })}
        </ul>
      </nav>
    </aside>
  );
}
