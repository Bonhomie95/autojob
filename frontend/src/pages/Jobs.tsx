import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { RefreshCw, Trash2 } from "lucide-react";
import { api, ApiClientError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PageSpinner } from "@/components/ui/spinner";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { cn } from "@/lib/utils";
import type { Job } from "@/types";

const STATUS_TABS = ["all", "done", "applied", "skipped", "pending"] as const;

function StatusBadge({ job }: { job: Job }) {
  if (job.replyDetected) return <Badge variant="success">replied</Badge>;
  if (job.bounced) return <Badge variant="destructive">bounced</Badge>;
  if (job.emailStatus === "sent")
    return <Badge variant="success">sent{job.followUpStatus === "sent" ? " · followed up" : ""}</Badge>;
  if (job.status === "done") return <Badge variant="info">ready to send</Badge>;
  return <Badge>{job.status}</Badge>;
}

export function Jobs() {
  const { show } = useToast();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "all";
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [awaiting, setAwaiting] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);

  async function load() {
    const data = await api.get<{ jobs: Job[]; awaiting: Job[] }>(`/api/jobs/?status=${status}`);
    setJobs(data.jobs);
    setAwaiting(data.awaiting);
  }

  useEffect(() => {
    setLoading(true);
    load().finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  async function handleCheckReplies() {
    setChecking(true);
    try {
      await api.post("/api/jobs/followups/run");
      show("Checking your inbox for replies and following up with non-responders…", "success");
    } catch (err) {
      show(err instanceof ApiClientError ? err.body?.error ?? err.message : "Could not start.", "error");
    } finally {
      setChecking(false);
    }
  }

  async function handleClear() {
    try {
      const res = await api.post<{ count: number }>("/api/jobs/clear");
      show(`Cleared ${res.count} job(s). They can be rediscovered on your next run.`, "success");
      await load();
    } catch (err) {
      show(err instanceof ApiClientError ? err.body?.error ?? err.message : "Could not clear jobs.", "error");
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-extrabold text-foreground">Jobs</h1>

      {awaiting.length > 0 && (
        <Card className="border-warning/40 bg-warning/5">
          <h3 className="font-bold text-card-foreground">
            {awaiting.length} repl{awaiting.length === 1 ? "y" : "ies"} need{awaiting.length === 1 ? "s" : ""} your attention
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            A recruiter replied — AutoJob won't auto-follow-up. Reply personally:
          </p>
          <ul className="mt-2 space-y-1 text-sm">
            {awaiting.map((j) => (
              <li key={j.id}>
                <Link to={`/app/jobs/${j.id}`} className="font-semibold text-primary hover:underline">
                  {j.title} @ {j.company}
                </Link>
                {j.hrEmail && <span className="text-muted-foreground"> — {j.hrEmail}</span>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1 overflow-x-auto rounded-md border border-border bg-card p-1">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setParams(s === "all" ? {} : { status: s })}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-semibold capitalize transition-colors",
                status === s ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
              )}
            >
              {s}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={handleCheckReplies} loading={checking}>
            <RefreshCw className="h-4 w-4" />
            Check replies &amp; follow up
          </Button>
          {!!jobs?.length && (
            <ConfirmDialog
              trigger={
                <Button variant="outline" size="sm" className="text-destructive hover:bg-destructive/10">
                  <Trash2 className="h-4 w-4" />
                  Clear jobs
                </Button>
              }
              title={`Clear all ${jobs.length} discovered job(s)?`}
              description="Previously-seen postings can resurface on your next run. This can't be undone."
              confirmLabel="Clear jobs"
              onConfirm={handleClear}
            />
          )}
        </div>
      </div>

      {loading ? (
        <PageSpinner />
      ) : jobs && jobs.length > 0 ? (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="px-4 py-3 font-semibold">Role</th>
                <th className="px-4 py-3 font-semibold">Company</th>
                <th className="px-4 py-3 font-semibold">Score</th>
                <th className="px-4 py-3 font-semibold">Contact</th>
                <th className="px-4 py-3 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                  <td className="px-4 py-3">
                    <Link to={`/app/jobs/${j.id}`} className="font-semibold text-primary hover:underline">
                      {j.title || "—"}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-foreground">{j.company || "—"}</td>
                  <td className="px-4 py-3 tabular-nums text-foreground">{j.score}</td>
                  <td className="px-4 py-3 text-foreground">{j.hrEmail || j.applicationEmail || "—"}</td>
                  <td className="px-4 py-3">
                    <StatusBadge job={j} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : (
        <Card>
          <p className="text-muted-foreground">
            No jobs yet. Run a discovery pass from the{" "}
            <Link to="/app" className="font-semibold text-primary hover:underline">
              Overview
            </Link>
            .
          </p>
        </Card>
      )}
    </div>
  );
}
