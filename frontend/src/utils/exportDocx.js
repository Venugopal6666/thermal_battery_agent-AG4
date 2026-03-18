/**
 * Export assistant response to DOCX format.
 * Converts markdown-like content to a structured Word document.
 */

import {
  Document, Packer, Paragraph, TextRun, HeadingLevel,
  Table, TableRow, TableCell, WidthType, BorderStyle,
  AlignmentType, ShadingType, Footer, PageNumber,
} from 'docx';
import { saveAs } from 'file-saver';

// ── Color constants ────────────────────────────────────────
const ACCENT = '6366F1';       // Indigo accent
const HEADER_BG = '1E1B4B';    // Dark header
const HEADER_TEXT = 'E2E8F0';   // Light text
const ROW_ALT = 'F1F0FF';      // Alternating row
const BORDER_COLOR = '94A3B8';  // Subtle border

/**
 * Parse markdown text into structured blocks.
 */
function parseMarkdown(text) {
  const lines = text.split('\n');
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Skip empty lines
    if (!line.trim()) { i++; continue; }

    // Headings
    const headingMatch = line.match(/^(#{1,6})\s+(.+)/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      blocks.push({ type: 'heading', level, text: headingMatch[2].trim() });
      i++;
      continue;
    }

    // Table detection (line with pipes)
    if (line.includes('|') && line.trim().startsWith('|')) {
      const tableRows = [];
      while (i < lines.length && lines[i].includes('|')) {
        const row = lines[i].trim();
        // Skip separator rows like |---|---|
        if (!row.match(/^\|[\s\-:|]+\|$/)) {
          const cells = row.split('|').filter(c => c.trim() !== '').map(c => c.trim());
          if (cells.length > 0) tableRows.push(cells);
        }
        i++;
      }
      if (tableRows.length > 0) {
        blocks.push({ type: 'table', rows: tableRows });
      }
      continue;
    }

    // Bullet list items
    if (line.match(/^\s*[-*]\s+/)) {
      const items = [];
      while (i < lines.length && lines[i].match(/^\s*[-*]\s+/)) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, '').trim());
        i++;
      }
      blocks.push({ type: 'list', items });
      continue;
    }

    // Numbered list items
    if (line.match(/^\s*\d+[.)]\s+/)) {
      const items = [];
      while (i < lines.length && lines[i].match(/^\s*\d+[.)]\s+/)) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, '').trim());
        i++;
      }
      blocks.push({ type: 'numbered_list', items });
      continue;
    }

    // Code blocks (skip chart blocks)
    if (line.trim().startsWith('```')) {
      const lang = line.trim().replace('```', '').trim();
      i++;
      const codeLines = [];
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      if (lang !== 'chart') {
        blocks.push({ type: 'code', lang, code: codeLines.join('\n') });
      }
      continue;
    }

    // Regular paragraph
    blocks.push({ type: 'paragraph', text: line.trim() });
    i++;
  }

  return blocks;
}

/**
 * Parse inline formatting (bold, italic, code) into TextRun array.
 */
function parseInlineFormatting(text) {
  const runs = [];
  // Simple regex to handle **bold**, *italic*, `code`
  const regex = /(\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`|([^*`]+))/g;
  let match;

  while ((match = regex.exec(text)) !== null) {
    if (match[2]) {
      // Bold
      runs.push(new TextRun({ text: match[2], bold: true, font: 'Calibri', size: 22 }));
    } else if (match[3]) {
      // Italic
      runs.push(new TextRun({ text: match[3], italics: true, font: 'Calibri', size: 22 }));
    } else if (match[4]) {
      // Code
      runs.push(new TextRun({
        text: match[4],
        font: 'Consolas',
        size: 20,
        color: ACCENT,
      }));
    } else if (match[5]) {
      runs.push(new TextRun({ text: match[5], font: 'Calibri', size: 22 }));
    }
  }

  return runs.length > 0 ? runs : [new TextRun({ text, font: 'Calibri', size: 22 })];
}

/**
 * Create a DOCX table from parsed rows.
 */
function createTable(rows) {
  if (rows.length === 0) return null;

  const numCols = Math.max(...rows.map(r => r.length));

  const docxRows = rows.map((row, rowIdx) => {
    const isHeader = rowIdx === 0;
    const isAlt = rowIdx % 2 === 0 && rowIdx > 0;

    const cells = [];
    for (let c = 0; c < numCols; c++) {
      const cellText = row[c] || '';
      cells.push(
        new TableCell({
          children: [
            new Paragraph({
              children: [
                new TextRun({
                  text: cellText,
                  bold: isHeader,
                  font: 'Calibri',
                  size: isHeader ? 20 : 20,
                  color: isHeader ? HEADER_TEXT : '1E293B',
                }),
              ],
              spacing: { before: 60, after: 60 },
            }),
          ],
          shading: isHeader
            ? { type: ShadingType.SOLID, color: HEADER_BG }
            : isAlt
              ? { type: ShadingType.SOLID, color: ROW_ALT }
              : undefined,
          width: { size: Math.floor(9000 / numCols), type: WidthType.DXA },
        })
      );
    }

    return new TableRow({ children: cells });
  });

  return new Table({
    rows: docxRows,
    width: { size: 9000, type: WidthType.DXA },
  });
}

/**
 * Convert markdown blocks to DOCX children (paragraphs, tables, etc.)
 */
