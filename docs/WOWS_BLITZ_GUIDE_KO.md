# WoWS Blitz 모델 추출 준비 가이드

이 문서는 WoWS Toolbox 5.0.61 이상에서 **사용자가 자신의 Android 환경에
정상적으로 설치하고 내려받은 World of Warships Blitz 데이터**를 준비해 모델을
추출하는 과정을 설명해요.

WoWS Toolbox는 에뮬레이터에 접속하거나 게임을 수정하지 않아요. 사용자가 PC로
복사해 둔 폴더만 읽기 전용으로 사용해요. APK, OBB, AssetBundle, 추출한 모델과
텍스처는 게임 자산이므로 다른 사람에게 배포하면 안 돼요.

## 1. 준비물과 지원 범위

필요한 항목은 다음과 같아요.

- WoWS Toolbox 5.0.61 이상
- WoWS Blitz를 실행할 수 있는 Android 기기 또는 에뮬레이터
- 앱 전용 폴더를 읽을 수 있는 root 권한과 ADB 연결
- 게임 업데이트와 항구 진입까지 끝난 본인 계정의 로컬 데이터
- 번들과 OBB를 보관할 충분한 PC 공간

Google Play Games 베타의 Windows 설치 폴더 자체는 WoWS Toolbox의 Blitz 경로로
사용할 수 없어요. Android 앱 전용 데이터가 일반 폴더로 노출되지 않기 때문이에요.
현재 가장 단순한 준비 방식은 root와 ADB를 제공하는 Android 에뮬레이터에서
게임 데이터를 PC로 복사하는 방법이에요. 에뮬레이터 종류와 ADB 포트는 제품과
버전에 따라 달라질 수 있어요.

WoWS Toolbox가 사용하는 Android 패키지 ID는 다음과 같아요.

```text
net.wargaming.wows.blitz
```

## 2. 먼저 게임 데이터를 충분히 내려받기

1. 공식 스토어를 통해 WoWS Blitz를 설치해요.
2. 게임을 최신 버전으로 업데이트해요.
3. 로그인하고 항구 화면까지 들어가요.
4. 추가 리소스 처리가 끝날 때까지 기다려요.
5. 가능하면 테크트리와 함선 상세 화면을 둘러본 뒤 게임을 정상 종료해요.

Blitz는 서버의 전체 함선 자료를 하나의 고정 다운로드로 제공하지 않아요.
`files/bundle`에는 **현재 게임이 로컬에 내려받은 번들만** 있어요. 따라서
WoWS Toolbox의 목록도 DesignData에 기록된 전체 항목과 실제 body 번들이 있는
추출 가능 항목을 구분해요. 테스트한 한 빌드에서는 964개 카탈로그 레코드 중
756개가 로컬 body 리소스를 가지고 있었지만, 이 수치는 게임 버전과 지역에 따라
달라질 수 있어요.

게임 업데이트 중이거나 실행 중인 상태에서 복사하면 서로 다른 시점의 파일이
섞일 수 있어요. 항구 로딩과 다운로드가 끝난 뒤 게임을 닫고 복사하는 게 좋아요.

## 3. ADB 연결과 root 권한 확인

에뮬레이터 설정에서 root 권한과 ADB 연결을 켜요. ADB 실행 파일 경로를 확인한 뒤
PowerShell 7에서 다음과 같이 지정해요.

```powershell
$adb = 'C:\path\to\adb.exe'
& $adb devices
```

목록이 비어 있으면 에뮬레이터 관리 화면에 표시된 ADB 주소와 포트를 사용해요.
포트는 환경마다 다르므로 아래의 `PORT`를 실제 값으로 바꿔야 해요.

```powershell
& $adb connect '127.0.0.1:PORT'
& $adb devices
```

기기가 둘 이상 표시되면 이후 명령에 `-s SERIAL`을 넣어 대상 기기를 지정해요.

