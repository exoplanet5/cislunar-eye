// Bundle web/app.js (esbuild IIFE, local three r170) and inline CSS, stars and textures into one page (MMX pattern).
// Data stay in classic-script sidecars loaded by relative <script src="<data>/...">; nothing is copied from --data.
//   node scripts/build_site.mjs [--data dist/data] [--out dist/index.html]
import { build } from 'esbuild';
import { readFile, writeFile, mkdir, access } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { SPEEDS } from '../web/time.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const arg = name => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : null; };
const out = path.resolve(root, arg('--out') || 'dist/index.html');
const data = path.resolve(root, arg('--data') || path.join(path.dirname(out), 'data'));
const dataBase = path.relative(path.dirname(out), data).split(path.sep).join('/') || '.';
const vendor = path.join(root, 'web/vendor/three');

const result = await build({
  entryPoints: [path.join(root, 'web/app.js')], bundle: true, write: false, format: 'iife',
  target: ['es2022'], minify: true, legalComments: 'inline',
  plugins: [{ name: 'local-three', setup(builder) {
    builder.onResolve({ filter: /^three$/ }, () => ({ path: path.join(vendor, 'three.module.min.js') }));
    builder.onResolve({ filter: /^three\/addons\// }, args => ({ path: path.join(vendor, args.path.slice(6)) }));
  } }],
});
const read = (p, enc) => readFile(path.join(root, p), enc);
const [template, theme, stellarReference, hipparcosRaw, earth, night, moon, threeLicense, referenceLicense, celestialLicense] = await Promise.all([
  read('web/index.template.html', 'utf8'), read('web/theme.css', 'utf8'), read('web/assets/stars.json', 'utf8'), read('web/assets/hipparcos.json', 'utf8'),
  read('web/assets/earth_4096.jpg'), read('web/assets/earth_night_2400.jpg'), read('web/assets/moon_4096.jpg'), read('web/vendor/three/LICENSE', 'utf8'),
  read('web/vendor/STARSHIP_LICENSE', 'utf8'), read('web/vendor/CELESTIAL_LICENSE', 'utf8'),
]);
const reference = JSON.parse(stellarReference), hipparcos = JSON.parse(hipparcosRaw);
const stellar = JSON.stringify({ stars: hipparcos.stars, names: reference.names, cons: reference.cons, lines: reference.lines,
  meta: { stars: hipparcos.meta, overlays: reference.meta } });
const license = `/* Three.js r170\n${threeLicense}\nStarship IFT-14 / SatObserver-MX reference patterns\n${referenceLicense}\nStellar catalog derived from d3-celestial\n${celestialLicense}\n*/\n`;
const script = (license + result.outputFiles[0].text).replaceAll('</script', '<\\/script');
const about = [
  '<p>Artificial objects in the Earth–Moon system and near Sun–Earth L1/L2, with natural minimoons as an optional layer. Positions are geometric ICRF states interpolated (cubic Hermite, never extrapolated in the page) from tracks computed offline; times are UTC. Bodies and distances are to scale; markers are enlarged.</p>',
  '<p><strong>Frames.</strong> <em>Sun–Earth rotating</em> (default): Earth-centred, x̂ anti-sunward so SE-L2 lies on +x, ẑ along the Earth–Sun orbital angular momentum; every trail sample is rotated with its own epoch, the Sun direction is fixed and the stars drift about 1° a day. <em>ECI</em>: ICRF axes, as MMX. <em>Earth–Moon rotating</em>: x̂ toward the Moon, ẑ along the lunar orbit normal.</p>',
  '<p><strong>Lagrange points.</strong> CR3BP collinear roots (μ<sub>EM</sub> = 0.0121506, μ<sub>SE</sub> = 3.0404×10⁻⁶) scaled to the instantaneous separations; EM-L4/L5 lie ±60° from the Moon in its instantaneous orbit plane. SE-L1/L2 are nearly stationary in the Sun–Earth frame; EM-L1…L5 co-move with the Moon.</p>',
  '<p><strong>Data quality.</strong> Each segment carries its source and class: ref / pred (agency ephemeris), fit (orbit fit inside its data arc), extrap (latest orbit propagated; hollow marker, dashed trail), gp (Space-Track GP; dimmer). Caveats from the target list are shown in the readout and the list tooltip. Objects without a public ephemeris are listed, not drawn.</p>',
  '<p>Earth: NASA Blue Marble NG, 4096×2048; night side: NASA Earth Observatory city lights, 2400×1200. Moon: LROC WAC mosaic. Stars: 9,827 Hipparcos stars (V &lt; 6.6), deliberately subdued.</p>',
  '<p class="fine">Data: JPL Horizons · JPL DE440s · Minor Planet Center NEOCP · Project Pluto / Bill Gray (find_orb, TLEs) · Space-Track.org · ESA Hipparcos · d3-celestial · three.js. Illustration: Mickey · base map NASA Blue Marble.</p>',
].join('');
const fields = {
  SPEEDS: SPEEDS.map(([v, text]) => `<option value="${v}"${v === 3600 ? ' selected' : ''}>${text}</option>`).join(''),
  WINDOW_S: '2592000', WINDOW_START: '', WINDOW_STOP: '', ABOUT: about, DATA_BASE: dataBase, DATA_BASE_JSON: JSON.stringify(dataBase),
  STELLAR_JSON: stellar.replaceAll('<', '\\u003c'), APP_SCRIPT: script,
  EARTH_TEXTURE: 'data:image/jpeg;base64,' + earth.toString('base64'), NIGHT_TEXTURE: 'data:image/jpeg;base64,' + night.toString('base64'), MOON_TEXTURE: 'data:image/jpeg;base64,' + moon.toString('base64'),
};
const html = template.replace('</style>', () => `${theme}</style>`).replace(/\{\{([A-Z_]+)\}\}/g, (_, key) => {   // web/theme.css: colour tokens, inlined last so they win
  if (!(key in fields)) throw new Error(`Unknown template field: ${key}`); return fields[key];
}).replace(/[\t ]+$/gm, '');
await mkdir(path.dirname(out), { recursive: true });
await writeFile(out, html);
const present = await Promise.all(['manifest.js', 'core.js', 'catalog.js'].map(f => access(path.join(data, f)).then(() => true, () => false)));
console.log(`Built ${path.relative(root, out)}: ${(Buffer.byteLength(html) / 1048576).toFixed(2)} MiB; sidecars from ./${dataBase}/`
  + (present.every(Boolean) ? '' : ` (warning: missing ${['manifest.js', 'core.js', 'catalog.js'].filter((_, i) => !present[i]).join(', ')} in ${path.relative(root, data)})`));
