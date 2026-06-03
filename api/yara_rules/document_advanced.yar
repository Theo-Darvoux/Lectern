rule RTF_Equation_Editor_Exploit
{
    meta:
        description = "RTF document exploiting Equation Editor (CVE-2017-11882 / CVE-2018-0802)"
        severity = "high"

    strings:
        // RTF magic
        $rtf = "{\\rtf"
        // Equation Editor OLE class IDs (common exploit targets)
        $eq1 = "0002CE02" ascii nocase
        $eq2 = "0002ce02-0000-0000-c000-000000000046" ascii nocase
        // Equation.3 ProgID
        $eq3 = "Equation.3" ascii nocase
        // OLE object embedding in RTF
        $obj = "\\objdata" ascii nocase
        $objemb = "\\objclass" ascii nocase

    condition:
        $rtf at 0 and ($obj or $objemb) and any of ($eq*)
}

rule RTF_Embedded_Object_Suspicious
{
    meta:
        description = "RTF with embedded OLE object and suspicious control words"
        severity = "medium"

    strings:
        $rtf = "{\\rtf"
        $obj = "\\object" ascii
        $embed = "\\objdata" ascii
        // Package shell object — used to embed arbitrary files
        $pkg = "Package" ascii nocase
        $shell = "OLE2Link" ascii nocase

    condition:
        $rtf at 0 and $obj and $embed and ($pkg or $shell)
}

rule XLM_Excel4_Macro
{
    meta:
        description = "Excel document with XLM (Excel 4.0) macro formulas"
        severity = "high"

    strings:
        // OLE2 header for .xls files
        $ole = { D0 CF 11 E0 A1 B1 1A E1 }
        // XLM dangerous function names in BIFF records
        $exec1 = "EXEC" ascii wide nocase
        $exec2 = "CALL" ascii wide nocase
        $exec3 = "RUN" ascii wide nocase
        $exec4 = "REGISTER" ascii wide nocase
        $exec5 = "HALT" ascii wide nocase
        // Excel 4.0 macro sheet markers
        $macro_sheet1 = "\x01\x04" // BIFF macro sheet type
        $macro_sheet2 = "Excel 4.0" ascii nocase

    condition:
        $ole at 0 and
        any of ($macro_sheet*) and
        2 of ($exec*)
}

rule OOXML_VBA_Project
{
    meta:
        description = "OOXML document containing VBA macros (vbaProject.bin)"
        severity = "medium"

    strings:
        $pk = "PK\x03\x04"
        // VBA project binary inside the ZIP
        $vba1 = "vbaProject.bin" ascii
        $vba2 = "VBAProject" ascii
        // Suspicious VBA keywords inside the binary
        $auto1 = "AutoOpen" ascii wide nocase
        $auto2 = "AutoExec" ascii wide nocase
        $auto3 = "Document_Open" ascii wide nocase
        $auto4 = "Workbook_Open" ascii wide nocase
        $exec1 = "Shell" ascii wide
        $exec2 = "WScript" ascii wide nocase
        $exec3 = "Powershell" ascii wide nocase
        $exec4 = "cmd.exe" ascii wide nocase

    condition:
        $pk at 0 and any of ($vba*) and
        (any of ($auto*) or any of ($exec*))
}

rule PDF_Name_Hex_Obfuscation
{
    meta:
        description = "PDF using hex-encoded name obfuscation to hide dangerous actions"
        severity = "high"

    strings:
        $pdf = "%PDF-"
        // Hex-obfuscated /JavaScript — e.g. /J#61vaScript, /Jav#61Script
        $js_obf1 = /\/J#[0-9a-fA-F]{2}/ ascii
        $js_obf2 = /\/Ja#[0-9a-fA-F]{2}/ ascii
        // Hex-obfuscated /OpenAction
        $oa_obf = /\/O#[0-9a-fA-F]{2}en/ ascii
        // Hex-obfuscated /Launch
        $la_obf = /\/L#[0-9a-fA-F]{2}unch/ ascii

    condition:
        $pdf at 0 and any of ($js_obf*, $oa_obf, $la_obf)
}

rule Encrypted_Document_Indicator
{
    meta:
        description = "Password-protected or encrypted document (cannot be scanned)"
        severity = "medium"

    strings:
        // OLE2 encrypted document markers
        $ole = { D0 CF 11 E0 A1 B1 1A E1 }
        $enc1 = "EncryptedPackage" ascii wide
        $enc2 = "StrongEncryptionDataSpace" ascii wide
        $enc3 = "Microsoft.Container.EncryptionTransform" ascii wide

        // ZIP with encryption bit set (general purpose bit flag, bit 0)
        // This covers encrypted OOXML documents
        $pk_enc = { 50 4B 03 04 ?? ?? 01 00 }

    condition:
        ($ole at 0 and any of ($enc*)) or
        ($pk_enc at 0)
}

rule DDE_In_OOXML
{
    meta:
        description = "OOXML document with DDE field codes (command execution without macros)"
        severity = "high"

    strings:
        $pk = "PK\x03\x04"
        // Real DDE attacks use Word field instructions: { DDEAUTO ... } or
        // { DDE ... }. Match the field-code form (keyword followed by whitespace),
        // not a bare "DDE" substring — the latter matches inside ordinary words
        // (e.g. "a[dde]d", "la[dde]r") and would false-positive on course
        // materials that discuss shell tooling (cmd.exe / powershell / …).
        $dde1 = "DDEAUTO" ascii nocase
        $dde2 = /DDE[ \t\r\n]/ ascii nocase
        // DDE typically targets these executables
        $cmd1 = "cmd.exe" ascii nocase
        $cmd2 = "powershell" ascii nocase
        $cmd3 = "mshta" ascii nocase
        $cmd4 = "certutil" ascii nocase

    condition:
        $pk at 0 and any of ($dde*) and any of ($cmd*)
}
