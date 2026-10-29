// Original, one-page PDF: a text heading and a vector rule, with exact stream/xref lengths.
// No third-party content or fonts are embedded. The rule also paints without system fonts.
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';

const stream = '0.15 g\n24 24 252 6 re f\nBT /F1 18 Tf 24 100 Td (Qarau report preview) Tj ET\n';
const objects = [
  '<< /Type /Catalog /Pages 2 0 R >>',
  '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
  '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 160] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
  '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}endstream`,
];
let pdf = '%PDF-1.4\n';
const offsets = [0];
for (const [index, object] of objects.entries()) {
  offsets.push(Buffer.byteLength(pdf));
  pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
}
const xref = Buffer.byteLength(pdf);
pdf += `xref\n0 6\n0000000000 65535 f \n${offsets
  .slice(1)
  .map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`)
  .join('')}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
writeFileSync(new URL('../src/test/fixtures/report.pdf', import.meta.url), pdf);
console.log(
  `${Buffer.byteLength(pdf)} bytes, SHA-256 ${createHash('sha256').update(pdf).digest('hex')}`,
);
