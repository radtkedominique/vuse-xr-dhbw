// Pipeline 2, Backend threejs – läuft im headless Chromium, gesteuert von threejs_render.py.
// Szene auf Einheitsgröße, Kamera im Kreis (leicht erhöht, Abstand 2.0, Blender-Standardkamera),
// weißes Umgebungslicht + Blender-Standard-Punktlicht, transparenter Hintergrund.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

// Blender-Standardkamera: 50 mm Brennweite, 36 mm Sensor -> 39.6° Bildwinkel (quadratisches Bild)
const FOV = THREE.MathUtils.radToDeg(2 * Math.atan(18 / 50));
const CAM_DIST = 2.0;
const ELEVATION = -0.2617993878; // DiffuRank 'z-circular-elevated'
// Blender-Standardlicht (Factory-Startup, 1000 W Punktlicht). Blender ist Z-up, three Y-up: (x, y, z) -> (x, z, -y)
const LIGHT_POS = new THREE.Vector3(4.0762, 5.9039, -1.0055);

let renderer, scene, camera, loader, opts, composer = null;
let root = null, parser = null, ctxMat = null, hlMat = null;

// Blender 3.4 (Cycles, Principled BSDF v1) addiert Glanz zum diffusen Anteil, three.js zieht ihn energieerhaltend ab.
// Bei leicht metallischen/glänzenden Materialien sind three-Bilder sonst ~10 % dunkler. energy: blender schaltet das ab.
const ENERGY_OLD = 'vec3 diffuse = material.diffuseColor * ( 1.0 - max( max( totalScattering.r, totalScattering.g ), totalScattering.b ) );';
function useBlenderEnergy() {
  const chunk = THREE.ShaderChunk.lights_physical_pars_fragment;
  if (!chunk.includes(ENERGY_OLD)) {
    console.error('energy: blender – Shader-Stelle in dieser three.js-Version nicht gefunden, bleibt energieerhaltend');
    return;
  }
  THREE.ShaderChunk.lights_physical_pars_fragment = chunk.replace(ENERGY_OLD, 'vec3 diffuse = material.diffuseColor;');
}

window.vuseInit = (o) => {
  opts = o;
  if (o.energy === 'blender') useBlenderEnergy();
  const size = o.resolution * o.supersample;
  renderer = new THREE.WebGLRenderer({
    canvas: document.getElementById('c'), antialias: true, alpha: true, preserveDrawingBuffer: true,
  });
  renderer.setPixelRatio(1);
  renderer.setSize(size, size, false);
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = o.colorspace === 'linear' ? THREE.LinearSRGBColorSpace : THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping; // wie Blender 'Standard'
  renderer.shadowMap.enabled = o.shadows;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  scene = new THREE.Scene();
  // Gleichmäßig weiße Umgebung = Blender-Welt (1, 1, 1) mit Stärke render.ambient. Spiegelt auch in Metall.
  const envScene = new THREE.Scene();
  envScene.background = new THREE.Color(1, 1, 1);
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(envScene).texture;
  scene.environmentIntensity = o.ambient;
  pmrem.dispose();

  const light = new THREE.PointLight(0xffffff, o.point_light, 0, 2);
  light.position.copy(LIGHT_POS);
  light.castShadow = o.shadows;
  light.shadow.mapSize.set(2048, 2048);
  light.shadow.camera.near = 1;
  light.shadow.camera.far = 20;
  light.shadow.bias = -0.0005;
  light.shadow.normalBias = 0.002;
  scene.add(light);

  camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 10); // Objekt liegt in Einheitswürfel, Kamera bei 2.0

  // Umgebungsverdeckung: Cycles verschattet Spalten/Innenräume automatisch, ein Rasterizer nicht.
  // Ohne AO ist z. B. das Gehäuseinnere unter dem Zentrifugendeckel hell statt dunkel.
  if (o.ao) {
    const rt = new THREE.WebGLRenderTarget(size, size, { type: THREE.HalfFloatType, samples: 4 });
    composer = new EffectComposer(renderer, rt);
    composer.setPixelRatio(1);
    composer.setSize(size, size);
    composer.addPass(new RenderPass(scene, camera));
    const gtao = new GTAOPass(scene, camera, size, size);
    gtao.updateGtaoMaterial({ radius: o.ao_radius, distanceExponent: 1, thickness: 1, scale: 1, samples: 16 });
    gtao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 4, radiusExponent: 1, rings: 2, samples: 16 });
    gtao.blendIntensity = o.ao_intensity;
    composer.addPass(gtao);
    composer.addPass(new OutputPass()); // Farbraum (linear/sRGB) wie ohne AO
  }

  loader = new GLTFLoader();
  loader.setDRACOLoader(new DRACOLoader().setDecoderPath('/three/examples/jsm/libs/draco/'));
  loader.setKTX2Loader(new KTX2Loader().setTranscoderPath('/three/examples/jsm/libs/basis/').detectSupport(renderer));
  loader.setMeshoptDecoder(MeshoptDecoder);

  const gl = renderer.getContext();
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  return { gpu: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER), three: THREE.REVISION };
};

function dispose(obj) {
  obj.traverse((o) => {
    if (!o.isMesh) return;
    o.geometry.dispose();
    for (const m of [].concat(o.material)) {
      for (const v of Object.values(m)) if (v && v.isTexture) v.dispose();
      m.dispose();
    }
  });
}

