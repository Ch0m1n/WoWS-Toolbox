# Blender scene assembler

Blender 3.5 백그라운드 모드에서 선체 GLB와 하드포인트별 부품 GLB를
하나의 장면으로 조립해요. 원본 구조와 숨김 상태를 보존하는 `.blend`,
선택적인 `.glb`, 현재 가시성 프로필의 선체·무장을 한 메시로 합친
`.obj`를 함께 만들어요.

## 실행

```powershell
& 'C:\Program Files\Blender Foundation\Blender 3.5\blender.exe' `
  --background `
  --factory-startup `
  --python .\assemble_scene.py `
  -- `
  --plan .\assembly_plan.json `
  --output .\assembled_ship.blend `
  --glb .\assembled_ship.glb `
  --obj .\assembled_ship_Combined.obj `
  --validation .\assembled_ship.validation.json
```

`--output`, `--glb`, `--obj`, `--validation`은 각각 JSON의
`output_blend`, `output_glb`, `output_combined_obj`, `validation_json`
보다 우선해요. `--glb`와 JSON의 `output_glb`를 모두 생략하면 GLB만
생략하고 BLEND와 통합 OBJ는 계속 만들어요.

OBJ는 항상 만드는 결과예요. `--obj`와 `output_combined_obj`를 모두
생략하면 출력 BLEND의 stem 뒤에 `_Combined.obj`를 붙여요. 예를 들어
`assembled_ship.blend`라면 `assembled_ship_Combined.obj`예요.
계획 JSON 내부에 지정한 상대 입력/출력 경로는 계획 JSON이 있는 폴더를 기준으로 해석해요.
반면 명시적인 CLI `--output`, `--glb`, `--obj`, `--validation`의 상대 경로는 실행 당시 현재 작업 폴더를 기준으로 해석해요.

## 계획 JSON

```json
{
  "hull_glbs": ["parts/hull_mid.glb"],
  "mounts": [
    {
      "hardpoint": "HP_AGS_1",
      "category": "MainGuns",
      "model_glb": "parts/main_gun.glb",
      "visible": true,
      "matrix": [
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        12.5, 3.0, -18.0, 1
      ]
    }
  ],
  "output_blend": "assembled_ship.blend",
  "output_glb": "assembled_ship.glb",
  "output_combined_obj": "Ticonderoga1990_Combined.obj",
  "validation_json": "assembled_ship.validation.json"
}
```

- `matrix`는 glTF와 같은 오른손/Y-up 좌표계의 **column-major 4×4**
  배열이에요.
- Blender glTF importer와 똑같이 `[x,y,z] -> [x,-z,y]` 축 변환을
  적용해요. 행렬은 `M_blender = B * M_gltf * B^-1`로 변환해요.
- 선체 GLB는 importer가 만든 원점을 그대로 유지해요.
- 같은 `model_glb`를 여러 번 쓰면 원본은 한 번만 import하고 Blender
  collection instance로 재사용해요. 메시, 재질, 이미지 데이터도 공유돼요.
- 부품은 `Mounts/<category>/<hardpoint>` 컬렉션에 정리돼요.
- `visible`은 bool이고 생략하면 `true`예요. `false`면 인스턴스는
  `.blend`에 보존하지만 `hide_viewport=true`, `hide_render=true`로 저장해요.
  GLB와 통합 OBJ에서는 이런 런타임 오버레이를 제외해요.

## 통합 OBJ 결과

OBJ에는 선택한 가시성 프로필에서 보이는 선체와 부품만 들어가요.
collection instance를 실제 메시로 변환한 뒤 모든 가시 메시를 하나의
`Ticonderoga1990_Combined` 메시 객체로 join해요. 무장, 레이더, VLS,
보트 같은 보이는 부품이 선체와 통합된 단일 OBJ로 나와요. 프로필에서 숨긴
부품은 OBJ와 MTL에 들어가지 않지만, 원본 `.blend`에는 인스턴스와 숨김
상태가 그대로 남아요.

OBJ 옆에는 다음 파일도 만들어져요.

```text
Ticonderoga1990_Combined.obj
Ticonderoga1990_Combined.mtl
textures/
  *.png