```powershell
& $adb -s '127.0.0.1:PORT' shell id
```

root 상태는 다음 순서로 확인할 수 있어요.

```powershell
& $adb shell id
& $adb root
& $adb shell id
& $adb shell su -c id
```

`uid=0(root)`가 나오면 앱 전용 폴더를 읽을 수 있어요. `adb root`가 지원되지 않아도
`su -c id`가 root를 반환하면 아래의 임시 공유 폴더 방식으로 복사할 수 있어요.
`su: not found` 또는 `permission denied`가 나오면 에뮬레이터의 root 설정과 사용 중인
ADB 실행 파일이 그 에뮬레이터용인지 다시 확인해야 해요.

## 4. 원본 데이터 위치 확인

다운로드 함선 번들의 기본 위치는 다음과 같아요.

```text
/data/data/net.wargaming.wows.blitz/files/bundle/
```

다음 명령으로 함선 body 폴더와 파일 수를 확인해요.

```powershell
& $adb shell su -c "ls -ld /data/data/net.wargaming.wows.blitz/files/bundle/prefab/ship/body"
& $adb shell su -c "find /data/data/net.wargaming.wows.blitz/files/bundle/prefab/ship/body -type f | wc -l"
```

기본 OBB는 보통 다음 위치 중 하나에 있어요.

```text
/sdcard/Android/obb/net.wargaming.wows.blitz/
/storage/emulated/0/Android/obb/net.wargaming.wows.blitz/
```

```powershell
& $adb shell "ls -l /sdcard/Android/obb/net.wargaming.wows.blitz/main.*.obb"
```

`main.<버전>.net.wargaming.wows.blitz.obb` 파일이 보여야 해요. OBB에는 무장,
공통 장식, 애니메이션, 셰이더처럼 함선 body가 참조하는 공통 자원이 들어 있어서
body 번들만 복사하면 완전한 함선을 조립할 수 없어요.

## 5. PC에 준비 데이터 폴더 만들기

아래 예시는 문서 폴더에 `WoWS-Blitz-Data`를 만들어요. 다른 위치를 사용해도 돼요.

```powershell
$blitzData = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'WoWS-Blitz-Data'
New-Item -ItemType Directory -Path $blitzData -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $blitzData 'downloads') -Force | Out-Null
```

### 방법 A: root ADB가 앱 폴더를 직접 읽는 경우

```powershell
& $adb pull '/data/data/net.wargaming.wows.blitz/files/bundle' $blitzData
Rename-Item -LiteralPath (Join-Path $blitzData 'bundle') -NewName 'full_bundle'

$obbRemote = ((& $adb shell "ls -1 /sdcard/Android/obb/net.wargaming.wows.blitz/main.*.obb") |
    Select-Object -First 1).Trim()
& $adb pull $obbRemote (Join-Path $blitzData 'downloads')
```

이미 `full_bundle`이 있으면 새 게임 버전과 섞지 말고 별도의 버전 폴더를 만드는 게
안전해요.

### 방법 B: `adb pull /data/data/...`가 권한 오류를 내는 경우

`su`로 앱 데이터를 Android 공유 저장소의 전용 임시 폴더에 복사한 뒤 PC로
가져올 수 있어요. 이 방법은 에뮬레이터에 번들 크기만큼의 임시 여유 공간이 더
필요해요.

```powershell
$stage = '/sdcard/Download/WoWSBlitzExport'
& $adb shell su -c "mkdir -p $stage"
& $adb shell su -c "cp -R /data/data/net.wargaming.wows.blitz/files/bundle $stage/"
& $adb pull "$stage/bundle" $blitzData
Rename-Item -LiteralPath (Join-Path $blitzData 'bundle') -NewName 'full_bundle'

$obbRemote = ((& $adb shell "ls -1 /sdcard/Android/obb/net.wargaming.wows.blitz/main.*.obb") |
    Select-Object -First 1).Trim()
& $adb pull $obbRemote (Join-Path $blitzData 'downloads')
```

