# WoWS Legends 자산 추출·Blender 스테이징 도구

이 도구는 Steam판 **World of Warships: Legends**의 `res_packages/*.idx`와
`*.pkg`를 읽어서 자산 목록을 만들고, 선택한 파일만 안전하게 추출해요.
게임 설치 폴더는 읽기 전용으로 다루며 원본 패키지, 실행 파일, 메모리는
수정하지 않아요.

현재 목표는 다음처럼 범위를 정확히 나누는 거예요.

1. Legends IDX/PKG 인벤토리와 검증 추출: 네이티브 지원
2. DDS 등 텍스처 추출과 Blender 이미지 로드: 지원
3. OBJ/FBX/glTF/GLB가 존재할 경우 Blender 가져오기: 지원
4. Legends `.geometry`: 추출은 지원, 메시 디코딩은 별도 모듈
5. `.model`/`.visual`: 메시로 가장하지 않고 descriptor로만 분류

## 로컬 설치에서 검증한 결과

검증 날짜는 2026-08-03이고 설치 경로는 아래였어요.

```text
D:\SteamLibrary\steamapps\common\World of Warships Legends
```

전체 766개 IDX와 766개 PKG를 오류 없이 읽었어요.

| 항목 | 확인값 |
|---|---:|
| 가상 파일 | 152,726 |
| 패키지 압축 크기 | 54,874,371,895 bytes |
| 예상 해제 크기 | 111,224,585,740 bytes |
| `.geometry` | 22,453 |
| `.dds` | 66,931 |
| OBJ/FBX/glTF/GLB | 0 |
| `.model`/`.visual`/`.primitives` | 0 |

즉 현재 Legends 빌드는 PC판의 오래된 `.visual + .primitives` 조합이 아니라,
실제 메시를 `.geometry`에 두고 장면 정보는 `content/assets.bin`에 모은
구조에 가까워요.

Richelieu IDX 이름 매칭 dry-run도 확인했어요.

```text
T7_PFSB108_Richelieu.idx
238 files (GameParams.data + assets.bin 포함)
908,647,932 bytes unpacked
```

실제 샘플 `.geometry`와 `.dds`는 블록 해제 후 크기와 CRC32가 모두
IDX 값과 일치했어요.

Ticonderoga 실제 소량 추출도 확인했어요. 전체 번들 대신 함체 geometry
2개와 AO DDS 1개만 정확한 가상 경로로 골랐고, dry-run 뒤 2 MiB 제한
안에서 실행했어요.

```text
zupd601_PXSD307_Ticonderoga_1990.idx
3 files
875,528 bytes unpacked
CRC32/size verification: passed
```

파일별 해시와 검증 범위는 `VERIFICATION.md`에 정리했어요.

## 안전 장치

- 추출 명령은 기본적으로 **dry-run**이에요.
- 실제 쓰기는 `--execute`를 명시해야 해요.
- 아카이브의 `..`, 절대 경로, 드라이브 경로를 거부해요.
- 모든 출력은 `--output-root` 아래로 제한해요.
- 파일 수, 전체 해제 크기, 단일 파일 크기 제한을 적용해요.
- 기존 출력은 `--overwrite` 없이는 덮어쓰지 않아요.
- 패키지 블록 크기, 해제 크기, CRC32를 모두 검증해요.
- 쓰기는 임시 `.part` 파일 뒤 원자적으로 교체해요.
- 게임 폴더에는 어떤 파일도 만들지 않아요.

## 요구 사항

- Python 3.10 이상
- Blender 애드온은 Blender 3.5 이상
- Python 외부 패키지는 필요 없어요.

도구 폴더에서 실행하면 돼요.

```powershell
Set-Location ".\BlenderExtractor\blender_extractor"
python -m legends_assets support
```

## 1. 설치 전체 조사

```powershell
python -m legends_assets probe `
  --game-dir "D:\SteamLibrary\steamapps\common\World of Warships Legends"
