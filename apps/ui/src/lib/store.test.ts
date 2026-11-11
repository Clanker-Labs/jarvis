import { makeEvent } from "@jarvis/protocol";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.stubGlobal("performance", { now: () => 0 });
const { useUi } = await import("./store");

describe("ui store", () => {
  beforeEach(() => useUi.setState({ state: "IDLE", tool: null, recent: [] }));

  it("follows state changes and clears the tool chip outside TOOL", () => {
    const s = useUi.getState();
    s.apply(makeEvent("state.change", { state: "TOOL", previous: "THINKING", reason: "" }));
    s.apply(
      makeEvent("agent.tool_call", { call_id: "1", tool: "weather", args: {}, side_effect: "none", label: "Weather", icon: "cloud" }),
    );
    expect(useUi.getState().tool?.label).toBe("Weather");
    s.apply(makeEvent("state.change", { state: "SPEAKING", previous: "TOOL", reason: "" }));
    expect(useUi.getState().tool).toBeNull();
  });

  it("keeps the last three exchanges", () => {
    const s = useUi.getState();
    for (let i = 0; i < 5; i++) {
      s.apply(makeEvent("listen.final", { text: `q${i}`, stt_provider: "" }));
      s.apply(
        makeEvent("agent.done", { text: `a${i}`, provider: "", model: "", input_tokens: 0, output_tokens: 0, cost_usd: 0, interrupted: false }),
      );
    }
    const recent = useUi.getState().recent;
    expect(recent).toHaveLength(3);
    expect(recent[0]).toMatchObject({ user: "q4", assistant: "a4" });
  });
});
