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
; AppUserModelID = APP_ID z dyktowanie_app.py: pasek zadań łączy okno programu ze skrótem.
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "pl.bapo.mydict"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon; AppUserModelID: "pl.bapo.mydict"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
    ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: autostart
; Ustawienia programu (QSettings: BAPO\MyDict, dawniej BAPO\BAPO-dyktowanie) — usuwane przy deinstalacji.
Root: HKCU; Subkey: "Software\BAPO"; Flags: uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\BAPO\{#AppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\BAPO\BAPO-dyktowanie"; Flags: uninsdeletekey dontcreatekey

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
; Dane programu: pobrane biblioteki karty graficznej, whisper.cpp i jego modele, dzienniki, pomiary.
Type: filesandordirs; Name: "{localappdata}\{#AppName}"

[Code]
// Modele mowy pobrane przez program do pamięci podręcznej Hugging Face (faster-whisper).
// Nazwy repozytoriów: dyktowanie_asr.MODELS → faster_whisper.utils._MODELS.
function HuggingFaceHubCache: String;
begin
  Result := GetEnv('HF_HUB_CACHE');
  if Result = '' then
  begin
    Result := GetEnv('HF_HOME');
    if Result <> '' then
      Result := AddBackslash(Result) + 'hub'
    else
      Result := AddBackslash(GetEnv('USERPROFILE')) + '.cache\huggingface\hub';
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Cache: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Cache := AddBackslash(HuggingFaceHubCache);
    DelTree(Cache + 'models--Systran--faster-whisper-small', True, True, True);
    DelTree(Cache + 'models--Systran--faster-whisper-medium', True, True, True);
    DelTree(Cache + 'models--Systran--faster-whisper-large-v3', True, True, True);
    DelTree(Cache + 'models--mobiuslabsgmbh--faster-whisper-large-v3-turbo', True, True, True);
  end;
end;

function IsSelfUpdate: Boolean;
var
  Index: Integer;
begin
  Result := False;
  for Index := 1 to ParamCount do
    if CompareText(ParamStr(Index), '/AKTUALIZACJA') = 0 then
      Result := True;
end;
