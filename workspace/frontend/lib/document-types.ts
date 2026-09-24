/**
 * Student documents: what the UI accepts, in one place.
 *
 * The frontend twin of `app/documents/types.py`. This is a usability guard —
 * it keeps a student from waiting on an upload that the server will reject —
 * and NOT a security boundary. The server re-derives the type from the file's
 * bytes, because an accept attribute is trivially bypassed and a browser's
 * reported MIME type is frequently wrong.
 *
 * Phase 1 supports PDF and DOCX only. Legacy .doc is deliberately excluded:
 * it is a different format that shares a stem, and silently accepting it
 * would fail deep in the server's parser instead of here, where the student
 * can act on it.
 */

export const DOCUMENT_EXTENSIONS = ['pdf', 'docx'] as const;

/** Person-facing processing stage, as reported by GET /v1/files/{id}/info. */
export type DocumentStage = 'reading' | 'understanding' | 'done' | 'failed' | 'unsupported';

/** Stages that will not change on their own — polling can stop. */
export const SETTLED_DOCUMENT_STAGES: readonly DocumentStage[] = ['done', 'failed', 'unsupported'];

/** True for a chat attachment that goes through the document pipeline. */
export function isDocumentAttachment(filename: string, contentType: string): boolean {
  const base = filename.split('/').pop() ?? '';
  const dot = base.lastIndexOf('.');
  const extension = dot > 0 ? base.slice(dot + 1).toLowerCase() : '';
  return (DOCUMENT_EXTENSIONS as readonly string[]).includes(extension)
    || (DOCUMENT_MIME_TYPES as readonly string[]).includes(contentType);
}

export const DOCUMENT_MIME_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
] as const;

/** `accept` attribute for a student-document file input. */
export const DOCUMENT_ACCEPT = '.pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document';

function extensionOf(filename: string): string {
  const base = filename.split('/').pop() ?? '';
  const dot = base.lastIndexOf('.');
  return dot > 0 ? base.slice(dot + 1).toLowerCase() : '';
}

export function isSupportedDocument(file: File): boolean {
  const extension = extensionOf(file.name);
  if ((DOCUMENT_EXTENSIONS as readonly string[]).includes(extension)) return true;
  // Some browsers omit the extension but report a correct MIME type.
  return (DOCUMENT_MIME_TYPES as readonly string[]).includes(file.type);
}

/**
 * True when a file *claims* to be a document type we do not support.
 *
 * Distinguishes "a .doc, which we must reject" from "a PNG, which is simply
 * not a document" — the first deserves an explanation, the second does not.
 */
export function isUnsupportedDocumentType(file: File): boolean {
  const extension = extensionOf(file.name);
  return ['doc', 'rtf', 'odt', 'pages', 'txt', 'md'].includes(extension);
}

export function unsupportedDocumentMessage(file: File): string {
  const extension = extensionOf(file.name);
  if (extension === 'doc') {
    return `"${file.name}" is a legacy Word file. Save it as PDF or .docx and try again.`;
  }
  return `"${file.name}" cannot be read as a document. Please upload a PDF or Word (.docx) file.`;
}
