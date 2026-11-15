import { useCallback, useEffect, useState } from "react";
import { getParsers, listDocuments } from "./api";
import DocumentCard from "./components/DocumentCard";
import UploadForm from "./components/UploadForm";
import type { DocumentBrief, ParserInfo } from "./types";

const POLL_ACTIVE_MS = 1500; // while something is being processed
const POLL_IDLE_MS = 10000; // otherwise, so documents added via curl show up too

export default function App() {
  const [parsers, setParsers] = useState<ParserInfo[]>([]);
  const [documents, setDocuments] = useState<DocumentBrief[] | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDocuments(await listDocuments());
      setConnectionError(null);
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : "Request failed");
    }
  }, []);

  useEffect(() => {
    getParsers()
      .then(setParsers)
      .catch(() => setConnectionError("Cannot reach the server"));
    void refresh();
  }, [refresh]);

  const active = documents?.some((d) => d.status === "queued" || d.status === "processing");
  useEffect(() => {
    const timer = setInterval(() => void refresh(), active ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    return () => clearInterval(timer);
  }, [active, refresh]);

  const labels = Object.fromEntries(parsers.map((p) => [p.name, p.label]));

  return (
    <main className="page">
      <header className="masthead">
        <h1>Document Parser</h1>
        <p>Upload PDFs, pick a parser, and get the text of every page plus a summary.</p>
      </header>

      {connectionError && (
        <div className="banner banner-error" role="alert">
          {connectionError}. Retrying…
        </div>
      )}

      <UploadForm parsers={parsers} onUploaded={refresh} />

      <section aria-labelledby="documents-heading">
        <h2 id="documents-heading">Documents</h2>
        {documents === null ? (
          <p className="muted">Loading…</p>
        ) : documents.length === 0 ? (
          <p className="muted empty">Nothing here yet. Upload a PDF above to get started.</p>
        ) : (
          <div className="list">
            {documents.map((doc) => (
              <DocumentCard key={doc.id} doc={doc} parserLabel={labels[doc.parser] ?? doc.parser} />
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
