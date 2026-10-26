---
name: docx
description: Create or edit Word documents, especially when preserving the style and layout of an existing DOCX.
---

# Word Documents

## When to Use This Skill
- Whenever the user asks to create, inspect, extract from, or modify a Word document.
- Especially when editing an existing DOCX, appending an annex, applying terminology changes, or matching its established style.

When the user explicitly asks to use this skill (for example, `@docx`), load this skill before choosing an implementation or writing helper code.

## Workflow
1. Identify the exact source and destination paths from the user's request. Honor any stated working-copy or temporary-file constraints; do not default to `/tmp/opencode`.
2. For an existing document, inspect its structure and formatting before editing: sections, page setup, headers and footers, paragraph and character styles, tables, lists, spacing, and representative runs. Treat the source document as the template.
3. Prefer Node.js in ocode-managed containers, where it is available. Choose an already available library or method that can safely edit the existing DOCX package and preserve its styles. The `docx` npm package is useful for creating documents, but do not assume it can import and edit an existing document without rebuilding it. Do not switch to Python or another runtime unless Node.js cannot meet the requirement; explain the limitation and ask first. Do not install dependencies automatically.
4. Edit a copy of the source rather than reconstructing the whole document. Reuse its styles and layout for new content. Preserve existing headers, footers, sections, tables, page setup, and inline formatting unless the user asks to change them.
5. When replacing text, account for text split across Word runs. Avoid assigning to an entire paragraph's `.text` when that would discard mixed formatting.
6. Save intermediate and final files only in the user-designated working copy/location. If access is denied, stop and report the denied path; do not try another directory unless the user authorizes it.
7. Do not replace the original until the requested output has been generated and checked. Overwrite the original only when the user has requested that.
8. Do not use text patch/edit tools directly on the binary DOCX. If a patch or verification step fails, stop and report it rather than claiming the document was updated.
9. Validate the DOCX archive and inspect the changed content and styles. If no rendering tool is available, say that visual layout has not been verified; a valid ZIP alone does not prove that formatting was preserved.

## Rules
- Preserve the source document's existing style names, fonts, theme, spacing, numbering, and layout; do not substitute generic `Heading 1` or `Body Text` styles when the document defines its own styles.
- Never hardcode fonts when the document's styles or theme provide them.
- Keep requested content and terminology changes scoped; do not add implementation details the user excluded.
- Handle images carefully and keep file sizes reasonable.
- Test the result in Microsoft Word or LibreOffice when available; rendering differs. Do not claim visual validation unless it was performed.
