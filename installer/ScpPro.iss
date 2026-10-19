#define MyAppName "ScpPro"
#define MyAppVersion "1.1.0"

[Setup]
AppId={{F4C0B0D8-74BD-4421-8D83-B61E529DE467}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Ginchanging
DefaultDirName={localappdata}\Programs\ScpPro
DefaultGroupName=ScpPro
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
OutputDir=..\dist
OutputBaseFilename=ScpPro-{#MyAppVersion}-Windows-11-x64-Setup
SetupIconFile=..\build\ScpPro.ico
UninstallDisplayIcon={app}\ScpPro.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "其他选项："; Flags: unchecked

[Files]
Source: "..\dist\ScpPro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ScpPro"; Filename: "{app}\ScpPro.exe"
Name: "{autodesktop}\ScpPro"; Filename: "{app}\ScpPro.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ScpPro.exe"; Description: "启动 ScpPro"; Flags: nowait postinstall skipifsilent
