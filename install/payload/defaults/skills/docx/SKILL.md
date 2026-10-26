---
name: docx
description: Read, extract, compare, create, or edit Word DOCX documents using Node.js by default and preserving existing styles.
---

# Word Documents

## When to Use This Skill
- Whenever the user asks to read, inspect, extract, compare, create, validate, or modify a Word document.
- Especially when editing an existing DOCX, appending an annex, applying terminology changes, or matching its established style.

When the user explicitly asks to use this skill (for example, `@docx`), load this skill before choosing an implementation or writing helper code.

## Workflow
1. Identify the exact source and destination paths from the user's request. Honor any stated working-copy or temporary-file constraints; do not default to `/tmp/opencode`.
2. Prefer Node.js for every DOCX operation in ocode-managed containers. For reading/extracting, do not treat missing `mammoth` or `docx` npm packages as proof that DOCX is unreadable: `docx` is primarily a document-generation library. Check for already-installed Node ZIP/XML libraries and system archive tools such as `unzip`, `7z`/`7zz`, or `bsdtar`; verify availability rather than guessing. If an archive tool exists, use Node.js to invoke it safely with argument arrays (not a shell-built command) and read only the needed DOCX XML entries, usually `word/document.xml` and, if necessary, `word/numbering.xml` or `word/styles.xml`. Extract the requested section while preserving paragraph order, tables, list numbering, and headings. Do not dump the entire document into the conversation. If no suitable Node method is available, identify the smallest suitable Node dependency and ask the user for explicit permission to install it locally; never install silently or globally. After approval, install only that dependency in the relevant working copy, verify it, and continue. If installation is denied or blocked, stop and report that. Only consider another runtime after explaining the limitation and asking.
3. For read-only requests (extracting a section or comparing document text with application code), extract only the requested content, preserving its order, headings, lists, tables, and numbering. If the requested section is not found in the main document XML, inspect relevant headers/footers or other document parts before concluding it is absent. Do not modify or regenerate the document. Compare implementation claims with the relevant source code, never a database.
4. For an existing document that must be edited, inspect its structure and formatting before editing: sections, page setup, headers and footers, paragraph and character styles, tables, lists, spacing, and representative runs. Treat the source document as the template.
5. Use an already available Node library or method that can safely edit the existing DOCX package and preserve its styles. The `docx` npm package is useful for creating documents, but do not assume it can import and edit an existing document without rebuilding it. Do not switch to Python or another runtime unless Node.js cannot meet the requirement; explain the limitation and ask first.
6. Edit a copy of the source rather than reconstructing the whole document. Reuse its styles and layout for new content. Preserve existing headers, footers, sections, tables, page setup, and inline formatting unless the user asks to change them.
7. When replacing text, account for text split across Word runs. Avoid assigning to an entire paragraph's `.text` when that would discard mixed formatting.
8. Save intermediate and final files only in the user-designated working copy/location. If access is denied, stop and report the denied path; do not try another directory unless the user authorizes it.
9. Do not replace the original until the requested output has been generated and checked. Overwrite the original only when the user has requested that.
10. Do not use text patch/edit tools directly on the binary DOCX. If a patch or verification step fails, stop and report it rather than claiming the document was updated.
11. Validate the DOCX archive and inspect the changed content and styles. If no rendering tool is available, say that visual layout has not been verified; a valid ZIP alone does not prove that formatting was preserved.

## Rules
- Preserve the source document's existing style names, fonts, theme, spacing, numbering, and layout; do not substitute generic `Heading 1` or `Body Text` styles when the document defines its own styles.
- Never hardcode fonts when the document's styles or theme provide them.
- Keep requested content and terminology changes scoped; do not add implementation details the user excluded.
- Handle images carefully and keep file sizes reasonable.
- Test the result in Microsoft Word or LibreOffice when available; rendering differs. Do not claim visual validation unless it was performed.