```

이 명령은 읽기 전용이고 JSON으로 확장자별 개수와 파싱 오류를 보여줘요.

## 2. 자산 검색

Richelieu 패키지에서 geometry와 DDS를 찾아보는 예예요.

```powershell
python -m legends_assets list `
  --game-dir "D:\SteamLibrary\steamapps\common\World of Warships Legends" `
  --index-pattern "*Richelieu*.idx" `
  --extensions "geometry,dds" `
  --limit 100
```

가상 경로 패턴도 쓸 수 있어요.

```powershell
python -m legends_assets list `
  --game-dir "D:\SteamLibrary\steamapps\common\World of Warships Legends" `
  --pattern "content/gameplay/france/ship/**/*.geometry"
```

## 3. 작은 샘플 추출

먼저 계획만 확인해요.

```powershell
python -m legends_assets sample `
  --game-dir "D:\SteamLibrary\steamapps\common\World of Warships Legends" `
  --extensions "geometry,dds" `
  --per-extension 1
```

계획이 맞으면 `--execute`를 붙여요.

```powershell
python -m legends_assets sample `
  --game-dir "D:\SteamLibrary\steamapps\common\World of Warships Legends" `
  --extensions "geometry,dds" `
  --per-extension 1 `
  --output-root ".\output\samples" `
  --max-files 2 `
  --max-total-mib 8 `
  --max-single-mib 8 `
  --execute
```

## 4. 함선 IDX 전체 번들 만들기

`--ship-index`는 함선의 게임 내부 이름이 아니라 **IDX 파일명 조각**과
대조해요. 매칭된 IDX의 모든 파일을 추출하고, 기본적으로
`system_data.idx`에서 다음 두 파일도 추가해요.

```text
content/GameParams.data
content/assets.bin
```

Richelieu dry-run:

```powershell
python -m legends_assets extract-ship `
  --game-dir "D:\SteamLibrary\steamapps\common\World of Warships Legends" `
  --ship-index "Richelieu" `
  --output-root ".\output\richelieu_bundle" `
  --max-files 1000 `
  --max-total-mib 4096
```

약 909MB 출력을 확인한 뒤에만 실제 추출해요.

```powershell
python -m legends_assets extract-ship `
  --game-dir "D:\SteamLibrary\steamapps\common\World of Warships Legends" `
  --ship-index "Richelieu" `
  --output-root ".\output\richelieu_bundle" `
  --max-files 1000 `
  --max-total-mib 4096 `
  --execute
```

## 5. Blender 애드온

애드온 폴더:

```text
blender_addon\wows_legends_importer
```

이 폴더를 Blender의 scripts/addons 아래에 복사하거나 ZIP으로 묶어
`Edit > Preferences > Add-ons > Install`에서 설치한 뒤 활성화해요.

메뉴에는 다음 두 항목이 생겨요.

- `File > Import > WoWS Legends Extracted Assets`
- `File > Import > WoWS Legends Extraction Manifest`

지원 행동:

- GLB/glTF/FBX/OBJ: Blender 장면으로 가져오기
- DDS/DD0/DD1/PNG/TGA/JPEG: Blender Image 데이터로 로드
- `.geometry/.model/.visual/.primitives`: 명확한 경고와 함께 건너뛰기

DDS는 자동으로 재질에 연결하지 않아요. Legends의 `_a`, `_n`, `_mg`
채널 의미와 `.mfm/assets.bin` 관계를 확인하지 않고 자동 연결하면 잘못된
결과가 되기 때문이에요.

백그라운드 Blender에는 `blender_batch_import.py`를 쓸 수 있어요.

```powershell
blender --background --python ".\blender_batch_import.py" -- `
  --manifest ".\output\samples\extraction_manifest.json" `
  --output-root ".\output" `
  --blend-out ".\output\scene.blend"
```

## Legends geometry 디코더 훅

`legends_assets.decoder_hook`은 별도 커스텀 디코더를 붙이기 위한 안정된
계약을 제공해요.

```python
def decode_geometry(input_path: Path, output_dir: Path) -> Iterable[Path]:
    ...
```

반환 형식은 OBJ, glTF, GLB만 허용하고, 모든 파일이 지정 출력 폴더
안에 실제로 생성됐는지 검사해요. GLB는 헤더와 선언 길이까지 검증해요.

