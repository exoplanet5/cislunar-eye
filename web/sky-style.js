// MMX web/sky-style.js retuned for "real but subdued" stars (ARCHITECTURE §9.8). The star core is pure grey (review V3):
// a tint would put the brightest stars within a few ΔE of a marker colour family.
export const SKY_STYLE = {
  background: '#04060c', grid: '#182238', constellation: '#3b4d75',
  raStepDegrees: 30, decStepDegrees: 30, gridOpacity: .32, constellationOpacity: .20, starCore: [.84, .84, .84],
};

export function starAppearance(magnitude) {
  // The reference chart specifies marker area in points²; WebGL takes a pixel diameter.
  const area = .30 * Math.max(0, 6.9 - magnitude) ** 2.0;
  const diameter = Math.max(.6, Math.sqrt(area) * 96 / 72);
  return {
    diameter,
    opacity: Math.max(.12, Math.min(.60, .12 + .075 * (6.6 - magnitude))),
    ringDiameter: 0,
  };
}
