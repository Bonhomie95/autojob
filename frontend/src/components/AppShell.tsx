import { NavLink, Outlet } from "react-router-dom";
import {
  LayoutDashboard,
  FileText,
  Briefcase,
  Settings as SettingsIcon,
  LogOut,
  ShieldCheck,
} from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { ThemeToggle } from "@/components/ThemeToggle";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/app", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/app/cv", label: "Your CV", icon: FileText },
  { to: "/app/jobs", label: "Jobs", icon: Briefcase },
  { to: "/app/settings", label: "Settings", icon: SettingsIcon },
];

/**
 * The persistent authenticated shell. Only <Outlet/> swaps on navigation —
 * the sidebar, header, and their state never remount between pages, which is
 * the whole point versus the old server-rendered app (a full page load on
 * every tab switch).
 */
export function AppShell() {
  const { user, logout } = useAuth();

  return (
    <div className="flex min-h-dvh bg-background">
      <aside className="hidden w-64 shrink-0 flex-col border-r border-border bg-card md:flex">
        <div className="flex h-16 items-center gap-2 px-6">
          <ShieldCheck className="h-6 w-6 text-primary" aria-hidden="true" />
          <span className="text-lg font-extrabold text-card-foreground">AutoJob</span>
        </div>
        <nav className="flex-1 space-y-1 px-3" aria-label="Primary">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-semibold transition-colors",
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )
              }
            >
              <item.icon className="h-[18px] w-[18px]" aria-hidden="true" />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-border p-3">
          <div className="mb-2 truncate px-3 text-sm text-muted-foreground">{user?.email}</div>
          <button
            type="button"
            onClick={() => void logout()}
            className="flex w-full cursor-pointer items-center gap-3 rounded-md px-3 py-2.5 text-sm font-semibold
                       text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <LogOut className="h-[18px] w-[18px]" aria-hidden="true" />
            Sign out
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 items-center justify-between border-b border-border bg-card px-4 md:px-8">
          <div className="flex items-center gap-2 md:hidden">
            <ShieldCheck className="h-6 w-6 text-primary" aria-hidden="true" />
            <span className="text-lg font-extrabold text-card-foreground">AutoJob</span>
          </div>
          <div className="hidden md:block" />
          <ThemeToggle />
        </header>

        <nav
          className="flex gap-1 overflow-x-auto border-b border-border bg-card px-2 md:hidden"
          aria-label="Primary"
        >
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2 whitespace-nowrap border-b-2 px-3 py-3 text-sm font-semibold transition-colors",
                  isActive
                    ? "border-primary text-primary"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                )
              }
            >
              <item.icon className="h-4 w-4" aria-hidden="true" />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <main id="main" className="flex-1 overflow-y-auto p-4 md:p-8">
          <div className="mx-auto max-w-6xl">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
