// MMX web/sky.js: own scene and camera, rotation-only, drawn first with a depth clear. Changes for cislunar-eye:
// retuned constants (§9.8), a uStarGain uniform, a root group rotated by the display frame R(t_now), separate
// star-name toggle, Earth and Moon occlusion, cached label sizes and a grid-hash shared with the body labels.
import * as THREE from 'three';
import { SKY_STYLE, starAppearance } from './sky-style.js';
import { decode } from './loader.js';

// Packed Hipparcos (scripts/pack_stars.py, §11): ra uint16 / dec int16 in units.deg, mag int16 in units.mag → [ra, dec, V].
export function unpackStars(S) {
  if (Array.isArray(S)) return S;
  const ra = decode(S.ra, 'u16'), dec = decode(S.dec, 'i16'), mag = decode(S.mag, 'i16'), { deg, mag: m } = S.units, out = new Array(S.n);
  for (let i = 0; i < S.n; i++) out[i] = [ra[i] * deg, dec[i] * deg, mag[i] * m];
  return out;
}

export function skyDirection(raDegrees, decDegrees) {
  const ra = raDegrees * Math.PI / 180, dec = decDegrees * Math.PI / 180;
  return new THREE.Vector3(Math.cos(dec) * Math.cos(ra), Math.sin(dec), -Math.cos(dec) * Math.sin(ra));
}

function segmentsObject(points, color, opacity) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
  return new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false }));
}

const _d = new THREE.Vector3(), _rel = new THREE.Vector3();

