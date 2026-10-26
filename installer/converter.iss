; Installer Windows di Converter (Inno Setup 6).
;
; Impacchetta la cartella onedir di PyInstaller (dist\Converter) in un setup.exe:
; collegamento nel menu Start, icona sul desktop facoltativa, disinstallazione da
; "App installate". Di default installa SOLO per l'utente corrente (niente UAC);
; il dialog iniziale permette di scegliere "tutti gli utenti" (richiede admin).
;
; Build (dalla radice del repo, dopo pyinstaller):
;   iscc /DAppVersion=0.1.3 installer\converter.iss
; Output: dist\Converter-v<versione>-setup.exe
;
; Config, preset e cronologia vivono in %APPDATA%\Converter: la disinstallazione
; NON li tocca (sono dati dell'utente, non dell'app).

#ifndef AppVersion
  #error Passa la versione: iscc /DAppVersion=x.y.z installer\converter.iss
#endif

#define AppName "Converter"
#define AppExe  "Converter.exe"

[Setup]
; AppId fisso: è ciò che fa riconoscere un aggiornamento come la STESSA app
; (installa sopra la versione precedente invece di duplicarla). Non cambiarlo mai.
AppId={{6E1B8C2A-4F7D-4B39-9A51-3C0D2E8F7A14}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Tommaso Brighina
AppPublisherURL=https://github.com/brighinatommaso-eng/Converter
AppSupportURL=https://github.com/brighinatommaso-eng/Converter/issues
AppUpdatesURL=https://github.com/brighinatommaso-eng/Converter/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\LICENSE
SetupIconFile=..\converter\assets\converter.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
OutputDir=..\dist
OutputBaseFilename={#AppName}-v{#AppVersion}-setup
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Un Converter.exe aperto blocca i file in _internal: chiede di chiuderlo.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Aggiornando sopra una versione vecchia, i moduli tolti dal bundle non devono
; restare in _internal (il selftest li considera un errore di distribuzione).
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Converter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
