; 云聊 —— 安装包脚本 (Inno Setup 6)
#define MyAppName "云聊"
#define MyAppExeName "云聊-客户端.exe"
#define MyAppVersion "1.1.3"

[Setup]
AppId={{171FD3E8-EB45-437D-97EE-4865AEC743B1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName}
AppPublisher=云聊
DefaultDirName={localappdata}\Programs\云聊
DefaultGroupName=云聊
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=云聊-Setup
SetupIconFile=app.ico
UninstallDisplayIcon={app}\app.ico
UninstallDisplayName={#MyAppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "dist\ylclient.exe"; DestDir: "{app}"; DestName: "云聊-客户端.exe"; Flags: ignoreversion
Source: "app.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "使用说明.txt"; DestDir: "{app}"; DestName: "使用说明.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\云聊"; Filename: "{app}\云聊-客户端.exe"; IconFilename: "{app}\app.ico"; Comment: "启动云聊"
Name: "{autodesktop}\云聊"; Filename: "{app}\云聊-客户端.exe"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\云聊-客户端.exe"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时保留 data\（用户数据与数据库），仅清理快捷方式由 Inno 自动完成
Type: files; Name: "{app}\data\config.json.tmp"
