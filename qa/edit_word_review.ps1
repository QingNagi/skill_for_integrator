param([Parameter(Mandatory)][string]$Document)
$ErrorActionPreference = 'Stop'
# The caller supplies a disposable generated draft for the compatibility test.
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($Document, $false, $false, $false)
    try {
        $range = $doc.Range($doc.Content.End - 1, $doc.Content.End - 1)
        $range.InsertAfter(' QA-EDIT-8642')
        $note = $doc.Comments.Add($range, 'QA reviewer annotation')
        $note.Author = 'QA Editor'
        $note.Initial = 'QA'
        $doc.Save()
        Write-Output "Word $($word.Version) saved an edit and an identified review annotation."
    } finally { $doc.Close(0) }
} finally { $word.Quit() }
