WoWS Toolbox 5.0.68
=======================================

[한국어 빠른 사용법]

WoWS Toolbox는 PC에 설치된 World of Warships 계열 게임에서 함선을 선택하여
파트별 OBJ로 내보내고, 모델과 장갑을 내장 3D 뷰어에서 확인하는 비공식
Windows 커뮤니티 도구입니다.

1. 게임 설치 폴더 설정
   프로그램의 설정 탭에서 사용하는 게임의 설치 폴더를 지정합니다.
   World of Warships Legends, World of Warships PC, Korabli, WoWS Blitz를 지원합니다.
   라이브 서버와 테스트 서버처럼 여러 설치본도 직접 선택할 수 있습니다.
   Blitz는 full_bundle(또는 bundle), 사용자가 직접 확보한
   main.*.net.wargaming.wows.blitz.obb, 선택적 DesignData가 함께 있는
   준비된 데이터 폴더를 선택합니다.
   자세한 준비 방법은 docs\WOWS_BLITZ_GUIDE_KO.md를 확인하세요.

2. 함선 목록 불러오기
   함선 추출 화면에서 게임 소스를 선택하고 "목록 새로고침"을 누릅니다.
   처음 읽는 설치본은 카탈로그 생성에 시간이 조금 걸릴 수 있습니다.

3. 함선 선택
   "함선 추가·편집"을 열고 원하는 함선을 더블클릭합니다.
   여러 함선을 추출하려면 왼쪽 추출 칸을 체크한 뒤 대기열에 적용합니다.

4. 모델 추출
   출력 폴더와 품질 설정을 확인하고 "추출 준비 검사"를 실행합니다.
   검사를 통과하면 "대기열 모델 추출"을 눌러 OBJ·MTL·PNG를 생성합니다.
   선체와 무장은 한 OBJ 안에서 선택 가능한 개별 오브젝트로 유지됩니다.
   PC판·코라블리는 기본 도색 또는 해당 함선의 고유 영구 위장을 선택할 수 있습니다.

5. 모델과 장갑 확인
   3D 모델 뷰어에서 "최근 추출 열기" 또는 "OBJ 파일 열기"를 사용합니다.
   파트 선택·숨김·단독 보기·이동·원위치, 장갑 두께·구역,
   해수면·단면·측정·비교 기능을 사용할 수 있습니다. 오른쪽 패널의
   경계와 구분선을 드래그하면 너비와 각 영역 높이를 조절할 수 있습니다.
   장갑판과 뒤쪽 외형 함선의 불투명도는 각각 따로 조절할 수 있습니다.
   뷰어에서 편집한 내용은 원본 OBJ에 저장되지 않습니다.

6. 업데이트 확인
   설정 > 업데이트에서 "시작할 때 자동 확인"을 켜거나 끌 수 있습니다.
   즉시 확인하려면 같은 항목의 "지금 확인"을 누릅니다.
   새 버전은 사용자 동의 후에만 내려받고 설치합니다.

필요한 구성 요소
-----------------
- Windows 10/11 64비트
- Windows PowerShell 5.1 또는 PowerShell 7
- Microsoft Edge WebView2 Runtime
- Python, Pillow, UnityPy는 프로그램에 포함되어 별도 설치가 필요하지 않습니다.
- OBJ 추출과 내장 뷰어에는 Blender가 필요하지 않습니다.

문제가 발생한 경우
------------------
- 작업 로그에서 마지막 오류를 확인합니다.
- 설정의 "진단 ZIP"을 사용하면 개인 경로와 모델 자산을 제외한 진단 자료를
  만들 수 있습니다.
- 버그 신고: https://github.com/Ch0m1n/WoWS-Toolbox/issues

게임 설치 폴더는 읽기 전용으로 사용합니다. 게임 자산의 권리는 각 권리자에게
있으며, 추출물은 해당 게임 EULA·플랫폼 약관과 관련 법률에 따라 사용해야
합니다. 자세한 내용은 LEGAL_NOTICE.txt를 확인하십시오.


[English Quick Start]

WoWS Toolbox is an unofficial Windows community tool for selecting ships from
locally installed World of Warships-family clients, exporting editable
part-based OBJ models, and inspecting models and armor in the built-in viewer.

1. Set game installation folders
   Open Settings and select the installation folder for each game you use.
   World of Warships Legends, World of Warships PC, Korabli, and WoWS Blitz are supported.
   Live, public-test, and other installations can be selected directly.
   For Blitz, select a prepared data root containing full_bundle (or bundle),
   the user's main.*.net.wargaming.wows.blitz.obb, and optional DesignData.
   See docs\WOWS_BLITZ_GUIDE.md for the complete preparation procedure.

2. Load the ship catalog
   On Ship Extraction, select a game source and click Refresh catalog.
   Building a catalog for a new installation may take a little time.

3. Select ships
   Open Add/Edit ships and double-click a ship to add it immediately.
   To extract several ships, check the Extract column and apply them to the queue.

4. Extract models
   Confirm the output folder and quality settings, then run Readiness check.
   After it passes, click Extract queued models to create OBJ, MTL, and PNG files.
   Hull and weapon parts remain individually selectable objects in one OBJ.
   PC and Korabli can use default paint or the selected ship permanent camouflage.

5. Inspect models and armor
   In the 3D Model Viewer, use Open recent extraction or Open OBJ file.
   You can select, hide, isolate, move, or reset parts; inspect armor thickness
   and zones; and use waterline, section, measurement, and comparison tools.
   Drag inspector borders to resize its width and section heights.
   Armor plates and the reference ship have separate opacity controls.

6. Check for updates
   In Settings > Updates, enable or disable Check automatically at startup.
   Click Check now to run an immediate update check. New versions are downloaded
   and installed only after you approve them.

Requirements
------------
- 64-bit Windows 10 or Windows 11
- Windows PowerShell 5.1 or PowerShell 7
- Microsoft Edge WebView2 Runtime
- Python, Pillow, and UnityPy are bundled; no separate installation is required.
- Blender is not required for OBJ extraction or the built-in viewer.

If something goes wrong
-----------------------
- Check the last error in the activity log.
- Use Diagnostic ZIP in Settings to create a report that excludes personal paths
  and model assets.
- Report bugs: https://github.com/Ch0m1n/WoWS-Toolbox/issues

Game installation folders are read only. All game assets remain the property of
their respective owners. Use exported content only as permitted by the applicable
game EULA, platform terms, and law. See LEGAL_NOTICE.txt for details.
