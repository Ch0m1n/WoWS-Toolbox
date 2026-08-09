# World of Warships: Legends 완성형 Blender 조립 연구

작성일: 2026-08-03  
대상 설치본: `D:\SteamLibrary\steamapps\common\World of Warships Legends`

## 결론

사용자가 말한 기준이 맞다. 선체 메시 하나가 보이는 상태는 **미리보기**일
뿐이다. 이 프로젝트에서 “완성”이라고 부르려면 최소한 아래가 함께
조립되어야 한다.

1. 선체와 모든 탑재 구성품
2. 각 구성품의 정확한 하드포인트 위치·회전·부모 피벗
3. 메시 섹션별 재질과 텍스처 채널
4. 본/노드 계층과 스킨 웨이트
5. 프로펠러, 해치 등 확인된 애니메이션
6. 누락 수와 오차를 보여주는 검증 보고서

현재 확인 결과는 다음과 같다.

- **정적 완전 조립**: 구현 가능성이 높다. Legends 전용
  `GameParams.data` 정규화기와 `assets.bin` 버전 대응이 핵심이다.
- **재질 조립**: 텍스처 파일은 충분하지만, 정확한 메시 섹션→재질 매핑은
  Legends `assets.bin`의 `VisualPrototype/render_sets`를 읽어야 한다.
- **스켈레톤/스킨**: geometry의 `iiiww`가 뼈 인덱스·웨이트를 보유한다.
  노드 계층 및 bind transform은 `assets.bin`에서 복원해야 한다.
- **`.anca`**: 공개·허용 라이선스 디코더 구조가 실제 Legends 파일과
  호환됨을 확인했고, 최소 디코더와 Blender 바인딩 JSON까지 구현했다.
- **`.anim`**: 설치본의 VLS 해치 파일은 확인했지만, 이 형식에 직접
  호환되는 공개 디코더는 확인하지 못했다. 현재 지원으로 표시하면 안 된다.
- 게임 셰이더를 Blender에서 픽셀 단위로 그대로 재현하는 것은 별도 목표다.
  1차 완료 기준은 “의미가 보존된 PBR 변환”으로 정의하는 편이 정직하다.

## 완료 상태 이름

UI와 산출물에 아래 상태를 명시해야 한다.

| 상태 | 의미 |
|---|---|
| `Preview` | 선체 또는 일부 geometry만 표시 |
| `Static assembled` | 모든 알려진 구성품과 피벗을 정적으로 조립 |
| `Material assembled` | 섹션별 텍스처와 PBR 노드 연결 |
| `Rigged` | 노드/본 계층과 스킨 웨이트 복원 |
| `Animated (partial)` | 지원되는 채널만 Action으로 변환 |
| `Animated (complete)` | 해당 모델이 참조하는 모든 애니메이션을 변환 |
| `Verified` | 구성품·재질·피벗·채널 누락 검사를 통과 |

`Preview`나 `Animated (partial)`을 “완성”으로 표시하면 안 된다.

## 설치본에서 직접 확인한 Legends 포맷

### 전체 자산

기존 인덱스 조사 결과:

- IDX/PKG: 766개
- 가상 파일: 152,726개
- `.geometry`: 22,453개
- `.dds`: 66,931개
- `.anca`: 1,276개
- `.anim`: 145개
- `.texanim`: 12개
- `.model`, `.visual`, `.primitives`, `.mfm`: 각각 0개

따라서 PC용 전통적인 `.model/.visual/.primitives/.mfm` 경로를 그대로
기대하는 변환기는 Legends에 직접 적용되지 않는다. 메타데이터가
`GameParams.data`, `assets.bin`, `.geometry`, `.anca/.anim` 쪽으로
이동했다고 보는 것이 맞다.

### GameParams.data

Legends 파일은 약 34 MB이며 `%bin` 헤더 뒤에 pickle 계열 객체 그래프가
들어 있다. PC 도구가 사용하는 “전체 바이트 역순→zlib→pickle” 형식이
아니다.

