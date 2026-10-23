// One THREE.Points per layer with a shared SDF sprite shader (ARCHITECTURE §9.3): per vertex colour, size (CSS px),
// shape (0 circle, 1 square, 2 diamond, 3 triangle, 4 ring, 5 Lagrange: hollow diamond + centre dot), hollow flag and
// alpha. Shapes are proportioned so that at the 8.5 px default the outlines of payload (circle), rocket body (square),
// debris (diamond) and natural (triangle, the circle's area) differ by ≥ 1.5 px in some direction (V4). Logdepth chunks
// included.
import * as THREE from 'three';

export const SHAPE = { circle: 0, square: 1, diamond: 2, triangle: 3, ring: 4, lagrange: 5 };
// Glyph proportions in units of r = size / 2 (square half-side, diamond (|x|+|y|)·√½ bound, triangle inradius with its
// centroid .15 r below the centre, Lagrange diamond), the hollow stroke half-width (px) and the sprite (px, scale · size +
// pad) that holds the farthest corner (the triangle's, 1.64 r) and the 1.6 px halo at every size.
export const SDF = { square: .96, diamond: .96, triangle: .78, lagrange: .80, stroke: 1.2, sprite: [1.66, 3.4] };
export const spriteSize = size => SDF.sprite[0] * size + SDF.sprite[1];
export const SIZE = { idle: 8.5, hover: 10, selected: 12 };

// Target marker style (V4): hollow = the position is not from a current ephemeris (stale or extrapolated) and sits at
// 60 %; otherwise GP fills at 82 % and derelicts are dimmed to 70 % of that; selected or hovered markers are full.
export function targetStyle(T, { selected = false, hovered = false, stale = false } = {}) {
  const q = T.seg?.quality, derelict = T.cat?.status === 'derelict';
  return { size: selected ? SIZE.selected : hovered ? SIZE.hover : SIZE.idle, shape: T.shape, hollow: stale || q === 'extrap',
    alpha: selected || hovered ? 1 : stale ? .6 : (q === 'gp' ? .82 : 1) * (derelict ? .7 : 1) };
}

// Circumradius (px from the sprite centre) of a shape at `size`, from the shader's constants: circle radius, square
// corner, diamond tip, triangle apex or base corner.
export function extent(shape, size) {
  const r = size / 2, c = SDF.triangle * r;
  if (shape === SHAPE.square) return SDF.square * r * Math.SQRT2;
  if (shape === SHAPE.diamond) return SDF.diamond * r * Math.SQRT2;
  if (shape === SHAPE.lagrange) return SDF.lagrange * r * Math.SQRT2;
  if (shape === SHAPE.triangle) return Math.max(2 * c - .15 * r, Math.hypot(Math.sqrt(3) * c, c + .15 * r));
  return r;
}

const f = x => x.toFixed(2);
const vertexShader = /* glsl */`
#include <common>
#include <logdepthbuf_pars_vertex>
attribute vec3 color;
attribute vec4 style;
uniform float pixelRatio;
varying vec3 vColor;
varying vec4 vStyle;
void main() {
  vColor = color; vStyle = style;
  vec4 mvPosition = modelViewMatrix * vec4( position, 1.0 );
  gl_Position = style.w > 0.0 ? projectionMatrix * mvPosition : vec4( 2.0, 2.0, 2.0, 1.0 );
  gl_PointSize = ( ${f(SDF.sprite[0])} * style.x + ${f(SDF.sprite[1])} ) * pixelRatio;
  #include <logdepthbuf_vertex>
}`;

const fragmentShader = /* glsl */`
uniform vec3 halo;
varying vec3 vColor;
varying vec4 vStyle;
#include <common>
#include <logdepthbuf_pars_fragment>
void main() {
  vec2 p = ( gl_PointCoord - 0.5 ) * ( ${f(SDF.sprite[0])} * vStyle.x + ${f(SDF.sprite[1])} );
  float r = 0.5 * vStyle.x, d;
  int shape = int( vStyle.y + 0.5 );
  if ( shape == 1 ) d = max( abs( p.x ), abs( p.y ) ) - ${f(SDF.square)} * r;
  else if ( shape == 2 ) d = ( abs( p.x ) + abs( p.y ) ) * 0.7071 - ${f(SDF.diamond)} * r;
  else if ( shape == 5 ) d = ( abs( p.x ) + abs( p.y ) ) * 0.7071 - ${f(SDF.lagrange)} * r;
  else if ( shape == 3 ) { vec2 q = vec2( abs( p.x ), p.y - 0.15 * r ); d = max( q.x * 0.866 - q.y * 0.5, q.y ) - ${f(SDF.triangle)} * r; }
  else d = length( p ) - r;
  float alpha;
  if ( shape == 4 || shape == 5 || vStyle.z > 0.5 ) {
    float w = shape == 4 ? 0.8 : ${f(SDF.stroke)};
    alpha = 1.0 - smoothstep( w - 0.5, w + 0.5, abs( d + w ) );
    if ( shape == 5 ) alpha = max( alpha, 1.0 - smoothstep( -0.5, 0.5, length( p ) - 0.36 * r ) );
  } else alpha = 1.0 - smoothstep( -0.5, 0.5, d );
  float edge = 1.0 - smoothstep( -0.5, 0.5, d - 1.6 );
  vec3 rgb = mix( halo, vColor, alpha / max( edge, 1e-3 ) );
  float a = max( alpha, 0.55 * edge ) * vStyle.w;
  if ( a < 0.01 ) discard;
  gl_FragColor = vec4( rgb, a );
  #include <logdepthbuf_fragment>
  #include <colorspace_fragment>
}`;

export class PointLayer {
  constructor(capacity, { depthTest = true, renderOrder = 2 } = {}) {
    this.capacity = capacity;
    this.position = new Float32Array(capacity * 3); this.color = new Float32Array(capacity * 3); this.style = new Float32Array(capacity * 4);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(this.position, 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('color', new THREE.BufferAttribute(this.color, 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('style', new THREE.BufferAttribute(this.style, 4).setUsage(THREE.DynamicDrawUsage));
    g.setDrawRange(0, 0);
    this.material = new THREE.ShaderMaterial({ uniforms: { pixelRatio: { value: 1 }, halo: { value: new THREE.Color('#04060c') } },
      vertexShader, fragmentShader, transparent: true, depthWrite: false, depthTest });
    this.points = new THREE.Points(g, this.material);
    this.points.frustumCulled = false; this.points.renderOrder = renderOrder;
    this.count = 0;
  }

  set(i, p, color, size, shape, hollow, alpha) {
    this.position[i * 3] = p[0]; this.position[i * 3 + 1] = p[1]; this.position[i * 3 + 2] = p[2];
    this.color[i * 3] = color.r; this.color[i * 3 + 1] = color.g; this.color[i * 3 + 2] = color.b;
    this.style[i * 4] = size; this.style[i * 4 + 1] = shape; this.style[i * 4 + 2] = hollow ? 1 : 0; this.style[i * 4 + 3] = alpha;
  }

  // A target's marker with the V4 semantics (flags: selected, hovered, stale — the caller decides staleness, §6.4).
  setTarget(i, p, T, flags) {
    const s = targetStyle(T, flags);
    this.set(i, p, T.color, s.size, s.shape, s.hollow, s.alpha);
  }

  commit(count, pixelRatio) {
    this.count = count;
    const g = this.points.geometry;
    g.setDrawRange(0, count);
    for (const name of ['position', 'color', 'style']) g.attributes[name].needsUpdate = true;
    this.material.uniforms.pixelRatio.value = pixelRatio;
  }
}
