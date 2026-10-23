---
name: docx
description: Use when generating or manipulating Word documents programmatically with Python or Node.js.
---

# Word Documents

## When to Use This Skill
- Generating reports or letters as .docx files
- Creating documents with tables, images, and styled text
- Extracting text or data from existing Word documents
- Filling templates with dynamic content

## Workflow
1. Install the library: `pip install python-docx` or `npm install docx`
2. Create a document: `doc = Document()` for Python or `new Document()` for Node
3. Add content: headings, paragraphs, tables, and images
4. Apply styles: built-in styles or custom formatting
5. For templates: load the template, find placeholder fields, and replace them
6. Save: `doc.save('output.docx')`
7. For extraction: parse the document and read paragraphs or tables

## Rules
- Use built-in styles (Heading 1, Body Text) for consistency
- Don't hardcode fonts — use the document's default or theme fonts
- Test output in Microsoft Word and LibreOffice — rendering differs
- Handle images carefully — set appropriate sizes and positions
- Use templates for repetitive documents, not manual construction
- Keep file sizes reasonable — compress images before embedding
