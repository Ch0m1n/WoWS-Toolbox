from __future__ import annotations

import argparse
import json
import mimetypes
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import Route, sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the offline WoWS model viewer.")
    parser.add_argument("viewer_root", type=Path)
    parser.add_argument("obj", type=Path)
    parser.add_argument("--edge", type=Path, default=Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"))
    parser.add_argument("--parse-only", action="store_true")
    parser.add_argument("--no-mtl", action="store_true")
    parser.add_argument("--pbr", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--base-screenshot", type=Path)
    parser.add_argument("--screenshot", type=Path)
    return parser.parse_args()


def safe_file(root: Path, request_path: str) -> Path:
    candidate = (root / unquote(request_path).lstrip("/")).resolve()
    candidate.relative_to(root)
    return candidate


def main() -> int:
    args = parse_args()
    viewer_root = args.viewer_root.resolve()
    obj_path = args.obj.resolve()
    model_root = obj_path.parent
    mtl_path = obj_path.with_suffix(".mtl")
    armor_path = obj_path.with_suffix(".armor.json")
    model_report = obj_path.with_suffix(".model.json")
    assembly_report = next(model_root.glob("*.validation.json"), None)
    counters = {"viewer_requests": 0, "model_requests": 0, "model_bytes": 0}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(args.edge),
            headless=True,
            args=["--enable-webgl", "--use-angle=swiftshader", "--disable-gpu-sandbox"],
        )
        context = browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 900})
        page = context.new_page()
        console_errors: list[str] = []
        page_errors: list[str] = []
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(error.stack or str(error)))

        def serve(route: Route) -> None:
            parsed = urlparse(route.request.url)
            if parsed.hostname == "viewer.local":
                root = viewer_root
                counters["viewer_requests"] += 1
            elif parsed.hostname == "model.local":
                root = model_root
                counters["model_requests"] += 1
            else:
                route.abort()
                return
            try:
                path = safe_file(root, parsed.path)
                if not path.is_file():
                    route.fulfill(status=404)
                    return
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                if parsed.hostname == "model.local":
                    counters["model_bytes"] += path.stat().st_size
                route.fulfill(path=str(path), content_type=content_type)
            except (OSError, ValueError):
                route.fulfill(status=403)

        context.route("https://viewer.local/**", serve)
        context.route("https://model.local/**", serve)
        page.add_init_script(
            """
            window.__viewerLongTasks = [];
            new PerformanceObserver((list) => {
              for (const entry of list.getEntries()) window.__viewerLongTasks.push(entry.duration);
            }).observe({ type: 'longtask', buffered: true });
            """
        )
        page.goto("https://viewer.local/index.html?lang=ko", wait_until="networkidle", timeout=60_000)
        page.wait_for_function("Boolean(window.WoWSViewerCore)", timeout=30_000)
        if args.parse_only:
            result = page.evaluate(
                """async (objUrl) => {
                  const module = await import('./vendor/OBJLoader.js');
                  let started = performance.now();
                  const source = await fetch(objUrl, { cache: 'no-store' }).then((response) => response.text());
                  const fetch_ms = performance.now() - started;
                  started = performance.now();
                  const root = new module.OBJLoader().parse(source);
                  const parse_ms = performance.now() - started;
                  let meshes = 0;
                  root.traverse((node) => { if (node.isMesh) meshes += 1; });
                  return { fetch_ms, parse_ms, source_bytes: source.length, meshes };
                }""",
                f"https://model.local/{obj_path.name}",
            )
            result.update(counters)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            context.close()
            browser.close()
            return 0
        message = {
            "type": "loadModel",
            "displayName": obj_path.stem,
            "objName": obj_path.name,
            "objUrl": f"https://model.local/{obj_path.name}",
            "mtlUrl": f"https://model.local/{mtl_path.name}" if mtl_path.is_file() and not args.no_mtl else "",
            "resourceBaseUrl": "https://model.local/",
            "armorUrl": f"https://model.local/{armor_path.name}" if armor_path.is_file() else "",
            "modelReportUrl": f"https://model.local/{model_report.name}" if model_report.is_file() else "",
            "assemblyReportUrl": f"https://model.local/{assembly_report.name}" if assembly_report else "",
        }
        started = time.perf_counter()
        result = page.evaluate(
            """async (message) => {
              const started = performance.now();
              await window.WoWSViewerCore.loadShip(message);
              const elapsed = performance.now() - started;
              let meshes = 0;
              let materials = 0;
              const uniqueMaterials = new Set();
              const textures = new Set();
              window.WoWSViewerCore.getModelContent()?.traverse((node) => {
                if (!node.isMesh) return;
                meshes += 1;
                const nodeMaterials = Array.isArray(node.material) ? node.material : [node.material];
                for (const material of nodeMaterials.filter(Boolean)) {
                  materials += 1;
                  uniqueMaterials.add(material);
                  for (const value of Object.values(material)) if (value?.isTexture) textures.add(value);
                  for (const value of Object.values(material.userData?.viewerPbrChannels || {})) {
                    if (value?.isTexture) textures.add(value);
                  }
                }
              });
              return {
                elapsed_ms: elapsed,
                meshes,
                materials,
                unique_materials: uniqueMaterials.size,
                textures: textures.size,
                parts: window.WoWSViewerCore.getParts().length,
                armor_meshes: window.WoWSViewerCore.getArmorMeshes().length,
                status: document.querySelector('#statusText')?.textContent,
                loading_hidden: document.querySelector('#loading')?.hidden,
                long_tasks: window.__viewerLongTasks || [],
              };
            }""",
            message,
        )
        result["wall_ms"] = (time.perf_counter() - started) * 1000
        result.update(counters)
        result["model_megabytes_requested"] = counters["model_bytes"] / 1048576
        result["long_task_total_ms"] = sum(result["long_tasks"])
        result["long_task_max_ms"] = max(result["long_tasks"], default=0)
        result["console_errors"] = console_errors
        result["page_errors"] = page_errors
        if args.diagnose:
            result["diagnostics"] = page.evaluate(
                """async () => {
                  const core = window.WoWSViewerCore;
                  const rect = (selector) => {
                    const node = document.querySelector(selector);
                    if (!node) return null;
                    const box = node.getBoundingClientRect();
                    return {
                      x: box.x, y: box.y, width: box.width, height: box.height,
                      display: getComputedStyle(node).display,
                      visibility: getComputedStyle(node).visibility,
                    };
                  };
                  const frameBefore = core.renderer.info.render.frame;
                  await new Promise((resolve) => setTimeout(resolve, 250));
                  const frameAfter = core.renderer.info.render.frame;
                  const unique = new Set();
                  const uniqueTextures = new Set();
                  const invalidTextures = [];
                  const materialStats = {
                    count: 0, basic: 0, standard: 0, mapped: 0,
                    averageColor: [0, 0, 0], samples: [],
                  };
                  core.getModelContent()?.traverse((node) => {
                    if (!node.isMesh) return;
                    const materials = Array.isArray(node.material) ? node.material : [node.material];
                    for (const material of materials.filter(Boolean)) {
                      for (const value of [
                        ...Object.values(material),
                        ...Object.values(material.userData?.viewerPbrChannels || {}),
                      ]) {
                        if (!value?.isTexture || uniqueTextures.has(value)) continue;
                        uniqueTextures.add(value);
                        if (value.image === undefined || value.image === null) {
                          invalidTextures.push({
                            material: material.name,
                            texture: value.name || null,
                            imageType: String(value.image),
                            version: value.version,
                            source: value.source?.data?.src || null,
                          });
                        }
                      }
                      if (unique.has(material)) continue;
                      unique.add(material);
                      materialStats.count += 1;
                      if (material.isMeshBasicMaterial) materialStats.basic += 1;
                      if (material.isMeshStandardMaterial) materialStats.standard += 1;
                      if (material.map) materialStats.mapped += 1;
                      materialStats.averageColor[0] += material.color?.r || 0;
                      materialStats.averageColor[1] += material.color?.g || 0;
                      materialStats.averageColor[2] += material.color?.b || 0;
                      if (materialStats.samples.length < 12) {
                        materialStats.samples.push({
                          name: material.name,
                          type: material.type,
                          color: material.color?.getHexString?.() || null,
                          map: material.map?.source?.data?.src || material.map?.name || null,
                          roughness: material.roughness ?? null,
                          metalness: material.metalness ?? null,
                        });
                      }
                    }
                  });
                  if (materialStats.count) {
                    materialStats.averageColor = materialStats.averageColor.map((value) => value / materialStats.count);
                  }
                  const part = core.getParts()[0];
                  const before = part?.position.clone();
                  core.selectPart(part);
                  const attached = core.transform.object === part;
                  const changed = core.recordObjectEdit([part], 'diagnostic move', () => { part.position.x += 0.125; });
                  const moved = part ? part.position.x - before.x : null;
                  const undone = core.undoViewerEdit();
                  const restored = part ? part.position.distanceTo(before) : null;
                  return {
                    layout: {
                      app: rect('#app'), viewport: rect('.viewport-shell'), inspector: rect('.inspector'),
                      selectionPane: rect('#selectionPane'), partList: rect('#partList'), lighting: rect('#lightingPanel'),
                      inspectorWidthVariable: getComputedStyle(document.documentElement).getPropertyValue('--inspector-width'),
                    },
                    interaction: {
                      frameBefore, frameAfter, framesAdvanced: frameAfter > frameBefore,
                      selected: core.getSelected() === part,
                      transformAttached: attached,
                      changed, moved, undone, restored,
                      selectionName: document.querySelector('#selectionName')?.textContent,
                    },
                    textures: { count: uniqueTextures.size, invalid: invalidTextures },
                    materialStats,
                  };
                }"""
            )
        if args.base_screenshot:
            args.base_screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.base_screenshot))
            result["base_screenshot"] = str(args.base_screenshot.resolve())
        if args.pbr:
            pbr_started = time.perf_counter()
            result["pbr"] = page.evaluate(
                """async () => {
                  const core = window.WoWSViewerCore;
                  const loaded = await core.ensurePbrTexturesLoaded();
                  const control = document.querySelector('#pbrPreviewControl');
                  control.checked = true;
                  control.dispatchEvent(new Event('change', { bubbles: true }));
                  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                  const stats = {
                    loaded, materials: 0, standard: 0, normal: 0, roughness: 0, ao: 0,
                    invalidTextures: [], frame: core.renderer.info.render.frame,
                  };
                  const textures = new Set();
                  core.getModelContent()?.traverse((node) => {
                    if (!node.isMesh) return;
                    const materials = Array.isArray(node.material) ? node.material : [node.material];
                    for (const material of materials.filter(Boolean)) {
                      stats.materials += 1;
                      if (material.isMeshStandardMaterial) stats.standard += 1;
                      if (material.normalMap) stats.normal += 1;
                      if (material.roughnessMap) stats.roughness += 1;
                      if (material.aoMap) stats.ao += 1;
                      for (const value of [
                        ...Object.values(material),
                        ...Object.values(material.userData?.viewerPbrChannels || {}),
                      ]) {
                        if (!value?.isTexture || textures.has(value)) continue;
                        textures.add(value);
                        if (value.image === undefined || value.image === null) {
                          stats.invalidTextures.push({
                            material: material.name,
                            imageType: String(value.image),
                            version: value.version,
                            source: value.source?.data?.src || null,
                          });
                        }
                      }
                    }
                  });
                  stats.textures = textures.size;
                  return stats;
                }"""
            )
            result["pbr"]["elapsed_ms"] = (time.perf_counter() - pbr_started) * 1000
            result["pbr"]["model_requests_after"] = counters["model_requests"]
            result["pbr"]["model_megabytes_after"] = counters["model_bytes"] / 1048576
        if not result["loading_hidden"] or not result["parts"] or "실패" in result["status"]:
            raise RuntimeError(f"viewer load contract failed: {result}")
        if armor_path.is_file() and not result["armor_meshes"]:
            raise RuntimeError(f"armor load contract failed: {result}")
        if args.pbr and (
            not result["pbr"]["loaded"]
            or result["pbr"]["standard"] != result["pbr"]["materials"]
            or not result["pbr"]["normal"]
            or not result["pbr"]["roughness"]
            or not result["pbr"]["ao"]
        ):
            raise RuntimeError(f"deferred PBR contract failed: {result['pbr']}")
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshot))
            result["screenshot"] = str(args.screenshot.resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())