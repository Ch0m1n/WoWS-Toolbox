"""Read-only installed aircraft/projectile discovery and portable OBJ export."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from game_archive import ArchiveReader, extract_entry, find_oodle_dll, latest_build, read_archive_index
import native_glb_export as native
from output_provenance import atomic_json, finalize_output

ROOT = Path(__file__).resolve().parent.parent
KINDS = ('aircraft', 'shell', 'torpedo', 'missile')
SCHEMA = 'wows-toolbox-other-models/v1'


def progress(percent, ko, en):
    native.progress('models', percent, ko, en)


def logical_path(value):
    value = str(value).replace('\\', '/').lstrip('/')
    path = PurePosixPath(value)
    if ':' in value or '..' in path.parts or not value.startswith('content/gameplay/'):
        raise ValueError('Invalid installed model path')
    return path.as_posix()


def kind_of(value):
    path = '/' + logical_path(value).casefold()
    if not path.endswith('.geometry') or re.search(r'[/_]lod[1-9]\d*(?:[/_.]|$)', path):
        return None
    if '/aircraft/' in path:
        return 'aircraft'
    if '/projectile/' not in path:
        return None
    if '/torpedo/' in path:
        return 'torpedo'
    if any(token in path for token in ('/missile/', '/rocket/', '/rockets/')):
        return 'missile'
    if '/artillery/' in path:
        return 'shell'
    return None


def source_identity(source, game):
    game = game.resolve(strict=True)
    if source == 'legends':
        indexes = sorted((game/'res_packages').glob('*.idx'))
        build = 'installed'
    else:
        build, directory = latest_build(game)
        indexes = sorted((directory/'idx').glob('*.idx'))
    if not indexes:
        raise ValueError('No package indexes found in the selected installation')
    identity = [SCHEMA, source, str(game).casefold(), str(build),
                [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in indexes]]
    token = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
    return build, token


class Installation:
    def __init__(self, source, game):
        self.source, self.game = source, game.resolve(strict=True)
        self.build, self.token = source_identity(source, game)
        self.reader = None
        if source == 'legends':
            sys.path.insert(0, str(ROOT/'BlenderExtractor/blender_extractor'))
            from legends_assets.core import iter_assets, read_asset_bytes
            self.read_legends = read_asset_bytes
            self.entries = {}
            priorities = {}
            def priority(entry):
                name = entry.idx_path.name.casefold()
                if name not in priorities:
                    match = re.match(r'zupd(\d+)', name)
                    priorities[name] = (1, int(match.group(1)), name) if match else (0, 0, name)
                return priorities[name]
            for entry in iter_assets(game):
                key = entry.virtual_path.casefold()
                previous = self.entries.get(key)
                if previous is None or priority(entry) >= priority(previous):
                    self.entries[key] = entry
        else:
            self.reader = ArchiveReader(game)
            _, self.reader.entries = read_archive_index(game)
            self.entries = {e.path.lstrip('/').casefold(): e for e in self.reader.entries.values()}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.reader:
            self.reader.__exit__(*exc)

    def read(self, value):
        value = logical_path(value) if str(value).startswith(('content/gameplay/', '/content/gameplay/')) else str(value).lstrip('/')
        entry = self.entries[value.casefold()]
        if self.source == 'legends':
            return self.read_legends(entry)
        return extract_entry(self.game, entry)

    def paths(self):
        return [e.virtual_path if self.source == 'legends' else e.path.lstrip('/') for e in self.entries.values()]


def catalog(source, game, cache, output, refresh=False):
    build, token = source_identity(source, game)
    saved = cache/'OtherModels'/source/(token+'.json')
    if saved.is_file() and not refresh:
        payload = json.loads(saved.read_text(encoding='utf-8'))
    else:
        progress(5, '설치된 기타 모델을 읽는 중', 'Reading installed aircraft and projectile models')
        with Installation(source, game) as install:
            rows = []
            for path in install.paths():
                try:
                    kind = kind_of(path)
                except ValueError:
                    continue
                if kind:
                    name = PurePosixPath(path).stem
                    rows.append({'Name': name.replace('_', ' '), 'Code': name.split('_')[0],
                                 'Kind': kind, 'Nation': PurePosixPath(path).parts[2],
                                 'Path': path, 'Selected': False})
        rows.sort(key=lambda r: (r['Kind'], r['Name'].casefold(), r['Path']))
        payload = {'schema': SCHEMA, 'source': source, 'build': build, 'identity': token,
                   'items': rows, 'counts': dict(Counter(r['Kind'] for r in rows))}
        saved.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(saved, payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(output, payload)
    progress(100, f"기타 모델 {len(payload['items'])}개 준비 완료", f"{len(payload['items'])} installed models ready")
    return {'ok': True, 'catalog': str(output), 'count': len(payload['items'])}


def is_effect(name, kind, fx=''):
    value = str(name).casefold()
    shader = str(fx or '').casefold()
    if re.search(r'propellermat|propeller[_ ]?(disk|blur)|disk_\dblade|rotor[_ ]?blur', value):
        return True
    if kind == 'shell' and (re.match(r'^(?:shell)?(?:head|friction|tail)(?:[_\s]|$)', value) or
                            re.search(r'shell_tracer_(head|friction|tail)', shader)):
        return True
    return False


def filter_document(document, kind, include_effects):
    materials = document.get('materials', [])
    removed = []
    for mesh in document.get('meshes', []):
        kept = []
        for primitive in mesh.get('primitives', []):
            index = primitive.get('material')
            name = materials[index].get('name', '') if index is not None else ''
            if not include_effects and is_effect(name, kind):
                removed.append(name)
            else:
                kept.append(primitive)
        mesh['primitives'] = kept
    # Compact material/texture/image tables: excluded effects must not leave PNGs.
    used = sorted({p['material'] for m in document.get('meshes', []) for p in m['primitives'] if 'material' in p})
    remap = {old: new for new, old in enumerate(used)}
    document['materials'] = [materials[i] for i in used]
    meshes, mesh_map = [], {}
    for i, mesh in enumerate(document.get('meshes', [])):
        if not mesh['primitives']:
            continue
        mesh_map[i] = len(meshes)
        for primitive in mesh['primitives']:
            if 'material' in primitive:
                primitive['material'] = remap[primitive['material']]
        meshes.append(mesh)
    if not meshes:
        raise ValueError('No static model remains; retry with effects included')
    document['meshes'] = meshes
    for node in document.get('nodes', []):
        if 'mesh' in node:
            old = node.pop('mesh')
            if old in mesh_map:
                node['mesh'] = mesh_map[old]
    slots = []
    def texture_slots(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.endswith('Texture') and isinstance(child, dict) and 'index' in child:
                    slots.append(child)
                else:
                    texture_slots(child)
        elif isinstance(value, list):
            for child in value:
                texture_slots(child)
    texture_slots(document['materials'])
    texture_ids = sorted({s['index'] for s in slots})
    texture_map = {old: new for new, old in enumerate(texture_ids)}
    textures = [document.get('textures', [])[i] for i in texture_ids]
    image_ids = sorted({t['source'] for t in textures if 'source' in t})
    image_map = {old: new for new, old in enumerate(image_ids)}
    document['images'] = [document.get('images', [])[i] for i in image_ids]
    for slot in slots:
        slot['index'] = texture_map[slot['index']]
    for texture in textures:
        if 'source' in texture:
            texture['source'] = image_map[texture['source']]
    document['textures'] = textures
    return removed


def choose_albedo(path, paths):
    """Conservative exact normalized model-stem match within its texture folder."""
    model = PurePosixPath(path)
    prefix = str(model.parent.parent/'textures').casefold()+'/'
    normalize = lambda s: re.sub('[^a-z0-9]', '', s.casefold())
    wanted = normalize(model.stem)
    candidates = {}
    for logical in paths:
        texture = PurePosixPath(logical)
        if not logical.casefold().startswith(prefix) or texture.suffix.casefold() not in ('.dds', '.dd0'):
            continue
        stem = texture.stem
        if not stem.casefold().endswith('_a') or normalize(stem[:-2]) != wanted:
            continue
        candidates.setdefault(normalize(stem), []).append(logical)
    if len(candidates) != 1:
        return None
    return sorted(next(iter(candidates.values())), key=lambda p: (not p.lower().endswith('.dd0'), p))[0]


def write_glb(path, document, binary):
    document['buffers'][0]['byteLength'] = len(binary)
    data = json.dumps(document, separators=(',', ':')).encode()
    data += b' '*((-len(data)) % 4)
    binary += b'\0'*((-len(binary)) % 4)
    chunks = struct.pack('<II', len(data), 0x4e4f534a)+data+struct.pack('<II', len(binary), 0x004e4942)+binary
    path.write_bytes(struct.pack('<III', 0x46546c67, 2, len(chunks)+12)+chunks)


def run(command):
    result = subprocess.run([str(p) for p in command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(result.stdout[-4000:])
    return result.stdout


def pc_model(install, path, kind, out, work, cache, effects):
    exporter = ROOT/'Backend/wowsunpack.exe'
    raw = work/'raw.glb'
    messages = run([exporter, '--game-dir', install.game, 'export-model', '/'+path, '-o', raw, '--lod', '0'])
    if 'No matching .visual found' in messages:
        raise ValueError('Visual metadata unavailable; raw geometry with mixed LODs is not exported: '+path)
    document, binary = native.load_glb(raw)
    removed = filter_document(document, kind, effects)
    warnings, bindings = [], []
    for material in document['materials']:
        if native.material_texture_index(document, material) is not None:
            continue
        if kind not in ('shell', 'torpedo', 'missile'):
            continue
        albedo = choose_albedo(path, install.paths())
        if not albedo:
            warnings.append('No unambiguous albedo for material: '+str(material.get('name')))
            continue
        decoded = work/'color.png'
        candidates = [albedo]
        sibling = str(PurePosixPath(albedo).with_suffix('.dds'))
        if albedo.lower().endswith('.dd0') and sibling.casefold() in install.entries:
            candidates.append(sibling)
        for candidate in candidates:
            source = work/('color'+PurePosixPath(candidate).suffix)
            source.write_bytes(install.read(candidate))
            try:
                run([exporter, 'decode-texture', source, '-o', decoded])
                if candidate != albedo:
                    warnings.append('Unsupported high-resolution color uses authored DDS mip: '+candidate)
                albedo = candidate
                break
            except RuntimeError:
                if candidate == candidates[-1]:
                    raise
        image_data = decoded.read_bytes()
        start = len(binary)
        binary += b'\0'*((-start) % 4)
        start = len(binary)
        binary += image_data
        document.setdefault('bufferViews', []).append({'buffer': 0, 'byteOffset': start, 'byteLength': len(image_data)})
        document.setdefault('images', []).append({'bufferView': len(document['bufferViews'])-1, 'mimeType': 'image/png', 'name': PurePosixPath(albedo).stem[:-2]})
        document.setdefault('textures', []).append({'source': len(document['images'])-1})
        material.setdefault('pbrMetallicRoughness', {})['baseColorTexture'] = {'index': len(document['textures'])-1}
        bindings.append({'material': material.get('name'), 'albedo': albedo, 'method': 'unique-normalized-model-stem'})
    prepared = work/'static.glb'
    write_glb(prepared, document, binary)
    report = native.build(SimpleNamespace(input=prepared, output=out/(PurePosixPath(path).stem+'.obj'),
                         report=out/'model-report.json', formats='obj', texture_max_size=0,
                         texture_library=cache/'SharedTextures', pbr_exporter=exporter,
                         pbr_game_dir=install.game, pbr_cache=cache/'pbr'))
    report.update(excluded_effects=removed, texture_bindings=bindings, warnings=warnings)
    return report


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def legends_context(install, cache):
    directory = cache/'legends-metadata'
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('assets.bin', 'prototypes.index.data', 'prototypes.data'):
        target = directory/name
        if not target.exists():
            data = install.read('content/'+name)
            temporary = target.with_suffix(target.suffix+'.'+uuid.uuid4().hex+'.part')
            temporary.write_bytes(data)
            os.replace(temporary, target)
    # Aircraft variants can have additional records between the variant and ID
    # arrays. The generic mapping reader follows the declared visual pointers.
    parser = module('other_model_metadata', ROOT/'FullAssembly/Ticonderoga1990/Mapping/build_ticonderoga_assembly.py')
    return parser, parser.AssetsV0(directory/'assets.bin'), parser.PrototypeIndex(directory/'prototypes.index.data', directory/'prototypes.data')


def legends_visual_record(blob, assets):
    """Read only LOD0 joins and declared textures; shader parameter layouts vary.

    In particular aircraft variant arrays and non-texture shader values differ
    from ship records. Their declared offsets remain the source of truth.
    """
    def read(fmt, offset):
        if offset < 0 or offset + struct.calcsize(fmt) > len(blob):
            raise ValueError('Model metadata pointer outside prototype')
        values = struct.unpack_from(fmt, blob, offset)
        return values[0] if len(values) == 1 else values
    lods, count = read('<II', 0)
    if not 1 <= lods <= 4096 or not 0 <= count <= 4096:
        raise ValueError('Unsupported model descriptor count')
    visual_base = read('<Q', 0x18)
    mfm_base, name_base, material_base = read('<QQQ', 0x20)
    materials, lookup = [], {}
    for i in range(count):
        base = material_base+i*0x70
        mfm_id, name_id = read('<Q', mfm_base+i*8), read('<I', name_base+i*4)
        properties = []
        # Upper 16 bits are material flags (e.g. aircraft alpha/glass).
        length = read('<H', base+0x0c)
        names = base+read('<q', base+0x18)
        codes = base+read('<q', base+0x20)
        texture_values = base+read('<q', base+0x40)
        for j in range(length):
            code = read('<B', codes+j)
            if code & 7 != 3:
                continue
            texture_id = read('<Q', texture_values+(code >> 3)*8)
            properties.append({'name': assets.get_string(read('<I', names+j*4)),
                               'type': 'texture', 'value': {'path': assets.resolve_resource(texture_id)}})
        lookup[(mfm_id, name_id)] = i
        materials.append({'mfm_path': assets.resolve_resource(mfm_id),
                          'material_name': assets.get_string(name_id),
                          'fx_path': assets.resolve_resource(read('<Q', base)), 'properties': properties})
    geometry = assets.resolve_resource(read('<Q', visual_base+0x18))
    if not geometry or not geometry.endswith('.primitives'):
        raise ValueError('LOD0 geometry metadata missing')
    renders = []
    length = read('<I', visual_base+0x20)
    if not 1 <= length <= 65536:
        raise ValueError('Unsupported render-set count')
    base = visual_base+read('<q', visual_base+0x28)
    for j in range(length):
        entry = base+j*0x20
        mfm_id, name_id, vertex_id, index_id = read('<QIII', entry)
        material_index = lookup.get((mfm_id, name_id))
        if material_index is None:
            raise ValueError('Render-set material is not declared')
        renders.append({'material_name': assets.get_string(name_id),
                        'material_mfm_path': assets.resolve_resource(mfm_id),
                        'vertices_section': assets.get_string(vertex_id),
                        'indices_section': assets.get_string(index_id),
                        'material_prototype_index': material_index})
    return {'visual_prototypes': [{'lod_index': 0, 'derived_geometry_path': geometry[:-11]+'.geometry', 'render_sets': renders}],
            'material_prototypes': materials}


def legends_model(install, path, kind, out, work, context, effects):
    parser, assets, prototypes = context
    model_path = str(PurePosixPath(path).with_suffix('.model'))
    resource_id = assets.resource_id(model_path)
    if resource_id is None:
        raise ValueError('Model metadata missing: '+model_path)
    blob, _ = prototypes.blob(resource_id)
    record = legends_visual_record(blob, assets)
    visual = next(v for v in record['visual_prototypes'] if v.get('lod_index') == 0)
    geometry = logical_path(visual['derived_geometry_path'])
    raw = work/'raw'
    target = raw/geometry
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(install.read(geometry))
    render_sets, removed, missing = [], [], []
    channels = {'diffuseMap': 'a', 'normalMap': 'n', 'metallicGlossMap': 'mg', 'ambientOcclusionMap': 'ao'}
    for render in visual['render_sets']:
        material = record['material_prototypes'][render['material_prototype_index']]
        name = render['material_name']
        if not effects and is_effect(name+' '+str(material.get('mfm_path', '')), kind, material.get('fx_path')):
            removed.append(name)
            continue
        maps = {}
        for prop in material.get('properties', []):
            channel = channels.get(prop.get('name'))
            value = prop.get('value')
            if channel and isinstance(value, dict) and value.get('path'):
                logical = logical_path(value['path'])
                if logical.casefold() not in install.entries:
                    missing.append(logical)
                    continue
                destination = raw/logical
                if not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(install.read(logical))
                maps[channel] = logical
        render_sets.append({**render, 'texture_maps': maps, 'material_fx_path': material.get('fx_path'), 'material_properties': material.get('properties', [])})
    if not render_sets:
        raise ValueError('No static model remains; retry with effects included')
    name = PurePosixPath(path).stem
    payload = {'schema': 1, 'name': name, 'texture_root': str(raw),
               'models': [{'name': name, 'geometry': str(target), 'render_sets': render_sets}]}
    manifest = work/'model.json'
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    converter = module('other_model_converter', ROOT/'FullAssembly/Ticonderoga1990/PBRConverter/convert.py')
    sets = converter.parse_render_sets(payload, manifest)
    parts, _ = converter.map_parts(converter.load_decoder(converter.default_decoder_root()), sets)
    materials, missing_maps = converter.resolve_materials(sets)
    counts = converter.write_obj(parts, out/(name+'.obj'), out/(name+'.mtl'))
    converter.prepare_blender_textures(materials, out)
    with (out/(name+'.mtl')).open('w', encoding='utf-8') as mtl:
        for material in materials:
            mtl.write('newmtl '+converter.safe_name(material['name'])+'\nKd 1 1 1\nKs 0.125 0.125 0.125\nNs 32\n')
            for channel, tag in (('a', 'map_Kd'), ('n', 'map_normal'), ('ao', 'map_ao')):
                file = material['maps'].get(channel)
                if file:
                    mtl.write(tag+' '+Path(file).relative_to(out).as_posix()+'\n')
            mtl.write('\n')
    return {'ok': True, 'obj': str(out/(name+'.obj')), 'formats': ['obj'], 'object_count': counts['parts'],
            'triangle_count': counts['triangles'], 'excluded_effects': removed, 'missing_maps': missing+missing_maps,
            'warnings': ['Missing authored texture: '+str(p) for p in missing]}


def export_request(request):
    payload = json.loads(request.read_text(encoding='utf-8-sig'))
    source, game, output = payload['source'], Path(payload['game_dir']), Path(payload['output_root']).resolve()
    if source not in ('pc', 'korabli', 'legends'):
        raise ValueError('Choose PC, Korabli or Legends')
    game = game.resolve(strict=True)
    if output == game or output.is_relative_to(game):
        raise ValueError('Output must be outside the game installation')
    paths = list(dict.fromkeys(logical_path(p) for p in payload['paths']))
    if not 1 <= len(paths) <= 200:
        raise ValueError('Select between 1 and 200 models')
    if payload.get('oodle_dll'):
        dll = Path(payload['oodle_dll'])
        if dll.is_file():
            os.environ['WOWS_OODLE_DLL'] = str(dll.resolve())
        elif source == 'korabli':
            print('[WARN] Configured Oodle DLL is missing; using automatic runtime discovery if needed', flush=True)
    if source == 'korabli' and not Path(os.environ.get('WOWS_OODLE_DLL', '')).is_file():
        os.environ['WOWS_OODLE_DLL'] = str(find_oodle_dll(game))
    output = output/'OtherModels'/source
    output.mkdir(parents=True, exist_ok=True)
    succeeded, failed = [], []
    with Installation(source, game) as install:
        cache = Path(payload['cache_root'])/'OtherModels'/source/install.token
        cache.mkdir(parents=True, exist_ok=True)
        context = None
        for i, path in enumerate(paths):
            progress(int(100*i/len(paths)), f'[{i+1}/{len(paths)}] {PurePosixPath(path).stem}', f'[{i+1}/{len(paths)}] {PurePosixPath(path).stem}')
            destination = None
            try:
                kind = kind_of(path)
                if not kind or path.casefold() not in install.entries:
                    raise ValueError('Model geometry is not present in this installation: '+path)
                destination = Path(tempfile.mkdtemp(prefix=native.safe_name(PurePosixPath(path).stem, 'Model')+'_', dir=output))
                with tempfile.TemporaryDirectory(prefix='.model-', dir=destination) as tmp:
                    if source == 'legends':
                        if context is None:
                            context = legends_context(install, cache)
                        result = legends_model(install, path, kind, destination, Path(tmp), context, bool(payload.get('include_effects')))
                    else:
                        result = pc_model(install, path, kind, destination, Path(tmp), cache, bool(payload.get('include_effects')))
                result.update(output_dir=str(destination), build=install.build, source=source, model_path=path,
                              quality_contract={'model_lod': 0, 'texture_max_size': 0})
                args = SimpleNamespace(source=source, output_root=output, toolbox_root=ROOT, selected_model_path=path,
                                       formats='obj', lod=0, texture_max_size=0)
                finalize_output(args, result)
                atomic_json(destination/'other-model.json', result)
                succeeded.append(result)
            except Exception as exc:
                failure = {'path': path, 'message': str(exc), 'output_dir': str(destination) if destination else None}
                failed.append(failure)
                if destination:
                    atomic_json(destination/'other-model-failure.json', failure)
                print('[WARN] '+str(exc), flush=True)
    result = {'ok': not failed, 'schema': SCHEMA, 'successes': succeeded, 'failures': failed,
              'output_dir': str(output), 'obj': succeeded[-1]['obj'] if succeeded else None}
    atomic_json(output/('batch-'+uuid.uuid4().hex+'.json'), result)
    progress(100, f'완료 {len(succeeded)}개 · 실패 {len(failed)}개', f'Completed {len(succeeded)}; failed {len(failed)}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('catalog')
    listing.add_argument('--source', choices=('pc', 'korabli', 'legends'), required=True)
    listing.add_argument('--game-dir', type=Path, required=True)
    listing.add_argument('--cache-root', type=Path, required=True)
    listing.add_argument('--output', type=Path, required=True)
    listing.add_argument('--refresh', action='store_true')
    exporting = commands.add_parser('export')
    exporting.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    result = catalog(args.source, args.game_dir, args.cache_root, args.output, args.refresh) if args.command == 'catalog' else export_request(args.request)
    print('[RESULT] '+json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('[ERROR] '+str(exc), flush=True)
        raise SystemExit(1)
