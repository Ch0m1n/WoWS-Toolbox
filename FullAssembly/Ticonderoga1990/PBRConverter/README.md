# Ticonderoga explicit-map PBR converter

이 디렉터리는 `ticonderoga_1990_verified_profile`의 20개 LOD0 모델을
명시적 재질 맵으로 변환하는 소스만 담아요. 게임 모델, 텍스처, 생성된
OBJ/GLB/`.blend`는 포함하지 않아요.

가장 안전한 사용법은 상위의 `Extract-TiconderogaFull.ps1`이에요. 그
오케스트레이터가 정확한 76개 리소스를 CRC 검증해 게임 폴더 밖으로
추출한 다음, 이 변환기를 호출해요.

## 명시적 재질 계약

`batch_ticon_models.py`는 ModelUber 재질 prototype의 속성에서 각 render
set의 실제 논리 경로를 받아요.

- `diffuseMap` → `a`
- `normalMap` → `n`
- `metallicGlossMap` → `mg`
- `ambientOcclusionMap` → `ao`
- `detailMap` → `detail`

`convert.py`는 이 `texture_maps`만 사용하며 이름 추측을 하지 않아요.
`fx_path`와 원본 material properties도 검증 JSON에 보존해요. `_a`는 sRGB,
`_n`/`_ao`/`_mg`는 Non-Color로 처리해요. `_mg`의 정확한 Legends 채널
계약은 확인되지 않아 Metallic/Roughness에는 임의로 연결하지 않아요.

투명 재질 정책은 확인된 FX/property 조합을 따라요.

- glass: `BLEND`
- grid alpha: `CLIP`, 임계값 `50/255`
- wire: `HASHED`

Blender 3.5가 직접 읽지 못하는 Legends DDS는 출력 폴더 안의 PNG 작업
사본으로 비파괴 변환해요. 원본 DDS는 바꾸지 않아요. 이 단계에는 DDS를
지원하는 Pillow가 필요해요.

Legends의 `normalMap`은 R/G에 부호 있는 tangent X/Y를 저장하고 B는 Blender가
기대하는 tangent Z가 아니에요. 그래서 PNG 작업 사본을 만들 때 R/G는 보존하고
양의 Z를 다시 계산해 Blender `Normal Map` 노드에 전달해요.

Z 계산은 Pillow의 C 구현 연산과 조회표를 사용하고, 선택한 함선 전체가 같은
`--shared-texture-dir`을 사용해 동일 DDS를 부품마다 다시 PNG로 변환하지 않아요.
배치 매니페스트의 파일 크기·SHA-256도 같은 실행 안에서 한 번만 계산해요.

## 직접 실행

보통은 상위 오케스트레이터를 쓰세요. 이미 사용자 소유 자산을 외부
폴더로 추출했고 mapping JSON도 만들었다면 다음처럼 배치만 실행할 수
있어요.

```powershell
python .\batch_ticon_models.py `
  --mapping 'D:\Exports\generated\ticonderoga_1990_static_assembly.json' `
  --extracted-root 'D:\Exports\extracted' `
  --output-root 'D:\Exports\pbr' `
  --decoder-root '..\..\..\BlenderExtractor\geometry_decoder' `
  --blender 'C:\Program Files\Blender Foundation\Blender 3.5\blender.exe'
```

배치는 선체 4개, combat 고유 모델 8개, misc 고유 모델 8개를 처리해요.
각 모델 폴더에 manifest, GLB, validation JSON을 만들고 루트에
`ticon_full_pbr_models.summary.json`을 써요. strict acceptance는 20개 모델,
모든 intact render set, 모든 선언 텍스처, GLB 컨테이너, 재질 정책이 모두
통과해야 `accepted=true`가 돼요.

2026-08-03 검증 기준은 20/20 모델, 32/32 intact render set, 선언 텍스처
52개 중 누락 0, 재질 29개, 정책 실패 0, GLB 20/20이에요.

이 결과는 번들된 Ticonderoga 1990 프로필에만 해당해요. 다른 함선까지
완전 자동 조립된다는 뜻은 아니에요.
