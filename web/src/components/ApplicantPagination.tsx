import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { fmt } from "../lib";
export default function ApplicantPagination({
  loading,
  page,
  size,
  total,
  update,
}: {
  loading: boolean;
  page: number;
  size: number;
  total: number;
  update: (changes: Record<string, string>, reset?: boolean) => void;
}) {
  const [jump, setJump] = useState("");
  const pages = Math.max(1, Math.ceil(total / size)),
    numbers = [
      ...new Set([
        1,
        ...Array.from({ length: 5 }, (_, i) => page - 2 + i).filter(
          (n) => n > 1 && n < pages,
        ),
        pages,
      ]),
    ].sort((a, b) => a - b);
  return (
    <div className="pagination">
      <div className="pagination-summary" role="status">
        {loading
          ? "Loading applicants…"
          : `${fmt(total ? (page - 1) * size + 1 : 0)}–${fmt(Math.min(page * size, total))} of ${fmt(total)} applicants`}
        <label>
          Per page
          <select
            value={size}
            onChange={(e) => update({ size: e.target.value })}
          >
            {[25, 50, 100].map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
      </div>
      <nav aria-label="Applicant pages">
        <button
          disabled={loading || page <= 1}
          onClick={() => update({ page: "1" }, false)}
        >
          First
        </button>
        <button
          aria-label="Previous page"
          disabled={loading || page <= 1}
          onClick={() => update({ page: String(page - 1) }, false)}
        >
          <ChevronLeft size={16} />
        </button>
        {numbers.map((n, i) => (
          <span key={n}>
            {i > 0 && n - numbers[i - 1] > 1 && (
              <span className="ellipsis">…</span>
            )}
            <button
              aria-current={n === page ? "page" : undefined}
              disabled={loading}
              onClick={() => update({ page: String(n) }, false)}
            >
              {n}
            </button>
          </span>
        ))}
        <button
          aria-label="Next page"
          disabled={loading || page >= pages}
          onClick={() => update({ page: String(page + 1) }, false)}
        >
          <ChevronRight size={16} />
        </button>
        <button
          disabled={loading || page >= pages}
          onClick={() => update({ page: String(pages) }, false)}
        >
          Last
        </button>
      </nav>
      <form
        className="page-jump"
        onSubmit={(e) => {
          e.preventDefault();
          update(
            {
              page: String(Math.max(1, Math.min(pages, Number(jump) || 1))),
            },
            false,
          );
          setJump("");
        }}
      >
        <label htmlFor="jump">Go to page</label>
        <input
          id="jump"
          inputMode="numeric"
          type="number"
          min="1"
          max={pages}
          value={jump}
          onChange={(e) => setJump(e.target.value)}
        />
        <button disabled={loading}>Go</button>
      </form>
    </div>
  );
}
