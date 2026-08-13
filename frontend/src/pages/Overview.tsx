import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Briefcase, CheckCircle2, Mail, MessageSquare, Play, Square, Upload } from "lucide-react";
import { api, ApiClientError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { StatTile } from "@/components/StatTile";
import { Card, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PageSpinner } from "@/components/ui/spinner";
import type { DashboardData } from "@/types";

type RunPhase = "idle" | "running" | "stopping";

export function Overview() {
  const { show } = useToast();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [runId, setRunId] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const sourceRef = useRef<EventSource | null>(null);

  const loadDashboard = useCallback(async () => {
    const d = await api.get<DashboardData>("/api/dashboard");
    setData(d);
    return d;
  }, []);

  const watch = useCallback(
    (id: string) => {
      sourceRef.current?.close();
      const source = new EventSource(`/api/runs/${id}/stream`);
      sourceRef.current = source;
      source.onmessage = (e) => {
        const payload = JSON.parse(e.data) as { message?: string; done?: boolean };
        if (payload.message) setLog((prev) => [...prev, payload.message!]);
        if (payload.done) {
          source.close();
          setPhase("idle");
          setRunId(null);
          void loadDashboard();
        }
      };
      source.onerror = () => {
        source.close();
        setPhase("idle");
        setRunId(null);
      };
    },
    [loadDashboard]
  );

  useEffect(() => {
    loadDashboard()
      .then((d) => {
        // Resume: a run already in progress server-side (e.g. this page was
        // reloaded mid-run) reattaches instead of showing "Run now" as if
        // nothing were happening.
        if (d.activeRunId) {
          setRunId(d.activeRunId);
          setPhase("running");
          setLog((prev) => [...prev, "↻ Reattached to a run already in progress…"]);
          watch(d.activeRunId);
        }
      })
      .finally(() => setLoading(false));

    return () => sourceRef.current?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleStart() {
    setPhase("running");
    setLog([]);
    try {
      const res = await api.post<{ runId: string }>("/api/runs/start");
      setRunId(res.runId);
      watch(res.runId);
    } catch (err) {
      const message = err instanceof ApiClientError ? err.body?.error ?? err.message : "Could not start";
      setLog((prev) => [...prev, `⚠ ${message}`]);
      setPhase("idle");
    }
  }

  async function handleStop() {
    if (!runId) return;
    setPhase("stopping");
    try {
      await api.post(`/api/runs/${runId}/cancel`);
      setLog((prev) => [...prev, "⏹ Stop requested — finishing the current step…"]);
    } catch {
      show("Could not send the stop request.", "error");
    } finally {
      setPhase("running");
    }
  }

  if (loading || !data) return <PageSpinner />;

  const { settings, stats, awaiting, hasCv } = data;

  return (
    <div className="space-y-6">
      {awaiting.length > 0 && (
        <Card className="border-warning/40 bg-warning/5">
          <CardTitle className="text-base">
            {awaiting.length} recruiter repl{awaiting.length === 1 ? "y" : "ies"} — follow up personally
          </CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            AutoJob paused auto-follow-up for these.{" "}
            <Link to="/app/jobs" className="font-semibold text-primary hover:underline">
              Review them →
            </Link>
          </p>
        </Card>
      )}

      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-extrabold text-foreground">Overview</h1>
        <Button asChild variant="accent" size="sm">
          <Link to="/app/cv">
            <Upload className="h-4 w-4" />
            Upload CV
          </Link>
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatTile label="Jobs discovered" value={stats.total} icon={Briefcase} />
        <StatTile label="Applications ready" value={stats.done} icon={CheckCircle2} accent="accent" />
        <StatTile label="Emails sent" value={stats.emails_sent} icon={Mail} />
        <StatTile label="Replies" value={stats.replies} icon={MessageSquare} accent="warning" />
      </div>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>Find &amp; score new jobs</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Runs a discovery pass against your CV across job boards.
            </p>
          </div>
          <div className="flex gap-2">
            {phase === "idle" ? (
              <Button onClick={handleStart}>
                <Play className="h-4 w-4" />
                Run now
              </Button>
            ) : (
              <>
                <Button disabled variant="outline" loading>
                  Running…
                </Button>
                <Button variant="destructive" onClick={handleStop} loading={phase === "stopping"}>
                  <Square className="h-4 w-4" />
                  Stop
                </Button>
              </>
            )}
          </div>
        </div>
        {log.length > 0 && (
          <pre
            className="mt-4 max-h-72 overflow-auto rounded-md bg-muted p-3 text-[13px] leading-relaxed text-foreground whitespace-pre-wrap"
            aria-live="polite"
          >
            {log.join("\n")}
          </pre>
        )}
      </Card>

      {!hasCv && (
        <Card>
          <CardTitle className="text-base">Next step: add your CV</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            Your private workspace is ready.{" "}
            <Link to="/app/cv" className="font-semibold text-primary hover:underline">
              Upload your CV
            </Link>{" "}
            and AutoJob will parse it and figure out which roles to search for.
          </p>
        </Card>
      )}

      <Card>
        <p className="text-sm text-muted-foreground">
          Auto-send is currently <strong className="text-foreground">{settings.autoSend ? "on" : "off"}</strong>,
          follow-ups <strong className="text-foreground">{settings.followUpEnabled ? "on" : "off"}</strong>,
          minimum match score <strong className="text-foreground">{settings.minMatchScore}</strong>.
        </p>
      </Card>
    </div>
  );
}