export class StellarBackground {
  constructor(catalog, parent) {
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(38, 1, .01, 10);
    this.root = new THREE.Group(); this.scene.add(this.root);
    this.layer = document.createElement('div'); this.layer.className = 'label-layer sky-labels'; parent.appendChild(this.layer);
    this.labels = [];
    const addLabel = (text, direction, kind) => {
      const el = document.createElement('div'); el.className = `sky-label ${kind}`; el.textContent = text; el.hidden = true;
      this.layer.appendChild(el); this.labels.push({ el, direction, kind, size: null });
    };
    const positions = [], appearances = [];
    for (const [ra, dec, magnitude] of unpackStars(catalog.stars)) {
      const style = starAppearance(magnitude);
      positions.push(...skyDirection(ra, dec).toArray());
      appearances.push(style.diameter, style.opacity, style.ringDiameter);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('appearance', new THREE.Float32BufferAttribute(appearances, 3));
    const core = SKY_STYLE.starCore.map(x => x.toFixed(3)).join(', ');
    this.starMaterial = new THREE.ShaderMaterial({
      uniforms: { pixelRatio: { value: Math.min(devicePixelRatio, 2) }, uStarGain: { value: 1 } }, transparent: true, depthWrite: false,
      vertexShader: `attribute vec3 appearance; uniform float pixelRatio;
        varying vec3 style; varying float spriteSize;
        void main() { style = appearance;
          gl_PointSize = max(1.0, (max(style.x, style.z) + 2.0) * pixelRatio);
          spriteSize = gl_PointSize / pixelRatio;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: `varying vec3 style; varying float spriteSize; uniform float uStarGain;
        void main() { float r = length(gl_PointCoord - vec2(0.5)) * spriteSize;
          float core = 1.0 - smoothstep(max(0.0, style.x * 0.5 - 0.5), style.x * 0.5 + 0.5, r);
          float alpha = min(1.0, core * style.y * uStarGain);
          if (alpha < 0.001) discard;
          gl_FragColor = vec4(vec3(${core}), alpha); }`,
    });
    this.stars = new THREE.Points(geometry, this.starMaterial); this.root.add(this.stars);
    for (const [ra, dec, name] of catalog.names) addLabel(name, skyDirection(ra, dec), 'star-name');
    for (const [ra, dec, name] of catalog.cons) addLabel(name.toUpperCase(), skyDirection(ra, dec), 'constellation-name');
    const lines = [];
    for (const polyline of catalog.lines) {
      for (let i = 1; i < polyline.length; i++) {
        const a = skyDirection(...polyline[i - 1]), b = skyDirection(...polyline[i]);
        const count = Math.max(1, Math.ceil(a.angleTo(b) / (2 * Math.PI / 180)));
        for (let j = 0; j < count; j++) lines.push(...a.clone().lerp(b, j / count).normalize().toArray(), ...a.clone().lerp(b, (j + 1) / count).normalize().toArray());
      }
    }
    this.constellations = segmentsObject(lines, SKY_STYLE.constellation, SKY_STYLE.constellationOpacity); this.root.add(this.constellations);
    const grid = [], top = 90 - SKY_STYLE.decStepDegrees;
    for (let ra = 0; ra < 360; ra += SKY_STYLE.raStepDegrees) {
      for (let dec = -90; dec < 90; dec += 2) grid.push(...skyDirection(ra, dec).toArray(), ...skyDirection(ra, dec + 2).toArray());
      for (let dec = -top; dec <= top; dec += SKY_STYLE.decStepDegrees) addLabel(`${ra / 15}h${dec ? ` ${dec > 0 ? '+' : '−'}${Math.abs(dec)}°` : ''}`, skyDirection(ra, dec), 'grid-label');
    }
    for (let dec = -top; dec <= top; dec += SKY_STYLE.decStepDegrees) {
      for (let ra = 0; ra < 360; ra += 2) grid.push(...skyDirection(ra, dec).toArray(), ...skyDirection(ra + 2, dec).toArray());
    }
    this.grid = segmentsObject(grid, SKY_STYLE.grid, SKY_STYLE.gridOpacity); this.root.add(this.grid);
  }

  render(renderer, camera, layers) {
    this.layers = layers;
    this.stars.visible = layers.stars;
    this.constellations.visible = layers.constellations;
    this.grid.visible = layers.grid;
    this.starMaterial.uniforms.pixelRatio.value = renderer.getPixelRatio();
    this.starMaterial.uniforms.uStarGain.value = layers.starGain ?? 1;
    this.camera.fov = camera.fov; this.camera.aspect = camera.aspect;
    this.camera.quaternion.copy(camera.quaternion); this.camera.updateProjectionMatrix(); this.camera.updateMatrixWorld(true);
    this.root.updateMatrixWorld(true);
    renderer.render(this.scene, this.camera);
  }

  // occupied: the body-label grid hash (labels.js); occluders: [{position, radius}] in scene units relative to the focus.
  positionLabels(width, height, occupied, camera, occluders) {
    for (const item of this.labels) {
      const enabled = item.kind === 'star-name' ? this.layers.stars && this.layers.starNames
        : item.kind === 'constellation-name' ? this.layers.constellations : this.layers.grid;
      if (!enabled) { item.el.hidden = true; continue; }
      const direction = _d.copy(item.direction).applyQuaternion(this.root.quaternion), ndc = direction.clone().project(this.camera);
      let visible = ndc.z > -1 && ndc.z < 1 && Math.abs(ndc.x) < .94 && Math.abs(ndc.y) < .89;
      if (visible) for (const body of occluders) {
        _rel.copy(body.position).sub(camera.position);
        const distance = _rel.dot(direction);
        if (distance > 0 && _rel.addScaledVector(direction, -distance).lengthSq() < body.radius ** 2) { visible = false; break; }
      }
      if (!visible) { item.el.hidden = true; continue; }
      item.el.hidden = false;
      item.size ||= [item.el.offsetWidth, item.el.offsetHeight];
      const box = { x: (ndc.x + 1) * width / 2 + (item.kind === 'star-name' ? 8 : 6), y: (1 - ndc.y) * height / 2 + (item.kind === 'star-name' ? 6 : -12),
        width: item.size[0], height: item.size[1] };
      if (box.y < 55 || box.y + box.height > height - 48 || box.x + box.width > width - 8 || occupied.collides(box, 6)) { item.el.hidden = true; continue; }
      occupied.add(box); item.el.style.transform = `translate(${box.x}px,${box.y}px)`;
    }
  }
}
