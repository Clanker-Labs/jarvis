import { describe, expect, it } from "vitest";
import { inWindow, toMinutes } from "./hooks";

const at = (h: number, m = 0) => new Date(2026, 0, 1, h, m);

describe("night window", () => {
  it("parses HH:MM", () => expect(toMinutes("23:30")).toBe(1410));
  it("handles windows wrapping midnight", () => {
    expect(inWindow(at(23, 30), "23:00", "07:00")).toBe(true);
    expect(inWindow(at(3), "23:00", "07:00")).toBe(true);
    expect(inWindow(at(7), "23:00", "07:00")).toBe(false);
    expect(inWindow(at(12), "23:00", "07:00")).toBe(false);
  });
  it("handles same-day windows", () => {
    expect(inWindow(at(13), "12:00", "14:00")).toBe(true);
    expect(inWindow(at(14), "12:00", "14:00")).toBe(false);
  });
});
