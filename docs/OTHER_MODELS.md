# Other models / 기타 모델

## 한국어

1. 메인 화면에서 **게임 소스**를 PC, 코라블리 또는 레전드로 선택합니다.
   설정에 해당 게임의 실제 설치 폴더가 지정돼 있어야 합니다.
2. 추출 대기열 영역의 **기타 모델** 버튼을 누릅니다.
3. 이름·코드·국가·리소스 경로로 검색하거나 항공기 / 포탄 / 어뢰 /
   미사일·로켓으로 분류를 선택합니다.
4. 원하는 모델의 체크박스를 선택합니다. 검색이나 분류를 바꿔도 선택은
   유지됩니다. 한 번에 1~200개를 추출할 수 있습니다.
5. **선택 모델 추출**을 누릅니다. 진행 상황과 실패 이유는 메인 작업 로그에
   표시됩니다. 메인 **취소** 버튼으로 추출을 취소할 수 있습니다.

출력은 설정한 추출 폴더의 `OtherModels/<게임 소스>/<모델별 새 폴더>`에
OBJ·MTL·PNG로 저장합니다. 최고 LOD와 원본 텍스처 크기를 사용하고 기존
결과를 덮어쓰지 않습니다. Blender는 필요하지 않습니다.
항공기 애니메이션과 스켈레톤 포즈는 포함하지 않는 정적 모델 출력입니다.
일부 항공기는 애니메이션으로 배치되는 부품이 분리된 원본 상태로 보일 수 있습니다.

프로펠러 회전 원판과 포탄 궤적용 머리·꼬리·마찰 효과는 기본 제외합니다.
원본 효과까지 필요한 경우 **프로펠러 회전판·포탄 궤적 효과도 포함**을 켭니다.
**추출 후 마지막 모델 열기**를 켜면 완료 후 내장 뷰어에서 확인할 수 있습니다.

목록은 현재 설치본에 실제로 존재하는 geometry 기준입니다. 이름은 내부 모델
이름이며, 포탄 공통 모델 하나를 여러 함포가 함께 사용할 수도 있습니다.
미사일·로켓 목록에는 항공 로켓도 포함되며, 유도 미사일만 뜻하지 않습니다.
데이터에 없는 모델은 추출할 수 없습니다. 지원하지 않는 모델 형식과 누락된
텍스처는 로그 및 결과 폴더의 진단 JSON에 표시합니다. 일부 항목이 실패해도
나머지는 계속 추출합니다.

PC·코라블리의 원본 GLB에 색상이 빠진 발사체는 해당 모델의 텍스처 폴더에서
이름이 정확히 대응하는 알베도만 보완합니다. 레전드는 ModelUber에 선언된
LOD0 렌더 세트와 텍스처 참조를 사용합니다. 텍스처 연결을 확정하지 못하면
다른 모델의 텍스처를 임의로 씌우지 않고 경고를 남깁니다.

코라블리에서 Oodle 압축 리소스를 읽는 경우 사용자 PC에 있는 런타임 DLL이
필요할 수 있습니다. 설정된 DLL이 삭제됐다면 기존 자동 탐색 경로를 사용합니다.
Oodle DLL이나 게임 리소스는 툴박스 배포본에 포함하지 않습니다.
Blitz의 기타 모델 메뉴는 이번 버전의 지원 범위에 포함되지 않습니다.

## English

Select **PC, Korabli or Legends** as the game source, configure its installation
path, and click **Other models** in the extraction queue area. Search by model
name, code, nation or resource path, choose a category, check 1–200 models,
and click **Extract selected models**. Selections survive search/category changes.

Results are independent new folders under the configured output directory:
`OtherModels/<source>/<model folder>`. Exports contain OBJ, MTL and PNG at LOD0
and original texture size. Blender is not required. Existing results are retained.
Aircraft are exported as static source geometry without skeletal animation poses;
animation-driven parts on some aircraft may remain separated in their source pose.
The main progress/log and Cancel button remain available during extraction.

Propeller rotation discs and shell tracer effects are excluded by default.
Enable **Include propeller discs and shell tracer effects** to retain them.
The optional viewer checkbox opens the last successful model after extraction.

Only installed geometry is listed. Names are internal resource names; multiple
guns can share a common shell model. The missile/rocket category also includes
aircraft rockets. Missing resources cannot be reconstructed. Unsupported models
and missing textures produce diagnostics; other selections continue on failure.

Missing PC/Korabli projectile albedo is supplemented only by an unambiguous
matching model-stem texture in its own texture directory. Legends uses declared
ModelUber LOD0 render-set joins and texture properties. Unresolved texture bindings
are reported instead of assigning another model's paint. Korabli may require a
locally installed Oodle runtime; game assets and Oodle DLLs are not redistributed.
Blitz other-model extraction is not supported in this release.
