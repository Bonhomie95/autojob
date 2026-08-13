import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export function StatTile({
  label,
  value,
  icon: Icon,
  accent,
}: {
  label: string;
  value: number | string;
  icon: LucideIcon;
  accent?: "primary" | "accent" | "warning" | "destructive";
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-5 shadow-sm transition-transform hover:scale-[1.02]">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-muted-foreground">{label}</span>
        <Icon
          className={cn(
            "h-4 w-4",
            accent === "accent" && "text-accent",
            accent === "warning" && "text-warning",
            accent === "destructive" && "text-destructive",
            (!accent || accent === "primary") && "text-primary"
          )}
          aria-hidden="true"
        />
      </div>
      <div className="mt-2 text-3xl font-extrabold tabular-nums text-card-foreground">{value}</div>
    </div>
  );
}
