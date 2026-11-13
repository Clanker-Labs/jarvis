/**
 * A small spiking "neural network" inside the core: neurons fire spontaneously at a rate set by
 * the conversation state (and voice level), and each spike travels along synapses to neighbours,
 * which may fire in turn. That cascade is what makes the core look like it is thinking.
 *
 * The simulation is plain TypeScript (unit-tested); NeuralNet only mirrors it into two buffers
 * per frame: neuron activation (one float per neuron) and per-synapse spike times. All drawing
 * happens in the shaders, so the CPU cost is ~100 neurons and ~200 edges per frame.
 */

export interface Edge {
  a: number;
  b: number;
  len: number;
}

export interface Spike {
  edge: number;
  /** +1 travels a → b, -1 travels b → a. */
  dir: 1 | -1;
  /** Simulation time the spike left its source. */
  at: number;
}

export const SPIKE_SPEED = 1.4; // units per second along a synapse
const REFRACTORY_S = 0.7;
const ACT_DECAY_S = 0.35;

/** Deterministic PRNG so the network (and its tests) are reproducible. */
export function mulberry32(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export class NeuralSim {
  readonly pos: Float32Array; // xyz per neuron
  readonly edges: Edge[] = [];
  readonly adjacency: number[][]; // neuron → edge indices
  readonly activation: Float32Array;
  readonly lastSpikeAt: Float32Array; // per edge: start time of the latest spike
  readonly lastSpikeDir: Float32Array; // per edge: ±1
  private refractoryUntil: Float32Array;
  private pending: { neuron: number; at: number }[] = [];
  private rand: () => number;
  time = 0;
  fired = 0; // total spikes (stats/tests)

  constructor(readonly n: number, seed = 7, links = 3) {
    this.rand = mulberry32(seed);
    this.pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      // Uniform direction, radius biased outward (more neurons near the "cortex").
      const u = this.rand() * 2 - 1;
      const th = this.rand() * Math.PI * 2;
      const s = Math.sqrt(1 - u * u);
      const r = 0.22 + 0.62 * Math.cbrt(this.rand());
      this.pos.set([s * Math.cos(th) * r, u * r, s * Math.sin(th) * r], i * 3);
    }
    this.adjacency = Array.from({ length: n }, () => []);
    const seen = new Set<number>();
    for (let i = 0; i < n; i++) {
      const near = [...Array(n).keys()]
        .filter((j) => j !== i)
        .map((j) => [j, this.dist(i, j)] as const)
        .sort((x, y) => x[1] - y[1])
        .slice(0, links);
      for (const [j, d] of near) {
        const key = Math.min(i, j) * n + Math.max(i, j);
        if (seen.has(key)) continue;
        seen.add(key);
        const e = this.edges.push({ a: i, b: j, len: d }) - 1;
        this.adjacency[i]!.push(e);
        this.adjacency[j]!.push(e);
      }
    }
    this.activation = new Float32Array(n);
    this.refractoryUntil = new Float32Array(n);
    this.lastSpikeAt = new Float32Array(this.edges.length).fill(-1e3);
    this.lastSpikeDir = new Float32Array(this.edges.length).fill(1);
  }

  dist(i: number, j: number): number {
    const p = this.pos;
    return Math.hypot(p[i * 3]! - p[j * 3]!, p[i * 3 + 1]! - p[j * 3 + 1]!, p[i * 3 + 2]! - p[j * 3 + 2]!);
  }

  /** Fire neuron `i` now (if it isn't refractory) and send spikes down some of its synapses. */
  fire(i: number, propagate: number): boolean {
    if (this.time < this.refractoryUntil[i]!) return false;
    this.refractoryUntil[i] = this.time + REFRACTORY_S;
    this.activation[i] = 1;
    this.fired++;
    for (const e of this.adjacency[i]!) {
      if (this.rand() > propagate) continue;
      const edge = this.edges[e]!;
      const to = edge.a === i ? edge.b : edge.a;
      this.lastSpikeAt[e] = this.time;
      this.lastSpikeDir[e] = edge.a === i ? 1 : -1;
      this.pending.push({ neuron: to, at: this.time + edge.len / SPIKE_SPEED });
    }
    return true;
  }

  /** Fire the `count` neurons nearest the centre: a burst that ripples outward (wake word). */
  burst(count: number, propagate: number) {
    const order = [...Array(this.n).keys()].sort((a, b) => this.radius(a) - this.radius(b));
    for (const i of order.slice(0, count)) this.fire(i, propagate);
  }

  private radius(i: number) {
    const p = this.pos;
    return Math.hypot(p[i * 3]!, p[i * 3 + 1]!, p[i * 3 + 2]!);
  }

  /**
   * Advance by dt. `rate`: spontaneous spikes per neuron per second; `propagate`: chance a spike
   * crosses each synapse (cascades grow when it's high, die out when low).
   */
  step(dt: number, rate: number, propagate: number) {
    this.time += dt;
    const decay = Math.exp(-dt / ACT_DECAY_S);
    for (let i = 0; i < this.n; i++) this.activation[i]! *= decay;
    // Arrivals first (cascades), then spontaneous firing.
    if (this.pending.length) {
      const still: typeof this.pending = [];
      for (const p of this.pending) {
        if (p.at <= this.time) this.fire(p.neuron, propagate);
        else still.push(p);
      }
      this.pending = still.length > 4 * this.n ? still.slice(-4 * this.n) : still;
    }
    const pFire = rate * dt;
    for (let i = 0; i < this.n; i++) if (this.rand() < pFire) this.fire(i, propagate);
  }
}