function blocksToDocxChildren(blocks) {
  const children = [];

  for (const block of blocks) {
    switch (block.type) {
      case 'heading': {
        const levelMap = {
          1: HeadingLevel.HEADING_1,
          2: HeadingLevel.HEADING_2,
          3: HeadingLevel.HEADING_3,
          4: HeadingLevel.HEADING_4,
        };
        children.push(
          new Paragraph({
            children: [
              new TextRun({
                text: block.text,
                bold: true,
                font: 'Calibri',
                size: [36, 30, 26, 24][block.level - 1] || 24,
                color: ACCENT,
              }),
            ],
            heading: levelMap[block.level] || HeadingLevel.HEADING_4,
            spacing: { before: 200, after: 100 },
          })
        );
        break;
      }

      case 'paragraph':
        children.push(
          new Paragraph({
            children: parseInlineFormatting(block.text),
            spacing: { before: 60, after: 60 },
          })
        );
        break;

      case 'table': {
        const table = createTable(block.rows);
        if (table) {
          children.push(table);
          children.push(new Paragraph({ text: '' })); // Spacer
        }
        break;
      }

      case 'list':
        block.items.forEach(item => {
          children.push(
            new Paragraph({
              children: parseInlineFormatting(item),
              bullet: { level: 0 },
              spacing: { before: 40, after: 40 },
            })
          );
        });
        break;

      case 'numbered_list':
        block.items.forEach((item, idx) => {
          children.push(
            new Paragraph({
              children: [
                new TextRun({ text: `${idx + 1}. `, bold: true, font: 'Calibri', size: 22 }),
                ...parseInlineFormatting(item),
              ],
              spacing: { before: 40, after: 40 },
            })
          );
        });
        break;

      case 'code':
        children.push(
          new Paragraph({
            children: [
              new TextRun({
                text: block.code,
                font: 'Consolas',
                size: 18,
                color: '334155',
              }),
            ],
            shading: { type: ShadingType.SOLID, color: 'F1F5F9' },
            spacing: { before: 100, after: 100 },
          })
        );
        break;

      default:
        break;
    }
  }

  return children;
}

/**
 * Export a message response as a DOCX file.
 *
 * @param {Object} message - The message object with content, rules_used, etc.
 * @param {string} [userQuestion] - Optional user question for context.
 */
export async function exportToDocx(message, userQuestion = '') {
  const blocks = parseMarkdown(message.content || '');
  const contentChildren = blocksToDocxChildren(blocks);

  // Build document sections
  const docChildren = [];

  // Title
  docChildren.push(
    new Paragraph({
      children: [
        new TextRun({
          text: 'RESL Thermal Battery Agent',
          bold: true,
          font: 'Calibri',
          size: 32,
          color: ACCENT,
        }),
      ],
      spacing: { after: 40 },
    })
  );

  docChildren.push(
    new Paragraph({
      children: [
        new TextRun({
          text: `Report generated on ${new Date().toLocaleDateString('en-IN', {
            year: 'numeric', month: 'long', day: 'numeric',
            hour: '2-digit', minute: '2-digit',
          })}`,
          font: 'Calibri',
          size: 18,
          color: '64748B',
          italics: true,
        }),
      ],
      spacing: { after: 200 },
    })
  );

  // Horizontal divider
  docChildren.push(
    new Paragraph({
      border: {
        bottom: { style: BorderStyle.SINGLE, size: 2, color: ACCENT },
      },
      spacing: { after: 200 },
    })
  );

  // User question (if provided)
  if (userQuestion) {
    docChildren.push(
      new Paragraph({
        children: [
          new TextRun({ text: 'Query: ', bold: true, font: 'Calibri', size: 22, color: ACCENT }),
          new TextRun({ text: userQuestion, font: 'Calibri', size: 22 }),
        ],
        spacing: { before: 100, after: 200 },
        shading: { type: ShadingType.SOLID, color: 'F8FAFC' },
      })
    );
  }

  // Main content
  docChildren.push(...contentChildren);

  // Rules Applied section
  if (message.rules_used && message.rules_used.length > 0) {
    docChildren.push(new Paragraph({ text: '' })); // Spacer
    docChildren.push(
      new Paragraph({
        border: {
          bottom: { style: BorderStyle.SINGLE, size: 1, color: BORDER_COLOR },
        },
        spacing: { after: 100 },
      })
    );
    docChildren.push(
      new Paragraph({
        children: [
          new TextRun({
            text: 'Rules Applied',
            bold: true,
            font: 'Calibri',
            size: 26,
            color: ACCENT,
          }),
        ],
        spacing: { before: 100, after: 100 },
      })
    );

    message.rules_used.forEach(rule => {
      docChildren.push(
        new Paragraph({
          children: [
            new TextRun({ text: '  \u2022  ', font: 'Calibri', size: 22, color: ACCENT }),
            new TextRun({ text: rule, font: 'Calibri', size: 22 }),
          ],
          spacing: { before: 40, after: 40 },
        })
      );
    });
  }

  // Create DOCX document
  const doc = new Document({
    styles: {
      default: {
        document: {
          run: { font: 'Calibri', size: 22 },
        },
      },
    },
    sections: [
      {
        properties: {
          page: {
            margin: { top: 1000, bottom: 1000, left: 1200, right: 1200 },
          },
        },
        children: docChildren,
        footers: {
          default: new Footer({
            children: [
              new Paragraph({
                children: [
                  new TextRun({
                    text: 'RESL Thermal Battery Agent  |  Page ',
                    font: 'Calibri',
                    size: 16,
                    color: '94A3B8',
                  }),
                  new TextRun({
                    children: [PageNumber.CURRENT],
                    font: 'Calibri',
                    size: 16,
                    color: '94A3B8',
                  }),
                ],
                alignment: AlignmentType.CENTER,
              }),
            ],
          }),
        },
      },
    ],
  });

  // Generate and download
  const blob = await Packer.toBlob(doc);
  const timestamp = new Date().toISOString().slice(0, 10);
  const filename = `RESL_Report_${timestamp}.docx`;
  saveAs(blob, filename);
}
