// Forked from the vendored three r170 LineMaterial (screen-space branch, logdepth chunks intact) for batched trails
// (ARCHITECTURE §9.4). Per instance: start/end (scene units relative to the batch anchor), sample times (s since
// the window start), arc length along the trail at the start (scene units), target index and style. Per target
// (DataTexture, 2 rows): rgb + visible; past_s, future_s, highlight, alpha gain (idle / derelict dimming, V2). The time
// window, fading and dashing run on the GPU; nothing is re-uploaded per frame. Dashes are screen-space (C1): each
// instance measures its own px per scene unit k along its direction at its midpoint (depth and foreshortening), k is
// rounded to a half octave so that neighbours share one dash unit, and the phase is the 3D arc: cycles stay within
// DASH_PX·[2^-¼, 2^¼] in every view, continuous along the trail (one phase step where k crosses a half octave) and
// fixed in time. While a target is selected (uAnySel) every other trail drops to 30 % alpha.
import * as THREE from 'three';

export const STYLE = { ref: 0, pred: 1, fit: 2, extrap: 3, gp: 4, conic: 5, HEAD: 16 };
export const DASH_PX = 10;                                                     // future dash cycle on screen
export const DASH = { future: [1, .58], extrap: [.5, .25], conic: [.4, .12] }; // [cycle, lit] in units of DASH_PX
export const MIN_FORESHORTEN = .05;                                            // k floor for trails seen end-on
// The shader's dash unit (scene units per cycle) for an instance with k px per scene unit.
export const dashUnit = k => DASH_PX / 2 ** (Math.round(2 * Math.log2(k)) / 2);
// k of a view-space segment: |d(px)/ds| = ½·H·P11/w · |d.xy + (m.xy/w)·d.z| (w = −m.z, d unit direction, m midpoint).
export function pxPerUnit(start, end, heightPx, projY) {
  const m = [0, 1, 2].map(i => (start[i] + end[i]) / 2), w = -m[2], L = Math.hypot(end[0] - start[0], end[1] - start[1], end[2] - start[2]);
  const d = [0, 1, 2].map(i => (end[i] - start[i]) / L), f = Math.hypot(d[0] + m[0] / w * d[2], d[1] + m[1] / w * d[2]);
  return .5 * heightPx * projY / w * Math.max(f, MIN_FORESHORTEN);
}
const f = x => x.toFixed(2);

const vertexShader = /* glsl */`
#include <common>
#include <logdepthbuf_pars_vertex>
uniform float linewidth;
uniform vec2 resolution;
uniform float uNow;
uniform sampler2D uTargets;
attribute vec3 instanceStart;
attribute vec3 instanceEnd;
attribute vec2 instanceTime;
attribute vec2 instanceInfo;
attribute float instanceArc;
varying vec2 vUv;
varying float vT;
varying vec4 vColor;
varying vec4 vParam;
varying float vStyle;
varying float vArc;
varying float vDash;

void trimSegment( const in vec4 start, inout vec4 end ) {
  float a = projectionMatrix[ 2 ][ 2 ];
  float b = projectionMatrix[ 3 ][ 2 ];
  float nearEstimate = - 0.5 * b / a;
  float alpha = ( nearEstimate - start.z ) / ( end.z - start.z );
  end.xyz = mix( start.xyz, end.xyz, alpha );
}

void main() {
  int index = int( instanceInfo.x + 0.5 );
  vColor = texelFetch( uTargets, ivec2( index, 0 ), 0 );
  vParam = texelFetch( uTargets, ivec2( index, 1 ), 0 );
  float style = instanceInfo.y;
  bool head = style >= 15.5;
  vStyle = head ? style - 16.0 : style;
  vT = ( position.y < 0.5 ) ? instanceTime.x : instanceTime.y;
  vUv = uv;
  bool hidden = vColor.a < 0.5 || instanceTime.y < uNow - vParam.x || instanceTime.x > uNow + vParam.y
    || ( ! head && instanceTime.x < uNow && instanceTime.y > uNow );
  float aspect = resolution.x / resolution.y;
  vec4 start = modelViewMatrix * vec4( instanceStart, 1.0 );
  vec4 end = modelViewMatrix * vec4( instanceEnd, 1.0 );
  bool perspective = ( projectionMatrix[ 2 ][ 3 ] == - 1.0 );
  if ( perspective ) {
    if ( start.z < 0.0 && end.z >= 0.0 ) trimSegment( start, end );
    else if ( end.z < 0.0 && start.z >= 0.0 ) trimSegment( end, start );
  }
  // C1: px per scene unit along this instance (pxPerUnit), rounded to a half octave; the phase is the 3D arc, taken
  // modulo the unit at the vertex (small varying, exact interpolation), identical for all six vertices of the instance.
  vec3 sd = end.xyz - start.xyz, sm = 0.5 * ( start.xyz + end.xyz );
  float sw = perspective ? max( 1e-9, - sm.z ) : 1.0, sl = length( sd );
  vec2 sf = sl > 0.0 ? ( sd.xy + ( perspective ? sm.xy / sw : vec2( 0.0 ) ) * sd.z ) / sl : vec2( 1.0, 0.0 );
  float k = 0.5 * resolution.y * projectionMatrix[ 1 ][ 1 ] / sw * max( length( sf ), ${f(MIN_FORESHORTEN)} );
  vDash = ${f(DASH_PX)} / exp2( round( 2.0 * log2( k ) ) * 0.5 );
  vArc = mod( instanceArc, vDash ) + ( ( position.y < 0.5 ) ? 0.0 : length( instanceEnd - instanceStart ) );
  vec4 clipStart = projectionMatrix * start;
  vec4 clipEnd = projectionMatrix * end;
  vec3 ndcStart = clipStart.xyz / clipStart.w;
  vec3 ndcEnd = clipEnd.xyz / clipEnd.w;
  vec2 dir = ndcEnd.xy - ndcStart.xy;
  dir.x *= aspect;
  dir = length( dir ) > 0.0 ? normalize( dir ) : vec2( 1.0, 0.0 );
  vec2 offset = vec2( dir.y, - dir.x );
  dir.x /= aspect;
  offset.x /= aspect;
  if ( position.x < 0.0 ) offset *= - 1.0;
  if ( position.y < 0.0 ) offset += - dir;
  else if ( position.y > 1.0 ) offset += dir;
  float future = 0.5 * ( instanceTime.x + instanceTime.y ) > uNow ? 1.0 : 0.0;
  float width = linewidth * mix( 1.0, 0.62, future ) * ( vParam.z > 0.5 ? 1.45 : 1.0 );
  offset *= width;
  offset /= resolution.y;
  vec4 clip = ( position.y < 0.5 ) ? clipStart : clipEnd;
  offset *= clip.w;
  clip.xy += offset;
  gl_Position = hidden ? vec4( 2.0, 2.0, 2.0, 1.0 ) : clip;
  vec4 mvPosition = ( position.y < 0.5 ) ? start : end;
  #include <logdepthbuf_vertex>
}`;

