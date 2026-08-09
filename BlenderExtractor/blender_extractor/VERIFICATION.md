# WoWS Legends 자산 도구 검증 기록

검증 날짜: 2026-08-03  
설치 경로: `D:\SteamLibrary\steamapps\common\World of Warships Legends`

## 설치 전체 인벤토리

`python -m legends_assets probe`로 게임 폴더를 읽기 전용 조사했어요.

| 항목 | 결과 |
|---|---:|
| IDX | 766 |
| PKG | 766 |
| 가상 파일 | 152,726 |
| 인덱스 파싱 오류 | 0 |
| 패키지 압축 크기 | 54,874,371,895 bytes |
| 예상 해제 크기 | 111,224,585,740 bytes |
| `.geometry` | 22,453 |
| `.dds` | 66,931 |
| OBJ/FBX/glTF/GLB | 0 |
| `.model`/`.visual`/`.primitives` | 0 |

검증된 IDX 변형은 `ISFP`, marker `0x01010005`, header version
`0x40`이에요. PKG의 raw-DEFLATE 블록과 원시 블록을 모두 처리하며,
출력 전에 해제 크기와 CRC32를 확인해요.

## Ticonderoga 실제 소량 추출

대상 인덱스:

```text
zupd601_PXSD307_Ticonderoga_1990.idx
```

전체 함선 묶음을 풀지 않고, 정확한 가상 경로 3개와 최대 2 MiB 제한을
지정했어요. dry-run에서 `3 files / 875,528 bytes`를 확인한 뒤에만
`--execute`를 붙였어요.

| 파일 | bytes | IDX CRC32 | SHA-256 |
|---|---:|---|---|
| `ASC307_Ticonderoga_1990.geometry` | 129,068 | `ffe43318` | `3DD8A696E7AB326C7CAFD5B418405770516ED8B6D0B5B28E49225FCB82B2E85C` |
| `ASC307_Ticonderoga_1990_Bow.geometry` | 47,268 | `7d0053d5` | `50D0755064ED421A76C464B8CDE02FD716EB7E77105CA79775A452A9E6E2DB8B` |
| `ASC307_Ticonderoga_1990_ao.dds` | 699,192 | `1d8bba7a` | `3EE7B3EC2892A3F7624F8705AECFC93878B3F0C44B22E774D0C7E98282424BC2` |

세 파일 모두 PKG 블록 해제, 예상 크기, CRC32 검사를 통과했고 DDS는
`DDS ` magic을 확인했어요. 결과와 manifest는
`output/verified_ticonderoga`에 있어요.

## 자동 테스트

```text
python -m unittest discover -s tests -v
Ran 11 tests
OK
```

확인 범위:

- 합성 Legends IDX/PKG 파싱
- 다중 raw-DEFLATE 블록과 원시 블록
- 크기 및 CRC 불일치 거부
- 경로 탈출과 출력 루트 이탈 차단
- 기본 dry-run
- 외부 디코더 출력 형식/경계 검증
- Blender 3.5 선언과 3.5/4.x OBJ Z-up 축 인자
- PC 변환기 경로가 명시적 experimental opt-in인지 확인

전체 Python 파일은 `python -m py_compile`도 통과했어요.

## 알려진 제한

추출 성공과 메시 변환 성공은 구분해요. Legends의 legacy section-table
`.geometry`는 실제로 추출되지만 Blender가 직접 읽을 수 없어요.
`wows-geometry-cli 0.2.1`은 이 포맷에서 Windows 예외 `0xC0000005`로
종료된 사례가 있어서 기본 변환기로 쓰지 않아요.

`convert-geometry`와 `export-ship`은 명시적 experimental 기능이고,
유효한 GLB 헤더와 선언 길이를 검증하기 전에는 성공으로 기록하지 않아요.
커스텀 디코더는 `legends_assets.decoder_hook` 계약을 통해 OBJ/glTF/GLB를
생성하도록 연결할 수 있어요.
