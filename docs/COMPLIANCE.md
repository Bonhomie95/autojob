# Compliance & Deliverability

AutoJob sends email on users' behalf and aggregates public job postings. As a
commercial multi-tenant product, that carries legal and deliverability
obligations the original single-user tool could ignore. This document is the
checklist; it is guidance, not legal advice — get a lawyer before launch.

## 1. Sending email on a user's behalf

**Consent gate (implemented).** AutoJob never sends until the user explicitly
opts in. `UserSettings.sending_consent_at` records the opt-in timestamp, and the
pipeline checks `RuntimeConfig.has_sending_consent` before any send. Auto-send is
off by default.

**CAN-SPAM / GDPR / CASL requirements before enabling sending at scale:**

- **Accurate headers & identity** — the From/Reply-To must be a real, monitored
  mailbox belonging to the user (or a routed alias back to them).
- **Unsubscribe / opt-out** — every application email must offer a way for the
  recipient to opt out, and opt-outs must be honoured within 10 business days.
  Maintain a per-recipient suppression list and check it before every send.
- **Physical postal address** — CAN-SPAM requires a valid physical address in
  commercial email.
- **No deception** — subject lines must not mislead.
- **Lawful basis (GDPR)** — recruiters are data subjects; document the
  legitimate-interest basis and honour erasure/objection requests.

The `dedup` guard (30-day window, per recipient) already prevents re-contacting
the same address repeatedly — extend it into a true suppression list keyed on
opt-outs and bounces.

## 2. Deliverability (so mail doesn't land in spam)

Cold outreach at volume destroys sender reputation unless configured correctly:

- **SPF, DKIM, DMARC** on the sending domain — all three, aligned. Without them,
  volume mail is filtered or rejected.
- **Dedicated sending domain/subdomain** separate from the user's primary domain,
  so reputation damage is contained.
- **IP / domain warm-up** — ramp send volume gradually over weeks.
- **Per-user daily caps** — `UserSettings.email_daily_limit` enforces a ceiling;
  keep it conservative.
- **Bounce & complaint handling** — process bounces and feedback loops; pause a
  sender automatically when complaint rates rise.

The recommended production model is a **managed shared sending service**: AutoJob
owns a warmed, authenticated domain and routes replies back to each user, rather
than asking every user to configure SMTP. This is also what makes "just upload a
CV" possible.

## 3. Job-board sourcing (ToS)

The tenant pipeline (`autojob/tasks.py::_scrapers`) uses only public API / RSS
sources: **RemoteOK, WeWorkRemotely, Jobicy, Remotive, Arbeitnow, HackerNews**.

**LinkedIn and Indeed scrapers from the legacy engine are intentionally excluded**
— automated scraping violates their Terms of Service and is a real legal risk for
a commercial product. To include them, use official/licensed APIs or a partner
job feed, not the HTML scrapers.

## 4. Data protection

- Per-user secrets are encrypted at rest (Fernet, `services/crypto.py`).
- Tenant isolation is enforced in the data-access layer and covered by tests
  (`tests/test_tenancy.py`).
- Provide account deletion that cascades (the models use `ondelete="CASCADE"`).
- Uploaded CVs contain personal data — set a retention policy and honour
  deletion/export (GDPR/CCPA data-subject rights).

## 5. Portal auto-submit

Submitting applications into third-party portals on a user's behalf should
require explicit per-application consent to be defensible, and must respect each
portal's ToS. Treat this as opt-in and auditable.
