// GLSL for the energy core. Kept deliberately cheap for the Pi's GPU:
// – vertex displacement with 3D simplex noise (1–2 octaves by quality)
// – fresnel rim + inner swirl in the fragment shader, no texture lookups
// – "bloom" is an additive billboard with an analytic falloff (no post-processing pass)

const SIMPLEX = /* glsl */ `
// Ashima Arts 3D simplex noise (MIT) — https://github.com/ashima/webgl-noise
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+10.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z); vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
  vec4 m=max(0.5-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
  return 105.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}
`;

export const coreVertex = (octaves: number) => /* glsl */ `
uniform float uTime;
uniform float uEnergy;     // 0..1 overall agitation
uniform float uAudio;      // 0..1 mic or TTS level (already smoothed)
uniform float uSwirl;      // internal flow speed multiplier
varying vec3 vNormalV;
varying vec3 vViewDir;
varying float vNoise;
varying vec3 vPos;
${SIMPLEX}
void main(){
  vec3 p = position;
  float t = uTime * (0.25 + uSwirl * 0.6);
  float n = snoise(p * 1.6 + vec3(0.0, t, t * 0.7));
  ${octaves > 1 ? "n += 0.35 * snoise(p * 3.0 - vec3(t * 1.3, 0.0, t));" : ""}
  float amp = 0.02 + uEnergy * 0.05 + uAudio * 0.1;
  // Slow, large "lobes" so the membrane breathes unevenly, like tissue rather than a ball.
  float lobes = sin(p.x * 2.1 + uTime * 0.31) * sin(p.y * 1.7 - uTime * 0.23) * sin(p.z * 1.9 + uTime * 0.27);
  p += normal * (n * amp + lobes * 0.035);
  vNoise = n;
  vPos = p;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  vNormalV = normalize(normalMatrix * normal);
  vViewDir = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}
`;

export const coreFragment = /* glsl */ `
uniform float uTime;
uniform vec3 uColor;
uniform float uEnergy;
uniform float uAudio;
uniform float uFlash;      // 0..1 white flash (wake)
uniform float uDim;        // global brightness (mute, sleep)
varying vec3 vNormalV;
varying vec3 vViewDir;
varying float vNoise;
varying vec3 vPos;
void main(){
  float facing = clamp(dot(normalize(vNormalV), normalize(vViewDir)), 0.0, 1.0);
  float fresnel = pow(1.0 - facing, 3.0);
  // Flowing filaments across the surface give the "energy" look without textures.
  float flow = sin(vNoise * 5.0 + vPos.y * 2.5 + uTime * 1.4) * 0.5 + 0.5;
  float filaments = pow(flow, 6.0);
  vec3 hot = mix(uColor, vec3(1.0), 0.18);
  vec3 col = uColor * (0.015 + 0.04 * flow);
  // Filaments fade toward the centre of the disc: a translucent membrane over the neurons.
  col += hot * filaments * (0.1 + uEnergy * 0.4 + uAudio * 0.45) * (0.35 + 0.65 * (1.0 - facing));
  col += hot * fresnel * (0.9 + uEnergy * 0.9 + uAudio * 0.6);
  col += mix(uColor, vec3(1.0), 0.5) * pow(facing, 6.0) * (0.03 + uAudio * 0.5 + uEnergy * 0.12);
  col = mix(col, vec3(1.0), uFlash);
  col *= uDim;
  // Premultiplied, additive: alpha tracks brightness so the canvas composites cleanly.
  gl_FragColor = vec4(col, clamp(max(col.r, max(col.g, col.b)), 0.0, 1.0));
  #include <colorspace_fragment>
}
`;

export const glowVertex = /* glsl */ `
varying vec2 vUv;
void main(){ vUv = uv * 2.0 - 1.0; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
`;

export const glowFragment = /* glsl */ `
uniform vec3 uColor;
uniform float uIntensity;
uniform float uFlash;
uniform float uDim;
varying vec2 vUv;
void main(){
  float r = length(vUv);
  // Soft halo: bright just outside the sphere edge (~0.42), long falloff.
  // Sphere edge sits at r≈0.72 on this plane; the halo reaches 0 before the plane (and canvas) edge.
  // Faint inside the sphere (it shows through the additive surface), peaks at the rim.
  float inner = mix(0.08, 1.0, smoothstep(0.5, 0.72, r));
  float outer = exp(-(r - 0.72) * 7.0) * smoothstep(1.0, 0.8, r);
  float halo = r < 0.72 ? inner : outer;
  vec3 col = mix(uColor, vec3(1.0), uFlash * 0.7) * halo * uIntensity * 0.8 * uDim;
  gl_FragColor = vec4(col, clamp(max(col.r, max(col.g, col.b)), 0.0, 1.0));
  #include <colorspace_fragment>
}
`;

