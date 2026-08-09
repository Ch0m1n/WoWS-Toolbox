# Ticonderoga 1990 verified static assembly

이 폴더는 Legends Steam판의 `PXSD307_Ticonderoga_1990`에 대해 검증한 정적
조립 프로필이에요. 보이는 선체·무장·레이더·보트와 부속을 하나의 통합 OBJ
mesh로도 내보내요. **범용 함선 완전 추출기가 아니며**, 다른 함선의 구성,
조건부 파츠, 재질과 애니메이션을 자동으로 알아낸다고 주장하지 않아요.

## GUI로 가장 쉽게 실행하기

패키지 루트로 올라가 `WoWS-Legends-Toolbox-GUI.cmd`를 더블클릭해요.

1. `추출 / 전체 조립` 탭에서 `GameDir`, `OutputRoot`, `Blender`를 골라요.
2. 먼저 `검사만`을 눌러 정확한 76개 자원과 실행 계획을 확인해요.
3. 검사가 통과하면 `전체 추출`을 누르고 확인창에서 승인해요.

GUI는 이 폴더의 검증된 PowerShell 파이프라인을 별도 프로세스로 호출하며
게임 설치에는 쓰지 않아요.

## 어떻게 이어지는가

1. `Profiles/ticonderoga_1990_resources.json`의 정확한 76개 논리 리소스를
   IDX에서 찾고 CRC가 포함된 계획을 만들어요.
2. 실제 실행 승인이 있을 때만 4개 시스템 sidecar, LOD0 geometry 20개,
   선언 texture 52개를 게임 밖 출력 폴더로 CRC 검증 추출해요.
3. Mapping v2가 GameParams와 ModelUber를 해석해 선체, combat hardpoint
   17개, misc 인스턴스 10개, runtime overlay 2개와 행렬을 만들어요.
4. PBR batch가 고유 모델 20개와 intact render set 32개를 명시적 texture
   map/FX/property로 변환해요.
5. scene-plan 생성기가 가시성 프로필을 적용하고 Blender가 선체 4개와
   mount 인스턴스 29개를 하나의 `.blend`와 GLB로 조립해요.
6. 보이는 mesh occurrence에 월드 변환을 적용해 합친 뒤 단일
   `Ticonderoga1990_Combined.obj`, MTL과 portable texture 폴더를 만들어요.

ANCA 디렉터리는 컨테이너와 채널 테이블 연구/부분 디코더를 보존하지만,
streamed bit-packed keyframe과 standalone `.anim`은 아직 완전 디코드하지
못해요. 따라서 최종 결과는 **검증된 정적 조립**이에요.

## CLI 고급 사용법: 검사만

패키지 루트에서 실행하세요. 아래 명령은 실제 설치의 IDX를 읽어 정확한
리소스와 CRC, 전체 명령 계획을 JSON으로 출력할 뿐 파일을 추출하지 않아요.

```powershell
pwsh -NoLogo -NoProfile `
  -File .\FullAssembly\Ticonderoga1990\Extract-TiconderogaFull.ps1 `
  -GameDir 'D:\SteamLibrary\steamapps\common\World of Warships Legends' `
  -OutputRoot 'D:\WoWSLegendsExports'
```

2026-08-03 로컬 Legends 8.6.0/build `722375` 설치에서 검사만 실행은 exit 0,
76/76 exact resource(4 sidecar, 20 geometry, 52 texture), 중복·누락 0으로
통과했어요. 출력 JSON에는 `writes_game_directory=false`와
`generic_ship_complete=false`가 기록돼요.

## CLI 고급 사용법: 실제 조립

계획을 확인한 뒤에만 `-Execute`를 붙여요. Blender 경로가 다르면
`-Blender`도 지정하세요.

```powershell
pwsh -NoLogo -NoProfile `
  -File .\FullAssembly\Ticonderoga1990\Extract-TiconderogaFull.ps1 `
  -GameDir 'D:\SteamLibrary\steamapps\common\World of Warships Legends' `
  -OutputRoot 'D:\WoWSLegendsExports' `
  -Blender 'C:\Program Files\Blender Foundation\Blender 3.5\blender.exe' `
  -VisibilityProfile harbor_dock `
  -Execute