PC 복사와 폴더 검사가 끝난 뒤에만 임시 폴더를 지워요. 아래 명령은 게임 원본이
아니라 정확히 `WoWSBlitzExport` 임시 폴더만 삭제해요.

```powershell
& $adb shell su -c "rm -rf /sdcard/Download/WoWSBlitzExport"
```

### 선택 사항: DesignData

`DesignData`가 있으면 프로그램 언어에 맞는 함선 이름, 티어와 카탈로그 정보가 더
정확해져요. 모델 추출 자체에는 필수가 아니며, 없으면 내부 리소스 이름으로 목록을
구성해요.

먼저 현재 설치본에서 위치를 찾아요.

```powershell
& $adb shell su -c "find /data/data/net.wargaming.wows.blitz -type f -name DesignData 2>/dev/null"
```

경로가 출력되면 그 파일을 준비 폴더 최상단의 `DesignData`로 복사해요.

```powershell
$designRemote = ((& $adb shell su -c "find /data/data/net.wargaming.wows.blitz -type f -name DesignData 2>/dev/null") |
    Select-Object -First 1).Trim()
if ($designRemote) {
    & $adb pull $designRemote (Join-Path $blitzData 'DesignData')
}
```

아무 경로도 나오지 않으면 건너뛰면 돼요. 다른 버전에서 가져온 DesignData를 섞으면
이름과 실제 body 리소스가 어긋날 수 있어요.

## 6. 완성된 폴더 구조 검사

정상 구조는 다음과 같아요.

```text
WoWS-Blitz-Data/
├─ full_bundle/
│  ├─ BundlePackInfo.bytes
│  ├─ prefab/ship/body/*.ab
│  ├─ artist/
│  ├─ shippaint/
│  └─ 국가별 번들 폴더들
├─ downloads/
│  └─ main.<버전>.net.wargaming.wows.blitz.obb
└─ DesignData                 선택 사항
```

PowerShell에서 다음 검사를 실행할 수 있어요.

```powershell
Test-Path (Join-Path $blitzData 'full_bundle\prefab\ship\body')
Get-ChildItem (Join-Path $blitzData 'full_bundle\prefab\ship\body') -Filter '*.ab' |
    Measure-Object
Get-ChildItem (Join-Path $blitzData 'downloads') -Filter 'main.*.obb' |
    Select-Object Name, Length
```

첫 번째 결과가 `True`, body 파일 수가 1개 이상, OBB 크기가 0보다 커야 해요.

## 7. WoWS Toolbox에서 추출하기

1. WoWS Toolbox의 **설정 > 게임 설치 폴더**로 이동해요.
2. **World of Warships Blitz**에 `WoWS-Blitz-Data` 폴더를 지정하고 저장해요.
3. **함선 추출**에서 게임 소스를 **World of Warships Blitz**로 바꿔요.
4. **목록 새로고침**을 눌러 현재 로컬 번들 카탈로그를 만들어요.
5. 함선을 추가하고 존재하는 경우 `기본 도색`, `도색 01`, `도색 02` 중 하나를 골라요.
6. 출력 폴더를 확인하고 **추출 준비 검사**를 실행해요.
7. 검사를 통과하면 **대기열 모델 추출**을 눌러요.

결과 폴더에는 편집 가능한 OBJ, MTL, `textures` 폴더와 추출 보고서가 생겨요.
첫 추출은 OBB의 CAB 위치 색인을 만들기 때문에 이후 추출보다 오래 걸릴 수 있어요.
찾은 의존성은 `%LOCALAPPDATA%\WoWSToolbox\Cache\Blitz` 아래에 캐시돼요.

## 8. 게임 업데이트 뒤 자료 갱신

게임 빌드가 바뀌면 새 버전 전용 준비 폴더를 만드는 게 가장 안전해요.

