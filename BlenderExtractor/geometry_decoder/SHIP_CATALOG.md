# Legends 함선 선택·번역 백엔드

GUI는 `List-LegendsShips.ps1`을 별도 프로세스로 실행하고 stdout의 JSON
배열을 읽습니다. 이 작업은 `res_packages`의 IDX 메타데이터와 선택한 언어의
`global.mo`만 읽으며 게임 파일을 추출하거나 수정하지 않습니다.

```powershell
.\List-LegendsShips.ps1 `
  -GameDir "D:\SteamLibrary\steamapps\common\World of Warships Legends" `
  -Language ko `
  -SupportedOnly
```

`-Language`의 기본값은 `ko`입니다. 경로 조작을 막기 위해 `ko`, `en`, `ja`,
`pt_br`처럼 ASCII 언어 토큰만 받습니다. 번역 파일은 다음 순서로 찾습니다.

1. `<게임>\res\texts\<언어>\LC_MESSAGES\global.mo`
2. `<게임>\texts\<언어>\LC_MESSAGES\global.mo`

두 경로에 파일이 없으면 검색한 정확한 경로를 포함한 오류로 끝납니다.

## JSON 행 계약

각 행의 주요 필드는 다음과 같습니다.

- `id`: 정확한 IDX와 내부 hull resource를 결합한 안정 식별자
- `index_filename`: `res_packages`에 있는 정확한 IDX basename
- `hull_resource`: IDX 안의 정확한 5분할 선체 resource 이름
- `hull_resource_path`: 선체 geometry가 있는 가상 parent 경로
- `output_slug`: 출력 하위 폴더로 바로 쓸 수 있는 안전한 이름
- `ship_code`: IDX의 플레이 가능 함선 코드(예: `PJSB018`)
- `localization_key`: 항상 `IDS_<ship_code>` 형식인 기본 번역 키
- `localized_name`: `global.mo`에서 찾은 실제 게임 표시명, 없으면 `null`
- `localized_language`: 정규화된 언어 토큰
- `display_label`: `localized_name`이 있으면 그 값, 없으면 `variant_label`
- `variant_label`: 종전 `display_label`이었던 내부 변형·resource 구분명
- `support_level`: `hull-only` 또는 `unsupported`
- `selectable`: 현재 범용 선체 추출기로 처리 가능한지 여부

`id`, `output_slug`, `index_filename`, `hull_resource`는 번역 적용 전후에 바뀌지
않습니다. 정렬은 번역 적용 뒤 `localized_name`(없으면 `variant_label`)의 `casefold()`와
`variant_label.casefold()` 순으로 결정됩니다. `localized_name`의 NBSP, 발음
기호 및 기타 UTF-8 문자는 게임 파일 원문 그대로 보존합니다. 검색용 공백
정규화가 필요하면 GUI에서 별도로 처리해야 합니다.

하나의 IDX에 Yamato 기본형·ARP·Star Trek 같은 여러 완전한 선체가 있으면
각 `hull_resource`를 별도 행으로 냅니다. 이 행들은 같은 `ship_code`와
`localized_name`을 공유하지만 서로 다른 `variant_label`, `id`,
`hull_resource`로 구분됩니다. GUI는 사람이 고를 때 다음처럼 실제 이름과
변형명을 함께 보여주는 것이 좋습니다.

```text
Yamato — Yamato StarTrek [JSB403] — Tier 8
```

## 번역 키 선택 규칙

함선 코드가 `PJSB018`이면 다음 두 키만 정확히 조회합니다.

1. `IDS_PJSB018`
2. 첫 값이 없거나 빈 문자열 또는 `!`일 때만 `IDS_PJSB018_FULL`

부분 문자열이나 비슷한 키를 검색하지 않습니다. 번들 MO reader는 헤더,
원문·번역 table, hash table 및 모든 문자열 범위를 파일 크기 안으로 제한하고
NUL 종료를 확인합니다. 필요한 키의 번역만 UTF-8로 해석하므로 gettext가
실패할 수 있는 잘못된 빈-key 메타데이터나 무관한 메시지 인코딩에는 영향을
받지 않습니다. 선택한 정확한 키 자체가 잘못된 UTF-8이면 명확히 실패합니다.

## 정확 선택 추출

선택 추출 시 GUI는 목록에서 받은 값을 그대로 전달해야 합니다.

```powershell
.\Extract-LegendsShip.ps1 `
  -GameDir "D:\SteamLibrary\steamapps\common\World of Warships Legends" `
  -ShipIndexFile "zupd601_PXSD307_Ticonderoga_1990.idx" `
  -ShipResource "ASC307_Ticonderoga_1990" `
  -OutputRoot "C:\Exports\SelectedShips\Ticonderoga"
```

`-ShipIndex`는 예전 substring 검색과의 호환을 위해서만 남아 있습니다.
GUI는 모호한 substring 대신 `-ShipIndexFile`과 `-ShipResource`를 사용해야
합니다.

`hull-only`는 카탈로그가 IDX 메타데이터에서 선체 geometry 5개,
diffuse map 2개와 예상 storage flag `(5, 1)`을 찾았다는 뜻입니다. 카탈로그
단계에서는 package payload를 읽거나 geometry를 디코딩하지 않습니다.
CRC·geometry decode와 OBJ/GLB/BLEND 결과 검증은 실제 추출 때 수행됩니다.
따라서 이 표시는 포탑·레이더·보트 등의 장착 계층까지 범용으로 조립된다는
뜻이 아닙니다.
