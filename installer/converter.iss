; Installer Windows di Converter (Inno Setup 6).
;
; Impacchetta la cartella onedir di PyInstaller (dist\Converter) in un setup.exe:
; collegamento nel menu Start, icona sul desktop (scelta esplicita nel wizard),
; disinstallazione da "App installate". Di default installa SOLO per l'utente
; corrente (niente UAC); il dialog iniziale permette di scegliere "tutti gli
; utenti" (richiede admin).
;
; Build (dalla radice del repo, dopo pyinstaller):
;   iscc /DAppVersion=0.1.6 installer\converter.iss
; Output: dist\Converter-v<versione>-setup.exe
;
; Config, preset e cronologia vivono in %APPDATA%\Converter: la disinstallazione
; NON li tocca (sono dati dell'utente, non dell'app).

#ifndef AppVersion
  #error Passa la versione: iscc /DAppVersion=x.y.z installer\converter.iss
#endif

#define AppName "Converter"
#define AppExe  "Converter.exe"
; Identita' dell'app per Windows (taskbar, menu Start, notifiche). DEVE
; combaciare con converter/gui/app.py (_set_windows_app_id): se i due divergono
; Windows non sa piu' a che icona associare l'app e mostra quella generica.
#define AppUserModelID "TommasoBrighina.Converter"

[Setup]
; AppId fisso: e' cio' che fa riconoscere un aggiornamento come la STESSA app
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
VersionInfoCompany=Tommaso Brighina
VersionInfoDescription=Converter - offline file converter
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\LICENSE
InfoBeforeFile=windows\welcome.txt
InfoAfterFile=windows\finish.txt
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
; Solo Windows 10 e successivi: il build e' x64 e l'app usa le API Qt 6 moderne.
MinVersion=10.0
; Un Converter.exe aperto blocca i file in _internal: chiede di chiuderlo e, se
; l'utente lo vuole, lo riapre dopo l'aggiornamento.
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
; La scelta e' esplicita nella pagina "Iconi aggiuntive" e attiva di default:
; dopo l'installazione l'app si trova sul desktop (dove la cerca l'utente),
; e la si toglie con un clic se non la vuole. (In Inno 6 un task senza
; "Flags: unchecked" e' gia' selezionato: non esiste il flag "checked".)
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Aggiornando sopra una versione vecchia, i moduli tolti dal bundle non devono
; restare in _internal (il selftest li considera un errore di distribuzione).
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Converter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
; Windows deve poter risolvere "quale icona" mostrare per l'AppUserModelID:
; senza questa registrazione la taskbar e il menu Start cadono sull'icona
; generica di Windows finche' la cache delle icone non si svuota (in pratica:
; serve un riavvio). E' il meccanismo documentato da Microsoft.
; Installazione per l'utente corrente -> HKCU; per tutti gli utenti -> HKLM.
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\{#AppUserModelID}"; ValueType: string; ValueName: "IconUri"; ValueData: "{app}\_internal\assets\converter.ico"; Flags: uninsdeletekey; Check: not IsAdminInstallMode
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\{#AppUserModelID}"; ValueType: string; ValueName: "DisplayName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Check: not IsAdminInstallMode
Root: HKLM; Subkey: "Software\Classes\AppUserModelId\{#AppUserModelID}"; ValueType: string; ValueName: "IconUri"; ValueData: "{app}\_internal\assets\converter.ico"; Flags: uninsdeletekey; Check: IsAdminInstallMode
Root: HKLM; Subkey: "Software\Classes\AppUserModelId\{#AppUserModelID}"; ValueType: string; ValueName: "DisplayName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Check: IsAdminInstallMode

[Icons]
; Menu Start: SEMPRE. E' il modo in cui l'app si trova dopo l'installazione
; (in Windows 11 sta in "Tutte le app", non nella schermata iniziale: un
; collegamento sul desktop, creato sopra, la rende subito visibile).
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"; IconIndex: 0; WorkingDir: "{app}"; Comment: "Convert files offline, honest about quality"
; Scorciatoia sul desktop: solo se l'utente lascia la spunta nella pagina
; "Iconi aggiuntive" (attiva di default).
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"; IconIndex: 0; WorkingDir: "{app}"; Tasks: desktopicon; Comment: "Convert files offline, honest about quality"

[Run]
; La cache delle icone di Explorer tiene l'icona del vecchio eseguibile: senza
; questo refresh, dopo un aggiornamento la barra delle applicazioni e il menu
; Start continuano a mostrare l'icona generica finche' Windows non viene
; riavviato. Va PRIMA del lancio dell'app, cosi' la finestra nasce gia' col
; logo giusto.
Filename: "{sys}\ie4uinit.exe"; Parameters: "-show"; Flags: runhidden; StatusMsg: "Refreshing icons..."; Check: FileExists(ExpandConstant('{sys}\ie4uinit.exe'))
; L'app parte subito: dopo il setup l'utente vede subito il programma.
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
