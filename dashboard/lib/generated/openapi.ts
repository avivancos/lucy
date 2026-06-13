/* eslint-disable */
// Generated from Lucy FastAPI OpenAPI. Run `npm run generate:openapi`.

export const openApiVersion = "3.1.0";
export const openApiTitle = "Lucy API";
export const openApiPaths = [
  "/agents",
  "/crm/events",
  "/deployments",
  "/evals",
  "/health",
  "/mcp/servers",
  "/metrics",
  "/metrics/realtime",
  "/models",
  "/pili/bookings",
  "/pili/health",
  "/pili/voice/events",
  "/sessions",
  "/traces",
] as const;
export type OpenApiPath = (typeof openApiPaths)[number];

export type AgentSummary = {
  "goal": string;
  "id": string;
  "name": string;
};

export type DeploymentSummary = {
  "agent_id": string;
  "environment": string;
  "id": string;
  "status": string;
};

export type FunnelStage = "qualified" | "interested" | "objection" | "booked" | "escalation" | "failed_booking";

export type HTTPValidationError = {
  "detail"?: ValidationError[];
};

export type HealthResponse = {
  "service": string;
  "status": string;
  "version": string;
};

export type LatencyWaterfall = {
  "llm_ms"?: number;
  "mcp_tools_ms"?: number;
  "rag_ms"?: number;
  "stt_ms"?: number;
  "transport_ms"?: number;
  "tts_ms"?: number;
};

export type McpCommandSummary = {
  "command_id": string;
  "server": string;
  "status": string;
  "tool": string;
};

export type McpServerSummary = {
  "allowed_tools": string[];
  "name": string;
  "status": string;
};

export type PiliBookingHoldRequest = {
  "lead_id": string;
  "requested_slot": string;
  "session_id": string;
  "source": string;
  "timezone": string;
};

export type PiliBookingHoldResponse = {
  "booking_id": string;
  "crm_sync_status": string;
  "mcp_audit_count": number;
  "mcp_commands": McpCommandSummary[];
  "status": string;
};

export type PiliHealthResponse = {
  "lucy_compatible": boolean;
  "service": string;
  "status": string;
};

export type PiliVoiceEventRequest = {
  "cost_per_minute": number;
  "funnel_stage": FunnelStage;
  "lead_id": string;
  "sentiment": SentimentLabel;
  "session_id": string;
  "transcript_excerpt": string;
};

export type PiliVoiceEventResponse = {
  "accepted": boolean;
  "crm_sync_status": string;
  "event_id": string;
  "mcp_audit_count": number;
  "mcp_commands": McpCommandSummary[];
};

export type SentimentLabel = "positive" | "neutral" | "negative";

export type SessionSummary = {
  "agent_id": string;
  "cost_per_minute": number;
  "id": string;
  "status": string;
};

export type TraceSummary = {
  "session_id": string;
  "waterfall": LatencyWaterfall;
};

export type ValidationError = {
  "ctx"?: Record<string, never>;
  "input"?: unknown;
  "loc": (string | number)[];
  "msg": string;
  "type": string;
};