export const particleVertex = /* glsl */ `
uniform float uTime;
uniform float uSize;
attribute vec3 aOrbit;   // radius, speed, phase
attribute vec3 aAxis;    // orbit plane normal
varying float vTwinkle;
void main(){
  float a = aOrbit.z + uTime * aOrbit.y;
  vec3 axis = normalize(aAxis);
  vec3 u = normalize(cross(axis, abs(axis.y) < 0.9 ? vec3(0.0,1.0,0.0) : vec3(1.0,0.0,0.0)));
  vec3 v = cross(axis, u);
  vec3 p = (u * cos(a) + v * sin(a)) * aOrbit.x;
  vTwinkle = 0.55 + 0.45 * sin(uTime * 3.0 + aOrbit.z * 7.0);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_PointSize = uSize * vTwinkle * (3.0 / -mv.z);
  gl_Position = projectionMatrix * mv;
}
`;

export const particleFragment = /* glsl */ `
uniform vec3 uColor;
uniform float uAlpha;
uniform float uDim;
varying float vTwinkle;
void main(){
  vec2 c = gl_PointCoord * 2.0 - 1.0;
  float d = dot(c, c);
  if (d > 1.0) discard;
  float a = (1.0 - d) * uAlpha * vTwinkle;
  vec3 col = mix(uColor, vec3(1.0), 0.5) * a * uDim;
  gl_FragColor = vec4(col, clamp(max(col.r, max(col.g, col.b)), 0.0, 1.0));
  #include <colorspace_fragment>
}
`;

export const ringFragment = /* glsl */ `
uniform vec3 uColor;
uniform float uAlpha;
uniform float uTime;
uniform float uDim;
varying vec2 vUv;
void main(){
  // A thin arc that sweeps around the ring (RingGeometry UVs are planar → use the polar angle).
  float ang = atan(vUv.y - 0.5, vUv.x - 0.5) / 6.2831853 + 0.5;
  float sweep = fract(ang - uTime * 0.35);
  float arc = smoothstep(0.0, 0.55, sweep) * (1.0 - smoothstep(0.55, 0.6, sweep));
  float a = (0.18 + arc * 0.82) * uAlpha;
  vec3 col = uColor * a * uDim;
  gl_FragColor = vec4(col, clamp(max(col.r, max(col.g, col.b)), 0.0, 1.0));
  #include <colorspace_fragment>
}
`;

export const ringVertex = /* glsl */ `
varying vec2 vUv;
void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
`;

// ---------------------------------------------------------------- neural network inside the core

// A slow, smooth drift field shared by neurons and synapses so the network "floats" organically
// and the lines stay attached to their neurons (both sample the same field at the same points).
const DRIFT = /* glsl */ `
vec3 drift(vec3 p, float t){
  return 0.035 * vec3(sin(t*0.53 + p.y*4.1), sin(t*0.47 + p.z*3.7), sin(t*0.61 + p.x*4.3));
}
`;

export const neuronVertex = /* glsl */ `
uniform float uTime;
uniform float uSize;
attribute float aAct;      // 0..1 activation (1 = just fired)
varying float vAct;
${DRIFT}
void main(){
  vAct = aAct;
  vec3 p = position + drift(position, uTime);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_PointSize = uSize * (0.45 + aAct * 1.6) * (3.0 / -mv.z);
  gl_Position = projectionMatrix * mv;
}
`;

export const neuronFragment = /* glsl */ `
uniform vec3 uColor;
uniform float uAlpha;
uniform float uDim;
varying float vAct;
void main(){
  vec2 c = gl_PointCoord * 2.0 - 1.0;
  float d = dot(c, c);
  if (d > 1.0) discard;
  float soma = exp(-d * 9.0);           // bright nucleus
  float halo = (1.0 - d) * 0.35;        // soft body
  float a = (soma + halo) * (0.16 + vAct * 1.4) * uAlpha;
  vec3 col = mix(uColor, vec3(1.0), 0.25 + vAct * 0.55) * a * uDim;
  gl_FragColor = vec4(col, clamp(max(col.r, max(col.g, col.b)), 0.0, 1.0));
  #include <colorspace_fragment>
}
`;

export const synapseVertex = /* glsl */ `
uniform float uTime;
attribute float aT;        // 0..1 position along the synapse (a → b)
attribute vec3 aSpike;     // age of the latest spike (s), direction ±1, synapse length
varying float vT;
varying vec3 vSpike;
${DRIFT}
void main(){
  vT = aT;
  vSpike = aSpike;
  vec3 p = position + drift(position, uTime);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
}
`;

export const synapseFragment = /* glsl */ `
uniform vec3 uColor;
uniform float uAlpha;
uniform float uDim;
uniform float uSpeed;
varying float vT;
varying vec3 vSpike;
void main(){
  float along = vSpike.y > 0.0 ? vT : 1.0 - vT;      // distance from the firing neuron, 0..1
  float front = vSpike.x * uSpeed / max(vSpike.z, 0.01); // how far the spike has travelled, 0..1+
  float behind = front - along;                     // > 0: the spike has passed this point
  float head = exp(-behind * behind * 90.0);
  float trail = behind > 0.0 ? exp(-behind * 5.0) * 0.45 : 0.0;
  float live = 1.0 - smoothstep(1.0, 1.6, front);   // fade once it has arrived
  float spike = (head + trail) * live;
  float a = (0.05 + spike * 1.1) * uAlpha;
  vec3 col = mix(uColor, vec3(1.0), spike * 0.6) * a * uDim;
  gl_FragColor = vec4(col, clamp(max(col.r, max(col.g, col.b)), 0.0, 1.0));
  #include <colorspace_fragment>
}
`;
