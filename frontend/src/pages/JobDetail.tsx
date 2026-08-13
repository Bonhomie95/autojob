import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ExternalLink, FileText } from "lucide-react";
import { api, downloadUrl, ApiClientError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { Card, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PageSpinner } from "@/components/ui/spinner";
import type { Job } from "@/types";

export function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const { show } = useToast();
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);

  async function load() {
    const data = await api.get<{ job: Job }>(`/api/jobs/${jobId}`);
    setJob(data.job);
  }

  useEffect(() => {
    load().finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  async function handleSend() {
    setSending(true);
    try {
      const res = await api.post<{ message: string }>(`/api/jobs/${jobId}/send`);
      show(res.message, "success");
      await load();
    } catch (err) {
      show(err instanceof ApiClientError ? err.body?.error ?? err.message : "Send failed.", "error");
      await load();
    } finally {
      setSending(false);
    }
  }

  if (loading || !job) return <PageSpinner />;

  const hasContact = Boolean(job.hrEmail || job.applicationEmail);

  return (
    <div className="space-y-6">
      <Link to="/app/jobs" className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline">
        <ArrowLeft className="h-4 w-4" />
        All jobs
      </Link>

      <Card>
        <h1 className="text-xl font-extrabold text-card-foreground">{job.title}</h1>
        <p className="mt-1 text-muted-foreground">
          {job.company}
          {job.location ? ` · ${job.location}` : ""}
        </p>
        <p className="mt-2 text-sm text-muted-foreground">
          Match score <strong className="text-card-foreground">{job.score}/100</strong> · Source {job.source || "—"} · Status{" "}
          <strong className="text-card-foreground">{job.status}</strong>
          {job.replyDetected && (
            <span className="font-semibold text-primary"> · Recruiter replied — follow up personally</span>
          )}
        </p>
        {job.url && (
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-2 inline-flex items-center gap-1 text-sm font-semibold text-primary hover:underline"
          >
            View original posting <ExternalLink className="h-3.5 w-3.5" />
          </a>
        )}
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardTitle>Contact</CardTitle>
          <p className="mb-2 text-sm text-muted-foreground">
            HR: <span className="text-card-foreground">{job.hrName || "—"}</span>
            {job.hrTitle ? ` (${job.hrTitle})` : ""}
          </p>
          <p className="mb-2 text-sm text-muted-foreground">
            Email: <span className="text-card-foreground">{job.hrEmail || job.applicationEmail || "—"}</span>
          </p>
          {job.applicationUrl && (
            <a
              href={job.applicationUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-semibold text-primary hover:underline"
            >
              Apply URL ↗
            </a>
          )}
        </Card>

        <Card>
          <CardTitle>Documents</CardTitle>
          {job.files && job.files.length > 0 ? (
            <ul className="mb-3 space-y-1.5">
              {job.files.map((f) => (
                <li key={f}>
                  <a
                    href={downloadUrl(job.id, f)}
                    className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
                  >
                    <FileText className="h-3.5 w-3.5" />
                    {f}
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">
              No documents generated{!hasContact ? " — no contact was found for this job." : ""}
            </p>
          )}

          {!!job.files?.length && hasContact && (
            <>
              <Button onClick={handleSend} loading={sending} disabled={job.emailStatus === "sent"}>
                {job.emailStatus === "sent" ? "Already sent" : "Send application"}
              </Button>
              {job.emailError && <p className="mt-2 text-sm font-medium text-destructive">{job.emailError}</p>}
            </>
          )}
        </Card>
      </div>

      {job.description && (
        <Card>
          <CardTitle>Job description</CardTitle>
          <p className="whitespace-pre-wrap text-sm text-muted-foreground">{job.description.slice(0, 4000)}</p>
        </Card>
      )}
    </div>
  );
}
