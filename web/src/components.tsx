import type { ReactNode } from "react";
import { LoaderCircle } from "lucide-react";
import { labels } from "./lib";
export function Status({ value }: { value: string }) {
  return <span className={`status ${value}`}>{labels[value] || value}</span>;
}
export function Heading({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="heading">
      <h1>{title}</h1>
      <p>{children}</p>
    </div>
  );
}
export function InitialState({ error }: { error: string }) {
  return (
    <div className="panel empty" role="status">
      {error ? (
        <>
          <h2>Workspace unavailable</h2>
          <p>Use Try again above to reconnect.</p>
        </>
      ) : (
        <>
          <LoaderCircle className="spinner" />
          <h2>Loading your workspace…</h2>
          <p>Fetching analysis files and lender policies.</p>
        </>
      )}
    </div>
  );
}
