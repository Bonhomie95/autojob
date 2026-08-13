import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PublicHeader } from "@/components/PublicHeader";
import { Button } from "@/components/ui/button";
import { Input, PasswordInput } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { useAuth, ApiClientError } from "@/auth/AuthContext";

export function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [acceptTerms, setAcceptTerms] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setErrors({});
    setBusy(true);
    try {
      await register({ name, email, password, confirm, accept_terms: acceptTerms });
      navigate("/app", { replace: true });
    } catch (err) {
      if (err instanceof ApiClientError) {
        setErrors(err.body?.errors ?? { email: err.body?.error ?? "Something went wrong." });
      } else {
        setErrors({ email: "Something went wrong." });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-dvh bg-background">
      <PublicHeader />
      <div className="mx-auto flex max-w-md flex-col px-4 py-16">
        <h1 className="text-2xl font-extrabold text-foreground">Create your account</h1>
        <p className="mt-1 text-muted-foreground">Start applying automatically in minutes.</p>

        <form onSubmit={handleSubmit} className="mt-8 space-y-5" noValidate>
          <Field label="Full name" htmlFor="name" error={errors.name}>
            <Input id="name" autoComplete="name" required value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Work email" htmlFor="email" error={errors.email}>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </Field>
          <Field
            label="Password"
            htmlFor="password"
            error={errors.password}
            helper="At least 8 characters, with a letter and a number."
          >
            <PasswordInput
              id="password"
              autoComplete="new-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <Field label="Confirm password" htmlFor="confirm" error={errors.confirm}>
            <PasswordInput
              id="confirm"
              autoComplete="new-password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </Field>
          <div>
            <div className="flex items-start gap-2.5">
              <Checkbox
                id="accept_terms"
                checked={acceptTerms}
                onCheckedChange={(v) => setAcceptTerms(v === true)}
              />
              <label htmlFor="accept_terms" className="cursor-pointer text-sm text-foreground">
                I agree to the Terms and Privacy Policy
              </label>
            </div>
            {errors.accept_terms && (
              <p role="alert" className="mt-1.5 text-sm font-medium text-destructive">
                {errors.accept_terms}
              </p>
            )}
          </div>
          <Button type="submit" variant="accent" className="w-full" loading={busy}>
            Create account
          </Button>
        </form>

        <p className="mt-6 text-center text-sm text-muted-foreground">
          Already have an account?{" "}
          <Link to="/login" className="font-semibold text-primary hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
