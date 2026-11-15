import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getDocument } from "../api";
import type { DocumentBrief, DocumentDetail, DocumentStatus } from "../types";

interface Props {
  doc: DocumentBrief;
  parserLabel: string;
}

const STATUS_LABEL: Record<DocumentStatus, string> = {
  queued: "Queued",
  processing: "Processing",
  done: "Done",
  failed: "Failed",
};

export default function DocumentCard({ doc, parserLabel }: Props) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [loadError, setLoadError] = useState(false);

  // The list omits page contents; fetch them once the document is done.
  useEffect(() => {
    if (doc.status !== "done") return;
    let cancelled = false;
    setLoadError(false);
    getDocument(doc.id)
      .then((full) => !cancelled && setDetail(full))
      .catch(() => !cancelled && setLoadError(true));
    return () => {
      cancelled = true;
    };
  }, [doc.id, doc.status, doc.updated_at]);

  const busy = doc.status === "queued" || doc.status === "processing";
  const meta = [
    parserLabel,
    doc.status === "done" ? `${doc.page_count} page${doc.page_count === 1 ? "" : "s"}` : null,
    new Date(doc.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
  ].filter(Boolean);

  return (
    <article className="card">
      <header className="card-head">
        <div>
          <h3>{doc.filename}</h3>
          <p className="muted">{meta.join(" · ")}</p>
        </div>
        <span className={`badge badge-${doc.status}`}>
          {busy && <span className="spinner" aria-hidden="true" />}
          {STATUS_LABEL[doc.status]}
        </span>
      </header>

      {busy && <p className="muted">This usually takes a few seconds; longer documents take more.</p>}

      {doc.status === "failed" && (
        <p className="notice notice-error" role="alert">
          {doc.error ?? "Processing failed."}
        </p>
      )}

      {doc.status === "done" && (
        <>
          <section className="summary" aria-label="Summary">
            <h4>Summary</h4>
            {doc.summary ? (
              <p>{doc.summary}</p>
            ) : (
              <p className="notice">{doc.summary_error ?? "Summary unavailable."}</p>
            )}
          </section>

          {loadError && <p className="notice notice-error">Could not load the pages.</p>}
          {!detail && !loadError && <p className="muted">Loading pages…</p>}
          {detail && (
            <div className="pages">
              {detail.pages.map((page) => (
                <details key={page.number}>
                  <summary>Page {page.number}</summary>
                  {!page.content ? (
                    <p className="muted">No text on this page.</p>
                  ) : doc.parser === "pypdf" ? (
                    <pre>{page.content}</pre>
                  ) : (
                    <div className="markdown">
                      <Markdown remarkPlugins={[remarkGfm]}>{page.content}</Markdown>
                    </div>
                  )}
                </details>
              ))}
            </div>
          )}
        </>
      )}
    </article>
  );
}