```

기존 `D:\WoWSLegendsExports\Ticonderoga1990_Verified`가 있으면 기본적으로
중단해요. 의도적으로 다시 만들 때만 `-Overwrite`를 추가하세요. 출력
루트는 게임 설치 폴더와 완전히 분리되어야 하며 게임 폴더 안쪽이나 상위
폴더는 거부해요.

생성 결과는 다음 구조예요.

```text
Ticonderoga1990_Verified/
  extracted/   CRC 검증된 사용자 로컬 게임 자산
  generated/   mapping, acceptance, scene plan
  pbr/         모델별 manifest, OBJ/GLB/.blend, validation, batch summary
  scene/
    Ticonderoga1990.blend
    Ticonderoga1990.glb
    Ticonderoga1990_Combined.obj
    Ticonderoga1990_Combined.mtl
    textures/
    scene validation
  logs/        단계별 stdout 로그
  Ticonderoga1990.pipeline.json
```

OBJ, MTL과 `textures` 폴더는 함께 옮겨야 상대 texture 경로가 유지돼요.
통합 OBJ 내부의 `o` 레코드는 하나이며, 보이는 선체·무장·레이더·보트와
부속이 같은 mesh 객체에 들어가요.

## 가시성과 검증된 통합 OBJ

- `harbor_dock`: mount 27 visible / runtime overlay 2 hidden
- `neutral_battle_intact`: mount 25 visible / 4 hidden

실제 harbor 결과는 visible mesh occurrence 43개를 mesh 하나로 합쳤고,
정점 308,409개 / 면 236,444개 / 재질 28개 / texture 53개예요. 실제 neutral
결과는 occurrence 41개, mesh 하나, 정점 307,545개 / 면 235,548개 / 재질
27개예요. 두 프로필 모두 clean re-import, 상대 MTL/texture 경로, texture
누락 0과 원본 `.blend` 보존 검사를 통과했어요.

숨긴 파츠는 `.blend`에 보존되지만 기본 GLB와 통합 OBJ에서는 제외돼요.
`harbor_dock`의 숨김 2개는 runtime launch overlay이고,
`neutral_battle_intact`에서는 dock cap 2개도 추가로 빠져요. replay의 실제
launcher/action 상태를 따라 동적으로 바꾸는 runtime 모드는 아직 이 오프라인
정적 파이프라인에 연결하지 않았어요.

## OBJ 형식의 제한

- OBJ는 정적 단일 mesh라 collection instance, 파츠별 계층, 숨김 상태와
  애니메이션을 보존하지 않아요.
- OBJ/MTL은 Blender의 전체 PBR node graph를 표현하지 못해요. portable
  Base Color/투명도 재질을 제공하지만 정밀 PBR 편집은 `.blend`나 GLB 쪽이
  더 정확해요.
- ANCA의 streamed bit-packed keyframe과 standalone `.anim`은 아직 완전
  지원하지 않아요.
- 상태를 바꾸거나 숨김 파츠를 다시 보이게 하려면 `.blend`를 열어야 해요.

## 안전과 권리

- 검사만 실행이 기본이며 추출에는 GUI 확인 또는 CLI `-Execute`가 필요해요.
- 게임 설치는 읽기 전용으로 열고 쓰기는 외부 출력에만 해요.
- 기존 결과 덮어쓰기는 `-Overwrite`가 있어야 해요.
- 이 도구 패키지에는 모델, 텍스처, 리플레이, 생성된 OBJ/MTL,
  mapping/GLB/`.blend` 같은 게임 또는 대형 산출물이 없어요.
- 사용자 로컬에서 추출된 자산의 이용·배포 권리는 적용되는 약관과 권리를
  직접 확인해야 해요.