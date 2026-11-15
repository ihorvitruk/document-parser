import { useEffect, useState } from "react";
import type { ChangeEvent, DragEvent, FormEvent } from "react";
import { uploadDocuments } from "../api";
import type { ParserInfo, ParserName } from "../types";

interface Props {
  parsers: ParserInfo[];
  onUploaded: () => void | Promise<void>;
}

const HINTS: Record<ParserName, string> = {
  pypdf: "Fast, plain text. Best for PDFs that already contain text.",
  gemini: "Markdown with headings and tables. Also reads scanned pages.",
  gpt5mini: "Markdown through OpenAI. Also reads scanned pages.",
};

const isPdf = (file: File) => file.type === "application/pdf" || /\.pdf$/i.test(file.name);

const formatSize = (bytes: number) =>
  bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

export default function UploadForm({ parsers, onUploaded }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [parser, setParser] = useState<ParserName>("pypdf");
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // If the chosen parser turns out to be unavailable, fall back to the first one that works.
  useEffect(() => {
    const chosen = parsers.find((p) => p.name === parser);
    if (chosen && !chosen.available) {
      const fallback = parsers.find((p) => p.available);
      if (fallback) setParser(fallback.name);
    }
  }, [parsers, parser]);

  function addFiles(incoming: File[]) {
    const pdfs = incoming.filter(isPdf);
    const skipped = incoming.length - pdfs.length;
    setError(skipped > 0 ? `${skipped} file(s) skipped: only PDF files are accepted.` : null);
    setFiles((current) => {
      const known = new Set(current.map((f) => `${f.name}:${f.size}:${f.lastModified}`));
      return [...current, ...pdfs.filter((f) => !known.has(`${f.name}:${f.size}:${f.lastModified}`))];
    });
  }

  function onPick(event: ChangeEvent<HTMLInputElement>) {
    addFiles(Array.from(event.target.files ?? []));
    event.target.value = ""; // allow picking the same file again later
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    addFiles(Array.from(event.dataTransfer.files));
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (files.length === 0 || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await uploadDocuments(files, parser);
      setFiles([]);
      await onUploaded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="upload" onSubmit={onSubmit}>
      <label
        className={`dropzone${dragging ? " dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <input type="file" accept="application/pdf,.pdf" multiple onChange={onPick} />
        <strong>Drop PDF files here</strong>
        <span>or click to choose one or more</span>
      </label>

      {files.length > 0 && (
        <ul className="files" aria-label="Selected files">
          {files.map((file, index) => (
            <li key={`${file.name}-${index}`}>
              <span className="file-name">{file.name}</span>
              <span className="muted">{formatSize(file.size)}</span>
              <button
                type="button"
                className="link"
                onClick={() => setFiles(files.filter((_, i) => i !== index))}
                aria-label={`Remove ${file.name}`}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      <fieldset className="parsers">
        <legend>Parser</legend>
        {parsers.map((p) => (
          <label key={p.name} className={`option${p.available ? "" : " disabled"}`}>
            <input
              type="radio"
              name="parser"
              value={p.name}
              checked={parser === p.name}
              disabled={!p.available}
              onChange={() => setParser(p.name)}
            />
            <span>
              <strong>{p.label}</strong>
              <small>{p.available ? HINTS[p.name] : (p.reason ?? "Unavailable")}</small>
            </span>
          </label>
        ))}
      </fieldset>

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      <button type="submit" className="primary" disabled={files.length === 0 || submitting}>
        {submitting ? "Uploading…" : files.length > 1 ? `Upload ${files.length} files` : "Upload"}
      </button>
    </form>
  );
}
