import { NavLink } from "react-router";

interface NavItem {
  label: string;
  to: string;
  icon: string;
  enabled: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { label: "Overview", to: "/dashboard", icon: "▦", enabled: true },
  { label: "Pipeline Status", to: "/pipeline-status", icon: "↻", enabled: true },
  { label: "Costs", to: "/costs", icon: "€", enabled: true },
  { label: "Email Log", to: "/email-log", icon: "✉", enabled: true },
  { label: "Manual Review", to: "/manual-review", icon: "✎", enabled: true },
  { label: "Settings", to: "/settings", icon: "⚙", enabled: true },
];

export function Sidebar() {
  return (
    <aside className="w-60 shrink-0 bg-surface-dark flex flex-col h-full">
      <div className="px-6 py-6">
        <span className="text-accent font-bold text-lg tracking-tight">
          Live-Data Engine
        </span>
      </div>

      <nav className="flex-1 px-3">
        <ul className="flex flex-col gap-1">
          {NAV_ITEMS.map(({ label, to, icon, enabled }) => (
            <li key={to}>
              {enabled ? (
                <NavLink
                  to={to}
                  end={to === "/"}
                  className={({ isActive }) =>
                    [
                      "px-3 py-2.5 text-sm font-medium border-l-2 flex items-center gap-2.5 rounded-sm",
                      isActive
                        ? "text-accent border-accent"
                        : "text-text-inverse-muted border-transparent hover:text-text-inverse",
                    ].join(" ")
                  }
                >
                  <span className="text-base leading-none">{icon}</span>
                  {label}
                </NavLink>
              ) : (
                <span
                  aria-disabled="true"
                  className="px-3 py-2.5 text-sm font-medium border-l-2 flex items-center gap-2.5 rounded-sm text-text-inverse-muted border-transparent pointer-events-none opacity-50"
                >
                  <span className="text-base leading-none">{icon}</span>
                  {label}
                </span>
              )}
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
}
