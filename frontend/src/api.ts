import type { DocumentBrief, DocumentDetail, ParserInfo, ParserName } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

// FastAPI answers errors with {"detail": "text"} or, for validation errors,
// {"detail": [{"msg": "..."}]}.
function describe(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (d as { msg?: string }).msg ?? "Invalid request").join("; ");
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError("Cannot reach the server", 0);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(describe(body, response.statusText || "Request failed"), response.status);
  }
  return response.json() as Promise<T>;
}

export const getParsers = () => request<ParserInfo[]>("/parsers");
export const listDocuments = () => request<DocumentBrief[]>("/documents");
export const getDocument = (id: string) => request<DocumentDetail>(`/documents/${id}`);

export function uploadDocuments(files: File[], parser: ParserName) {
  const form = new FormData();
  form.append("parser", parser);
  files.forEach((file) => form.append("files", file));
  return request<{ documents: Pick<DocumentBrief, "id" | "filename" | "parser" | "status">[] }>(
    "/documents",
    { method: "POST", body: form },
  );
}