## PC WoWS 공개 변환기 주의

공개 도구 [wows-model-exporter](https://github.com/wows-tools/wows-model-exporter)는
PC판 `.geometry`, `GameParams.data`, `assets.bin`을 GLB로 조립하는 좋은
참고 자료예요. 하지만 Legends의 legacy section-table geometry에서
`wows-geometry-cli 0.2.1`이 Windows 예외 `0xC0000005`로 종료되고 파일을
만들지 못한 실제 사례가 확인됐어요.

그래서 `convert-geometry`와 `export-ship`은 **실험용 명시적 opt-in**일
뿐이고 기본 경로가 아니에요. 프로세스 종료 코드가 0이어도 유효한 GLB를
검증하기 전에는 성공으로 표시하지 않아요.

실험용 호출 계획만 보는 예:

```powershell
python -m legends_assets export-ship `
  --output-root ".\output\richelieu_bundle" `
  --ship "Richelieu" `
  --output ".\output\richelieu_bundle\richelieu.glb" `
  --exporter "C:\tools\wows-gltf-exporter.exe"
```

`--execute`를 붙이지 않으면 외부 실행 파일을 시작하지 않아요.

## 포맷 지원표

| 포맷 | 패키지 탐지/추출 | Blender 직접 처리 | 현재 판정 |
|---|---:|---:|---|
| OBJ | 예 | 예 | 현 빌드에서 0개 |
| FBX | 예 | 예 | 현 빌드에서 0개 |
| glTF/GLB | 예 | 예 | 현 빌드에서 0개 |
| DDS/DD0/DD1 | 예 | 이미지 로드 | 지원 |
| `.geometry` | 예 | 아니요 | 커스텀 디코더 필요 |
| `.primitive(s)` | 예 | 아니요 | 현 빌드에서 0개 |
| `.visual` | 예 | 아니요 | 현 빌드에서 0개; descriptor |
| `.model` | 예 | 아니요 | 현 빌드에서 0개; descriptor |
| `.mfm` | 예 | 아니요 | 현 빌드에는 독립 파일 0개 |

## 참고한 공개 자료

- [wows-depack](https://github.com/wows-tools/wows-depack) — MIT 라이선스,
  IDX/PKG 공개 명세와 구현
- [wowsunpack](https://github.com/landaire/wowsunpack) — IDX/PKG 인벤토리와
  추출 구현 참고
- [wows-model-exporter](https://github.com/wows-tools/wows-model-exporter) —
  MIT 라이선스, PC WoWS geometry/assets.bin/GLB 파이프라인
- [wows-extractor-gui](https://github.com/wows-tools/wows-extractor-gui) —
  GPL-3.0, PC WoWS 자산 브라우저와 glTF 내보내기
- [blender-primitives-tool](https://github.com/ShadowyBandit/blender-primitives-tool) —
  과거 BigWorld `.visual + .primitives` Blender 작업 참고

이 프로젝트는 위 도구의 코드를 복사해 포함하지 않고, 공개 포맷 연구와
로컬 Legends 파일 검증을 바탕으로 Python 표준 라이브러리만 사용해
작성했어요.

## 테스트

```powershell
python -m unittest discover -s tests -v
python -m py_compile `
  legends_assets\core.py `
  legends_assets\cli.py `
  legends_assets\exporters.py `
  legends_assets\decoder_hook.py `
  blender_batch_import.py `
  blender_addon\wows_legends_importer\__init__.py
```

테스트는 합성 IDX/PKG를 생성해 다중 블록 raw-DEFLATE, 원시 블록, CRC,
dry-run, 경로 탈출 차단, 출력 경계, 디코더 훅을 확인해요.

## 저작권과 사용 범위

추출된 함선 모델과 텍스처의 저작권은 원 권리자에게 있어요. 개인적인
연구·검증·Blender 작업 범위로 사용하고, 추출 자산을 재배포하거나 판매하지
않는 쪽이 안전해요. 이 도구는 게임 보호를 우회하거나 실행 중 메모리를
후킹하지 않아요.
