import { describe, expect, it } from "vitest";
import { NeuralSim } from "./neural";

describe("neural sim", () => {
  it("builds a connected-ish network inside the core", () => {
    const sim = new NeuralSim(90);
    expect(sim.edges.length).toBeGreaterThan(90);
    for (let i = 0; i < 90; i++) {
      const r = Math.hypot(sim.pos[i * 3]!, sim.pos[i * 3 + 1]!, sim.pos[i * 3 + 2]!);
      expect(r).toBeLessThan(0.9); // stays under the membrane (radius 1)
      expect(sim.adjacency[i]!.length).toBeGreaterThan(0);
    }
  });

  it("is quiet when idle and busy when thinking", () => {
    const run = (rate: number, spread: number) => {
      const sim = new NeuralSim(90);
      for (let i = 0; i < 600; i++) sim.step(1 / 60, rate, spread); // 10 s
      return sim.fired;
    };
    const idle = run(0.035, 0.3);
    const thinking = run(0.32, 0.6);
    expect(idle).toBeGreaterThan(5); // never dead
    expect(thinking).toBeGreaterThan(idle * 4);
  });

  it("spikes travel along synapses and trigger neighbours", () => {
    const sim = new NeuralSim(40);
    sim.fire(0, 1);
    expect(sim.activation[0]).toBe(1);
    expect(sim.fire(0, 1)).toBe(false); // refractory: can't fire again straight away
    const lit = sim.adjacency[0]!.filter((e) => sim.lastSpikeAt[e] === 0);
    expect(lit.length).toBe(sim.adjacency[0]!.length);
    for (let i = 0; i < 120; i++) sim.step(1 / 60, 0, 1); // 2 s, no spontaneous firing
    expect(sim.fired).toBeGreaterThan(1 + lit.length - 1);
  });
});
