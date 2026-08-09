# Ticonderoga 1990 verified assembly mapping

이 디렉터리는 Legends 8.6.0/build `722375`의
`PXSD307_Ticonderoga_1990`에 대해 검증한 정적 조립 mapping 생성기예요.
범용 `--ship-key` 추출기가 아니에요.

## 포함 파일

- `build_ticonderoga_assembly_v2.py` — 검증된 진입점
- `build_ticonderoga_assembly.py` — 공유 바이너리 파서와 조립 코어
- `ticonderoga_1990_profile_manifest.json` — Ticon 전용 acceptance와 canonical
  output 계약
- `ACCEPTANCE_VERIFIED.md` — 사람이 읽는 검증 결과
- `test_path_independent_mapping_contract.py` — 출력 경로 독립성 회귀 테스트

생성되는 `ticonderoga_1990_static_assembly.json`은 약 1.4MB인 사용자 로컬
산출물이라 이 도구 패키지에는 넣지 않았어요.

## 결정론적 출력

source metadata에는 로컬 절대경로를 기록하지 않아요. 네 입력은 다음
형태로만 기록돼요.

```json
{
  "logical_source": "content/GameParams.data",
  "size": 34301142,
  "sha256": "509fd300...",
  "access": "read-only"
}
```

그래서 같은 내용이면 입력·출력 폴더가 달라도 mapping 전체 바이트와
SHA-256이 같아요. 현재 canonical SHA-256은 다음 값이에요.

```text
690F0EC02AB455D7DB7094A5E7D7052F0BC1F6110A87B5CEB6BB106B2C276337
```

서로 다른 두 입력 디렉터리를 사용한 실제 path-variance 생성에서 mapping과
acceptance 보고서가 모두 byte-identical이었어요.

## v2가 필요한 이유

Legends v0 MaterialPrototype의 property layout은 다음과 같아요.

- `property_count`: material header `+0x0c`의 unsigned 16-bit
- property tag type `0`: bool
- property tag type `1`: int

v2 진입점은 이 해석을 적용한 뒤 read-only 코어를 호출해요. Ticon profile
재현에는 공유 코어를 직접 실행하지 말고 v2를 사용하세요.

## 조립 의미

- 상세 선체 mesh: Bow, MidFront, MidBack, Stern 4개
- GameParams combat hardpoint: 17/17
- authored misc: 10개, 그중 Mk141 cap 2개는 dock 조건
- `AM5058` runtime launch overlay: 2개, 정적 scene에서는 기본 숨김
- 상세 ModelUber model: 26개
- render set: 194개
- texture property: 155/155
- unresolved field/path, non-finite matrix: 0

combat placement는 `HP_world * correction`이에요. correction은
`Rotate_Y_BlendBone`, 다음으로 `Root_BlendBone`의 local rotation 역행렬을
사용하고 둘 다 없으면 identity예요. 출력 행렬은 glTF right-handed Y-up의
column-major MAT4예요.

## 직접 재현

보통은 상위 `Extract-TiconderogaFull.ps1`가 이 단계를 자동으로 호출해요.
이미 네 sidecar를 외부 출력에 추출했다면 직접 실행할 수도 있어요.

```powershell
python .\build_ticonderoga_assembly_v2.py `
  --game-params 'D:\Exports\extracted\content\GameParams.data' `
  --assets 'D:\Exports\extracted\content\assets.bin' `
  --prototype-index 'D:\Exports\extracted\content\prototypes.index.data' `
  --prototype-data 'D:\Exports\extracted\content\prototypes.data' `
  --output 'D:\Exports\generated\ticonderoga_1990_static_assembly.json' `
  --acceptance 'D:\Exports\generated\mapping_acceptance.md'
```

입력은 읽기 전용으로 열어요.

## acceptance 경계

`static_assembly_acceptance=true`가 의미하는 건 Ticonderoga의 온전한 정적
계층, 파츠, 명시적 재질 정보, 배치 행렬을 검증했다는 뜻이에요. 다음은
포함하지 않아요.

- replay event decoding
- 동적 포탑·레이더 애니메이션
- 발사·해치 타이밍
- damage/dead swap
- particle, wake, destruction effect

다른 함선은 segment 이름, component family, nested mount, 조건부 port,
action/dead 모델 의미가 다를 수 있어요. 그래서 일반화 전에는 함선별
발견과 acceptance가 새로 필요해요.