제한된 inert unpickler로 읽었을 때 루트 dict 약 14,295개가 나왔고,
Ticonderoga 1990 키는 `PXSD307_Ticonderoga_1990`이었다. 확인된 연결
예시는 다음과 같다.

- 선체: `ASC307_Ticonderoga_1990` 모델
- `HP_AGA_1/2`: AGA2021 Mk15 Phalanx
- `HP_ARS_*`: ARS2007/2008/2009/2012 레이더
- `HP_AGS_1/2`: AGS2026
- `HP_AGM_1/2`: AGM2028 VLS 및 AM5058
- `HP_AGR_1/2`: AGR2016

클래스 상태가 일반 dict가 아니라 Cython 객체의 위치 기반 tuple인 경우가
있어 PC GameParams 파서를 그대로 재사용할 수 없다. 또한 일반
`pickle.load()`를 게임 파일에 무제한 적용하면 안 된다. 허용 클래스가
없는 inert/allow-list unpickler가 필요하다.

### assets.bin

- Legends magic: `BDWB`
- Legends version: `0x01000000`
- 공개 PC 도구가 요구하는 version: `0x01010000`

같은 PrototypeDatabase 계열이지만 버전이 다르다. 단순히 버전 검사 한 줄을
우회하는 것은 안전한 이식이 아니다. 각 table/relative pointer/record
stride를 경계 검사하면서 Legends 샘플로 다시 검증해야 한다.

### Ticonderoga 구성품·텍스처

선체 패키지 주변에서 아래 geometry와 대응 텍스처가 확인됐다.

- AGA2021
- AGM2028
- AGS2026
- ARS2007/2008/2009/2012
- AM5029/5031/5042/5045/5048/5049/5058

텍스처는 보통 `_a`, `_n`, `_mg`, 일부 `_ao` 및 dead/damage 변형을
포함한다. 따라서 파일명 stem만으로 “비슷한 재질”을 붙이는 미리보기는
가능하지만, 정확한 섹션 매핑에는 `assets.bin`의 render set 정보가
필수다.

## 공개 소스 조사

### wows-tools/wows-model-exporter

- 저장소: <https://github.com/wows-tools/wows-model-exporter>
- 확인 commit: `284fd8fdbb1b6b90dbaa3872ed47c2917a00446d`
- 라이선스: MIT

PC 월드 오브 워쉽 정적 조립의 가장 구체적인 공개 기준이다.

중요 구현 위치:

- `lib/game_params.cpp:16`: 구성품 종류
- `lib/game_params.cpp:61-72`: PC GameParams 로딩
- `lib/ship_export.cpp:106`: 함선 GLB 조립 진입점
- `lib/ship_export.cpp:209-299`: HP와 포탑/탑재물 배치
- `lib/ship_export.cpp:304`: 프로펠러 위치가 아직 정확하지 않다는 TODO
- `lib/assets_bin.cpp:133,237`: `0x01010000` 버전 고정
- `lib/assets_bin.cpp:938`: `HP_` 노드 수집
- `lib/assets_bin.cpp:979-981`: `Rotate_Y_BlendBone`, 이후
  `Root_BlendBone` 보정
- `lib/stitch.cpp:448-471`: POSITION/NORMAL/UV만 GLTF로 내보냄
- `lib/stitch.cpp:933`: 현재 재질에서 `_a`만 실제 로드
- `GEOMETRY.md:181`: `iiiww` 뼈 인덱스 3개와 웨이트 2개
- `MODEL.md:177-184`: Y-up 오른손 좌표, HP 행렬의 world-space/column-major

재사용 가치:

- GameParams→구성품 그래프
- HP transform 및 BlendBone 보정 개념
- render set→MFM path→texture stem 연결
- 손상 LOD 제외 방식

그대로 가져오면 부족한 부분:

- Legends의 GameParams 형식
- Legends `assets.bin` 버전
- 스킨 JOINTS/WEIGHTS
- `_n`, `_mg`, `_ao` 전체 PBR
- 프로펠러 정확한 배치
- 애니메이션

### Simi4/WoT-Blender-Addons

