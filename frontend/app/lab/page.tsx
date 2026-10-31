"use client";
import { useState } from "react";
import {
  runScenario,
  type Scenario,
  type ScenarioResult,
} from "../../lib/scenarios";
import { money } from "../../lib/demo";
const scenarios: { id: Scenario; title: string; copy: string }[] = [
  {
    id: "replay",
    title: "Duplicate request",
    copy: "Send the same transfer twice. Does the money move twice?",
  },
  {
    id: "conflict",
    title: "Conflicting key",
    copy: "Reuse an idempotency key with a different amount.",
  },
  {
    id: "overdraft",
    title: "Insufficient funds",
    copy: "Try to spend funds reserved by a card authorisation.",
  },
  {
    id: "fx",
    title: "Cross-currency posting",
    copy: "Follow one conversion across five balanced entries.",
  },
];
export default function FailureLab() {
  const [result, setResult] = useState<ScenarioResult | null>(null);
  return (
    <main className="standalone-page">
      <header className="section-heading">
        <a className="brand" href="/">
          ledger.
        </a>
        <div className="route-links">
          <a href="/">Playground</a>
          <a href="/live">Live API</a>
        </div>
      </header>
      <div className="page-heading">
        <div>
          <span className="eyebrow">FAILURE LAB</span>
          <h1>Good systems expect bad days.</h1>
          <p>
            Run repeatable experiments. Inspect what changes, and what stays
            protected.
          </p>
        </div>
      </div>
      <div className="simulation-note">
        These experiments run the in-memory demo adapter. Each starts from a
        fresh fixture; no API or database is modified.
      </div>
      <section className="architecture-cards">
        {scenarios.map((s) => (
          <article className="panel" key={s.id}>
            <span className="eyebrow">EXECUTABLE SCENARIO</span>
            <h2>{s.title}</h2>
            <p>{s.copy}</p>
            <button
              className="button"
              onClick={() => setResult(runScenario(s.id))}
            >
              Run {s.title.toLowerCase()}
            </button>
          </article>
        ))}
      </section>
      {result && (
        <section
          className="panel scenario-result"
          aria-label="Scenario result"
          aria-live="polite"
        >
          <span className="eyebrow">EXPERIMENT RESULT</span>
          <h2>{result.title}</h2>
          <ol>
            {result.steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          <div className="scenario-metrics">
            <div>
              Sender balance before
              <strong>{money(result.before, "NGN")}</strong>
            </div>
            <div>
              Sender balance after<strong>{money(result.after, "NGN")}</strong>
            </div>
            <div>
              Posting count
              <strong>
                {result.postingsBefore} → {result.postingsAfter}
              </strong>
            </div>
          </div>
          <code>{result.code}</code>
          <div className="balance-proof">
            {Object.entries(result.totals).map(([currency, total]) => (
              <p key={currency}>
                {currency}: sum = {total}
              </p>
            ))}
          </div>
        </section>
      )}
      <section className="panel scenario-result">
        <span className="eyebrow">BACKEND FAILURE COVERAGE</span>
        <h2>Beyond the browser</h2>
        <p>
          The PostgreSQL suite exercises signed webhook rejection, duplicate
          provider events, failed payout reversals, hold expiry, refund limits,
          database immutability, and concurrent transfers. A separate worker
          smoke check exercises Redis/Celery delivery. These are backend checks;
          the browser experiments above do not simulate a real worker.
        </p>
        <div className="route-links">
          <a href="/live">Inspect real database postings →</a>
          <a
            href="https://github.com/Brownie-08/Ledger"
            target="_blank"
            rel="noreferrer"
          >
            Source and verification evidence ↗
          </a>
        </div>
      </section>
    </main>
  );
}
