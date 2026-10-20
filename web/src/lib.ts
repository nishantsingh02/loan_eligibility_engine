export const API =
  import.meta.env.VITE_API_BASE_URL ??
  (import.meta.env.DEV ? "http://localhost:8000" : "");
export const fmt = (n: number) => new Intl.NumberFormat("en-IN").format(n || 0);
export const date = (v: string) =>
  new Date(v).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
export const labels: Record<string, string> = {
  matched: "Matched",
  review: "Needs review",
  unmatched: "No match",
  processing: "Processing",
  queued: "Queued",
  completed: "Completed",
  failed: "Failed",
  eligible: "Eligible",
  ineligible: "Not eligible",
};
export const errors: Record<string, string> = {
  empty_csv: "The CSV has no applicant rows.",
  invalid_csv: "Check the CSV formatting and upload it again.",
  invalid_csv_schema:
    "Use the required columns shown below, then upload the corrected CSV.",
  invalid_encoding: "Save the CSV as UTF-8 and upload it again.",
  invalid_row:
    "Check for missing or invalid applicant values, then upload the corrected CSV.",
  too_many_rows: "Use a CSV with at most 100,000 applicants.",
  matching_failed:
    "Matching could not finish. Retry to continue from the last saved batch.",
  workflow_unavailable:
    "Matching is unavailable. Retry when the service is restored.",
  import_failed: "Import could not finish. Check the CSV and upload it again.",
  file_too_large: "The CSV exceeds the upload limit.",
};