- 저장소: <https://github.com/Simi4/WoT-Blender-Addons>
- 확인 commit: `e711c6478cdb23503df8b17baac43db147b999d8`
- 라이선스: WTFPL v2
- 관련 파일:
  `tank_viewer/map_viewer/compiled_space/anca_reader/__init__.py`

공개 reader가 설명하는 `.anca` 구조:

- animation identifier와 internal identifier
- channel type 1–5
- type 4의 scale/position/rotation compression error
- scale key: 시간 포함 4 float
- position key: 시간 포함 4 float
- rotation key: 시간 포함 5 float
- 각 key index table
- type 5의 fallback scale/position/rotation
- packed entry version 6

원본 공개 reader는 값 배열을 건너뛰고, duration을 정수로 읽는다.
Legends 샘플의 첫 값은 float `12.0`이므로 최소 디코더에서 float로
수정했다.

#### 실제 Legends 호환 검증

`AM5048.anca`의
`content/animation/common/propeller_R.animation`을 읽은 결과:

- duration raw: `12.0`
- channel: 8개
- `Scene Root`: streamed
- `ref`: inline interpolated type 4
- `Propeller`, `Propeller_mesh`, LOD1/LOD2 노드: streamed
- Blender fallback pivot 대상: 7개
- channel table 끝: 1364
- stream length prefix 뒤 payload 시작: 1368
- container preload boundary: 1368
- stream payload: 956 bytes

`AM5049.anca`는 같은 구조이며 identifier가 `propeller_L`이다.

AM5048/AM5049의 기본·LOD1·LOD2 총 6개 파일 모두 경계·길이 검증을
통과했다.

구현 산출물:

- `FullAssembly/Ticonderoga1990/ANCA/decode_anca.py`
- `FullAssembly/Ticonderoga1990/ANCA/validate_samples.py`
- generated `AM5048.channels.json` (research-only, not bundled)
- generated `AM5049.channels.json` (research-only, not bundled)

지원 경계:

- 컨테이너, channel table, inline key, fallback pivot: 디코드
- trailing bit-packed streamed keyframes: 길이 검증·보존·hash만 제공
- Blender F-curve: 아직 생성하지 않음
- standalone `.anim`: 명시적으로 거부

### wotcuk/WoT-Blender-Toolkit

- 저장소: <https://github.com/wotcuk/WoT-Blender-Toolkit>
- 확인 commit: `59c5ca0af2e043b4091df788f2cc562e22980af8`
- 라이선스: GPL-3.0

`import_bw_animation.py`는 `.anim_processed`를 읽어 Blender armature에
P/R/S keyframe을 넣는 참고 구현이다. 하지만 Legends의
`AM5058_VLS_Hatch_open.anim`은 이 레이아웃과 맞지 않는다.

따라서:

- Blender Action/F-curve 생성 방식은 설계 참고 가능
- 코드를 직접 섞으면 GPL 의무가 생김
- Legends `.anim` 지원의 근거로 사용하면 안 됨

### wows-tools/wows-depack

- 저장소: <https://github.com/wows-tools/wows-depack>
- 라이선스: MIT

PC 자원 unpack 흐름의 공개 기준이다. Legends IDX v5/PKG 대응은 현재
프로젝트의 별도 extractor가 담당하므로, 라이선스 고지와 개념 참고 범위가
적절하다.

### 재사용하면 안 되는 자료

- 라이선스 없는 `wot-model-converter`: 조사 참고만 가능
- GitHub에 복제된 BigWorld 엔진 소스: 저작권·기밀 표기가 있어 복사 금지
- BigWorld vendor 문서 mirror: 고수준 개념 인용만 하고 구현 코드를
  재구성하거나 번들하지 않음
- 공식 PC/Korabli Content SDK: PC 저작 도구 참고용이며 Legends 호환과
  재배포 허용을 가정하지 않음

## 재질 완성 구현

### 정확한 매핑 흐름

```text
geometry section
→ assets.bin VisualPrototype render_set
→ material ID / MFM path
→ texture stem
→ _a / _n / _mg / _ao 및 변형
→ Blender material slot
```

