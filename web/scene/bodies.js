// Earth (Blue Marble, core quaternion, terminator #ffd54f, optional Sun −12° ring #4fc3f7) and the textured Moon,
// tidally oriented to face Earth (ARCHITECTURE §9.6–9.7, AM7). MMX app.js:257-342 patterns.
import * as THREE from 'three';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { solarTerminator, RE_KM } from '../states.js';

export const SUN = '#ffd54f', ACCENT = '#4fc3f7';
const LIFT = 1.0002, AXIS = new THREE.Vector3(0, 0, 1);

function texture(img, renderer) {
  const t = new THREE.Texture(img); t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  if (img.complete && img.naturalWidth) t.needsUpdate = true; else img.addEventListener('load', () => { t.needsUpdate = true; }, { once: true });
  return t;
}

export function ring(color, width, opacity, materials) {
  const g = new LineGeometry();
  g.setPositions(Array.from({ length: 721 }, (_, i) => [Math.cos(i * Math.PI / 360), Math.sin(i * Math.PI / 360), 0]).flat());
  const m = new LineMaterial({ color, linewidth: width, transparent: true, opacity, worldUnits: false, depthWrite: false });
  materials.push(m);
  const line = new Line2(g, m); line.renderOrder = 3; return line;
}

export class Bodies {
  constructor(world, scene, renderer, images, materials) {
    this.w = world;
    // C8: with a night-lights image (images.night, NASA Earth Observatory city lights) the night side carries city lights as an emissive map, faded out across
    // the terminator by the Sun direction (cos z from −0.05 to −0.2 ≈ the Sun 3°–12° below the horizon), and the ambient
    // drops 0.22 → 0.10 so the night hemisphere reads as night.
    const night = images.night?.src ? texture(images.night, renderer) : null;
    scene.add(new THREE.AmbientLight(0xffffff, night ? .10 : .22));
    this.light = new THREE.DirectionalLight(0xffffff, 2.3); scene.add(this.light, this.light.target);
    const earthMat = new THREE.MeshPhongMaterial({ map: texture(images.earth, renderer), shininess: 5, specular: 0x151b22,
      ...(night ? { emissiveMap: night, emissive: 0xffffff, emissiveIntensity: .75 } : {}) });
    if (night) {
      earthMat.onBeforeCompile = sh => {
        sh.fragmentShader = sh.fragmentShader.replace('#include <emissivemap_fragment>', '#include <emissivemap_fragment>\n'
          + '#if NUM_DIR_LIGHTS > 0\n  totalEmissiveRadiance *= 1.0 - smoothstep(-0.2, -0.05, dot(normal, directionalLights[0].direction));\n#endif');
      };
      earthMat.customProgramCacheKey = () => 'earth-night';
    }
    this.earth = new THREE.Mesh(new THREE.SphereGeometry(1, 128, 64), earthMat);
    this.moonRadius = world.moonRadius / RE_KM;
    this.moon = new THREE.Mesh(new THREE.SphereGeometry(this.moonRadius, 96, 48),
      new THREE.MeshPhongMaterial({ map: texture(images.moon, renderer), shininess: 2, specular: 0x050505, color: 0xd8dade }));
    this.terminator = ring(SUN, 1.2, .55, materials);
    this.twilight = ring(ACCENT, 1.0, .5, materials); this.twilight.visible = false;
    scene.add(this.earth, this.moon, this.terminator, this.twilight);
    this.qf = new THREE.Quaternion(); this.q = new THREE.Quaternion(); this.q0 = new THREE.Quaternion(); this.q1 = new THREE.Quaternion();
    this.m4 = new THREE.Matrix4(); this.v = new THREE.Vector3(); this.n = new THREE.Vector3();
  }

  // Scene rotation of the frame as a quaternion.
  frameQuaternion(S, q) {
    this.m4.set(S[0], S[1], S[2], 0, S[3], S[4], S[5], 0, S[6], S[7], S[8], 0, 0, 0, 0, 1);
    return q.setFromRotationMatrix(this.m4);
  }

  update(t, focusKm, layers) {
    const w = this.w, f = focusKm, sv = (p, out) => out.set((p[0] - f[0]) / RE_KM, (p[2] - f[2]) / RE_KM, -(p[1] - f[1]) / RE_KM);
    this.frameQuaternion(w.S, this.qf);
    sv([0, 0, 0], this.earth.position);
    this.earth.quaternion.copy(this.qf).multiply(w.earthQuaternion(t, this.q, this.q0, this.q1));
    sv(w.moonF, this.moon.position);
    // Tidal orientation: body +X (0° longitude) toward Earth, +Z along the ecliptic pole (the lunar spin axis is 1.5° from
    // it, 6.7° from the orbit normal; Cassini's laws), mapped like the globe.
    const r = w.moon, s0 = w.sun, h = [s0[1] * s0[5] - s0[2] * s0[4], s0[2] * s0[3] - s0[0] * s0[5], s0[0] * s0[4] - s0[1] * s0[3]];
    const hn = Math.hypot(...h), z = h.map(c => c / hn), rz = r[0] * z[0] + r[1] * z[1] + r[2] * z[2];
    const xr = [rz * z[0] - r[0], rz * z[1] - r[1], rz * z[2] - r[2]], xn = Math.hypot(...xr);
    const x = xr.map(c => c / xn), y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]];
    const toScene = c => [c[0], c[2], -c[1]];
    const ex = toScene(x), ey = toScene(y), ez = toScene(z);   // body axes in ICRF scene coords; local = (bx, bz, −by)
    this.m4.set(ex[0], ez[0], -ey[0], 0, ex[1], ez[1], -ey[1], 0, ex[2], ez[2], -ey[2], 0, 0, 0, 0, 1);
    this.moon.quaternion.setFromRotationMatrix(this.m4).premultiply(this.qf);
    this.moon.visible = layers.moon !== false;
    const s = w.sunF;
    this.light.position.set(s[0], s[2], -s[1]).normalize().multiplyScalar(1000); this.light.target.position.set(0, 0, 0);
    const boundary = solarTerminator([s[0] / RE_KM, s[1] / RE_KM, s[2] / RE_KM], 1);
    const normal = this.n.set(boundary.normal[0], boundary.normal[2], -boundary.normal[1]);
    this.terminator.quaternion.setFromUnitVectors(AXIS, normal);
    this.terminator.position.copy(this.earth.position).addScaledVector(normal, Math.hypot(...boundary.center) * LIFT);
    this.terminator.scale.setScalar(boundary.radius * LIFT);
    // Sun −12°: the small circle 102° from the subsolar point.
    const a = (90 + 12) * Math.PI / 180;
    this.twilight.visible = !!layers.twilight;
    this.twilight.quaternion.copy(this.terminator.quaternion);
    this.twilight.position.copy(this.earth.position).addScaledVector(normal, Math.cos(a) * LIFT);
    this.twilight.scale.setScalar(Math.sin(a) * LIFT);
  }
}
