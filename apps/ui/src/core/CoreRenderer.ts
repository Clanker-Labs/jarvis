import * as THREE from "three";

/** ONE/ONE additive blending with premultiplied output (shaders write alpha = brightness). */
const ADDITIVE = {
  blending: THREE.CustomBlending,
  blendSrc: THREE.OneFactor,
  blendDst: THREE.OneFactor,
  blendSrcAlpha: THREE.OneFactor,
  blendDstAlpha: THREE.OneFactor,
  premultipliedAlpha: true,
} as const;
import type { JarvisState } from "@jarvis/protocol";
import { approach, levels, STATE_PARAMS, type StateParams } from "./params";
import {
  coreFragment,
  coreVertex,
  glowFragment,
  glowVertex,
  neuronFragment,
  neuronVertex,
  particleFragment,
  particleVertex,
  ringFragment,
  ringVertex,
  synapseFragment,
  synapseVertex,
} from "./shaders";
import { mulberry32, NeuralSim, SPIKE_SPEED } from "./neural";

export type Quality = "high" | "medium" | "low";

const QUALITY: Record<
  Quality,
  { detail: number; octaves: number; particles: number; neurons: number; fps: number; dpr: number }
> = {
  high: { detail: 28, octaves: 2, particles: 220, neurons: 130, fps: 60, dpr: 1.25 },
  medium: { detail: 18, octaves: 2, particles: 120, neurons: 90, fps: 60, dpr: 1 },
  low: { detail: 10, octaves: 1, particles: 60, neurons: 56, fps: 30, dpr: 0.85 },
};

const SYNAPSE_SEGMENTS = 6; // each synapse is a gently bowed curve, not a straight wire

export interface FrameStats {
  fps: number;
  quality: Quality;
}

/**
 * Imperative WebGL renderer for the energy core. React only creates/destroys it and pushes
 * targets; per-frame values (levels, easing) never go through React state.
 */
export class CoreRenderer {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(32, 1, 0.1, 20);
  private group = new THREE.Group();
  private core!: THREE.Mesh<THREE.IcosahedronGeometry, THREE.ShaderMaterial>;
  private glow: THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial>;
  private particles!: THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>;
  private ring: THREE.Mesh<THREE.RingGeometry, THREE.ShaderMaterial>;
  private sim!: NeuralSim;
  private neurons!: THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>;
  private synapses!: THREE.LineSegments<THREE.BufferGeometry, THREE.ShaderMaterial>;
  private raf = 0;
  private last = 0;
  private lastDraw = 0;
  private t = 0;
  private quality: Quality;

  // Eased values
  private color = new THREE.Color();
  private targetColor = new THREE.Color();
  private cur: StateParams = { ...STATE_PARAMS.IDLE };
  private target: StateParams = STATE_PARAMS.IDLE;
  private audio = 0;
  private flash = 0;
  private dim = 1;
  private targetDim = 1;
  private timeScale = 1;
  private state: JarvisState = "IDLE";

  // fps accounting
  private frames = 0;
  private fpsWindowStart = 0;
  onStats?: (s: FrameStats) => void;