파일명 stem만 사용하는 fallback은 `Preview`에서만 허용한다.

### Blender PBR 기본 변환

| 게임 텍스처 | Blender 연결 | 색 공간 |
|---|---|---|
| `_a` | Principled Base Color, 필요 시 Alpha | sRGB |
| `_n` | Normal Map node → Normal | Non-Color |
| `_mg` | 채널 분리 후 Metallic/Gloss 변환 | Non-Color |
| `_ao` | Base Color와 곱하거나 별도 AO 속성 | Non-Color |

`_mg`의 정확한 채널 의미와 gloss→roughness 반전은 실물 샘플로 확인해야
한다. 확인 전에 임의의 채널 규칙을 전 함선에 고정하면 안 된다.

`materialKind`는 주로 충돌·소리·효과 같은 표면 의미다. Blender PBR에
필수인 시각 재질은 아니므로 custom property로 보존하는 것이 좋다.

### 재질 검증

- geometry section 수 = material slot 매핑 수
- 기본 LOD에서 누락 texture stem 0개
- `_a` 해상도와 UV 범위 확인
- normal 방향 및 DirectX/OpenGL Y 채널 비교
- `_mg` 채널별 histogram과 금속/비금속 샘플 육안 비교
- dead/damage/alpha 변형을 기본 재질과 섞지 않음

## 스켈레톤·피벗 완성 구현

### 정적 계층

구성품을 선체 vertex에 bake하지 말고 아래처럼 부모 관계를 유지해야 한다.

```text
ShipRoot
└─ HullRoot
   ├─ hull render nodes
   ├─ HP_AGA_1
   │  └─ AGA2021 root/blend bones
   ├─ HP_AGM_1
   │  └─ AGM2028
   │     └─ AM5058 hatch nodes
   ├─ HP_ARS_*
   │  └─ radar nodes
   └─ propeller sockets
      └─ AM5048 / AM5049 node hierarchies
```

HP transform은 공개 PC 기준으로 world-space column-major 행렬이다.
탑재 모델의 `Rotate_Y_BlendBone` 또는 `Root_BlendBone` 보정과 좌표계
변환을 분리해서 기록해야 한다.

### 스킨

geometry의 `iiiww`를 읽어:

1. bone index 3개
2. 저장된 weight 2개
3. 남은 weight를 포맷 규칙에 따라 복원
4. 합을 정규화
5. render set이 참조하는 bone palette에 매핑

해야 한다. `iiiww` 존재만으로 Armature가 완성되는 것은 아니다.
`assets.bin` 노드 계층, inverse bind transform, section별 bone palette가
같이 필요하다.

## 애니메이션 구현 순서

### 1단계: `.anca` fallback과 binding

현재 구현됨:

- packed section 선택
- duration과 identifier
- channel 이름/type
- inline key/index
- streamed fallback pivot
- Blender object/pose-bone 이름 바인딩용 JSON

이 단계는 “피벗·채널 목록 복원”이며 완전 애니메이션은 아니다.

### 2단계: `.anca` streamed payload

다음 연구 대상:

1. payload 956 bytes의 bitstream frame 구조
2. 채널별 scale/position/quaternion quantization
3. `ref` channel의 시간/index와 streamed channel의 연결
4. 13개 raw time sample과 12.0 duration의 관계
5. L/R propeller의 회전 부호·축 비교

디코더는 payload length prefix와 SHA-256을 이미 출력하므로, 추후
디코딩 결과의 재현성을 확인할 수 있다.

### 3단계: Blender Action

- 대상 object 또는 pose bone을 exact name으로 찾기
- `x,y,z,w` quaternion을 Blender `w,x,y,z`로 재배열
- BigWorld→Blender 축 변환
- parent-local transform으로 변환
- time unit을 scene FPS로 변환
- interpolation을 우선 linear로 보존
- Action 이름에 원본 identifier 저장

축·시간 규칙이 검증되기 전에는 JSON 값을 곧바로 bone pose에 쓰지 않는다.