```

- OBJ는 UV, 법선, 면별 재질 할당을 보존해요.
- `mtllib`은 같은 폴더의 MTL 파일명을 상대 경로로 참조해요.
- MTL의 `map_*` 항목은 `textures/*.png`만 상대 경로로 참조해요.
- Blender에 packed된 이미지는 OBJ용 PNG로 materialize하고, MTL에서
  실제로 참조하지 않는 PNG는 정리해요.

OBJ/MTL은 완전한 PBR 형식이 아니에요. Principled 노드 그래프,
metallic/roughness 패킹, AO/detail 혼합, 투명 모드 같은 셰이더 의미는
Blender MTL exporter가 근사해요. packed normal/data map을 PNG로 바꾸는
과정에서도 색 공간 변환이나 정밀도 손실이 생길 수 있어요. 정확한 PBR
표현이 필요하면 `.blend` 또는 `.glb`를 기준으로 쓰고, OBJ는 통합 메시
호환용으로 보는 게 좋아요.

검증 JSON에는 요청/실제 부품 수, 누락 파일이나 잘못된 행렬, 고유 모델 수,
`default_visible`/`default_hidden`, 장면 바운드, 평가된 메시 수, 재질과
이미지 목록과 `combined_obj` 검증 결과가 들어가요.

통합 OBJ 검증은 다음을 확인해요.

- vertex/face가 있고 OBJ object가 정확히 하나인지
- UV, 법선, 사용 재질과 MTL 재질 수가 보존되는지
- `mtllib`과 `textures/*.png` 경로가 상대 경로이고 누락이 없는지
- 프로필에서 숨긴 부품 이름이 OBJ/MTL에서 제외됐는지
- 가시 메시 occurrence 수가 원본 장면과 일치하는지
- join 전후 bounds와 깨끗한 Blender 재가져오기 bounds가 허용 오차
  `1e-4` 안에서 일치하는지
- 새 장면으로 OBJ를 다시 가져왔을 때 메시가 하나이고 UV와 재질이 남는지
- OBJ 검증 뒤 원본 BLEND를 다시 열어 모든 mount instance와 숨김 상태가
  그대로 보존됐는지

누락이나 검증 실패가 있어도 가능한 결과와 validation JSON은 남기지만,
Blender 프로세스 종료 코드는 `2`가 돼요.

## Ticonderoga 검증 프로필 계획 만들기
보통은 상위 `Extract-TiconderogaFull.ps1`가 이 단계까지 자동으로 호출해요.
아래는 이미 사용자 로컬 mapping과 PBR batch가 만들어졌을 때의 직접 실행
예시예요. 생성 mapping/GLB는 도구 패키지에 포함되지 않아요.

```powershell
python .\build_ticon_scene_plan.py `
  --assembly 'D:\Exports\generated\ticonderoga_1990_static_assembly.json' `
  --profile-manifest ..\Mapping\ticonderoga_1990_profile_manifest.json `
  --batch-summary 'D:\Exports\pbr\ticon_full_pbr_models.summary.json' `
  --visibility-profile harbor_dock `
  --output-combined-obj .\Ticonderoga1990_Combined.obj `
  --output .\assembly_plan.json
```

생성기는 배치 요약의 각 `results[].output_glb`를 모델 stem에 연결하고 아래
구성을 엄격히 확인해요.

- 선체 GLB 4개
- combat 인스턴스 17개
- misc 인스턴스 10개
- runtime overlay 인스턴스 2개
- `mounts` 합계 29개

`harbor_dock` 프로필에서는 dock 전용 Mk141 cap 두 개가 보이고,
`AM5058` VLS action overlay 두 개는 `visible:false`로 `.blend`에만
보존돼요. 따라서 기본 가시 인스턴스는 27개, 숨김 인스턴스는 2개예요.
`neutral_battle_intact`를 고르면 dock cap 두 개도 숨겨져요.

## 합성 테스트

```powershell
pwsh -NoLogo -NoProfile -File .\run_self_test.ps1
```

이 명령은 먼저 순수 Python 계획 생성기 테스트를 실행해 harbor
`27 visible / 2 hidden`과 neutral battle `25 visible / 4 hidden`을
확인하고, 이어서 Blender 합성 테스트를 실행해요.

테스트는 선체/부품 GLB를 직접 만든 다음 아래를 확인해요.

- glTF 이동 `(2,3,4)`가 Blender `(2,-4,3)`으로 변환되는지
- glTF Y축 90도 회전이 Blender Z축 회전과 일치하는지
- 네 부품 인스턴스가 하나의 template collection을 공유하는지
- 숨긴 오버레이가 `.blend`에는 남고 viewport/render에서 숨겨지는지
- 숨긴 오버레이가 export한 GLB와 통합 OBJ에서는 제외되는지
- 가시 선체/부품이 한 OBJ 메시로 통합되는지
- OBJ의 UV, 법선, MTL, 상대 PNG 참조가 보존되는지
- OBJ를 깨끗한 장면에 재가져와도 한 메시이며 bounds가 일치하는지
- `.blend`, 선택적 `.glb`, 통합 `.obj/.mtl`, validation JSON이
  실제로 생성되는지
