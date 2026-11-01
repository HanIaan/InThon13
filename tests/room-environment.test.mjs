import test from 'node:test';
import assert from 'node:assert/strict';
import { interpolateWindowLight, prepareWindow, projectSunPatch } from '../lib/room-light.ts';
import { prepareFloorNoise, floorNoiseScore } from '../lib/floor-noise.ts';

const lat = 37.58, lng = 127.02;
const lngM = 111000 * Math.cos(lat * Math.PI / 180);
const point = (x, y) => [lat + y / 111000, lng + x / lngM];
const ring = (x0, y0, x1, y1) => [point(x0, y0), point(x1, y0), point(x1, y1), point(x0, y1), point(x0, y0)];
const home = { lat, lng, ring: ring(-5, -5, 5, 5) };
const south = { floor: 1, direction: 'south' };

test('night and sun behind the window produce no indoor direct light', () => {
  const field = prepareWindow(home, []);
  assert.equal(field.sample(south, -1, 180).strength, 0);
  assert.equal(field.sample(south, 40, 0).visibility, 0);
  assert.equal(field.sample(south, 40, 180).visibility, 1);
});

test('a neighboring building blocks a low floor; a higher window clears its roof', () => {
  const field = prepareWindow(home, [{ ring: ring(-30, -30, 30, -20), height: 12, levels: 4, ground: 0 }]);
  assert.equal(field.sample(south, 20, 180).visibility, 0);
  assert.equal(field.sample({ ...south, floor: 8 }, 20, 180).visibility, 1);
});

test('partial roof obstruction lights part of the window, not an all-or-nothing image', () => {
  const field = prepareWindow(home, [{ ring: ring(-30, -30, 30, -20), height: 6.6, levels: 2, ground: 0 }]);
  const light = field.sample(south, 20, 180);
  assert.ok(light.visibility > 0 && light.visibility < 1, String(light.visibility));
  assert.ok(light.patches.some((p) => p.bottom > 0.45 && p.bottom < p.top));
});

test('terrain and unknown building heights participate in occlusion', () => {
  assert.equal(prepareWindow(home, [], (y) => y < lat - 100 / 111000 ? 200 : 0).sample(south, 10, 180).visibility, 0);
  assert.equal(prepareWindow(home, [{ ring: ring(-30, -30, 30, -20), levels: null }]).sample(south, 10, 180).visibility, 0);
});

test('sun patches move with angle, clip at the room boundary, and stay finite', () => {
  const field = prepareWindow(home, []);
  const a = field.sample(south, 30, 160), b = field.sample(south, 65, 200);
  assert.notEqual(projectSunPatch(a.patches[5], a, 'bed'), projectSunPatch(b.patches[5], b, 'bed'));
  for (const altitude of [0.01, 10, 40, 89]) for (const azimuth of [90.51, 140, 180, 230, 269.49]) {
    const light = field.sample(south, altitude, azimuth);
    for (const patch of light.patches) for (const surface of ['floor', 'bed', 'wall', 'leftWall', 'rightWall']) {
      assert.doesNotMatch(projectSunPatch(patch, light, surface), /NaN|Infinity/);
    }
  }
});

test('morning and afternoon reach opposite side walls, with a shared floor seam', () => {
  const field = prepareWindow(home, []);
  const coordinates = path => [...path.matchAll(/[ML](-?[\d.]+),(-?[\d.]+)/g)].map(m => `${m[1]},${m[2]}`);
  for (const [azimuth,wall,other] of [[125,'rightWall','leftWall'],[235,'leftWall','rightWall']]) {
    const light = field.sample(south, 25, azimuth);
    let junctions = 0;
    for (const patch of light.patches) {
      const floor = coordinates(projectSunPatch(patch,light,'floor'));
      const side = coordinates(projectSunPatch(patch,light,wall));
      assert.equal(projectSunPatch(patch,light,other),'');
      junctions += floor.filter(p => side.includes(p)).length;
    }
    assert.ok(junctions >= 2, `${wall}: ${junctions} common edge vertices`);
  }
});

test('a slowly moving roof shadow changes continuously rather than blinking whole rows', () => {
  const field = prepareWindow(home, [{ ring: ring(-30, -30, 30, -20), height: 6.6, levels: 2, ground: 0 }]);
  const a = field.sample(south, 20, 180), b = field.sample(south, 20.01, 180);
  assert.ok(b.visibility > a.visibility);
  assert.ok(b.visibility - a.visibility < 0.01);
});

test('day/night and changed column visibility crossfade without reassigning patches', () => {
  const field = prepareWindow(home, []);
  const night = field.sample(south, -10, 0), noon = field.sample(south, 30, 180);
  const entering = interpolateWindowLight(night, noon, 0.5);
  assert.equal(entering.altitude, noon.altitude);
  assert.ok(entering.patches.every((p) => p.opacity === 0.5));
  assert.ok(interpolateWindowLight(noon, night, 0.5).strength > 0);
  assert.equal(interpolateWindowLight(noon, night, 1).patches.length, 0);
  const fewer = { ...noon, patches: noon.patches.slice(1) };
  const mid = interpolateWindowLight(noon, fewer, 0.5);
  const first = mid.patches.find((p) => p.left === noon.patches[0].left);
  assert.equal(first.opacity, 0.5);
  assert.equal(first.right, noon.patches[0].right);
});

test('revisiting a sampled time reuses the calculation without querying terrain again', () => {
  let reads = 0;
  const field = prepareWindow(home, [], () => { reads++; return 0; });
  const first = field.sample(south, 30, 180), count = reads;
  assert.equal(field.sample(south, 30, 180), first);
  assert.equal(reads, count);
});

test('floor noise uses slant distance and preserves the reference at the first floor', () => {
  const road = [{ width: 10, line: [point(-100, -15), point(100, -15)] }];
  const estimate = prepareFloorNoise(home, road);
  assert.equal(estimate(60, 1).db, 60);
  assert.ok(estimate(60, 10).db < estimate(60, 3).db);
  const expected = -10 * Math.log10(Math.hypot(10, 28) / Math.hypot(10, 1));
  assert.ok(Math.abs(estimate(60, 10).deltaDb - expected) < 0.001);
});

test('higher floors can be louder when they approach an elevated road', () => {
  const road = [{ width: 10, line: [point(-100, -15), point(100, -15)] }];
  const estimate = prepareFloorNoise(home, road, (y) => y < lat - 10 / 111000 ? 20 : 0);
  assert.ok(estimate(60, 7).db > estimate(60, 1).db);
});

test('missing data or unknown roads never invent floor attenuation', () => {
  assert.equal(prepareFloorNoise(home, [])(null, 10).db, null);
  assert.equal(prepareFloorNoise(home, [])(60, 10).db, 60);
  assert.equal(prepareFloorNoise(home, [])(60, 10).roadM, null);
});

test('floor correction updates the score without replacing missing server scores', () => {
  assert.equal(floorNoiseScore(null, 60, 55), null);
  assert.equal(floorNoiseScore(50, null, 55), null);
  assert.equal(floorNoiseScore(50, 60, 60), 50);
  assert.ok(floorNoiseScore(50, 60, 55) > 50);
  const cal = { percentiles: [5, 50, 95], values: [40, 50, 60] };
  assert.equal(floorNoiseScore(50, 50, 45, cal), 75);
});
