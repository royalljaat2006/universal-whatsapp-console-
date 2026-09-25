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
  | "campaign.progress";

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
  updated_at: ISODateTime;
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
  "campaign_recipients" | "campaign_summary" | "messages" | "costs" | "analytics_daily";

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