function normalize(obj) {
  // wie DiffuRank normalize_scene() inkl. VUSE-Patch: Skalierung um den Weltursprung, dann zentrieren
  obj.updateMatrixWorld(true);
  let box = new THREE.Box3().setFromObject(obj);
  if (box.isEmpty()) throw new Error('keine Meshes im Asset');
  const size = box.getSize(new THREE.Vector3());
  const s = 1 / Math.max(size.x, size.y, size.z);
  obj.scale.multiplyScalar(s);
  obj.position.multiplyScalar(s);
  obj.updateMatrixWorld(true);
  box = new THREE.Box3().setFromObject(obj);
  obj.position.sub(box.getCenter(new THREE.Vector3()));
  obj.updateMatrixWorld(true);
}

window.vuseLoad = async (url) => {
  if (root) { scene.remove(root); dispose(root); }
  ctxMat = hlMat = null;
  const gltf = await loader.loadAsync(url);
  root = gltf.scene;
  parser = gltf.parser;
  // mitgelieferte Kameras/Lichter würden Beleuchtung verfälschen
  const drop = [];
  root.traverse((o) => { if (o.isLight || o.isCamera) drop.push(o); });
  drop.forEach((o) => o.removeFromParent());
  let meshes = 0;
  root.traverse((o) => {
    if (!o.isMesh) return;
    meshes++;
    o.castShadow = o.receiveShadow = opts.shadows;
    // Teil-GLBs aus Pipeline 1 (trimesh-Export) haben keine Normalen -> ohne das alles schwarz.
    // Blender rechnet sie beim Import selbst nach; trimesh lässt die Vertices getrennt, harte Kanten bleiben hart.
    if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
  });
  scene.add(root);
  normalize(root);
  return { meshes };
};

window.vuseRenderViews = (n) => {
  const out = [];
  for (let i = 0; i < n; i++) {
    // Blender: Kamera bei -dist * normalize(-cos a, -sin a, elev) = dist * normalize(cos a, sin a, -elev), in three (x, z, -y)
    const a = (i / n) * 2 * Math.PI;
    camera.position.set(Math.cos(a), -ELEVATION, -Math.sin(a)).normalize().multiplyScalar(CAM_DIST);
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld();
    if (composer) composer.render(); else renderer.render(scene, camera);
    out.push(renderer.domElement.toDataURL('image/png'));
  }
  return out;
};

// --------------------------------------------------------------------------- highlight
const clean = (s) => s.replace(/[^A-Za-z0-9_-]/g, '_');

function findTarget(node, kind) {
  // GLTFLoader bereinigt object.name (Flywheel.001 -> Flywheel001), der Originalname steht in userData.name
  let hit = null;
  root.traverse((o) => { if (!hit && o.userData.name === node) hit = o; });
  if (!hit) root.traverse((o) => { if (!hit && o.userData.name && clean(o.userData.name) === clean(node)) hit = o; });
  if (!hit) { // Pipeline 1 benennt namenlose Knoten node_<index>
    const m = /^node_(\d+)$/.exec(node);
    if (m) root.traverse((o) => { const a = parser.associations.get(o); if (!hit && a && a.nodes === +m[1]) hit = o; });
  }
  if (!hit) return [];
  if (kind === 'mesh') {
    // nur der Knoten selbst. Meshes mit mehreren Materialien sind in three eine Gruppe aus Primitiven
    // (Kinder ohne eigenen glTF-Knoten); echte glTF-Kinder (Zollstock-Segmente) gehören nicht dazu.
    const own = hit.isMesh ? [hit] : [];
    for (const c of hit.children) {
      const a = parser.associations.get(c);
      if (c.isMesh && a && a.nodes === undefined) own.push(c);
    }
    return own;
  }
  const all = [];
  hit.traverse((o) => { if (o.isMesh) all.push(o); });
  return all;
}

function meshBox(objs) {
  const box = new THREE.Box3();
  for (const o of objs) {
    if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
    box.union(o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld));
  }
  return box;
}

window.vuseHighlightSetup = (o) => {
  ctxMat = new THREE.MeshStandardMaterial({
    color: new THREE.Color(0.6, 0.6, 0.6), roughness: 0.6,
    transparent: o.context_alpha < 1, opacity: o.context_alpha, depthWrite: o.context_alpha >= 1,
  });
  const c = new THREE.Color(...o.highlight_color);
  hlMat = new THREE.MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: 0.3, roughness: 0.6 });
  root.traverse((m) => { if (m.isMesh) m.material = ctxMat; });
};

window.vuseRenderPart = (node, kind, n) => {
  const targets = findTarget(node, kind);
  if (!targets.length) return { error: `Knoten '${node}' nicht im Asset gefunden` };
  const assetMeshes = [];
  root.traverse((o) => { if (o.isMesh) assetMeshes.push(o); });
  targets.forEach((o) => { o.material = hlMat; });
  try {
    const views = window.vuseRenderViews(n);
    const ts = meshBox(targets).getSize(new THREE.Vector3());
    const as = meshBox(assetMeshes).getSize(new THREE.Vector3());
    return {
      views,
      matched: targets.map((o) => o.userData.name || o.name),
      rel_size: Math.max(ts.x, ts.y, ts.z) / Math.max(as.x, as.y, as.z),
    };
  } finally {
    targets.forEach((o) => { o.material = ctxMat; });
  }
};

window.__vuse = { THREE, get scene() { return scene; }, get camera() { return camera; }, get renderer() { return renderer; } }; // Debugging
window.vuseReady = true;
