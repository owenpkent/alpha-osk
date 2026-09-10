; Protect settings from uninstallers shipped before 1.4.0. Keep the copy in
; a sibling registry tree: the old binary deletes the whole organisation.
; Copy registry values directly, including their types and nested keys.
; Never import an editable .reg file into this elevated process.
!include "LogicLib.nsh"
!include "FileFunc.nsh"

Function RunPreviousUninstaller
  ; Command on the stack. Preserve the caller's registers (customInstall
  ; also keeps the previous installation's path in them).
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  StrCpy $3 0
  StrCpy $4 ""

  ; ERROR_FILE_NOT_FOUND is a fresh install with no settings to preserve.
  ; Any other error must stop us before the old binary can delete anything.
  System::Call 'advapi32::RegOpenKeyExW(p 0x80000001, w "Software\${APP_ORG}", i 0, i 0x20019, *p .r2) i .r1'
  ${If} $1 == 2
    Goto runOldUninstaller
  ${ElseIf} $1 != 0
    Goto backupFailed
  ${EndIf}

  InitPluginsDir
  ${GetFileName} "$PLUGINSDIR" $4
  StrCpy $4 "Software\${APP_ORG}-upgrade-backups\$4"
  ; Require a NEW key. Never overwrite a retained recovery copy.
  System::Call 'advapi32::RegCreateKeyExW(p 0x80000001, w r4, i 0, p 0, i 0, i 0xF003F, p 0, *p .r3, *i .r5) i .r1'
  ${If} $1 != 0
    System::Call 'advapi32::RegCloseKey(p r2)'
    Goto backupFailed
  ${EndIf}
  ${If} $5 != 1
    System::Call 'advapi32::RegCloseKey(p r2)'
    System::Call 'advapi32::RegCloseKey(p r3)'
    Goto backupFailed
  ${EndIf}
  System::Call 'advapi32::RegCopyTreeW(p r2, p 0, p r3) i .r1'
  System::Call 'advapi32::RegCloseKey(p r2)'
  ${If} $1 == 0
    ; Make the recovery copy durable before running destructive old code.
    System::Call 'advapi32::RegFlushKey(p r3) i .r1'
  ${EndIf}
  ${If} $1 != 0
    System::Call 'advapi32::RegCloseKey(p r3)'
    DeleteRegKey HKCU "$4"
    DeleteRegKey /ifempty HKCU "Software\${APP_ORG}-upgrade-backups"
    Goto backupFailed
  ${EndIf}

  runOldUninstaller:
  ClearErrors
  ExecWait '$0' $6
  ${If} ${Errors}
    StrCpy $6 -1
  ${EndIf}

  ; Restore even when the old process fails: it may already have erased
  ; preferences before returning an error. Do this before copying files
  ; or relaunching the app, which would otherwise write fresh defaults.
  ${If} $3 != 0
    System::Call 'advapi32::RegCreateKeyExW(p 0x80000001, w "Software\${APP_ORG}", i 0, p 0, i 0, i 0xF003F, p 0, *p .r2, p 0) i .r1'
    ${If} $1 == 0
      System::Call 'advapi32::RegCopyTreeW(p r3, p 0, p r2) i .r1'
      ${If} $1 == 0
        System::Call 'advapi32::RegFlushKey(p r2) i .r1'
      ${EndIf}
      System::Call 'advapi32::RegCloseKey(p r2)'
    ${EndIf}
    System::Call 'advapi32::RegCloseKey(p r3)'
    ${If} $1 != 0
      DetailPrint "Settings recovery copy retained at HKCU\$4"
      MessageBox MB_OK|MB_ICONSTOP "Setup could not restore your settings. A recovery copy is saved in the registry at HKCU\$4. Setup will stop to avoid replacing them with defaults." /SD IDOK
      SetErrorLevel 1
      Abort
    ${EndIf}
    DeleteRegKey HKCU "$4"
    DeleteRegKey /ifempty HKCU "Software\${APP_ORG}-upgrade-backups"
  ${EndIf}
  ${If} $6 != 0
    MessageBox MB_OK|MB_ICONSTOP "The previous uninstaller failed. Your saved settings have been preserved. Please retry setup." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
  Return

  backupFailed:
  MessageBox MB_OK|MB_ICONSTOP "Setup could not back up your settings. The previous uninstaller has not been run. Please retry setup." /SD IDOK
  SetErrorLevel 1
  Abort
FunctionEnd
