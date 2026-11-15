#define MyAppName "Task Wallpaper"
#define MyAppVersion "2.1.0"
#define MyAppExeName "TaskWallpaper.exe"
#define MyBuildDir "dist\TaskWallpaper"

[Setup]
AppId={{B0A541E1-C541-4A0D-A03F-E165CC3A4B30}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion=2.1.0.0

DefaultDirName={localappdata}\Programs\TaskWallpaper
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

PrivilegesRequired=lowest

OutputDir=installer_output
OutputBaseFilename=TaskWallpaper-Setup-{#MyAppVersion}

SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

Compression=lzma2
SolidCompression=yes
WizardStyle=modern

CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "{#MyBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Удалить Task Wallpaper"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\Task Wallpaper"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--tray"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить Task Wallpaper"; Flags: nowait postinstall skipifsilent