; Instalator Windows (Inno Setup 6).
;   iscc /DAppVersion=2.0.0 packaging\windows\installer.iss
; Instalacja „na użytkownika” — bez uprawnień administratora, dzięki czemu
; program potrafi sam się aktualizować w trybie cichym.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "MyDict"
#define AppExe "MyDict.exe"

[Setup]
AppId={{6F0B7C1E-5D2A-4E0B-9B55-BA9D0D1C7A20}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=BAPO
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename={#AppName}-{#AppVersion}-Windows-Setup
SetupIconFile=..\..\zasoby\ikona.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ShowLanguageDialog=no
LicenseFile=..\..\LICENSE.txt
AppPublisherURL=https://github.com/BartekPopinski82/MyDict
AppSupportURL=https://github.com/BartekPopinski82/MyDict/issues
#ifdef Podpis
; Podpis kodu: polecenie „mydict” podaje build.py (/Smydict=…) ze zmiennej MYDICT_SIGN_CMD.
SignTool=mydict
SignedUninstaller=yes
#endif

[Languages]
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"

[Tasks]
Name: "desktopicon"; Description: "Utwórz skrót na pulpicie"; GroupDescription: "Skróty:"
Name: "autostart"; Description: "Uruchamiaj razem z Windows"; GroupDescription: "Uruchamianie:"; Flags: unchecked

[Files]
Source: "..\..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\THIRD-PARTY-NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Aktualizacja: usuń biblioteki poprzedniej wersji, żeby nie zostały osierocone pliki.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
    ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
; Składniki zależne od sprzętu pobieramy w trakcie instalacji, żeby program był od razu
; w pełni gotowy: biblioteki karty NVIDIA (tylko gdy jest taka karta) i model mowy.
; Program sam rozpoznaje sprzęt i pokazuje postęp; gdy nic nie brakuje, kończy od razu.
Filename: "{app}\{#AppExe}"; Parameters: "--przygotuj"; \
    StatusMsg: "Pobieranie składników: model rozpoznawania mowy i obsługa karty graficznej…"; \
    Flags: waituntilterminated
; Zwykła instalacja: pole wyboru „Uruchom” na ostatniej stronie.
Filename: "{app}\{#AppExe}"; Description: "Uruchom {#AppName}"; Flags: nowait postinstall skipifsilent
; Aktualizacja uruchomiona przez program (/SILENT /AKTUALIZACJA): wróć do pracy bez pytania.
Filename: "{app}\{#AppExe}"; Parameters: "--po-aktualizacji"; Flags: nowait; Check: IsSelfUpdate

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Code]
function IsSelfUpdate: Boolean;
var
  Index: Integer;
begin
  Result := False;
  for Index := 1 to ParamCount do
    if CompareText(ParamStr(Index), '/AKTUALIZACJA') = 0 then
      Result := True;
end;