  constructor(
    private canvas: HTMLCanvasElement,
    quality: Quality,
    private autoQuality: boolean,
  ) {
    this.quality = quality;
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: false,
      alpha: true,
      premultipliedAlpha: true,
      powerPreference: "high-performance",
      preserveDrawingBuffer: false,
    });
    this.renderer.setClearColor(0x000000, 0);
    this.color.set("#0EA5E9");
    this.targetColor.copy(this.color);
    this.camera.position.set(0, 0, 4.2);
    this.scene.add(this.group);

    this.glow = new THREE.Mesh(
      new THREE.PlaneGeometry(2.6, 2.6),
      new THREE.ShaderMaterial({
        vertexShader: glowVertex,
        fragmentShader: glowFragment,
        uniforms: {
          uColor: { value: this.color },
          uIntensity: { value: 0.6 },
          uFlash: { value: 0 },
          uDim: { value: 1 },
        },
        ...ADDITIVE,
        depthWrite: false,
        transparent: true,
      }),
    );
    this.glow.position.z = -0.5;
    this.scene.add(this.glow);

    this.ring = new THREE.Mesh(
      new THREE.RingGeometry(1.32, 1.345, 128, 1),
      new THREE.ShaderMaterial({
        vertexShader: ringVertex,
        fragmentShader: ringFragment,
        uniforms: { uColor: { value: this.color }, uAlpha: { value: 0 }, uTime: { value: 0 }, uDim: { value: 1 } },
        ...ADDITIVE,
        depthWrite: false,
        transparent: true,
        side: THREE.DoubleSide,
      }),
    );
    this.ring.rotation.x = -1.15;
    this.group.add(this.ring);

    this.build();
    this.resize();
  }

  private build() {
    const q = QUALITY[this.quality];
    if (this.core) {
      this.group.remove(this.core);
      this.core.geometry.dispose();
      this.core.material.dispose();
    }
    if (this.particles) {
      this.group.remove(this.particles);
      this.particles.geometry.dispose();
      this.particles.material.dispose();
    }
    this.core = new THREE.Mesh(
      new THREE.IcosahedronGeometry(1, q.detail),
      new THREE.ShaderMaterial({
        vertexShader: coreVertex(q.octaves),
        fragmentShader: coreFragment,
        uniforms: {
          uTime: { value: 0 },
          uEnergy: { value: 0 },
          uAudio: { value: 0 },
          uSwirl: { value: 0 },
          uColor: { value: this.color },
          uFlash: { value: 0 },
          uDim: { value: 1 },
        },
        ...ADDITIVE,
        transparent: true,
        depthWrite: false,
      }),
    );
    this.group.add(this.core);

    const n = q.particles;
    const orbit = new Float32Array(n * 3);
    const axis = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      orbit[i * 3] = 1.25 + Math.random() * 0.45;
      orbit[i * 3 + 1] = (0.35 + Math.random() * 0.9) * (Math.random() < 0.5 ? -1 : 1);
      orbit[i * 3 + 2] = Math.random() * Math.PI * 2;
      axis[i * 3] = Math.random() - 0.5;
      axis[i * 3 + 1] = 1.5 + Math.random();
      axis[i * 3 + 2] = Math.random() - 0.5;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    g.setAttribute("aOrbit", new THREE.BufferAttribute(orbit, 3));
    g.setAttribute("aAxis", new THREE.BufferAttribute(axis, 3));
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 2);
    this.particles = new THREE.Points(
      g,
      new THREE.ShaderMaterial({
        vertexShader: particleVertex,
        fragmentShader: particleFragment,
        uniforms: {
          uTime: { value: 0 },
          uSize: { value: 9 },
          uColor: { value: this.color },
          uAlpha: { value: 0 },
          uDim: { value: 1 },
        },
        ...ADDITIVE,
        depthWrite: false,
        transparent: true,
      }),
    );
    this.group.add(this.particles);
    this.buildNetwork(q.neurons);
  }

  private buildNetwork(n: number) {
    if (this.neurons) {
      for (const o of [this.neurons, this.synapses]) {
        this.group.remove(o);
        o.geometry.dispose();
        o.material.dispose();
      }
    }
    const sim = (this.sim = new NeuralSim(n));
    const ng = new THREE.BufferGeometry();
    ng.setAttribute("position", new THREE.BufferAttribute(sim.pos, 3));
    ng.setAttribute("aAct", new THREE.BufferAttribute(sim.activation, 1).setUsage(THREE.DynamicDrawUsage));
    ng.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1);
    this.neurons = new THREE.Points(
      ng,
      new THREE.ShaderMaterial({
        vertexShader: neuronVertex,
        fragmentShader: neuronFragment,
        uniforms: {
          uTime: { value: 0 },
          uSize: { value: 11 },
          uColor: { value: this.color },
          uAlpha: { value: 1 },
          uDim: { value: 1 },
        },
        ...ADDITIVE,
        depthWrite: false,
        transparent: true,
      }),
    );

    // Synapses: each edge is SEGMENTS line pieces along a curve bowed slightly off the straight line.
    const S = SYNAPSE_SEGMENTS;
    const verts = sim.edges.length * S * 2;
    const pos = new Float32Array(verts * 3);
    const tAttr = new Float32Array(verts);
    const rand = mulberry32(11);
    const P = sim.pos;
    sim.edges.forEach((e, ei) => {
      const a = [P[e.a * 3]!, P[e.a * 3 + 1]!, P[e.a * 3 + 2]!];
      const b = [P[e.b * 3]!, P[e.b * 3 + 1]!, P[e.b * 3 + 2]!];
      const bow = e.len * 0.28;
      const c = a.map((v, k) => (v + b[k]!) / 2 + (rand() - 0.5) * bow);
      const at = (t: number) => a.map((v, k) => (1 - t) * (1 - t) * v + 2 * (1 - t) * t * c[k]! + t * t * b[k]!);
      for (let s = 0; s < S; s++) {
        for (const [j, t] of [
          [0, s / S],
          [1, (s + 1) / S],
        ] as const) {
          const v = (ei * S + s) * 2 + j;
          pos.set(at(t), v * 3);
          tAttr[v] = t;
        }
      }
    });
    const sg = new THREE.BufferGeometry();
    sg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    sg.setAttribute("aT", new THREE.BufferAttribute(tAttr, 1));
    sg.setAttribute(
      "aSpike",
      new THREE.BufferAttribute(new Float32Array(verts * 3), 3).setUsage(THREE.DynamicDrawUsage),
    );
    sg.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1);
    this.synapses = new THREE.LineSegments(
      sg,
      new THREE.ShaderMaterial({
        vertexShader: synapseVertex,
        fragmentShader: synapseFragment,
        uniforms: {
          uTime: { value: 0 },
          uColor: { value: this.color },
          uAlpha: { value: 1 },
          uDim: { value: 1 },
          uSpeed: { value: SPIKE_SPEED },
        },
        ...ADDITIVE,
        depthWrite: false,
        transparent: true,
      }),
    );
    // Drawn before the membrane so the shell reads as a translucent skin over the network.
    this.group.add(this.synapses, this.neurons);
  }

  /** Step the spiking network and copy its state into the two dynamic buffers. */
  private stepNetwork(dt: number) {
    const cur = this.cur;
    const sim = this.sim;
    // Voice drives activity: talking/listening makes the network fire with the sound.
    const rate = cur.neural + this.audio * 0.9;
    const spread = Math.min(0.75, cur.spread + this.audio * 0.25);
    sim.step(dt, rate, spread);
    this.neurons.geometry.attributes.aAct!.needsUpdate = true;

    const spike = this.synapses.geometry.attributes.aSpike as THREE.BufferAttribute;
    const arr = spike.array as Float32Array;
    const per = SYNAPSE_SEGMENTS * 2;
    for (let e = 0; e < sim.edges.length; e++) {
      const age = Math.min(sim.time - sim.lastSpikeAt[e]!, 99);
      const dir = sim.lastSpikeDir[e]!;
      const len = sim.edges[e]!.len;
      for (let v = e * per, end = v + per; v < end; v++) {
        arr[v * 3] = age;
        arr[v * 3 + 1] = dir;
        arr[v * 3 + 2] = len;
      }
    }
    spike.needsUpdate = true;
  }

  setQuality(q: Quality) {
    if (q === this.quality) return;
    this.quality = q;
    this.build();
    this.resize();
  }

  setState(state: JarvisState, color: string) {
    if (state === "WAKE" && this.state !== "WAKE") {
      this.flash = 1;
      this.sim.burst(6, 0.7); // the wake word ripples out from the centre
    }
    this.state = state;
    this.target = STATE_PARAMS[state];
    this.targetColor.set(color);
  }

  setColor(color: string) {
    this.targetColor.set(color);
  }

  /** dim: 0..1 global brightness; timeScale slows animation (night mode). */
  setAmbience(dim: number, timeScale: number) {
    this.targetDim = dim;
    this.timeScale = timeScale;
  }

  resize() {
    const { clientWidth: w, clientHeight: h } = this.canvas;
    if (!w || !h) return;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, QUALITY[this.quality].dpr));
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  start() {
    const loop = (now: number) => {
      this.raf = requestAnimationFrame(loop);
      const minDelta = 1000 / QUALITY[this.quality].fps;
      if (now - this.lastDraw < minDelta - 1) return;
      this.lastDraw = now;
      // Screen asleep: stop drawing entirely once faded out (saves GPU + power).
      if (this.targetDim === 0 && this.dim < 0.01) {
        this.last = now;
        return;
      }
      const dt = this.last ? Math.min((now - this.last) / 1000, 0.1) : 1 / 60;
      this.last = now;
      this.frame(dt, now);
      this.countFps(now);
    };
    this.raf = requestAnimationFrame(loop);
  }

  private countFps(now: number) {
    this.frames++;
    if (!this.fpsWindowStart) this.fpsWindowStart = now;
    const span = now - this.fpsWindowStart;
    if (span < 3000) return;
    const fps = (this.frames * 1000) / span;
    this.frames = 0;
    this.fpsWindowStart = now;
    // Degrade only (never oscillate back up): high→medium below 50, medium→low below 40.
    // "low" is capped at 30 fps by design.
    if (this.autoQuality && document.visibilityState === "visible") {
      if (this.quality === "high" && fps < 50) this.setQuality("medium");
      else if (this.quality === "medium" && fps < 40) this.setQuality("low");
    }
    this.onStats?.({ fps, quality: this.quality });
  }

  private frame(dt: number, now: number) {
    const k = approach(dt, 0.28);
    const cur = this.cur;
    const tgt = this.target;
    cur.energy += (tgt.energy - cur.energy) * k;
    cur.swirl += (tgt.swirl - cur.swirl) * k;
    cur.glow += (tgt.glow - cur.glow) * k;
    cur.particles += (tgt.particles - cur.particles) * approach(dt, 0.4);
    cur.ring += (tgt.ring - cur.ring) * approach(dt, 0.3);
    cur.breathe += (tgt.breathe - cur.breathe) * approach(dt, 0.6);
    cur.pulse += (tgt.pulse - cur.pulse) * k;
    cur.neural += (tgt.neural - cur.neural) * approach(dt, 0.5);
    cur.spread += (tgt.spread - cur.spread) * approach(dt, 0.5);
    cur.network += (tgt.network - cur.network) * approach(dt, 0.6);
    this.color.lerp(this.targetColor, approach(dt, 0.22));
    this.dim += (this.targetDim - this.dim) * approach(dt, 0.8);
    this.flash = Math.max(0, this.flash - dt / 0.3);

    // Audio level: attack fast, release slow. Stale levels (no updates for 250 ms) decay to 0.
    let raw = 0;
    if (tgt.audio === "mic" && now - levels.micAt < 250) raw = levels.mic;
    if (tgt.audio === "tts" && now - levels.ttsAt < 250) raw = levels.tts;
    this.audio += (raw - this.audio) * approach(dt, raw > this.audio ? 0.05 : 0.18);

    this.t += dt * this.timeScale;
    const t = this.t;
    const breath = Math.sin((t * Math.PI * 2) / 5) * 0.025 * cur.breathe; // 5 s cycle
    const heartbeat = cur.pulse * Math.pow(Math.max(0, Math.sin(t * Math.PI * 2 * 1.2)), 8) * 0.06;
    const scale = 1 + breath + heartbeat + this.audio * 0.06;
    this.core.scale.setScalar(scale);
    this.group.rotation.y = t * 0.05;
    this.group.rotation.x = Math.sin(t * 0.13) * 0.12; // a slow sway, never a mechanical spin

    this.stepNetwork(dt * this.timeScale);
    for (const m of [this.neurons.material, this.synapses.material]) {
      m.uniforms.uTime!.value = t;
      m.uniforms.uAlpha!.value = cur.network * (1 + this.flash * 0.8);
      m.uniforms.uDim!.value = this.dim;
    }

    const cu = this.core.material.uniforms;
    cu.uTime!.value = t;
    cu.uEnergy!.value = cur.energy + heartbeat * 4;
    cu.uAudio!.value = this.audio;
    cu.uSwirl!.value = cur.swirl;
    cu.uFlash!.value = this.flash * 0.85;
    cu.uDim!.value = this.dim;

    const gu = this.glow.material.uniforms;
    gu.uIntensity!.value = cur.glow * (0.75 + breath * 4 + this.audio * 0.9 + heartbeat * 6);
    gu.uFlash!.value = this.flash;
    gu.uDim!.value = this.dim;
    this.glow.scale.setScalar(scale);

    const pu = this.particles.material.uniforms;
    pu.uTime!.value = t;
    pu.uAlpha!.value = cur.particles;
    pu.uDim!.value = this.dim;
    this.particles.visible = cur.particles > 0.01;

    const ru = this.ring.material.uniforms;
    ru.uTime!.value = t;
    ru.uAlpha!.value = cur.ring;
    ru.uDim!.value = this.dim;
    this.ring.visible = cur.ring > 0.01;
    this.ring.rotation.z = t * 0.6;

    this.renderer.render(this.scene, this.camera);
  }

  dispose() {
    cancelAnimationFrame(this.raf);
    this.scene.traverse((o) => {
      const m = o as THREE.Mesh;
      m.geometry?.dispose?.();
      (m.material as THREE.Material | undefined)?.dispose?.();
    });
    this.renderer.dispose();
  }
}