const fragmentShader = /* glsl */`
uniform float uNow;
uniform float uAnySel;
uniform float opacity;
varying vec2 vUv;
varying float vT;
varying vec4 vColor;
varying vec4 vParam;
varying float vStyle;
varying float vArc;
varying float vDash;
#include <common>
#include <logdepthbuf_pars_fragment>

void main() {
  if ( abs( vUv.y ) > 1.0 ) {
    float a = vUv.x;
    float b = ( vUv.y > 0.0 ) ? vUv.y - 1.0 : vUv.y + 1.0;
    if ( a * a + b * b > 1.0 ) discard;
  }
  float age = uNow - vT;
  if ( age > vParam.x || - age > vParam.y ) discard;
  float u = mod( vArc, vDash ) / vDash;                // position inside the dash cycle, 0..1
  float alpha;
  if ( age >= 0.0 ) {
    alpha = mix( 0.95, 0.10, clamp( age / max( vParam.x, 1.0 ), 0.0, 1.0 ) );
    if ( vParam.z > 0.5 ) alpha = max( alpha, 0.45 );
  } else {
    alpha = vParam.z > 0.5 ? 0.75 : 0.55;
    if ( u > ${f(DASH.future[1])} ) discard;
  }
  int style = int( vStyle + 0.5 );
  if ( style == 3 ) {                                 // extrap: short dashes everywhere, 60 %
    alpha *= 0.6;
    if ( age >= 0.0 && mod( u, ${f(DASH.extrap[0])} ) > ${f(DASH.extrap[1])} ) discard;
  } else if ( style == 4 ) {                          // gp: dimmer
    alpha *= 0.68;
  } else if ( style == 2 ) {                          // fit: slightly dimmer than an agency ephemeris
    alpha *= 0.85;
  } else if ( style == 5 ) {                          // conic: dotted
    alpha *= 0.5;
    if ( mod( u, ${f(DASH.conic[0])} ) > ${f(DASH.conic[1])} ) discard;
  }
  if ( uAnySel > 0.5 && vParam.z < 0.5 ) alpha *= 0.3;
  gl_FragColor = vec4( vColor.rgb, alpha * vParam.w * opacity );
  #include <logdepthbuf_fragment>
  #include <colorspace_fragment>
}`;

export function trailMaterial(texture) {
  return new THREE.ShaderMaterial({
    uniforms: { linewidth: { value: 1.5 }, resolution: { value: new THREE.Vector2(1, 1) }, uNow: { value: 0 }, uAnySel: { value: 0 }, opacity: { value: 1 },
      uTargets: { value: texture } },
    vertexShader, fragmentShader, transparent: true, depthWrite: false,
  });
}

const ATTRS = ['instanceStart', 'instanceEnd', 'instanceTime', 'instanceInfo', 'instanceArc'];

// One instanced batch of `capacity` segments built on the LineSegmentsGeometry quad.
export function trailGeometry(capacity) {
  const g = new THREE.InstancedBufferGeometry();
  g.setIndex([0, 2, 1, 2, 3, 1, 2, 4, 3, 4, 5, 3, 4, 6, 5, 6, 7, 5]);
  g.setAttribute('position', new THREE.Float32BufferAttribute([-1, 2, 0, 1, 2, 0, -1, 1, 0, 1, 1, 0, -1, 0, 0, 1, 0, 0, -1, -1, 0, 1, -1, 0], 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute([-1, 2, 1, 2, -1, 1, 1, 1, -1, -1, 1, -1, -1, -2, 1, -2], 2));
  const attr = (name, size) => {
    const a = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(1, capacity) * size), size);
    a.setUsage(THREE.DynamicDrawUsage); g.setAttribute(name, a); return a.array;
  };
  const buf = { start: attr('instanceStart', 3), end: attr('instanceEnd', 3), time: attr('instanceTime', 2), info: attr('instanceInfo', 2), arc: attr('instanceArc', 1) };
  g.instanceCount = 0;
  return { geometry: g, buf, capacity };
}

export function markDirty(geometry, count) {
  geometry.instanceCount = count;
  for (const name of ATTRS) {
    const a = geometry.attributes[name];
    a.clearUpdateRanges(); a.addUpdateRange(0, Math.max(1, count) * a.itemSize); a.needsUpdate = true;
  }
}
