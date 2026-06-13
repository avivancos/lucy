import type {
  HealthResponse as GeneratedHealthResponse,
  SessionSummary as GeneratedSessionSummary,
  TraceSummary as GeneratedTraceSummary
} from "./generated/openapi";

export type HealthResponse = GeneratedHealthResponse;
export type SessionSummary = GeneratedSessionSummary;
export type TraceSummary = GeneratedTraceSummary & {
  waterfall: GeneratedTraceSummary["waterfall"] & {
    total_ms: number;
  };
};

export const demoSessions: SessionSummary[] = [
  {
    id: "sess_demo",
    agent_id: "agent_sales_booking",
    status: "live",
    cost_per_minute: 0.031667
  }
];

export const demoTrace: TraceSummary = {
  session_id: "sess_demo",
  waterfall: {
    stt_ms: 145,
    rag_ms: 38,
    llm_ms: 210,
    mcp_tools_ms: 42,
    tts_ms: 95,
    transport_ms: 32,
    total_ms: 562
  }
};
