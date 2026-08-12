(() => {
  'use strict';

  const language = new URLSearchParams(window.location.search).get('lang') === 'ko' ? 'ko' : 'en';
  document.documentElement.lang = language;
  window.WoWSToolboxLanguage = language;
  const formalKoreanReplacements = [
    [/열어볼까요\?/g, '열어보시겠습니까?'],
    [/추출할까요\?/g, '추출하시겠습니까?'],
    [/업데이트할까요\?/g, '업데이트하시겠습니까?'],
    [/확인할까요\?/g, '확인하시겠습니까?'],
    [/하시겠어요\?/g, '하시겠습니까?'],
    [/할까요\?/g, '하시겠습니까?'],
    [/열까요\?/g, '여시겠습니까?'],
    [/비울까요\?/g, '비우시겠습니까?'],
    [/닫을까요\?/g, '닫으시겠습니까?'],
    [/됐어요/g, '되었습니다'], [/되었어요/g, '되었습니다'], [/했어요/g, '했습니다'],
    [/왔어요/g, '왔습니다'], [/갔어요/g, '갔습니다'], [/났어요/g, '났습니다'],
    [/찾았어요/g, '찾았습니다'], [/고쳤어요/g, '고쳤습니다'], [/새로고쳤어요/g, '새로고쳤습니다'],
    [/남겼어요/g, '남겼습니다'], [/넘었어요/g, '넘었습니다'], [/커졌어요/g, '커졌습니다'],
    [/만들었어요/g, '만들었습니다'], [/비웠어요/g, '비웠습니다'], [/끝났어요/g, '끝났습니다'], [/열었어요/g, '열었습니다'],
    [/닫았어요/g, '닫았습니다'], [/켰어요/g, '켰습니다'], [/껐어요/g, '껐습니다'],
    [/보였어요/g, '보였습니다'], [/아니에요/g, '아닙니다'], [/이에요/g, '입니다'],
    [/거예요/g, '것입니다'], [/예요/g, '입니다'], [/돼요/g, '됩니다'],
    [/있어요/g, '있습니다'], [/없어요/g, '없습니다'], [/않아요/g, '않습니다'],
    [/맞아요/g, '맞습니다'], [/해요/g, '합니다'], [/보여요/g, '보입니다'],
    [/열어요/g, '엽니다'], [/읽어요/g, '읽습니다'], [/내보내요/g, '내보냅니다'],
    [/골라요/g, '고릅니다'], [/남겨요/g, '남깁니다'], [/줄여요/g, '줄입니다'],
    [/비워요/g, '비웁니다'], [/지워요/g, '지웁니다'], [/바꿔요/g, '바꿉니다'],
    [/닫아요/g, '닫습니다'], [/켜요/g, '켭니다'], [/꺼요/g, '끕니다'],
    [/넣어요/g, '넣습니다'], [/받아요/g, '받습니다'], [/끝나요/g, '끝납니다'],
    [/남아요/g, '남습니다'], [/넘어요/g, '넘습니다'], [/달라요/g, '다릅니다'],
    [/나타나요/g, '나타납니다'], [/돌아가요/g, '돌아갑니다'], [/들어가요/g, '들어갑니다'],
    [/가져와요/g, '가져옵니다'], [/골라주세요/g, '선택해 주세요'], [/골라 주세요/g, '선택해 주세요']
  ];

  function formalizeKorean(text) {
    let result = text;
    for (const [pattern, replacement] of formalKoreanReplacements) {
      result = result.replace(pattern, replacement);
    }
    return result;
  }

  const exact = new Map(Object.entries({
    '모델을 열어 주세요': 'Open a model',
    '원근': 'Perspective',
    '정면': 'Front',
    '측면': 'Side',
    '상면': 'Top',
    '모델': 'Model',
    '장갑': 'Armor',
    '배경': 'Background',
    '조명': 'Lighting',
    '스튜디오 조명': 'Studio lighting',
    '조명과 표면': 'Lighting & surface',
    '기본값': 'Reset',
    '위치': 'Position',
    '패널 위치 초기화': 'Reset panel position',
    '빈 곳을 드래그해 패널을 옮길 수 있어요': 'Drag an empty header area to move the panel',
    '노출': 'Exposure',
    '주광': 'Key light',
    '환경광': 'Environment',
    '표면 굴곡': 'Surface detail',
    'PBR 상세 미리보기': 'PBR detail preview',
    '기본값은 원본 색상 우선이며 모든 파트에 동일하게 적용됩니다.': 'The default prioritizes source color and applies consistently to every part.',
    '방위': 'Azimuth',
    '고도': 'Elevation',
    '알베도 검사': 'Albedo inspection',
    '모든 광원과 PBR 채널을 무시하고 원본 색상 텍스처만 표시합니다.': 'Show only the source color texture, ignoring all lights and PBR channels.',
    'PBR 채널과 표면 굴곡을 확인하는 비파괴 미리보기 설정이에요.': 'Non-destructive preview controls for inspecting PBR channels and surface relief.',
    '표시 설정만 바뀌며 원본 OBJ와 텍스처는 수정되지 않습니다.': 'Only the preview changes; the source OBJ and textures are not modified.',
    '조명 기본값을 복원했습니다.': 'Lighting defaults restored.',
    '격자': 'Grid',
    '와이어': 'Wireframe',
    '단면': 'Section',
    '측정': 'Measure',
    '해수면': 'Waterline',
    '비교 겹침': 'Overlay comparison',
    '비교 좌우': 'Side-by-side comparison',
    '비교 닫기': 'Close comparison',
    '캡처': 'Capture',
    '추출한 OBJ를\n바로 점검해요.': 'Inspect exported OBJ files\nright away.',
    '메인 화면의 파일 열기 또는 최근 추출 열기를 사용해 주세요.': 'Use Open OBJ or Open recent extraction on the main screen.',
    '모델 읽는 중': 'Loading model',
    'OBJ 구조를 분석하고 있어요.': 'Analyzing the OBJ structure.',
    '뷰어 준비됨': 'Viewer ready',
    '파트 목록': 'Part list',
    '해수면 높이': 'Waterline height',
    '선택한 파트': 'Selected part',
    '선택 없음': 'Nothing selected',
    '이동 G': 'Move G',
    '회전 R': 'Rotate R',
    '단독 보기': 'Isolate',
    '좌회전 15°': 'Rotate left 15°',
    '우회전 15°': 'Rotate right 15°',
    '파트 회전': 'Part rotation',
    '무장 회전': 'Weapon rotation',
    '무장': 'Weapon',
    'X·Y·Z 회전값과 아래 선회·포신 앙각을 따로 조절할 수 있어요.': 'Adjust X/Y/Z rotation, traverse, and barrel elevation independently.',
    'X·Y·Z 값 또는 R 회전 도구로 이 파트를 자체 중심축 기준 회전해요.': 'Rotate this part around its own pivot with X/Y/Z values or the R tool.',
    'X·Y·Z 값을 직접 입력하거나 회전 도구를 사용할 수 있어요.': 'Enter X, Y, and Z values directly or use the rotation tool.',
    '전체 보기': 'Show all',
    '숨김 Del': 'Hide Del',
    '좌 선회 15°': 'Turn left 15°',
    '우 선회 15°': 'Turn right 15°',
    '원위치': 'Reset',
    '무장 축 제어': 'Weapon pivot controls',
    '파트 중심축': 'Part center pivot',
    '선회각': 'Traverse',
    '포신 앙각': 'Barrel elevation',
    '포신 형상을 분석하면 앙각을 조절할 수 있어요.': 'Analyze barrel geometry to adjust elevation.',
    '모델 불투명도': 'Model opacity',
    '장갑 두께': 'Armor thickness',
    '장갑 구역': 'Armor zone',
    '단면 분석': 'Section analysis',
    '전체': 'All',
    '끄기': 'Off',
    '가로': 'Width',
    '높이': 'Height',
    '세로': 'Length',
    '장갑 불투명도': 'Armor opacity',
    '표면을 가리키면 두께·구역·입사각을 계산해요.': 'Point at a surface to calculate thickness, zone, and impact angle.',
    '장갑 데이터가 없어요.': 'No armor data.',
    '왼쪽 드래그': 'Left drag',
    '오른쪽 드래그': 'Right drag',
    '휠': 'Wheel',
    '회전': 'Rotate',
    '이동': 'Pan',
    '확대·축소': 'Zoom',
    '선체': 'Hull',
    '상부구조': 'Superstructure',
    '주함포': 'Main guns',
    '부포': 'Secondary guns',
    '대공포': 'AA guns',
    '어뢰': 'Torpedoes',
    '미사일': 'Missiles',
    '레이더': 'Radar',
    '항공기': 'Aircraft',
    '장식': 'Decoration',
    '기타': 'Other',
    '미분류': 'Unclassified',
    '두께 미상': 'Unknown thickness',
    '검색 결과가 없어요.': 'No matching parts.',
    '모델을 열면 개별 파트가 여기에 표시돼요.': 'Individual parts appear here after a model is opened.',
    '숨기기': 'Hide',
    '보이기': 'Show',
    '장갑 분석': 'Armor analysis',
    '파트 이동 도구': 'Part move tool',
    '파트 회전 도구': 'Part rotate tool',
    '비교 모델을 닫았어요': 'Comparison model closed',
    '측정 모드 꺼짐': 'Measurement mode off',
    '비교 모델을 읽는 중이에요...': 'Loading comparison model...',
    '해수면 표시 켜짐': 'Waterline shown',
    '해수면 표시 꺼짐': 'Waterline hidden',
    '배경 표시': 'Background shown',
    '배경 숨김': 'Background hidden',
    '정확 두께 정보 없음': 'Exact thickness unavailable',
    '형상 중심 자동 원점': 'Automatic geometry-center pivot',
    '뷰어 편집은 미리보기 전용이며 원본 OBJ에는 저장되지 않아요.': 'Viewer edits are previews only and are not saved to the source OBJ.',
    '추출 엔티티 원점': 'Extracted entity pivot'
  }));

  const replacements = [
    [/함포, 선체, 코드 검색/g, 'Search guns, hull, or code'],
    [/Ctrl\+Z 취소 · Ctrl\+Y 복원 · G 이동 · R 회전 · B 배경 · Home 전체 보기 · Esc 선택 해제/g,
      'Ctrl+Z Undo · Ctrl+Y Redo · G Move · R Rotate · B Background · Home Frame all · Esc Clear selection'],
    [/파트 (\d+) · 삼각형 (\d+)/g, 'Parts $1 · triangles $2'],
    [/파트 (\d+)/g, 'Parts $1'],
    [/삼각형/g, 'triangles'],
    [/장갑 (\d+)/g, 'armor $1'],
    [/전체 (\d+)/g, 'All $1'],
    [/(\d+)개 파트 선택/g, '$1 parts selected'],
    [/표시 ([\d,]+) \/ 전체 ([\d,]+) 삼각형 · (\d+)개 구역/g, 'Visible $1 / $2 triangles · $3 zones'],
    [/파트 숨김/g, 'Hide part'],
    [/파트 표시/g, 'Show part'],
    [/파트 회전/g, 'Rotate part'],
    [/파트 이동/g, 'Move part'],
    [/실행 취소/g, 'Undo'],
    [/다시 실행/g, 'Redo'],
    [/모델 로딩 중/g, 'Loading model'],
    [/재질과 OBJ 파트를 준비하고 있어요\./g, 'Preparing materials and OBJ parts.'],
    [/리소스 ([\d,]+) \/ ([\d,]+) 불러오는 중/g, 'Loading resources $1 / $2'],
    [/장갑 구역과 두께 데이터를 준비하는 중/g, 'Preparing armor zones and thickness data'],
    [/모델·장갑 준비 완료/g, 'Model and armor ready'],
    [/모델 준비 완료 · 장갑 데이터 없음/g, 'Model ready · no armor data'],
    [/불러오기 실패:/g, 'Load failed:'],
    [/비교 모델 준비 완료/g, 'Comparison model ready'],
    [/두 점 거리/g, 'Two-point distance'],
    [/먼저 회전할 파트를 선택해 주세요/g, 'Select parts to rotate first'],
    [/개 파트를 자체 중심축으로/g, ' parts around their local pivots by'],
    [/조명 패널 위치를 초기화했어요/g, 'Lighting panel position reset'],
    [/독립 포신 엔티티가 없어 포탑 선회만 사용할 수 있어요\./g, 'No independent barrel entity is available; only turret traverse can be used.'],
    [/모델 단위/g, 'model units'],
    [/측정 완료/g, 'Measurement complete'],
    [/첫 번째 점 지정 · 두 번째 점을 골라 주세요/g, 'First point set · select the second point'],
    [/측정 모드 · 표면의 두 점을 차례로 선택해요/g, 'Measurement mode · select two surface points'],
    [/정확값을 보려면 이 버전에서 함선을 다시 추출해 주세요\./g, 'Re-extract this ship with this version for exact values.'],
    [/시선 기준 유효 두께 약/g, 'Approx. effective thickness along view'],
    [/입사각/g, 'impact angle'],
    [/전체 장갑층/g, 'All armor layers'],
    [/개 감지 · 자체 앙각축 사용/g, ' detected · using local elevation axes'],
    [/선회 기능은 그대로 사용할 수 있어요\./g, 'Traverse remains available.'],
    [/먼저 회전할 무장 파트를 선택해 주세요/g, 'Select weapon parts to rotate first'],
    [/먼저 되돌릴 파트를 선택해 주세요/g, 'Select parts to reset first'],
    [/개 파트를 추출 위치로 되돌렸어요/g, ' parts reset to extracted positions'],
    [/해수면 표시 전환/g, 'Toggle waterline'],
    [/배경 표시 전환/g, 'Toggle background'],
    [/카메라와 표시 도구/g, 'Camera and display tools'],
    [/3D 함선 뷰포트/g, '3D ship viewport'],
    [/파트 분류/g, 'Part categories'],
    [/함선 오브젝트/g, 'Ship objects']
  ];

  function translate(text) {
    if (!text) return text;
    if (language === 'ko') return formalizeKorean(text);
    const trimmed = text.trim();
    if (exact.has(trimmed)) {
      const prefix = text.slice(0, text.indexOf(trimmed));
      const suffix = text.slice(text.indexOf(trimmed) + trimmed.length);
      return prefix + exact.get(trimmed) + suffix;
    }
    let result = text;
    for (const [pattern, replacement] of replacements) result = result.replace(pattern, replacement);
    return result;
  }

  function translateElement(element) {
    if (!(element instanceof Element)) return;
    for (const attribute of ['title', 'aria-label', 'placeholder']) {
      if (!element.hasAttribute(attribute)) continue;
      const before = element.getAttribute(attribute);
      const after = translate(before);
      if (after !== before) element.setAttribute(attribute, after);
    }
  }

  function translateTree(root) {
    if (root.nodeType === Node.TEXT_NODE) {
      const before = root.nodeValue;
      const after = translate(before);
      if (after !== before) root.nodeValue = after;
      return;
    }
    if (!(root instanceof Element) && root !== document) return;
    if (root instanceof Element) translateElement(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (node.nodeType === Node.TEXT_NODE) {
        const before = node.nodeValue;
        const after = translate(before);
        if (after !== before) node.nodeValue = after;
      } else {
        translateElement(node);
      }
    }
  }

  translateTree(document);
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'characterData') translateTree(mutation.target);
      for (const node of mutation.addedNodes) translateTree(node);
    }
  });
  observer.observe(document.body, { childList: true, characterData: true, subtree: true });
  window.WoWSToolboxI18n = { language, translate };
})();
