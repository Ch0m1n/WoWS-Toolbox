WoWS Toolbox 5.0.30
=======================================

[한국어 빠른 사용법]

WoWS Toolbox는 내 PC에 설치된 World of Warships 계열 게임에서 원하는 함선을
선택해 파트별 OBJ로 내보내고, 모델과 장갑을 내장 3D 뷰어에서 확인하는
Windows용 비공식 커뮤니티 도구예요.

[5.0.30 · English default and GitHub-ready source]
- 새 설치와 포터블 배포본의 기본 언어를 영어로 바꾸고, 설치 언어 화면도 영어를 기본 선택으로 열어요. 기존 설치의 언어 선택은 업데이트 때 보존해요.
- 영문 README, 기여·보안 정책, 이슈·PR 양식과 Windows CI를 추가해 GitHub에서 소스와 변경 이력을 관리할 수 있게 정리했어요.
- 개인 경로가 들어 있던 로컬 배치 예제는 제거하고 공개 가능한 일반 예제로 교체했어요.

[5.0.29 · 안전한 WebView2 업데이트와 무손실 기본 품질]
- 설치기가 실행 중인 WoWS Toolbox/WebView2를 강제로 끄지 않아요. 업데이트 전 프로그램을 정상 종료하도록 안내하고, GUI도 종료 시 WebView2를 명시적으로 정리해 0x80000003 팝업을 막아요.
- 새 추출 기본값과 기존 5.0.x 설정을 최고 메시 LOD0 + 원본 크기 텍스처로 맞춰요. 2K/1K와 낮은 LOD는 사용자가 직접 선택할 때만 적용돼요.
- PC 실데이터 비교에서 LOD0 240,471삼각형, LOD1 168,030삼각형을 확인했어요. Legends는 ModelUber LOD0 렌더셋만 받고, 결과 보고서에 실제 품질 계약을 기록해요.

[5.0.27 · 안정적인 장갑 표시와 인플레이스 업데이트]
- 장갑 모드에서 본체와 장갑판의 렌더 순서를 고정하고 양면 투명 장갑을 단일 패스로 그려, 카메라 회전 때 색과 겹침이 튀던 현상을 줄였어요.
- 같은 제품 ID와 설치 경로를 유지해 기존 WoWS Toolbox를 제거하지 않고 새 설치기로 바로 업데이트해요.
- 업데이트 때 기존 인터페이스 언어, 사용자 설정, 캐시, output 추출물을 보존하며 실행 중인 프로그램만 안전하게 닫았다가 다시 열 수 있어요.

[5.0.26 · Legends 조립 방향·주함포 복구]
- Legends 순수 Python OBJ 좌표계를 Blender의 -Z Forward / Y Up 규칙과 일치시켜 선체와 상부구조물이 앞뒤로 뒤집히던 문제를 고쳤어요.
- A1_Artillery 같은 번호형 함선 모듈을 선택 선체 계열로 인식해 누락되던 주함포를 해당 HP_AGM 위치에 조립해요.
- 내장 뷰어가 새 native OBJ 파트·원점 메타데이터를 직접 읽어 방향/원점 경고 없이 파트 조작 정보를 사용해요.
- Legends Alaska 실제 재추출에서 주함포 3기, 전투 하드포인트 86/86, 최종 독립 오브젝트 577개를 확인했어요.

[5.0.25 · 세 게임 공통 Blender 없는 OBJ 정식판]
- Legends·PC판·Korabli의 선체·무장·부품 OBJ 조립은 내장 Python으로 처리하며 Blender를 실행하지 않아요.
- 정식판의 출력 형식은 "OBJ만 · 세 게임 공통 · Blender 불필요"로 고정돼요.
- Blender 경로는 설정·준비 검사·대기열 명령에 사용하지 않아요. GLB/FBX 출력은 제공하지 않아요.
- 전용 Python 3.10.11과 Pillow 12.3.0을 내장해 시스템 Python 설치가 필요 없어요.
- 설치 중 시작 메뉴·바탕화면 바로가기를 각각 선택할 수 있고, 바로가기는 콘솔 창 없는 `WoWS Toolbox.exe`를 실행해요.
1. 실행과 언어
   설치 시작 시 한국어 또는 English를 선택해요. 선택한 언어는 메인 도구,
   함선 선택 창과 3D/장갑 뷰어에 적용돼요. 나중에는 프로그램의
   "설정 > 인터페이스 언어"에서 바꾸고 다시 열면 돼요.

