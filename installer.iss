; MT5 Copy Manual - Inno Setup Script
; Requiere: PyInstaller output en dist\MT5CopyManual\
; Compilar con: ISCC.exe installer.iss

#define AppName      "MT5 Copy Manual"
#define AppVersion   "1.0.0"
#define AppExeName   "MT5CopyManual.exe"
#define AppPublisher "Adrian Nieves"

[Setup]
AppId={{9F3A5C28-B17E-4D2A-8C60-1E7A543F9B2D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppVerName={#AppName} {#AppVersion}

; AppData\Local: escribible en runtime, no sincronizado por OneDrive
DefaultDirName={localappdata}\MT5CopyManual
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes

; Salida
OutputDir=dist
OutputBaseFilename=MT5CopyManual_Setup_{#AppVersion}

; Compresion
Compression=lzma2
SolidCompression=yes

; UI
WizardStyle=modern

; Requiere administrador para instalar
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Files]
; Copia toda la carpeta generada por PyInstaller
Source: "dist\MT5CopyManual\*"; \
  DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; Crea carpetas de runtime vacias
Name: "{app}\logs"
Name: "{app}\data\state"
Name: "{app}\data\screenshots"
Name: "{app}\data\images"

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\{#AppExeName}"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}";   Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; \
  Description: "Iniciar {#AppName} ahora"; \
  Flags: nowait postinstall skipifsilent

[Messages]
; Mensajes personalizados en espanol
FinishedLabel=La instalacion de [name] ha terminado.%n%nLos archivos de MetaTrader 5 (Expert Advisors) se encuentran en:%n%n  {app}\mql5\Experts\%n%nCopiados manualmente a la carpeta MQL5\Experts de cada terminal MT5.
