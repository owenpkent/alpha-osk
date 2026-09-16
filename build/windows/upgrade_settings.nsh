; Protect settings from uninstallers shipped before 1.4.0. Keep the copy in
; a sibling registry tree: the old binary deletes the whole organisation.
; Copy registry values directly, including their types and nested keys.
; Never import an editable .reg file into this elevated process.
;
; An include guard, because this file defines a Function at top level: a
; second !include is a compile error rather than a no-op, and the NSIS
; harness pattern this project prescribes (lift the functions out of the
; generated script) is exactly what would include it twice. Both headers
; below carry !ifndef guards for the same reason.
!ifndef ALPHA_OSK_UPGRADE_SETTINGS_NSH
!define ALPHA_OSK_UPGRADE_SETTINGS_NSH

!include "LogicLib.nsh"
!include "FileFunc.nsh"

; Where a recovery copy is announced when a restore fails. Fixed, because
; the copy itself lives under a unique per-run name that the user has no
; way to guess, and on the silent path the auto-updater drives there is
; nothing else to read: /SD IDOK auto-answers the message box and
; DetailPrint writes to a details pane silent mode never shows.
!define UPGRADE_BACKUP_ROOT "Software\${APP_ORG}-upgrade-backups"

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
  Push $7
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
  ${GetFileName} "$PLUGINSDIR" $7
  StrCpy $5 0
  ; Require a NEW key, so a retained recovery copy is never overwritten,
  ; and pick another name rather than giving up if one is in the way.
  ; GetTempFileName is only unique against what is in %TEMP% right now, so
  ; after a temp clean a later run can be handed a name it used before;
  ; aborting on that collision bricked every retry until somebody deleted
  ; the key by hand, which is not a thing this keyboard's user can do.
  nextBackupName:
  ${If} $5 == 0
    StrCpy $4 "${UPGRADE_BACKUP_ROOT}\$7"
  ${Else}
    StrCpy $4 "${UPGRADE_BACKUP_ROOT}\$7-$5"
  ${EndIf}
  System::Call 'advapi32::RegCreateKeyExW(p 0x80000001, w r4, i 0, p 0, i 0, i 0xF003F, p 0, *p .r3, *i .r6) i .r1'
  ${If} $1 != 0
    System::Call 'advapi32::RegCloseKey(p r2)'
    DeleteRegKey /ifempty HKCU "${UPGRADE_BACKUP_ROOT}"
    Goto backupFailed
  ${EndIf}
  ${If} $6 != 1
    ; REG_OPENED_EXISTING_KEY: somebody else's copy. Leave it untouched.
    System::Call 'advapi32::RegCloseKey(p r3)'
    StrCpy $3 0
    IntOp $5 $5 + 1
    ${If} $5 < 50
      Goto nextBackupName
    ${EndIf}
    System::Call 'advapi32::RegCloseKey(p r2)'
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
    StrCpy $3 0
    DeleteRegKey HKCU "$4"
    DeleteRegKey /ifempty HKCU "${UPGRADE_BACKUP_ROOT}"
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
      ; Record where the copy is somewhere the user can actually find it.
      ; The unique key name is unguessable and both of the channels this
      ; used to rely on are silent on the auto-updater's path.
      WriteRegStr HKCU "${UPGRADE_BACKUP_ROOT}" "LastRetainedBackup" "$4"
      ClearErrors
      FileOpen $2 "$INSTDIR\settings-recovery.txt" w
      ${IfNot} ${Errors}
        FileWrite $2 "Alpha-OSK setup could not restore your settings.$\r$\n"
        FileWrite $2 "A recovery copy of them is in the Windows registry at:$\r$\n"
        FileWrite $2 "HKEY_CURRENT_USER\$4$\r$\n"
        FileClose $2
      ${EndIf}
      DetailPrint "Settings recovery copy retained at HKCU\$4"
      MessageBox MB_OK|MB_ICONSTOP "Setup could not restore your settings. A recovery copy is saved in the registry at HKCU\$4, and the same path is written to $INSTDIR\settings-recovery.txt. Setup will stop to avoid replacing them with defaults." /SD IDOK
      SetErrorLevel 1
      Abort
    ${EndIf}
    DeleteRegKey HKCU "$4"
    DeleteRegKey /ifempty HKCU "${UPGRADE_BACKUP_ROOT}"
  ${EndIf}

  ; The old uninstaller's own exit code does NOT stop setup, and the
  ; asymmetry with the two aborts above is deliberate. On the
  ; same-directory path this function runs before the replacement files
  ; are extracted, so aborting here leaves a user whose only input device
  ; is this keyboard with the old install already removed, no new one, no
  ; shortcuts, and a retry that fails at the same point every time. The
  ; relauncher cannot rescue it either: it waits for a newly written
  ; executable, which an aborted install never produces. A backup or
  ; restore failure is different and stays fail-closed, because nothing
  ; destructive has run yet in the first case and the settings are still
  ; only in the copy in the second.
  ${If} $6 != 0
    ${If} $3 != 0
      DetailPrint "The previous uninstaller reported an error ($6). Your saved settings were preserved and setup is continuing."
    ${Else}
      DetailPrint "The previous uninstaller reported an error ($6). There were no saved settings to preserve. Setup is continuing."
    ${EndIf}
  ${EndIf}
  Pop $7
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

!endif