### 4단계: standalone `.anim`

실제 파일:

- `AM5058_VLS_Hatch_open.anim`
- `AM5058_VLS_Hatch_close.anim`
- 각각 2755 bytes
- header에서 version 계열 값 16, rate 계열 float 30.0
- `Launcher_Hatch`, LOD1/LOD2 노드 이름 포함

공개 WoT `.anim_processed` parser의 offset 27 모델과 일치하지 않는다.
따라서 다음과 같이 별도 포맷 연구가 필요하다.

1. 145개 전체 corpus에서 version/header cluster
2. open/close 쌍의 byte diff
3. node-name table과 offset table 경계
4. 길이·count에 대한 bounded parser
5. AM5058 open/close end pose를 첫 검증 대상으로 사용

호환 디코더와 실제 pose 검증 전까지 `.anim`은 `Unsupported`로 유지한다.

## 우선순위

### P0 — 완료 정의와 상태 표기

- Preview와 Complete를 UI에서 분리
- 각 내보내기에 누락 보고서 포함

### P1 — Legends GameParams normalizer

- `%bin` wrapper 처리
- inert/allow-list unpickler
- Cython positional state를 표준 ship/component dict로 변환
- 모든 HP→component/model 연결 출력

### P2 — Legends assets.bin v0x01000000

- version별 schema 분기
- relative pointer와 record stride 경계 검사
- VisualPrototype node/matrix/render_set/material path 추출

### P3 — 정적 전체 조립

- hull 모든 geometry
- GameParams의 모든 장착 카테고리
- HP world transform
- BlendBone correction
- hierarchy와 pivot 유지
- cross-package virtual path 전역 검색

### P4 — 재질

- render set별 material slot
- `_a/_n/_mg/_ao`
- alpha/dead/damage 분리
- 누락/대체 목록

### P5 — rig/skin

- 노드 계층과 armature
- `iiiww` palette/weight
- bind transform

### P6 — `.anca`

- 현재 최소 디코더를 extractor에 연결
- streamed payload 디코드
- 프로펠러 Action

### P7 — `.anim`

- 독립 parser와 VLS open/close Action
- 확인 전 지원 표시 금지

### P8 — 검증과 범용화

- Ticonderoga를 기준 함선으로 완료
- 다른 국가/함종 3개 이상으로 일반화
- 구성품 수, material coverage, hierarchy, animation end pose 자동 검사

## 라이선스·배포

| 자료 | 라이선스 | 사용 방침 |
|---|---|---|
| wows-model-exporter | MIT | 고지 후 이식 가능 |
| wows-depack | MIT | 고지 후 이식 가능 |
| Simi4 ANCA reader | WTFPL v2 | 고지 후 이식 가능 |
| WoT-Blender-Toolkit | GPL-3.0 | 코드 혼합 시 GPL 의무; 직접 복사 지양 |
| 라이선스 없는 변환기 | 없음 | 코드 복사 금지 |
| BigWorld source mirror | 독점/불명확 | 코드 복사 금지 |
| 게임 assets | 게임 약관 적용 | 추출물 재배포 금지, 사용자 설치본에서만 동작 |

도구는 geometry/texture/animation 원본을 번들하지 않고, 사용자가 소유한
설치본에서 로컬로 읽어 Blender 파일을 생성하는 형태가 안전하다.

## 최종 판단

“전부 조립되어야 완성”이라는 요구는 기술적으로 타당하다. 현재 프로젝트는
선체 미리보기에서 정적 전체 조립으로 넘어갈 근거를 확보했고, Legends
`.anca`의 피벗/채널 JSON까지 실제 파일로 검증했다.

그러나 아직 완성이라고 부르면 안 된다. 남은 핵심 관문은:

1. Legends `assets.bin` node/render-set parser
2. 전체 HP 구성품 계층 조립
3. section별 재질
4. skin/bind transform
5. `.anca` streamed keyframe
6. standalone `.anim`

이 여섯 항목을 통과하고 자동 누락 검사를 통과한 Blender 산출물만
`Verified complete`로 표시해야 한다.