2. 게임 설치본 선택
   게임 소스를 고른 뒤 "게임 폴더 선택"으로 라이브/테스트 서버 등 원하는
   클라이언트 폴더를 지정해요. bin이나 res_packages를 골라도 루트를 찾아요.

3. 함선 선택과 추출
   "목록 새로고침" 후 "함선 추가·편집"을 열어요. 행을 더블클릭하면 바로
   선택되고, 여러 함선을 체크하면 대기열에 함께 담을 수 있어요. 출력 형식을
   고르고 "추출 준비 검사" 뒤 "대기열 모델 추출"을 눌러요.

4. 확인
   "3D 모델 뷰어"에서 최근 추출을 열어요. 파트 선택·숨김·이동·회전,
   포탑 선회·포신 앙각, 정확한 장갑 두께, 해수면과 단면을 확인할 수 있어요.

필수/선택 구성 요소
-------------------
- Windows 10/11 64비트
- Windows PowerShell 5.1 또는 PowerShell 7 (설치되어 있으면 7을 우선 사용)
- Python/Pillow: 프로그램에 전용 런타임이 포함되어 별도 설치 불필요
- Microsoft Edge WebView2 Runtime: 설치기가 확인하고, 없을 때 Microsoft 서명 부트스트래퍼로 설치
- Blender: 세 게임 OBJ 추출과 내장 뷰어 모두 불필요
- 일부 Korabli 패키지: 설치된 게임의 호환 Oodle DLL이 필요할 수 있음

데이터와 제거
-------------
- 게임 설치 폴더는 읽기 전용으로 사용해요.
- 게임 자산, Oodle DLL과 이미 추출된 모델은 설치 파일에 포함하지 않아요.
- 사용자 설정/캐시는 %LOCALAPPDATA%\WoWSToolbox에 저장돼요.
- output의 사용자 생성 파일은 제거 프로그램 소유 파일로 등록하지 않지만,
  중요한 결과물은 별도 폴더에 백업하는 편이 안전해요.

법적 고지
---------
WoWS Toolbox는 비공식 커뮤니티 도구이며 Wargaming Group Limited, Lesta Games,
각 라이선스 제공자 또는 관계사의 승인·후원·제휴를 받은 제품이 아니에요.
게임 명칭, 상표, 로고, 게임 데이터, 3D 모델, 텍스처, 음원 및 그 밖의 모든
게임 자산에 관한 권리는 각 권리자에게 있어요.

WoWS Toolbox의 MIT 라이선스는 WoWS Toolbox 자체 코드에만 적용되며, 추출된 게임
자산을 사용·배포·판매할 권리를 부여하지 않아요. 사용자는 해당 게임 EULA,
플랫폼 약관과 지역 법률을 직접 확인하고 따라야 해요. 이 문구는 일반 안내이며
법률 자문이 아니에요.


[English Quick Start]

WoWS Toolbox is an unofficial Windows community tool that selects ships from a
locally installed World of Warships-family client, exports editable part-based
OBJ models, and inspects models and armor in the built-in 3D viewer.

1. Launch and language
   English is selected by default when setup starts; Korean remains available. The selected language applies to
   the main toolbox, ship picker, and 3D/armor viewer. To change it later, use
   Settings > Interface language, save, and restart WoWS Toolbox.

2. Select a game installation
   Choose a game source, then use "Select game folder" to point at the live,
   public-test, or other client. A bin or res_packages subfolder is accepted.

3. Select and extract ships
   Refresh the catalog and open the ship picker. Double-click a row for a quick
   selection, or check multiple ships to build a queue. Select the output format,
   run the readiness check, then start queue extraction.

