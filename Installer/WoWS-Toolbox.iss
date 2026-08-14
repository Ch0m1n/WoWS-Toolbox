#define MyAppName "WoWS Toolbox"
#define MyAppVersion "5.0.42"
#define MyAppPublisher "WoWS Toolbox contributors"
#define ReleaseRoot "..\..\..\outputs\WoWS-Toolbox-v5.0.42"

[Setup]
AppId={{88AA1660-CC89-4EDA-9895-BC051E8CAD26}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppCopyright=Copyright (c) 2026 WoWS Toolbox contributors
AppComments=Unofficial WoWS-family model extraction and inspection toolbox
VersionInfoVersion=5.0.42.0
VersionInfoProductName={#MyAppName}
VersionInfoDescription={#MyAppName} Installer
VersionInfoCompany={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\WoWS Toolbox
DefaultGroupName=WoWS Toolbox
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern
WizardSizePercent=110
SetupIconFile=assets\WoWS-Toolbox.ico
UninstallDisplayIcon={app}\Branding\WoWS-Toolbox.ico
OutputDir=..\..\..\outputs
OutputBaseFilename=WoWS-Toolbox-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
SetupLogging=yes
ShowLanguageDialog=yes
LanguageDetectionMethod=none
UsePreviousLanguage=yes
UsePreviousAppDir=yes
UsePreviousGroup=no
DirExistsWarning=no
DisableWelcomePage=no
CloseApplications=no
RestartApplications=no
RestartIfNeededByRun=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"; InfoBeforeFile: "LEGAL_EN.txt"
Name: "ko"; MessagesFile: "compiler:Languages\Korean.isl"; InfoBeforeFile: "LEGAL_KO.txt"

[CustomMessages]
en.ViewReadme=Open the Korean/English quick-start README
ko.ViewReadme=한국어/영어 빠른 사용법 README.txt 열기
en.MainShortcut=WoWS Toolbox model toolbox
ko.MainShortcut=WoWS Toolbox 함선 모델 도구
en.ShortcutGroup=Choose which shortcuts to create:
ko.ShortcutGroup=생성할 바로가기를 선택합니다:
en.CreateDesktopShortcut=Create a desktop shortcut
ko.CreateDesktopShortcut=바탕화면 바로가기 만들기
en.MissingRuntimeTitle=Required runtime not found
en.MissingRuntimeBody=The following required command was not found in PATH:%n%n%1%n%nWoWS Toolbox may not start until it is installed. Continue installation anyway?
ko.MissingRuntimeTitle=필수 실행 환경을 찾지 못했습니다
ko.MissingRuntimeBody=PATH에서 다음 필수 명령을 찾지 못했습니다:%n%n%1%n%n설치하기 전에는 WoWS Toolbox를 실행할 수 없습니다. 그래도 설치를 계속하시겠습니까?
en.MissingPowerShell=Windows PowerShell 5.1 or PowerShell 7 was not found. WoWS Toolbox cannot start on this Windows installation.
ko.MissingPowerShell=Windows PowerShell 5.1 또는 PowerShell 7을 찾지 못했습니다. 이 Windows 설치에서는 WoWS Toolbox를 실행할 수 없습니다.
en.InstallingWebView2=Installing the Microsoft Edge WebView2 Runtime for the 3D viewer...
ko.InstallingWebView2=3D 뷰어에 필요한 Microsoft Edge WebView2 Runtime을 설치하는 중입니다...
en.WebView2Note=Microsoft Edge WebView2 Runtime is installed only when it is missing.
ko.WebView2Note=Microsoft Edge WebView2 Runtime이 없을 때만 설치합니다.
en.WebView2InstallFailed=WebView2 Runtime could not be verified after setup. Model extraction still works, but the integrated 3D viewer may be unavailable until WebView2 is installed.
ko.WebView2InstallFailed=설치 후 WebView2 Runtime을 확인하지 못했습니다. 모델 추출은 가능하지만 WebView2를 설치하기 전에는 내장 3D 뷰어를 사용하지 못할 수 있습니다.
en.UpgradeWelcome=WoWS Toolbox %1 is already installed.%n%nSetup will update it to %2 in the same folder. Your settings, cache, and exported models will be kept.
ko.UpgradeWelcome=WoWS Toolbox %1이(가) 이미 설치되어 있습니다.%n%n같은 폴더에서 %2 버전으로 업데이트하며 설정, 캐시와 추출 모델은 그대로 보존합니다.
en.CloseAppForUpdate=Close WoWS Toolbox before installing or updating, then click Next again. This prevents the embedded WebView2 process from being terminated forcibly.
ko.CloseAppForUpdate=설치 또는 업데이트 전에 WoWS Toolbox를 정상적으로 닫고 다시 다음을 눌러 주세요. 내장 WebView2 프로세스가 강제로 종료되는 오류를 막기 위한 절차입니다.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopShortcut}"; GroupDescription: "{cm:ShortcutGroup}"; Flags: unchecked

[Files]
Source: "{#ReleaseRoot}\*"; DestDir: "{app}"; Excludes: "app-language.txt"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "app-language-ko.txt"; DestDir: "{app}"; DestName: "app-language.txt"; Languages: "ko"; Flags: ignoreversion onlyifdoesntexist
Source: "app-language-en.txt"; DestDir: "{app}"; DestName: "app-language.txt"; Languages: "en"; Flags: ignoreversion onlyifdoesntexist
Source: "dependencies\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2

[Dirs]
Name: "{app}"; Attribs: notcontentindexed
Name: "{app}\output"; Attribs: notcontentindexed

[Icons]
Name: "{group}\WoWS Toolbox"; Filename: "{app}\WoWS Toolbox.exe"; WorkingDir: "{app}"; Comment: "{cm:MainShortcut}"
Name: "{group}\README"; Filename: "{app}\README.txt"; WorkingDir: "{app}"
Name: "{group}\Legal Notice"; Filename: "{app}\LEGAL_NOTICE.txt"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,WoWS Toolbox}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\WoWS Toolbox"; Filename: "{app}\WoWS Toolbox.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[InstallDelete]
; Rebuild only WoWS Toolbox-owned shortcuts so the new task choices win.
Type: filesandordirs; Name: "{userprograms}\WoWS Toolbox"
Type: files; Name: "{userdesktop}\WoWS Toolbox.lnk"
; Remove obsolete 5.0.22 product-owned names during an in-place upgrade.
Type: filesandordirs; Name: "{userprograms}\NavalForge"
Type: files; Name: "{userdesktop}\NavalForge.lnk"
Type: files; Name: "{app}\NavalForge.cmd"
Type: files; Name: "{app}\Branding\NavalForge.ico"
; Remove development and migration files shipped by older installers.
Type: filesandordirs; Name: "{app}\Launcher"
Type: files; Name: "{app}\WoWS-Toolbox-GUI.cmd"
Type: files; Name: "{app}\WoWS-Legends-Toolbox-GUI.cmd"
Type: files; Name: "{app}\Run-SelfTests*.ps1"
Type: files; Name: "{app}\Upgrade-*.ps1"
Type: files; Name: "{app}\README_KO*.md"
Type: files; Name: "{app}\test*.py"
Type: files; Name: "{app}\Backend\test_*.py"
Type: files; Name: "{app}\BlenderExtractor\test_*.py"
; Remove the retired experimental weapon/part rotation module during upgrades.
Type: files; Name: "{app}\Viewer\web\weapon-kinematics.js"
[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "{cm:InstallingWebView2}"; Flags: waituntilterminated; Check: NeedsWebView2; AfterInstall: VerifyWebView2Install
Filename: "{app}\README.txt"; Description: "{cm:ViewReadme}"; Flags: postinstall shellexec skipifsilent skipifdoesntexist
Filename: "{app}\WoWS Toolbox.exe"; Description: "{cm:LaunchProgram,WoWS Toolbox}"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent unchecked

[Code]
var
  NeedWebView2Install: Boolean;
  ExistingVersion: String;

function InstalledVersion(var Version: String): Boolean;
var
  UninstallKey: String;
begin
  UninstallKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{88AA1660-CC89-4EDA-9895-BC051E8CAD26}_is1';
  Result :=
    RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', Version) or
    RegQueryStringValue(HKLM32, UninstallKey, 'DisplayVersion', Version) or
    RegQueryStringValue(HKLM64, UninstallKey, 'DisplayVersion', Version);
end;

function PowerShellAvailable: Boolean;
begin
  Result :=
    FileExists(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe')) or
    FileExists(ExpandConstant('{pf}\PowerShell\7\pwsh.exe')) or
    FileExists(ExpandConstant('{localappdata}\Programs\PowerShell\7\pwsh.exe'));
end;

function WebView2RuntimeInstalled: Boolean;
var
  Version: String;
  ClientKey: String;
begin
  ClientKey := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result :=
    RegQueryStringValue(HKLM32, ClientKey, 'pv', Version) or
    RegQueryStringValue(HKLM64, ClientKey, 'pv', Version) or
    RegQueryStringValue(HKCU, ClientKey, 'pv', Version);
  Result := Result and (Version <> '') and (Version <> '0.0.0.0');
end;

function NeedsWebView2: Boolean;
begin
  Result := NeedWebView2Install;
end;

procedure VerifyWebView2Install;
begin
  if not WebView2RuntimeInstalled then
    MsgBox(ExpandConstant('{cm:WebView2InstallFailed}'), mbError, MB_OK);
end;

function InitializeSetup: Boolean;
begin
  if not PowerShellAvailable then
  begin
    MsgBox(ExpandConstant('{cm:MissingPowerShell}'), mbError, MB_OK);
    Result := False;
    Exit;
  end;
  ExistingVersion := '';
  InstalledVersion(ExistingVersion);
  NeedWebView2Install := not WebView2RuntimeInstalled;
  Result := True;
end;

procedure InitializeWizard;
begin
  if ExistingVersion <> '' then
    WizardForm.WelcomeLabel2.Caption := FmtMessage(CustomMessage('UpgradeWelcome'), [ExistingVersion, '{#MyAppVersion}']);

end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if FindWindowByWindowName('{#MyAppName}') <> 0 then
    Result := CustomMessage('CloseAppForUpdate');
end;
