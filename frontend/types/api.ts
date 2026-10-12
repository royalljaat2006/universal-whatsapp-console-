// Response contracts of the backend REST API (/api/v1). These mirror the
// backend serializers; the OpenAPI schema at /api/v1/schema/ is the source of
// truth and can later generate these types (see docs/api.md).

export type UUID = string;
export type ISODateTime = string;

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
    request_id: string | null;
  };
}

export interface CursorPage<T> {
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface User {
  id: UUID;
  email: string;
  full_name: string;
  is_staff: boolean;
  is_email_verified: boolean;
  mfa_enabled: boolean;
}

/** Returned by sign-in when the password was right but a second factor is still needed. */
export interface MfaChallenge {
  mfa_required: true;
}

export interface MfaStatus {
  enabled: boolean;
  recovery_codes_remaining: number;
  required_for_staff: boolean;
}

export type ConnectionHealthState = "ok" | "warning" | "problem" | "unknown";
export type ConnectionHealthOverall = "healthy" | "attention" | "problem" | "unknown";

export interface ConnectionHealthCheck {
  code: string;
  state: ConnectionHealthState;
  title: string;
  detail: string;
  next_step: string;
  /** Where this can be fixed: an action in this console, only in Meta's own tools, or nowhere. */
  fix_in: "console" | "meta" | "none";
  /** Whether the signal came from Meta or from this console's own threshold. */
  basis: "provider" | "console";
  /** "refresh" = re-reading Meta can help right now. */
  action: string;
}

export interface ConnectionHealthAccountRow {
  id: UUID;
  name: string;
  business_id: UUID;
  business_name: string;
  overall: ConnectionHealthOverall;
  last_checked_at: ISODateTime | null;
  checks: ConnectionHealthCheck[];
}

export interface ConnectionHealthNumberRow {
  id: UUID;
  label: string;
  display_phone_number: string;
  verified_name: string;
  business_id: UUID;
  business_name: string;
  business_account_id: UUID;
  overall: ConnectionHealthOverall;
  last_checked_at: ISODateTime | null;
  checks: ConnectionHealthCheck[];
}

export interface ConnectionHealth {
  generated_at: ISODateTime;
  overall: ConnectionHealthOverall;
  summary: string;
  accounts: ConnectionHealthAccountRow[];
  numbers: ConnectionHealthNumberRow[];
  recent_alerts: {
    id: UUID;
    severity: "info" | "warning" | "critical";
    summary: string;
    occurred_at: ISODateTime;
    business_name: string;
  }[];
  notes: string[];
}

export interface MfaEnrolment {
  secret: string;
  otpauth_uri: string;
}

export interface OrganizationSummary {
  id: UUID;
  name: string;
  slug: string;
  status: "active" | "suspended";
  timezone: string;
}

export interface MyMembership {
  id: UUID;
  organization: OrganizationSummary;
  role: string;
  status: string;
  permissions: string[];
}

export interface UsageSummary {
  members: number;
  businesses: number;
  whatsapp_business_accounts: number;
  phone_numbers: number;
  contacts: number;
  messages_this_period: number;
  period_start: ISODateTime;
}

export interface Role {
  id: UUID;
  key: string;
  name: string;
  description: string;
  is_system: boolean;
  permissions: string[];
  created_at: ISODateTime;
}

export interface PermissionCatalogueEntry {
  code: string;
  description: string;
}

export interface ApiKey {
  id: UUID;
  name: string;
  key_prefix: string;
  scopes: string[];
  created_by_email: string;
  is_active: boolean;
  expires_at: ISODateTime | null;
  last_used_at: ISODateTime | null;
  revoked_at: ISODateTime | null;
  created_at: ISODateTime;
}

/** Only the create response ever carries the raw key — shown once, never
 * retrievable again (the backend stores only its hash). */
export interface CreatedApiKey extends ApiKey {
  key: string;
}

export interface Business {
  id: UUID;
  name: string;
  slug: string;
  description: string;
  status: "active" | "suspended" | "archived";
  timezone: string;
  business_account_count: number;
  phone_number_count: number;
  created_at: ISODateTime;
}

export type ConnectionState = "pending" | "connected" | "error" | "disconnected";
export type SyncState = "never" | "syncing" | "succeeded" | "failed";

export interface BusinessAccount {
  id: UUID;
  business_id: UUID;
  business_name: string;
  provider: string;
  external_id: string;
  business_portfolio_external_id: string;
  name: string;
  status: "active" | "disconnected" | "restricted";
  connection_state: ConnectionState;
  connection_error: string;
  connection_error_code: string;
  last_verified_at: ISODateTime | null;
  sync_state: SyncState;
  sync_error: string;
  last_synced_at: ISODateTime | null;
  template_sync_state: SyncState;
  template_sync_error: string;
  templates_last_synced_at: ISODateTime | null;
  webhooks_subscribed: boolean;
  account_review_status?: string;
  business_verification_status?: string;
  phone_number_count: number;
  created_at: ISODateTime;
}

export interface EmbeddedSignupConfig {
  app_id: string;
  config_id: string;
  graph_api_version: string;
  /** False if META_APP_ID or META_EMBEDDED_SIGNUP_CONFIG_ID isn't set yet. */
  configured: boolean;
}

export interface PhoneNumber {
  id: UUID;
  business_id: UUID;
  business_account_id: UUID;
  business_account_external_id: string;
  business_account_name: string;
  business_account_connection_state: ConnectionState;
  business_account_sync_state: SyncState;
  provider: string;
  external_id: string;
  display_phone_number: string;
  verified_name: string;
  label: string;
  status: "pending" | "connected" | "disconnected" | "flagged" | "restricted";
  provider_status: string;
  quality_rating: string;
  name_status: string;
  messaging_limit_tier: string;
  last_synced_at: ISODateTime | null;
  last_error: string;
  created_at: ISODateTime;
}

export type ConversationStatus = "open" | "pending" | "resolved" | "archived";

export interface Conversation {
  id: UUID;
  business_id: UUID;
  contact_id: UUID;
  contact_name: string;
  contact_phone_number: string;
  phone_number_id: UUID;
  phone_number_display: string;
  status: ConversationStatus;
  unread_count: number;
  last_message_at: ISODateTime | null;
  last_inbound_at: ISODateTime | null;
  last_message_preview: string;
  last_message_direction: "inbound" | "outbound" | "";
  service_window_expires_at: ISODateTime | null;
  service_window_open: boolean;
  created_at: ISODateTime;
}

export type MessageStatus =
  "queued" | "sending" | "accepted" | "sent" | "delivered" | "read" | "failed" | "received";

export interface Message {
  id: UUID;
  business_id: UUID;
  conversation_id: UUID;
  phone_number_id: UUID;
  contact_id: UUID;
  direction: "inbound" | "outbound";
  message_type: string;
  status: MessageStatus;
  text: string;
  /** Not part of the Cycle 2 API payload; kept optional for older responses. */
  content?: Record<string, unknown>;
  external_id: string | null;
  occurred_at: ISODateTime;
  accepted_at?: ISODateTime | null;
  sent_at: ISODateTime | null;
  delivered_at: ISODateTime | null;
  read_at: ISODateTime | null;
  failed_at: ISODateTime | null;
  error_code: string;
  error_message: string;
  created_at: ISODateTime;
}

export interface HealthCheck {
  ok: boolean;
  critical: boolean;
  duration_ms: number;
  detail?: string;
}

export interface HealthReport {
  status: "ok" | "unavailable";
  checks: Record<string, HealthCheck>;
}

export type RealtimeEventName =
  | "connection.ready"
  | "pong"
  | "message.created"
  | "message.status_changed"
  | "message.media_ready"
  | "conversation.created"
  | "conversation.updated"
  | "conversation.assigned"
  | "conversation.tags_changed"
  | "contact.updated"
  | "number.status_changed"
  | "account.alert"
  | "campaign.updated"
  | "campaign.progress"
  | "notification.created";

export interface RealtimeMessageEvent {
  id: UUID;
  business_id: UUID;
  conversation_id: UUID;
  phone_number_id: UUID;
  direction: "inbound" | "outbound";
  status: MessageStatus;
  message_type?: string;
  error_code?: string;
  occurred_at?: ISODateTime;
  attachment_id?: UUID;
  storage_status?: Attachment["storage_status"];
}

export interface RealtimeConversationEvent {
  id: UUID;
  business_id: UUID;
  phone_number_id: UUID;
  status: ConversationStatus;
  unread_count: number;
}

export interface RealtimeEvent<T = Record<string, unknown>> {
  event: RealtimeEventName;
  data?: T;
  emitted_at?: ISODateTime;
}

// ---------------------------------------------------------------------------
// Build Cycle 2
// ---------------------------------------------------------------------------

export interface TagBrief {
  id: UUID;
  name: string;
  color: string;
}

export interface Tag extends TagBrief {
  business_id: UUID;
}

export interface SendPolicy {
  free_form_allowed: boolean;
  template_allowed: boolean;
  window_open: boolean;
  window_expires_at: ISODateTime | null;
  reason: string;
}

export interface Assignee {
  membership_id: UUID;
  name: string;
  email: string;
}

export interface InboxConversation extends Conversation {
  assignee: Assignee | null;
  team: { id: UUID; name: string } | null;
  tags: TagBrief[];
  send_policy: SendPolicy;
}

export interface Attachment {
  id: UUID;
  role: "body" | "header";
  kind: string;
  mime_type: string;
  file_size: number | null;
  filename: string;
  caption: string;
  storage_status: "pending" | "stored" | "failed";
  last_error: string;
  media_asset_id: UUID | null;
  content_url: string | null;
}

export interface InboxMessage extends Message {
  business_account_id: UUID | null;
  template_id: UUID | null;
  reply_to_external_id: string;
  billable: boolean | null;
  pricing_category: string;
  template: { name: string; language: string; category: string; parameters?: unknown } | null;
  attachments: Attachment[];
  cost_status: "none" | "free" | "pending_rate" | "priced";
  cost: { amount: string; currency: string; category: string } | null;
}

export interface Contact {
  id: UUID;
  business_id: UUID;
  display_name: string;
  profile_name: string;
  phone_number: string;
  country_code: string;
  attributes: Record<string, string>;
  opt_in_status: "unknown" | "opted_in" | "opted_out";
  opt_in_source: string;
  identities: { kind: string; value: string }[];
  tags: Tag[];
  conversation_count: number | null;
  first_seen_at: ISODateTime;
  last_message_at: ISODateTime | null;
  last_inbound_at: ISODateTime | null;
  erased_at: ISODateTime | null;
  updated_at: ISODateTime;
}

export interface ContactImportJob {
  id: UUID;
  business_id: UUID;
  filename: string;
  tag_name: string;
  status: "pending" | "running" | "succeeded" | "succeeded_with_errors" | "failed";
  total_rows: number | null;
  created_count: number;
  updated_count: number;
  failed_count: number;
  row_errors: { row: number; error: string }[];
  error: string;
  started_at: ISODateTime | null;
  finished_at: ISODateTime | null;
  requested_by_email: string | null;
  created_at: ISODateTime;
}

export interface ContactNote {
  id: UUID;
  body: string;
  author_email: string | null;
  created_at: ISODateTime;
}

export interface AssignmentEntry {
  id: UUID;
  new_assignee_name: string | null;
  new_team_name: string | null;
  assigned_by_email: string | null;
  created_at: ISODateTime;
}

export interface AssigneeOptions {
  members: (Assignee & { role: string })[];
  teams: { id: UUID; name: string }[];
}

export interface TemplateParameterSpec {
  component: "header" | "body" | "button";
  key: string;
  kind: "text" | "image" | "video" | "document";
  example: string;
  button_index: number | null;
}

export interface MessageTemplate {
  id: UUID;
  business_id: UUID;
  business_account_id: UUID;
  business_account_external_id: string;
  name: string;
  language: string;
  category: string;
  status: string;
  sendable: boolean;
  parameter_format: string;
  quality_score: string;
  rejected_reason: string;
  missing_from_provider: boolean;
  last_synced_at: ISODateTime | null;
}

export interface MessageTemplateDetail extends MessageTemplate {
  components: Record<string, unknown>[];
  structure:
    | { supported: false; reason: string }
    | {
        supported: true;
        parameter_format: string;
        header_format: string | null;
        header_text: string;
        body_text: string;
        footer_text: string;
        buttons: { type: string; text: string; url?: string }[];
        parameters: TemplateParameterSpec[];
      };
}

export interface MediaAsset {
  id: UUID;
  business_id: UUID;
  kind: string;
  mime_type: string;
  file_size: number;
  original_filename: string;
  content_url: string;
}

export interface AccountEvent {
  id: UUID;
  business_id: UUID;
  business_account_id: UUID;
  phone_number_id: UUID | null;
  field: string;
  event: string;
  severity: "info" | "warning" | "critical";
  summary: string;
  payload: Record<string, unknown>;
  occurred_at: ISODateTime;
}

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

export interface Notification {
  id: UUID;
  kind: string;
  title: string;
  body: string;
  data: Record<string, unknown>;
  read_at: ISODateTime | null;
  created_at: ISODateTime;
}

export interface NotificationPreferences {
  muted_kinds: string[];
  available_kinds: Record<string, string>;
}

export interface Member {
  id: UUID;
  email: string;
  full_name: string;
  role: string;
  status: string;
  joined_at: ISODateTime | null;
  created_at: ISODateTime;
}

export interface AccessRow {
  membership_id: UUID;
  email: string;
  name: string;
  role: string;
  status: string;
  business_scope: "all" | "selected";
  has_access: boolean;
  access_via: "all_businesses" | "grant" | null;
  is_self: boolean;
  manageable: boolean;
}

// ---------------------------------------------------------------------------
// Build Cycle 3 — analytics, campaigns, exports
// ---------------------------------------------------------------------------

export interface AnalyticsRange {
  date_from: string;
  date_to: string;
  timezone: string;
}

export interface AnalyticsTotals {
  inbound: number;
  outbound: number;
  in_flight: number;
  accepted: number;
  sent: number;
  delivered: number;
  read: number;
  failed: number;
  rejected: number;
  templates_sent: number;
  media_sent: number;
  media_received: number;
  cost_priced: number;
  cost_pending_rate: number;
  cost_free: number;
}

/** null = no data (denominator 0), never a measured 0 %. */
export interface AnalyticsRates {
  delivery_rate: number | null;
  read_rate: number | null;
  failure_rate: number | null;
  inbound_outbound_ratio: number | null;
}

export interface CurrencyAmount {
  currency: string;
  amount: string;
  priced_messages?: number;
  messages?: number;
  average_per_message?: string | null;
}

export interface CostSummary {
  state: "none" | "pending_rate" | "partial" | "priced";
  by_currency: CurrencyAmount[];
  pending_rate_messages: number;
  free_messages: number;
  awaiting_pricing_report?: number;
}

export interface AnalyticsOverview {
  range: AnalyticsRange;
  has_data: boolean;
  totals: AnalyticsTotals;
  rates: AnalyticsRates;
  unique_contacts: number;
  active_conversations: number;
  resolved_conversations: number | null;
  cost: CostSummary;
  freshness: { computed_at: ISODateTime | null; pending_buckets: number };
}

export interface TimeseriesPoint {
  bucket: string;
  inbound: number;
  outbound: number;
  sent: number;
  delivered: number;
  read: number;
  failed: number;
  in_flight: number;
}

export interface AnalyticsTimeseries {
  range: AnalyticsRange;
  granularity: "hour" | "day";
  points: TimeseriesPoint[];
  cost: { date: string; currency: string; amount: string }[];
}

export interface NumberAnalytics {
  phone_number_id: UUID;
  business_id: UUID;
  display_phone_number: string;
  label: string;
  verified_name: string;
  status: string;
  quality_rating: string;
  messaging_limit_tier: string;
  business_account_external_id: string;
  has_data: boolean;
  totals: AnalyticsTotals;
  rates: AnalyticsRates;
  unique_contacts: number;
  active_conversations: number;
  templates_used: number;
  cost: CurrencyAmount[];
}

export interface TemplateAnalytics {
  template_id: UUID;
  name: string;
  language: string;
  category: string;
  status: string;
  sends: number;
  totals: AnalyticsTotals;
  rates: AnalyticsRates;
  unique_recipients: number;
  first_used: string | null;
  last_used: string | null;
  cost: CurrencyAmount[];
}

export type CostDimension =
  "business" | "business_account" | "phone_number" | "template" | "campaign" | "category" | "date";

export interface CostBreakdown {
  range: AnalyticsRange;
  group_by: CostDimension;
  rows: { key: string | null; label: string; currency: string; amount: string; messages: number }[];
}

export interface FailureGroup {
  category: string;
  label: string;
  retryable: boolean;
  count: number;
  codes?: { code: string; count: number }[];
  reasons?: { code: string; message: string; count: number }[];
}

export interface AnalyticsFailures {
  range: AnalyticsRange;
  total: number;
  groups: FailureGroup[];
  recent: {
    message_id: UUID;
    occurred_at: ISODateTime;
    phone_number: string;
    contact: string;
    template: string | null;
    campaign_id: UUID | null;
    error_code: string;
    error_message: string;
    category: string;
  }[];
}

export type CampaignStatus =
  "draft" | "scheduled" | "queued" | "running" | "paused" | "completed" | "cancelled" | "failed";

export type RecipientStatus =
  | "pending"
  | "queued"
  | "sending"
  | "accepted"
  | "sent"
  | "delivered"
  | "read"
  | "failed"
  | "skipped"
  | "cancelled";

export interface CampaignProgress {
  total: number;
  by_status: Record<RecipientStatus, number>;
  processed: number;
  in_flight: number;
  accepted_or_later: number;
  sent_or_later: number;
  delivered_or_later: number;
  read: number;
  failed: number;
  skipped: number;
  cancelled: number;
  pending: number;
}

export interface AudienceCondition {
  field: string;
  op: string;
  key?: string;
  value?: string | number;
}

export interface AudienceDefinition {
  match: "all" | "any";
  conditions: AudienceCondition[];
}

export interface VariableMappingEntry {
  source: string;
  key?: string;
  value?: string;
  fallback?: string;
  media_asset_id?: string;
}

export interface VariableMapping {
  header?: VariableMappingEntry;
  body?: Record<string, VariableMappingEntry>;
  buttons?: Record<string, VariableMappingEntry>;
}

export interface Campaign {
  id: UUID;
  business_id: UUID;
  name: string;
  description: string;
  status: CampaignStatus;
  phone_number_id: UUID;
  phone_number_display: string;
  template_id: UUID;
  template_name: string;
  template_language: string;
  template_category: string;
  audience_id: UUID | null;
  audience_definition: AudienceDefinition;
  variable_mapping: VariableMapping;
  max_messages_per_minute: number;
  scheduled_at: ISODateTime | null;
  timezone: string;
  metadata: Record<string, unknown>;
  audience_count: number | null;
  snapshot_built_at: ISODateTime | null;
  launched_at: ISODateTime | null;
  started_at: ISODateTime | null;
  paused_at: ISODateTime | null;
  completed_at: ISODateTime | null;
  cancelled_at: ISODateTime | null;
  failure_code: string;
  failure_reason: string;
  created_by_email: string | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
  progress: CampaignProgress;
}

export interface LaunchCheck {
  code: string;
  ok: boolean;
  severity: "error" | "warning";
  message: string;
}

export interface AudiencePreview {
  definition: AudienceDefinition;
  conditions: string[];
  match: "all" | "any";
  count: number;
  count_is_exact: boolean;
  reachable_contacts_in_business: number;
  excluded_always: string[];
  sample: { id: UUID; name: string; phone_number: string; opt_in_status: string }[];
}

export interface LaunchPreview {
  launchable: boolean;
  checks: LaunchCheck[];
  summary: Record<string, unknown> & {
    recipients?: number;
    rate_per_minute?: number;
    estimated_duration_minutes?: number | null;
  };
  audience?: AudiencePreview | { error: unknown };
}

export interface CampaignFailures {
  groups: FailureGroup[];
  skipped: { reason: string; count: number }[];
  retryable_recipients: number;
}

export interface CampaignReport {
  campaign: {
    id: UUID;
    name: string;
    status: CampaignStatus;
    business: string;
    phone_number: string;
    template: { id: UUID; name: string; language: string; category: string };
  };
  progress: CampaignProgress;
  cost: CostSummary;
  failures: CampaignFailures;
  timing: {
    launched_at: ISODateTime | null;
    started_at: ISODateTime | null;
    finished_at: ISODateTime | null;
    duration_seconds: number | null;
    running: boolean;
  };
  messages_total: number;
}

export interface CampaignRecipientRow {
  id: UUID;
  contact_id: UUID;
  contact_name: string;
  phone_number: string;
  status: RecipientStatus;
  status_changed_at: ISODateTime | null;
  attempts: number;
  skip_reason: string;
  error_code: string;
  error_message: string;
  failure_category: string;
  message_id: UUID | null;
  variables: Record<string, unknown>;
}

export interface CampaignEventRow {
  id: UUID;
  event: string;
  actor_email: string | null;
  data: Record<string, unknown>;
  created_at: ISODateTime;
}

export interface SavedAudience {
  id: UUID;
  business_id: UUID;
  name: string;
  description: string;
  definition: AudienceDefinition;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export type ExportKind =
  | "campaign_recipients"
  | "campaign_summary"
  | "messages"
  | "costs"
  | "analytics_daily"
  | "contacts";

export interface ExportJob {
  id: UUID;
  business_id: UUID;
  kind: ExportKind;
  params: Record<string, string>;
  status: "pending" | "running" | "succeeded" | "failed";
  row_count: number | null;
  file_size: number | null;
  filename: string;
  error: string;
  started_at: ISODateTime | null;
  finished_at: ISODateTime | null;
  requested_by_email: string | null;
  created_at: ISODateTime;
  download_url: string | null;
}

// --- Platform (super-admin) console — cross-tenant, is_staff only ----------

export interface PlatformOrganization {
  id: UUID;
  name: string;
  slug: string;
  status: "active" | "suspended";
  timezone: string;
  member_count: number;
  business_count: number;
  created_at: ISODateTime;
}

export type OrgRoleKey = "owner" | "admin" | "manager" | "agent" | "viewer";

export interface ProvisionedOrganization extends PlatformOrganization {
  invitation: {
    id: UUID;
    email: string;
    role: OrgRoleKey;
    /** Shown exactly once — only its hash is ever stored server-side. */
    token: string;
    accept_path: string;
  };
}

export interface Invitation {
  id: UUID;
  email: string;
  role: string;
  status: "pending" | "accepted" | "revoked";
  invited_by_email: string;
  expires_at: ISODateTime;
  accepted_at: ISODateTime | null;
  created_at: ISODateTime;
}

export interface CreatedInvitation extends Invitation {
  /** Shown exactly once — only its hash is ever stored server-side. */
  token: string;
  accept_path: string;
}

export interface PlatformAuditEntry {
  id: UUID;
  actor_type: string;
  actor_email: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  metadata: Record<string, unknown>;
  ip_address: string | null;
  request_id: string | null;
  created_at: ISODateTime;
  organization: UUID | null;
}

export interface HealthCheckResult {
  ok: boolean;
  critical: boolean;
  duration_ms: number;
  detail?: string;
}

export interface OperationsSummary {
  organizations: { total: number; active: number; suspended: number };
  webhooks_last_24h: { dead: number; failed: number; processed: number; partial: number };
  infrastructure: Record<string, HealthCheckResult>;
}

export interface PlatformWebhookEvent {
  id: UUID;
  provider: string;
  organization_ids: UUID[];
  object_type: string;
  fields: string[];
  status: "pending" | "processing" | "processed" | "partial" | "failed" | "dead";
  attempts: number;
  received_at: ISODateTime;
  processed_at: ISODateTime | null;
  next_attempt_at: ISODateTime | null;
  last_error: string;
  request_id: string;
}

export interface FailedJob {
  id: UUID;
  organization_id: UUID;
  business_id: UUID;
  updated_at: ISODateTime;
}

export interface FailedCampaign extends FailedJob {
  name: string;
  failure_code: string;
  failure_reason: string;
}

export interface FailedContactImport extends FailedJob {
  filename: string;
  error: string;
}

export interface FailedExport extends FailedJob {
  kind: string;
  error: string;
}

export interface FailedJobsSummary {
  campaigns: FailedCampaign[];
  contact_imports: FailedContactImport[];
  exports: FailedExport[];
}

// --- Failed-message recovery (GET /messages/failures/, /recovery/, POST /retry/) ---

export type RecoveryKind = "retryable" | "not_retryable" | "unknown" | "external";

export interface RecoveryBlocker {
  code: string;
  message: string;
}

export interface RecoveryLink {
  label: string;
  url: string;
}

/** What the retry message itself currently is, exactly as stored (never inferred). */
export interface RetryAttempt {
  id: string;
  status: string;
  status_label: string;
  created_at: string;
  accepted_at: string | null;
  sent_at: string | null;
  delivered_at: string | null;
  read_at: string | null;
  failed_at: string | null;
  failure: { category: string; label: string; safe_error: string; can_retry: boolean } | null;
}

export interface FailureRecovery {
  category: string;
  label: string;
  kind: RecoveryKind;
  explanation: string;
  next_step: string;
  provider_code: string;
  safe_error: string;
  can_retry: boolean;
  blockers: RecoveryBlocker[];
  links: RecoveryLink[];
  retried_as: RetryAttempt | null;
}

export interface FailedMessage {
  id: string;
  conversation_id: string;
  business_id: string;
  message_type: string;
  status: string;
  preview: string;
  occurred_at: string;
  failed_at: string | null;
  recipient: { name: string; phone_number: string };
  from_number: string;
  recovery: FailureRecovery;
}

export interface FailedMessagesResponse {
  generated_at: string;
  days: number;
  total: number;
  shown: number;
  kinds: Partial<Record<RecoveryKind, number>>;
  results: FailedMessage[];
}

export interface RetryPreview {
  destination: { name: string; phone_number: string; from_number: string; from_label: string };
  message_text: string;
  duplicate_risk: { level: string; text: string };
  cost: { status: "free" | "estimate" | "unknown"; amount?: string; currency?: string; note: string };
  confirmation_required: boolean;
}

export interface FailureDetail extends FailedMessage {
  message_text: string;
  retry_preview: RetryPreview | null;
}

export interface RetryResponse {
  created: boolean;
  message: { id: string; status: string; delivered_at: string | null };
  status_note: string;
}

// --- WhatsApp setup readiness + verified test message (/whatsapp/setup/, /test-message/*) ---

export type SetupState = "verified" | "pending" | "failed" | "unsupported" | "unknown";
export type SetupOverall = "not_started" | "in_progress" | "blocked" | "ready";

export interface SetupStep {
  id: string;
  title: string;
  state: SetupState;
  detail: string;
  next_step: string;
  fix_in: "console" | "meta" | "administrator" | "none";
  action: "" | "connect" | "refresh" | "test_message";
  evidence: Record<string, unknown>;
}

export interface SetupStatus {
  generated_at: string;
  business: { id: string; name: string } | null;
  overall: SetupOverall;
  summary: string;
  next_step: string;
  steps: SetupStep[];
  server: SetupStep;
  capabilities: Record<"embedded_signup" | "callback_check", { state: SetupState; detail: string }>;
  webhook: { callback_url: string; verify_token_configured: boolean };
  accounts: { id: string; name: string; business_id: string; connection_state: string }[];
  numbers: {
    id: string;
    label: string;
    display_phone_number: string;
    status: string;
    business_id: string;
  }[];
}

export interface TestMessagePreview {
  can_send: boolean;
  sender: { id: string; display_phone_number: string; label: string; business: string };
  recipient: { name: string; phone_number: string };
  message_type: string;
  content: string;
  template: { id: string; name: string; language: string; category: string } | null;
  cost: { status: "free" | "estimate" | "unknown"; amount?: string; currency?: string; note: string };
  restrictions: { source: string; text: string }[];
  blockers: { code: string; message: string }[];
  authorization: string;
  approval: { token: string; expires_in: number } | null;
}

export interface TestMessageSent {
  created: boolean;
  message_id: string;
  status: string;
  status_note: string;
}

export interface TestMessageStatus {
  id: string;
  status: string;
  accepted_at: string | null;
  sent_at: string | null;
  delivered_at: string | null;
  read_at: string | null;
  failed_at: string | null;
  error_message: string;
}

// --- Template Studio (/templates/studio/*, /templates/drafts/*, /templates/<id>/guidance/) ---

export type StudioButton = {
  type: "QUICK_REPLY" | "URL" | "PHONE_NUMBER";
  text: string;
  url?: string;
  url_example?: string;
  phone_number?: string;
};

export interface StudioContent {
  header_text: string;
  body: string;
  examples: Record<string, string>;
  footer: string;
  buttons: StudioButton[];
}

export interface StudioIssue {
  code: string;
  field: string;
  message: string;
}

export interface StudioChecks {
  ok: boolean;
  errors: StudioIssue[];
  warnings: StudioIssue[];
  counts: Record<string, { used: number; limit: number }>;
  preview: {
    header: string;
    body: string;
    footer: string;
    buttons: { type: string; text: string }[];
    variables: string[];
  };
}

export interface StudioOptions {
  categories: { value: string; label: string; when: string; supported: boolean; note?: string }[];
  languages: { value: string; label: string }[];
  parameter_formats: ("POSITIONAL" | "NAMED")[];
  button_types: { value: StudioButton["type"]; label: string; max: number }[];
  limits: Record<string, number>;
  unsupported: string[];
  starters: { id: string; title: string; category: string; content: StudioContent }[];
  source: string;
}

export type TemplateState =
  | "draft"
  | "submitted"
  | "pending"
  | "approved"
  | "rejected"
  | "paused"
  | "disabled"
  | "unknown";

export interface TemplateGuidance {
  state: TemplateState;
  meta_status: string;
  verified_by_meta: boolean;
  explanation: string;
  next_step: string;
  reason: { code: string; meaning: string; how_to_fix: string } | null;
  can_send: boolean;
  can_fix_as_new: boolean;
  links: { label: string; url: string }[];
  review_note: string;
}

export type DraftStatus = "draft" | "submitting" | "submitted" | "unconfirmed";

export interface TemplateDraft {
  id: string;
  business_account_id: string;
  name: string;
  language: string;
  category: string;
  parameter_format: "POSITIONAL" | "NAMED";
  content: StudioContent;
  status: DraftStatus;
  status_label: string;
  template_id: string | null;
  attempts: number;
  submitted_at: string | null;
  last_error: string;
  last_error_code: string;
  updated_at: string;
  checks: StudioChecks;
  content_hash: string;
  can_submit: boolean;
  guidance: TemplateGuidance;
  components?: Record<string, unknown>[];
  note?: string;
}

export interface StudioValidation extends StudioChecks {
  components: Record<string, unknown>[];
}
