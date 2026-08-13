import { Link } from "react-router-dom";
import { Upload, Search, FileCheck2, Send, ShieldCheck, Lock, RefreshCw } from "lucide-react";
import { PublicHeader } from "@/components/PublicHeader";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const STEPS = [
  {
    icon: Upload,
    title: "Upload your CV",
    body: "PDF, DOCX, or TXT. AutoJob parses it and shows exactly what it understood — your roles, seniority, and skills.",
  },
  {
    icon: Search,
    title: "It finds matching roles",
    body: "Scans public job boards and scores every posting against your CV, so you only see roles worth applying to.",
  },
  {
    icon: FileCheck2,
    title: "Tailors an application",
    body: "A fresh CV and cover letter, generated per company — not one generic copy sent everywhere.",
  },
  {
    icon: Send,
    title: "Sends it from your inbox",
    body: "With your explicit consent, from your own mailbox — never a shared sender that gets flagged as spam.",
  },
];

const FEATURES = [
  {
    icon: Lock,
    title: "Your data stays yours",
    body: "Every account is isolated. Your CV, credentials, and job history are never visible to another user.",
  },
  {
    icon: ShieldCheck,
    title: "Encrypted credentials",
    body: "SMTP passwords and API keys are encrypted at rest and only ever decrypted in memory during a run.",
  },
  {
    icon: RefreshCw,
    title: "Never applies twice",
    body: "AutoJob remembers every job it's touched, so re-running never spams the same recruiter.",
  },
];

export function Landing() {
  return (
    <div className="min-h-dvh bg-background">
      <PublicHeader />

      <section className="mx-auto max-w-4xl px-4 py-20 text-center sm:py-28">
        <h1 className="text-4xl font-extrabold tracking-tight text-foreground sm:text-5xl">
          Upload your CV once.
          <br />
          <span className="text-primary">AutoJob applies for you.</span>
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-lg text-muted-foreground">
          It finds real roles that match your CV, tailors an application for each, sends it from
          your own mailbox, and follows up — all kept private to your account.
        </p>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Button asChild size="md">
            <Link to="/register">Upload your CV — it's free</Link>
          </Button>
          <Button asChild variant="outline" size="md">
            <Link to="/login">Sign in</Link>
          </Button>
        </div>
      </section>

      <section id="how" className="border-t border-border bg-card/50 py-20">
        <div className="mx-auto max-w-6xl px-4">
          <h2 className="text-center text-2xl font-extrabold text-foreground">How it works</h2>
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((step, i) => (
              <Card key={step.title} className="relative">
                <span className="absolute -top-3 -left-1 flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
                  {i + 1}
                </span>
                <step.icon className="h-6 w-6 text-primary" aria-hidden="true" />
                <h3 className="mt-3 font-bold text-card-foreground">{step.title}</h3>
                <p className="mt-1.5 text-sm text-muted-foreground">{step.body}</p>
              </Card>
            ))}
          </div>
        </div>
      </section>

      <section id="features" className="py-20">
        <div className="mx-auto max-w-6xl px-4">
          <h2 className="text-center text-2xl font-extrabold text-foreground">
            Built to be trusted with your job search
          </h2>
          <div className="mt-10 grid gap-4 sm:grid-cols-3">
            {FEATURES.map((f) => (
              <Card key={f.title}>
                <f.icon className="h-6 w-6 text-accent" aria-hidden="true" />
                <h3 className="mt-3 font-bold text-card-foreground">{f.title}</h3>
                <p className="mt-1.5 text-sm text-muted-foreground">{f.body}</p>
              </Card>
            ))}
          </div>
        </div>
      </section>

      <footer className="border-t border-border py-8">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 px-4 text-sm text-muted-foreground sm:flex-row">
          <span>&copy; {new Date().getFullYear()} AutoJob. Built to get you hired.</span>
          <div className="flex gap-4">
            <a href="#how" className="hover:text-foreground">
              How it works
            </a>
            <a href="#features" className="hover:text-foreground">
              Features
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