1. 게임 업데이트와 항구 로딩을 끝내요.
2. 게임을 종료해요.
3. 새 `files/bundle`과 새 `main.*.obb`를 함께 복사해요.
4. 필요하면 같은 빌드의 DesignData도 복사해요.
5. WoWS Toolbox에서 새 폴더를 선택하고 목록을 새로고침해요.

예전 OBB와 새 body 번들을 섞으면 CAB 의존성 오류, 누락된 무장 또는 흰색 재질이
생길 수 있어요. 기존 폴더 위에 일부만 덮어쓰기보다 버전별 폴더를 권해요.

## 9. 문제 해결

### `경로 없음` 또는 Blitz 경로를 인식하지 못해요

선택한 폴더 바로 아래에
`full_bundle\prefab\ship\body` 또는 `bundle\prefab\ship\body`가 있는지 확인해요.
`full_bundle\bundle\prefab`처럼 한 단계 더 들어갔다면 안쪽 폴더를 선택하거나
구조를 위 예시처럼 정리해요.

### `main.*.obb 기본 OBB가 없어요`

준비 폴더 최상단이나 `downloads` 폴더에 OBB를 넣어요. 파일 이름은 바꾸지 않는 게
좋아요. body 번들과 같은 게임 빌드에서 복사한 파일이어야 해요.

### 목록이 적거나 원하는 함선이 없어요

WoWS Toolbox는 서버 목록이 아니라 로컬 `files/bundle`에 실제로 있는 body 번들을
추출해요. 게임에서 리소스 로딩을 끝내고 해당 화면을 방문한 뒤 번들 폴더를 다시
복사해요. 그래도 body 파일이 없으면 현재 설치본에는 그 모델이 내려오지 않은
상태예요.

### 함선 이름이 내부 코드로 보여요

DesignData가 없거나 현재 번들과 버전이 맞지 않을 수 있어요. 추출에는 지장이 없지만
번역 이름과 티어 정보가 제한돼요.

### 일부 주포·부포·대공포가 흰색이에요

WoWS Toolbox 5.0.61 이상을 설치하고 해당 함선을 **다시 추출**해요. 이전 출력 폴더는
자동 수정되지 않아요. 5.0.61은 무장 재질이 다시 참조하는 2단계 텍스처 CAB까지
재귀적으로 찾아요. 최신 버전에서도 계속 흰색이면 OBB와 bundle의 버전 불일치나
불완전한 복사를 먼저 의심해야 해요.

### CAB 또는 외부 참조를 찾지 못했다는 오류가 나요

- OBB와 `full_bundle`을 같은 게임 빌드에서 다시 복사해요.
- 복사 도중 게임이 업데이트되지 않았는지 확인해요.
- **설정 > 캐시 > 캐시 비우기** 후 한 척을 다시 추출해요.
- 계속 실패하면 진단 ZIP만 첨부하고 게임 자산은 첨부하지 말아 주세요.

### ADB에 기기가 여러 개 보여요

모든 명령에 `-s SERIAL`을 넣어요.

```powershell
& $adb -s 'SERIAL' shell su -c id
```

## 10. 안전과 권리

- 원본 Android 폴더는 읽기와 복사만 하고 수정하지 않는 게 좋아요.
- root 권한은 데이터 준비용 에뮬레이터 안에서만 사용해요.
- 계정 토큰, 로그인 자료, 기기 식별 정보가 든 파일은 공유하지 마세요.
- APK, OBB, AssetBundle, OBJ, MTL과 텍스처를 GitHub 이슈나 릴리스에 올리지 마세요.
- 추출물은 게임 EULA, 플랫폼 약관과 지역 법률이 허용하는 범위에서만 사용해요.

문제가 생기면 WoWS Toolbox의 **설정 > 진단 ZIP**을 사용해요. 진단 ZIP은 개인
경로와 모델 자산을 제외하도록 만들어져 있어요.
