export type ParserName = "pypdf" | "gemini" | "gpt5mini";
export type DocumentStatus = "queued" | "processing" | "done" | "failed";

export interface ParserInfo {
  name: ParserName;
  label: string;
  available: boolean;
  reason: string | null;
}

export interface DocumentBrief {
  id: string;
  filename: string;
  parser: ParserName;
  status: DocumentStatus;
  error: string | null;
  summary: string | null;
  summary_error: string | null;
  page_count: number;
  created_at: string;
  updated_at: string;
}

export interface Page {
  number: number;
  content: string;
}

export interface DocumentDetail extends DocumentBrief {
  pages: Page[];
}
