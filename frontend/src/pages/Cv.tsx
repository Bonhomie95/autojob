import { useEffect, useRef, useState, type FormEvent } from "react";
import { AlertTriangle, Upload } from "lucide-react";
import { api, ApiClientError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { Card, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PageSpinner } from "@/components/ui/spinner";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import type { CvBundle } from "@/types";

export function Cv() {
  const { show } = useToast();
  const [bundle, setBundle] = useState<CvBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function load() {
    const data = await api.get<CvBundle>("/api/cv/profile");
    setBundle(data);
  }

  useEffect(() => {
    load().finally(() => setLoading(false));
  }, []);

  async function handleUpload(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const file = fileInputRef.current?.files?.[0];
    if (!file) {
      show("Choose a CV file to upload.", "error");
      return;
    }
    const formData = new FormData();
    formData.append("cv", file);
    setUploading(true);
    try {
      const data = await api.postForm<CvBundle>("/api/cv/upload", formData);
      setBundle(data);
      show("CV uploaded and parsed. Review the details below.", "success");
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (err) {
      show(err instanceof ApiClientError ? err.body?.error ?? err.message : "Upload failed.", "error");
    } finally {
      setUploading(false);
    }
  }

  async function handleRemove() {
    try {
      await api.post("/api/cv/remove");
      await load();
      show("CV removed. Upload a new one whenever you're ready.", "success");
    } catch (err) {
      show(err instanceof ApiClientError ? err.body?.error ?? err.message : "Could not remove CV.", "error");
    }
  }

  async function handleResolve(field: string, value: string, customValue: string) {
    try {
      const data = await api.post<CvBundle>("/api/cv/resolve", {
        field,
        value: customValue ? "" : value,
        custom_value: customValue,
      });
      setBundle(data);
      show("Saved.", "success");
    } catch (err) {
      show(err instanceof ApiClientError ? err.body?.error ?? err.message : "Could not save.", "error");
    }
  }

  if (loading || !bundle) return <PageSpinner />;

  const { doc, profile, sendable, blockers } = bundle;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-extrabold text-foreground">Your CV</h1>

      <Card>
        {doc ? (
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-foreground">
              ✓ Current CV: <strong>{doc.filename}</strong>{" "}
              <span className="text-muted-foreground">({(doc.sizeBytes / 1024).toFixed(1)} KB)</span>
            </p>
            <ConfirmDialog
              trigger={
                <Button variant="ghost" size="sm" className="text-destructive hover:bg-destructive/10">
                  Remove
                </Button>
              }
              title={`Remove ${doc.filename}?`}
              description="You can upload a new one anytime."
              confirmLabel="Remove"
              onConfirm={handleRemove}
            />
          </div>
        ) : (
          <p className="mb-4 text-muted-foreground">
            Upload your CV once. AutoJob reads it to decide which roles to search for and how to
            tailor each application. PDF, DOCX or TXT, up to 10&nbsp;MB.
          </p>
        )}
        {doc && <p className="mb-3 text-sm text-muted-foreground">Uploading a new file below replaces this one.</p>}
        <form onSubmit={handleUpload} className="flex flex-wrap items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            name="cv"
            accept=".pdf,.docx,.txt"
            required
            aria-label="CV file"
            className="text-sm text-foreground"
          />
          <Button type="submit" variant="accent" loading={uploading}>
            <Upload className="h-4 w-4" />
            {doc ? "Replace CV" : "Upload & parse"}
          </Button>
        </form>
      </Card>

      {profile ? (
        <>
          {!sendable && (
            <Card className="border-destructive/40 bg-destructive/5">
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-5 w-5 text-destructive" aria-hidden="true" />
                <CardTitle className="text-base">A few things need fixing before AutoJob can apply</CardTitle>
              </div>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                {blockers.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            </Card>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <Card>
              <CardTitle>What we understood</CardTitle>
              <p className="mb-2 font-semibold text-card-foreground">{profile.name || "—"}</p>
              <p className="mb-2 text-sm text-muted-foreground">
                {profile.seniority}
                {profile.years_experience ? ` · ${profile.years_experience} yrs` : ""}
              </p>
              <p className="mb-2 text-sm text-muted-foreground">
                {profile.skills?.length ?? 0} skills · {profile.experience?.length ?? 0} roles ·{" "}
                {profile.projects?.length ?? 0} projects
              </p>
              {!!profile.titles?.length && (
                <p className="text-sm text-muted-foreground">
                  Searching for: <strong className="text-card-foreground">{profile.titles.slice(0, 4).join(", ")}</strong>
                </p>
              )}
            </Card>

            <Card>
              <CardTitle>Contact details</CardTitle>
              {["email", "phone", "location", "linkedin", "github"].map((field) => {
                const c = profile.contact_choices?.[field];
                if (!c?.value) return null;
                return (
                  <p key={field} className="mb-2 text-sm">
                    <span className="capitalize text-muted-foreground">{field}: </span>
                    <span className="text-card-foreground">{c.value}</span>
                  </p>
                );
              })}
            </Card>
          </div>

          {!!profile.ambiguous_fields?.length && (
            <Card>
              <CardTitle>Which of these should we use?</CardTitle>
              <p className="mb-4 text-sm text-muted-foreground">
                Your CV lists more than one value for these — pick the current one, or type a
                different one below if neither still works.
              </p>
              <div className="space-y-3">
                {profile.ambiguous_fields.map((field) => (
                  <AmbiguousFieldRow
                    key={field}
                    field={field}
                    choice={profile.contact_choices?.[field]}
                    onResolve={handleResolve}
                  />
                ))}
              </div>
            </Card>
          )}
        </>
      ) : doc ? (
        <Card>
          <p className="text-muted-foreground">We couldn't read any text from that file. Try a different export of your CV.</p>
        </Card>
      ) : null}
    </div>
  );
}

function AmbiguousFieldRow({
  field,
  choice,
  onResolve,
}: {
  field: string;
  choice?: { value: string; options: string[] };
  onResolve: (field: string, value: string, customValue: string) => void;
}) {
  const [selected, setSelected] = useState(choice?.value ?? "");
  const [custom, setCustom] = useState("");

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="w-20 shrink-0 text-sm font-medium capitalize text-foreground">{field}</label>
      <select
        value={selected}
        onChange={(e) => setSelected(e.target.value)}
        className="h-11 rounded-md border border-border bg-card px-3 text-sm text-foreground"
      >
        {choice?.options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
      <span className="text-sm text-muted-foreground">or</span>
      <input
        type="text"
        value={custom}
        onChange={(e) => setCustom(e.target.value)}
        placeholder={`type a different ${field}`}
        className="h-11 min-w-[180px] flex-1 rounded-md border border-border bg-card px-3 text-sm text-foreground placeholder:text-muted-foreground"
      />
      <Button variant="ghost" size="sm" onClick={() => onResolve(field, selected, custom)}>
        Save
      </Button>
    </div>
  );
}
