#define MyAppName "Task Wallpaper"
#define MyAppVersion "2.2.1"
#define MyAppExeName "TaskWallpaper.exe"
#define MyBuildDir "dist\TaskWallpaper"

[Setup]
AppId={{B0A541E1-C541-4A0D-A03F-E165CC3A4B30}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}.0

DefaultDirName={localappdata}\Programs\TaskWallpaper
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes

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

[Code]
const
  AppUninstallKey =
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
    '{B0A541E1-C541-4A0D-A03F-E165CC3A4B30}_is1';


function ParseAppVersion(
  const Text: String;
  var PackedVersion: Int64
): Boolean;
var
  Normalized: String;
  DotCount: Integer;
  I: Integer;
begin
  Result := False;
  Normalized := Trim(Text);

  if Normalized = '' then
    Exit;

  DotCount := 0;

  for I := 1 to Length(Normalized) do
  begin
    if Normalized[I] = '.' then
      DotCount := DotCount + 1
    else
    begin
      if (Normalized[I] < '0') or
         (Normalized[I] > '9') then
        Exit;
    end;
  end;

  if DotCount > 3 then
    Exit;

  while DotCount < 3 do
  begin
    Normalized := Normalized + '.0';
    DotCount := DotCount + 1;
  end;

  Result := StrToVersion(
    Normalized,
    PackedVersion
  );
end;


procedure ReportBlockedInstallation(
  const Text: String
);
begin
  Log(Text);

  if not WizardSilent then
    MsgBox(Text, mbError, MB_OK);
end;


function InitializeSetup(): Boolean;
var
  InstalledVersionText: String;
  InstalledVersion: Int64;
  InstallerVersion: Int64;
  Comparison: Integer;
  Question: String;
begin
  Result := False;

  { Версия самого установщика должна быть корректной. }
  if not ParseAppVersion(
    '{#MyAppVersion}',
    InstallerVersion
  ) then
  begin
    ReportBlockedInstallation(
      'Некорректная версия установщика: ' +
      '{#MyAppVersion}' + #13#10#13#10 +
      'Установка отменена.'
    );
    Exit;
  end;

  { Установки с нашим AppId для текущего пользователя нет. }
  if not RegKeyExists(HKCU, AppUninstallKey) then
  begin
    Result := True;
    Exit;
  end;

  { Установка существует: читаем её версию. }
  if not RegQueryStringValue(
    HKCU,
    AppUninstallKey,
    'DisplayVersion',
    InstalledVersionText
  ) then
  begin
    ReportBlockedInstallation(
      'Найдена установленная Task Wallpaper, ' +
      'но не удалось прочитать её версию.' +
      '' + #13#10#13#10 +
      'Установка отменена, чтобы не заменить ' +
      'неизвестную версию приложения.'
    );
    Exit;
  end;

  if not ParseAppVersion(
    InstalledVersionText,
    InstalledVersion
  ) then
  begin
    ReportBlockedInstallation(
      'Не удалось определить версию ' +
      'установленной Task Wallpaper:' +
      '' + #13#10 +
      InstalledVersionText +
      '' + #13#10#13#10 +
      'Установка отменена.'
    );
    Exit;
  end;

  Comparison := ComparePackedVersion(
    InstalledVersion,
    InstallerVersion
  );

  Log(
    'Installed version: ' + InstalledVersionText +
    '; installer version: {#MyAppVersion}'
  );

  { Понижение версии запрещено и вручную, и в silent-режиме. }
  if Comparison > 0 then
  begin
    ReportBlockedInstallation(
      'Уже установлена более новая версия ' +
      'Task Wallpaper: ' +
      InstalledVersionText +
      '' + #13#10 +
      'Версия этого установщика: {#MyAppVersion}' +
      '' + #13#10#13#10 +
      'Установка более старой версии запрещена.'
    );
    Exit;
  end;

  { Автообновлятор уже получил согласие пользователя. }
  if WizardSilent then
  begin
    Result := True;
    Exit;
  end;

  if Comparison = 0 then
  begin
    Question :=
      'Task Wallpaper ' +
      InstalledVersionText +
      ' уже установлена.' +
      '' + #13#10#13#10 +
      'Переустановить приложение?' +
      '' + #13#10#13#10 +
      'Пользовательские данные не будут удалены.' +
      '' + #13#10#13#10 +
      'Да — продолжить переустановку.' +
      '' + #13#10 +
      'Нет — выйти из установщика.';
  end
  else
  begin
    Question :=
      'Установлена Task Wallpaper ' +
      InstalledVersionText +
      '.' +
      '' + #13#10 +
      'Новая версия: {#MyAppVersion}.' +
      '' + #13#10#13#10 +
      'Обновить приложение?' +
      '' + #13#10#13#10 +
      'Пользовательские данные не будут удалены.' +
      '' + #13#10#13#10 +
      'Да — продолжить обновление.' +
      '' + #13#10 +
      'Нет — выйти из установщика.';
  end;

  Result :=
    MsgBox(
      Question,
      mbConfirmation,
      MB_YESNO or MB_DEFBUTTON2
    ) = IDYES;
end;