# Fonts of the PDF seam

These are Geist Regular and Geist SemiBold, version 1.7.2 of the `geist`
package, under the SIL Open Font License 1.1 (`OFL.txt`). Geist declares no
Reserved Font Name.

**One change.** We removed the GSUB table, so the fonts make no ligature. With
GSUB, MuPDF wrote a wrong character for each "tt", "ff" and "fi" ligature. Then
"settings" copied and searched as "seǪings" (WS-47 WAC-12, review round 1).
The kerning (GPOS) stays.

`gateway.pdf_render` loads them for a document (source kind `document`). The
fence is `tests/unit/test_wac_documents.py`.

To make them again from a newer `geist`, run fontTools on each file:
`TTFont(src)`, then `del font["GSUB"]`, then `font.save(dst)`.
