export interface User {
  id: string;
  email: string;
  name: string;
  plan: string;
  isAdmin: boolean;
  emailVerified: boolean;
}

export interface DashboardStats {
  total: number;
  done: number;
  skipped: number;
  emails_sent: number;
  portal_submitted: number;
  replies: number;
  follow_ups: number;
  by_board: Record<string, number>;
}

export interface AwaitingJob {
  id: string;
  title: string;
  company: string;
  hrEmail: string;
}

export interface DashboardSettings {
  autoSend: boolean;
  followUpEnabled: boolean;
  minMatchScore: number;
}

export interface DashboardData {
  settings: DashboardSettings;
  stats: DashboardStats;
  awaiting: AwaitingJob[];
  hasCv: boolean;
  activeRunId: string | null;
}

export interface CvDoc {
  filename: string;
  sizeBytes: number;
}

export interface ContactChoice {
  value: string;
  options: string[];
}

export interface CvProfile {
  name?: string;
  seniority?: string;
  years_experience?: number;
  skills?: string[];
  titles?: string[];
  experience?: unknown[];
  projects?: unknown[];
  contact_choices?: Record<string, ContactChoice>;
  ambiguous_fields?: string[];
  issues?: string[];
  [key: string]: unknown;
}

export interface CvBundle {
  doc: CvDoc | null;
  profile: CvProfile | null;
  sendable: boolean;
  blockers: string[];
}

export interface Job {
  id: string;
  title: string;
  company: string;
  location: string;
  url: string;
  description: string;
  salary: string;
  source: string;
  score: number;
  hrName: string;
  hrEmail: string;
  hrTitle: string;
  applicationEmail: string;
  applicationUrl: string;
  contactNotes: string;
  status: string;
  emailStatus: string;
  emailError: string;
  followUpStatus: string;
  replyDetected: boolean;
  bounced: boolean;
  portalStatus: string;
  createdAt: string | null;
  hasDocuments: boolean;
  files?: string[];
}

export interface Settings {
  blacklistKeywords: string;
  minMatchScore: number;
  minSalary: number;
  remoteOnly: boolean;
  targetCountries: string;
  autoSend: boolean;
  generateDocsWithoutHr: boolean;
  followUpEnabled: boolean;
  followUpDays: number;
  emailDailyLimit: number;
  dedupWindowDays: number;
  useOwnApiKeys: boolean;
  aiProvider: string;
  scheduleEnabled: boolean;
  hasSendingConsent: boolean;
}

export const AI_PROVIDERS = ["groq", "openai", "anthropic", "gemini", "grok", "openrouter"] as const;
export type AiProvider = (typeof AI_PROVIDERS)[number];

export type CredentialMap = Record<string, boolean>;

export interface ApiError {
  error?: string;
  errors?: Record<string, string>;
}
