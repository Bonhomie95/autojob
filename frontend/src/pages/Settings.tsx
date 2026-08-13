import { useEffect, useState } from "react";
import { api, ApiClientError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { Card, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, PasswordInput } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { SwitchField } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { PageSpinner } from "@/components/ui/spinner";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { AI_PROVIDERS, type AiProvider } from "@/types";
import type { CredentialMap, Settings as SettingsType } from "@/types";

const AI_PROVIDER_FIELDS: { key: AiProvider; label: string; placeholder: string }[] = [
  { key: "groq", label: "Groq API key(s)", placeholder: "gsk_…" },
  { key: "openai", label: "OpenAI API key", placeholder: "sk-…" },
  { key: "anthropic", label: "Anthropic API key", placeholder: "sk-ant-…" },
  { key: "gemini", label: "Google Gemini API key", placeholder: "AIza…" },
  { key: "grok", label: "xAI Grok API key", placeholder: "xai-…" },
  { key: "openrouter", label: "OpenRouter API key", placeholder: "sk-or-…" },
];

const AI_PROVIDER_LABELS: Record<AiProvider, string> = {
  groq: "Groq",
  openai: "OpenAI",
  anthropic: "Anthropic (Claude)",
  gemini: "Google Gemini",
  grok: "xAI (Grok)",
  openrouter: "OpenRouter",
};

const CREDENTIAL_FIELDS: { key: string; label: string; placeholder: string }[] = [
  { key: "hunter", label: "Hunter.io key", placeholder: "optional" },
  { key: "prospeo", label: "Prospeo key", placeholder: "optional" },
  { key: "reoon", label: "Reoon verifier key", placeholder: "optional" },
  { key: "million", label: "MillionVerifier key", placeholder: "optional" },
];

export function Settings() {
  const { show } = useToast();
  const [settings, setSettings] = useState<SettingsType | null>(null);
  const [creds, setCreds] = useState<CredentialMap>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<{ settings: SettingsType; credentials: CredentialMap }>("/api/settings/")
      .then((data) => {
        setSettings(data.settings);
        setCreds(data.credentials);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading || !settings) return <PageSpinner />;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-extrabold text-foreground">Settings</h1>
      <GeneralSection settings={settings} onSaved={setSettings} />
      <ConsentSection settings={settings} onSaved={setSettings} />
      <CredentialsSection
        settings={settings}
        onSaved={setSettings}
        creds={creds}
        onCredsChange={setCreds}
        show={show}
      />
    </div>
  );
}

function GeneralSection({
  settings,
  onSaved,
}: {
  settings: SettingsType;
  onSaved: (s: SettingsType) => void;
}) {
  const { show } = useToast();
  const [form, setForm] = useState(settings);
  const [saving, setSaving] = useState(false);

  async function handleSubmit() {
    setSaving(true);
    try {
      const res = await api.post<{ settings: SettingsType }>("/api/settings/general", form);
      onSaved(res.settings);
      show("Settings saved.", "success");
    } catch (err) {
      show(err instanceof ApiClientError ? err.body?.error ?? err.message : "Could not save.", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardTitle>Targeting &amp; behaviour</CardTitle>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Minimum match score (0–100)" htmlFor="minMatchScore">
          <Input
            id="minMatchScore"
            type="number"
            min={0}
            max={100}
            value={form.minMatchScore}
            onChange={(e) => setForm({ ...form, minMatchScore: Number(e.target.value) })}
          />
        </Field>
        <Field label="Minimum salary (0 = any)" htmlFor="minSalary">
          <Input
            id="minSalary"
            type="number"
            min={0}
            value={form.minSalary}
            onChange={(e) => setForm({ ...form, minSalary: Number(e.target.value) })}
          />
        </Field>
        <Field label="Target locations (comma-separated)" htmlFor="targetCountries">
          <Input
            id="targetCountries"
            value={form.targetCountries}
            onChange={(e) => setForm({ ...form, targetCountries: e.target.value })}
          />
        </Field>
        <Field label="Blacklist keywords (comma-separated)" htmlFor="blacklistKeywords">
          <Input
            id="blacklistKeywords"
            value={form.blacklistKeywords}
            onChange={(e) => setForm({ ...form, blacklistKeywords: e.target.value })}
          />
        </Field>
        <Field label="Follow up after (days)" htmlFor="followUpDays">
          <Input
            id="followUpDays"
            type="number"
            min={1}
            max={60}
            value={form.followUpDays}
            onChange={(e) => setForm({ ...form, followUpDays: Number(e.target.value) })}
          />
        </Field>
        <Field label="Max emails to send per day" htmlFor="emailDailyLimit">
          <Input
            id="emailDailyLimit"
            type="number"
            min={1}
            max={1000}
            value={form.emailDailyLimit}
            onChange={(e) => setForm({ ...form, emailDailyLimit: Number(e.target.value) })}
          />
        </Field>
        <Field label="Don't re-email the same contact within (days)" htmlFor="dedupWindowDays">
          <Input
            id="dedupWindowDays"
            type="number"
            min={1}
            max={365}
            value={form.dedupWindowDays}
            onChange={(e) => setForm({ ...form, dedupWindowDays: Number(e.target.value) })}
          />
        </Field>
      </div>

      <div className="mt-2 divide-y divide-border">
        <SwitchField
          id="remoteOnly"
          label="Remote roles only"
          checked={form.remoteOnly}
          onCheckedChange={(v) => setForm({ ...form, remoteOnly: v })}
        />
        <SwitchField
          id="followUpEnabled"
          label="Send one follow-up if no reply"
          checked={form.followUpEnabled}
          onCheckedChange={(v) => setForm({ ...form, followUpEnabled: v })}
        />
        <SwitchField
          id="generateDocsWithoutHr"
          label="Prepare documents even when no recruiter contact is found"
          checked={form.generateDocsWithoutHr}
          onCheckedChange={(v) => setForm({ ...form, generateDocsWithoutHr: v })}
        />
        <SwitchField
          id="scheduleEnabled"
          label="Run automatically on a schedule"
          checked={form.scheduleEnabled}
          onCheckedChange={(v) => setForm({ ...form, scheduleEnabled: v })}
        />
      </div>

      <Button className="mt-4" onClick={handleSubmit} loading={saving}>
        Save settings
      </Button>
    </Card>
  );
}

function ConsentSection({
  settings,
  onSaved,
}: {
  settings: SettingsType;
  onSaved: (s: SettingsType) => void;
}) {
  const { show } = useToast();
  const [consent, setConsent] = useState(settings.hasSendingConsent);
  const [autoSend, setAutoSend] = useState(settings.autoSend);
  const [saving, setSaving] = useState(false);

  async function handleSubmit() {
    setSaving(true);
    try {
      const res = await api.post<{ settings: SettingsType }>("/api/settings/sending-consent", {
        consent,
        autoSend,
      });
      onSaved(res.settings);
      setConsent(res.settings.hasSendingConsent);
      setAutoSend(res.settings.autoSend);
      show(
        consent ? "Auto-send preferences updated." : "Auto-send disabled. AutoJob won't send anything on your behalf.",
        consent ? "success" : "warning"
      );
    } catch (err) {
      show(err instanceof ApiClientError ? err.body?.error ?? err.message : "Could not update.", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardTitle>Auto-send consent</CardTitle>
      <p className="mb-2 text-sm text-muted-foreground">
        AutoJob will only email recruiters on your behalf after you explicitly opt in. You stay in
        control and can turn this off anytime.
      </p>
      <div className="divide-y divide-border">
        <SwitchField
          id="consent"
          label="I authorise AutoJob to send applications from my configured sender"
          checked={consent}
          onCheckedChange={setConsent}
        />
        <SwitchField
          id="autoSend"
          label="Send automatically during each run (otherwise I'll review first)"
          checked={autoSend}
          onCheckedChange={setAutoSend}
          disabled={!consent}
        />
      </div>
      <Button className="mt-4" onClick={handleSubmit} loading={saving}>
        Update
      </Button>
    </Card>
  );
}

function CredentialsSection({
  settings,
  onSaved,
  creds,
  onCredsChange,
  show,
}: {
  settings: SettingsType;
  onSaved: (s: SettingsType) => void;
  creds: CredentialMap;
  onCredsChange: (c: CredentialMap) => void;
  show: (message: string, type?: "success" | "error" | "warning" | "info") => void;
}) {
  const [useOwnApiKeys, setUseOwnApiKeys] = useState(settings.useOwnApiKeys);
  const [aiProvider, setAiProvider] = useState<AiProvider>(
    (settings.aiProvider as AiProvider) || "groq"
  );
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  function field(key: string) {
    return values[key] ?? "";
  }
  function setField(key: string, v: string) {
    setValues((prev) => ({ ...prev, [key]: v }));
  }

  async function handleSubmit() {
    setSaving(true);
    try {
      const res = await api.post<{ settings: SettingsType; credentials: CredentialMap }>(
        "/api/settings/credentials",
        {
          useOwnApiKeys,
          aiProvider,
          groq: field("groq"),
          openai: field("openai"),
          anthropic: field("anthropic"),
          gemini: field("gemini"),
          grok: field("grok"),
          openrouter: field("openrouter"),
          hunter: field("hunter"),
          prospeo: field("prospeo"),
          reoon: field("reoon"),
          million: field("million"),
          smtpHost: field("smtpHost"),
          smtpPort: field("smtpPort"),
          smtpUser: field("smtpUser"),
          smtpFrom: field("smtpFrom"),
          smtpFromName: field("smtpFromName"),
          smtpPassword: field("smtpPassword"),
          imapHost: field("imapHost"),
          imapPort: field("imapPort"),
          imapUser: field("imapUser"),
          imapPassword: field("imapPassword"),
        }
      );
      onSaved(res.settings);
      onCredsChange(res.credentials);
      setValues({});
      show("Credentials saved securely.", "success");
    } catch (err) {
      show(err instanceof ApiClientError ? err.body?.error ?? err.message : "Could not save.", "error");
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove(provider: string) {
    try {
      const res = await api.post<{ credentials: CredentialMap }>(
        `/api/settings/credentials/${provider}/remove`
      );
      onCredsChange(res.credentials);
      show(`Removed your saved ${provider} credential.`, "success");
    } catch (err) {
      show(err instanceof ApiClientError ? err.body?.error ?? err.message : "Could not remove.", "error");
    }
  }

  function CredBadge({ provider, label }: { provider: string; label: string }) {
    if (!creds[provider]) return null;
    return (
      <span className="ml-2 inline-flex items-center gap-1.5">
        <Badge variant="success">✓ set</Badge>
        <ConfirmDialog
          trigger={
            <button type="button" className="cursor-pointer text-xs font-semibold text-destructive hover:underline">
              Remove
            </button>
          }
          title={`Remove your saved ${label} credential?`}
          description="You can add a new one anytime."
          confirmLabel="Remove"
          onConfirm={() => handleRemove(provider)}
        />
      </span>
    );
  }

  return (
    <Card>
      <CardTitle>Credentials</CardTitle>
      <p className="mb-4 text-sm text-muted-foreground">
        Optional. Leave blank to use AutoJob's managed service where available. Stored encrypted —
        we never show these back to you.
      </p>

      <SwitchField
        id="useOwnApiKeys"
        label="Use my own AI & enrichment keys below"
        description="Uncheck to always use AutoJob's managed defaults for these instead — even if you have keys saved below (handy if a saved key stops working and you want a quick fallback without deleting it)."
        checked={useOwnApiKeys}
        onCheckedChange={setUseOwnApiKeys}
      />

      <div className="mt-4">
        <Field label="AI provider (tailors your CV/cover letter, scores jobs)" htmlFor="aiProvider">
          <select
            id="aiProvider"
            value={aiProvider}
            onChange={(e) => setAiProvider(e.target.value as AiProvider)}
            className="h-11 w-full rounded-md border border-border bg-card px-3 text-[15px] text-foreground"
          >
            {AI_PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {AI_PROVIDER_LABELS[p]}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        Add a key for whichever provider you pick below — or leave it blank to use AutoJob's
        managed default where the operator has configured one.
      </p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        {AI_PROVIDER_FIELDS.map((f) => (
          <Field
            key={f.key}
            label={
              <>
                {f.label}
                {f.key === aiProvider && <Badge variant="info" className="ml-2">active</Badge>}
                <CredBadge provider={f.key} label={f.label} />
              </>
            }
            htmlFor={f.key}
          >
            <PasswordInput
              id={f.key}
              autoComplete="off"
              placeholder={creds[f.key] ? "•••••• (leave blank to keep)" : f.placeholder}
              value={field(f.key)}
              onChange={(e) => setField(f.key, e.target.value)}
            />
          </Field>
        ))}
      </div>

      <h3 className="mt-6 mb-2 font-bold text-card-foreground">Contact enrichment</h3>
      <div className="grid gap-4 sm:grid-cols-2">
        {CREDENTIAL_FIELDS.map((f) => (
          <Field
            key={f.key}
            label={
              <>
                {f.label}
                <CredBadge provider={f.key} label={f.label} />
              </>
            }
            htmlFor={f.key}
          >
            <PasswordInput
              id={f.key}
              autoComplete="off"
              placeholder={creds[f.key] ? "•••••• (leave blank to keep)" : f.placeholder}
              value={field(f.key)}
              onChange={(e) => setField(f.key, e.target.value)}
            />
          </Field>
        ))}
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        Leave any blank to use AutoJob's managed contact-finding where available.
      </p>

      <h3 className="mt-6 mb-2 font-bold text-card-foreground">
        Sending (SMTP)
        <CredBadge provider="smtp" label="SMTP" />
      </h3>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="SMTP host" htmlFor="smtpHost">
          <Input id="smtpHost" placeholder="smtp.provider.com" value={field("smtpHost")} onChange={(e) => setField("smtpHost", e.target.value)} />
        </Field>
        <Field label="Port" htmlFor="smtpPort">
          <Input id="smtpPort" placeholder="587" value={field("smtpPort")} onChange={(e) => setField("smtpPort", e.target.value)} />
        </Field>
        <Field label="Username" htmlFor="smtpUser">
          <Input id="smtpUser" autoComplete="off" value={field("smtpUser")} onChange={(e) => setField("smtpUser", e.target.value)} />
        </Field>
        <Field label="From address" htmlFor="smtpFrom">
          <Input id="smtpFrom" type="email" autoComplete="off" value={field("smtpFrom")} onChange={(e) => setField("smtpFrom", e.target.value)} />
        </Field>
        <Field label="From name" htmlFor="smtpFromName">
          <Input id="smtpFromName" autoComplete="off" placeholder="Your Name" value={field("smtpFromName")} onChange={(e) => setField("smtpFromName", e.target.value)} />
        </Field>
        <Field label="Password / App password" htmlFor="smtpPassword">
          <PasswordInput
            id="smtpPassword"
            autoComplete="off"
            placeholder={creds.smtp ? "••••••" : ""}
            value={field("smtpPassword")}
            onChange={(e) => setField("smtpPassword", e.target.value)}
          />
        </Field>
      </div>

      <h3 className="mt-6 mb-2 font-bold text-card-foreground">
        Reply detection (IMAP)
        <CredBadge provider="imap" label="IMAP" />
      </h3>
      <p className="mb-2 text-sm text-muted-foreground">
        Used to detect recruiter replies (Gmail, Outlook, or any IMAP mailbox — use an app password).
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="IMAP host" htmlFor="imapHost">
          <Input id="imapHost" placeholder="imap.gmail.com" value={field("imapHost")} onChange={(e) => setField("imapHost", e.target.value)} />
        </Field>
        <Field label="Port" htmlFor="imapPort">
          <Input id="imapPort" placeholder="993" value={field("imapPort")} onChange={(e) => setField("imapPort", e.target.value)} />
        </Field>
        <Field label="Username" htmlFor="imapUser">
          <Input id="imapUser" autoComplete="off" value={field("imapUser")} onChange={(e) => setField("imapUser", e.target.value)} />
        </Field>
        <Field label="Password / App password" htmlFor="imapPassword">
          <PasswordInput
            id="imapPassword"
            autoComplete="off"
            placeholder={creds.imap ? "••••••" : ""}
            value={field("imapPassword")}
            onChange={(e) => setField("imapPassword", e.target.value)}
          />
        </Field>
      </div>

      <Button className="mt-4" onClick={handleSubmit} loading={saving}>
        Save credentials
      </Button>
    </Card>
  );
}