4. Inspect
   Open the 3D Model Viewer to select, hide, move, and rotate parts; traverse
   turrets, elevate barrels, and inspect exact armor thickness, waterline, and
   section views.

[5.0.30 · English default and GitHub-ready source]
- English is now the default for new and portable installations, including the initial installer language selection. In-place updates preserve the user's existing language choice.
- Adds an English project README, contribution and security policies, issue and pull-request templates, and Windows CI for public source management.
- Removes a local batch request containing personal absolute paths and replaces it with a sanitized example.

[5.0.29 · Safe WebView2 updates and lossless default quality]
- Setup no longer force-terminates a running WoWS Toolbox/WebView2 process. It asks the user to close the app normally, while the GUI explicitly disposes WebView2 on exit to avoid 0x80000003 popups.
- New and migrated 5.0.x settings default to highest-detail mesh LOD0 plus original-size textures. 2K/1K textures and lower LODs are used only when explicitly selected.
- A real PC asset comparison measured 240,471 triangles at LOD0 versus 168,030 at LOD1. Legends consumes only ModelUber LOD0 render sets, and export reports now record the effective quality contract.

[5.0.27 · Stable armor display and in-place updates]
- Fixes model/armor render order and uses a single pass for double-sided transparent armor so orbiting no longer reshuffles colors and overlaps.
- Keeps the same product ID and install directory, allowing a newer setup to update an existing WoWS Toolbox installation without uninstalling it first.
- In-place updates preserve the selected UI language, user settings, cache, and exported models while safely closing the running application when needed.

[5.0.26 · Legends orientation and main-battery repair]
- Matches the native Legends OBJ basis to Blender's -Z Forward / Y Up convention, fixing the fore/aft mirror between hull and fittings.
- Treats numbered modules such as A1_Artillery as members of the selected hull family, restoring main batteries at their HP_AGM hardpoints.
- The integrated viewer now accepts native OBJ part/pivot metadata without assembly-orientation or part-origin warnings.
- A full Legends Alaska extraction verified three main turrets, 86/86 combat hardpoints, and 577 independent OBJ objects.

[5.0.25 · Blender-free OBJ release for all three games]
- Legends, World of Warships PC, and Korabli hull, weapon, and equipment OBJ assembly runs in the bundled Python runtime and never launches Blender.
- The release fixes output to "OBJ only · All three games · No Blender required".
- No Blender path is used by settings, readiness checks, or queue commands. GLB/FBX output is not exposed.
- Private Python 3.10.11 and Pillow 12.3.0 runtimes are bundled; no system Python is required.
- Setup lets you choose Start menu and desktop shortcuts separately; both launch the console-free `WoWS Toolbox.exe`.

Requirements / optional tools
-----------------------------
- 64-bit Windows 10 or 11
- Windows PowerShell 5.1 or PowerShell 7 (7 is preferred when both exist)
- Python/Pillow: private runtimes are bundled; no separate installation is required
- Microsoft Edge WebView2 Runtime: setup checks it and invokes the Microsoft-signed bootstrapper only when missing
- Blender is not required for extraction or the integrated viewer for any supported game source
- Some Korabli packages may require a compatible Oodle DLL from that client

Data and uninstall behavior
---------------------------
- Game installation folders are read only.
- No game assets, Oodle DLLs, or previously extracted models are bundled.
- User settings and caches are stored under %LOCALAPPDATA%\WoWSToolbox.
- User-created files under output are not installer-owned, but important exports
  should still be backed up separately.

Legal notice
------------
WoWS Toolbox is an unofficial community tool and is not endorsed by, sponsored
by, or affiliated with Wargaming Group Limited, Lesta Games, their licensors,
or their affiliates. Game names, trademarks, logos, game data, 3D models,
textures, audio, and all other game assets remain the property of their owners.

The WoWS Toolbox MIT license applies only to WoWS Toolbox code and does not grant
rights to use, distribute, or sell extracted game assets. Users are responsible
for following the applicable game EULA, platform terms, and local law. This
notice is general information, not legal advice.