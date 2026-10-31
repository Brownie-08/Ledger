"use client";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import { money, parseAmount, type Currency } from "../../lib/demo";

type Wallet = {
  id: number;
  currency: Currency;
  balance: number;
  available_balance: number;
  held_amount: number;
};
type Posting = {
  transaction_id: number;
  type: string;
  status: string;
  reference: string;
  created_at: string;
  entries: {
    account_id: number;
    amount: number;
    currency: Currency;
    label: string;
  }[];
  totals: Record<string, number>;
};
type Page = {
  results: Posting[];
  next: string | null;
  previous: string | null;
};
export default function LivePage() {
  const [authenticated, setAuthenticated] = useState(false);
  const [busy, setBusy] = useState(false);
  const [wallets, setWallets] = useState<Wallet[]>([]);
  const [postings, setPostings] = useState<Posting[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [selected, setSelected] = useState<Posting | null>(null);
  const [notice, setNotice] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [source, setSource] = useState("");
  const [destination, setDestination] = useState("");
  const [amount, setAmount] = useState("100.00");
  const [reference, setReference] = useState("Live demo transfer");
  const [key, setKey] = useState("");
  const [search, setSearch] = useState("");
  const load = useCallback(async () => {
    const [{ data: walletData }, { data: page }] = await Promise.all([
      api<Wallet[]>("/api/backend/wallets/"),
      api<Page>("/api/backend/transactions/"),
    ]);
    setWallets(walletData);
    setPostings(page.results);
    setNext(page.next);
    setAuthenticated(true);
    setKey((value) => value || crypto.randomUUID());
    setSource((value) => value || String(walletData[0]?.id || ""));
  }, []);
  useEffect(() => {
    load().catch((error) => {
      if (!(error instanceof ApiError && error.status === 401))
        setNotice(error.message);
    });
  }, [load]);
  async function login(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice("");
    try {
      await api("/api/session", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      setPassword("");
      await load();
      setKey(crypto.randomUUID());
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function send(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice("");
    try {
      const result = await api<{ transaction_id: number }>(
        "/api/backend/transfers/",
        {
          method: "POST",
          headers: { "Idempotency-Key": key },
          body: JSON.stringify({
            source_wallet: Number(source),
            destination_wallet: Number(destination),
            amount: parseAmount(amount),
            reference,
          }),
        },
      );
      const detail = await api<Posting>(
        `/api/backend/transactions/${result.data.transaction_id}/`,
      );
      setSelected(detail.data);
      await load();
      setNotice(
        result.replayed
          ? `Transaction #${result.data.transaction_id} replayed. No duplicate posting.`
          : `Transaction #${result.data.transaction_id} committed to PostgreSQL.`,
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 401)
        setAuthenticated(false);
      setNotice((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function more() {
    if (!next) return;
    setBusy(true);
    try {
      const page = await api<Page>(
        `/api/backend/transactions/${new URL(next).search}`,
      );
      setPostings((p) => [...p, ...page.data.results]);
      setNext(page.data.next);
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const filtered = postings.filter((p) =>
    `${p.transaction_id} ${p.reference} ${p.type}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <main className="standalone-page">
      <header className="section-heading">
        <a className="brand" href="/">
          ledger.
        </a>
        <div className="route-links">
          <a href="/">Playground</a>
          <a href="/lab">Failure lab</a>
          <a href="/live" aria-current="page">
            Live API
          </a>
        </div>
      </header>
      <div className="page-heading">
        <div>
          <span className="eyebrow">POSTGRESQL WORKSPACE</span>
          <h1>Your ledger, connected.</h1>
          <p>
            Authenticated requests to the Django API. Mock payment rails; real
            database postings.
          </p>
        </div>
        {authenticated && (
          <button
            className="button secondary"
            onClick={async () => {
              await api("/api/session", { method: "DELETE" });
              setAuthenticated(false);
              setWallets([]);
              setPostings([]);
              setSelected(null);
              setUsername("");
              setNotice("Signed out. Local session cookies removed.");
            }}
          >
            Sign out
          </button>
        )}
      </div>
      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
      {!authenticated ? (
        <section className="panel login-panel">
          <h2>Connect your account</h2>
          <p>
            Use a seeded demo user or an account created through the API. Tokens
            stay in HTTP-only cookies.
          </p>
          <form onSubmit={login}>
            <label htmlFor="username">Username</label>
            <input
              id="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              maxLength={150}
            />
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              maxLength={256}
            />
            <button className="button" disabled={busy}>
              {busy ? "Connecting…" : "Sign in"}
            </button>
          </form>
          <a href="/">Explore without signing in →</a>
        </section>
      ) : (
        <>
          <section className="wallet-grid" aria-label="Live wallets">
            {wallets.map((w) => (
              <article className="panel live-wallet" key={w.id}>
                <h2>
                  {w.currency} wallet <small>#{w.id}</small>
                </h2>
                <span className="balance-label">Available balance</span>
                <strong className="wallet-amount">
                  {money(w.available_balance, w.currency)}
                </strong>
                <p>
                  Posted {money(w.balance, w.currency)} · Held{" "}
                  {money(w.held_amount, w.currency)}
                </p>
              </article>
            ))}
            {!wallets.length && (
              <div className="panel live-wallet">
                <h2>No wallets yet</h2>
                <p>Create an NGN wallet to get started.</p>
                <button
                  className="button"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await api("/api/backend/wallets/", {
                        method: "POST",
                        headers: { "Idempotency-Key": crypto.randomUUID() },
                        body: JSON.stringify({ currency: "NGN" }),
                      });
                      await load();
                    } catch (error) {
                      setNotice((error as Error).message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Create NGN wallet
                </button>
              </div>
            )}
          </section>
          <div className="dashboard-grid">
            <section className="panel live-postings">
              <div className="panel-heading">
                <h2>Live ledger</h2>
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() =>
                    load().catch((error) => setNotice(error.message))
                  }
                >
                  Refresh
                </button>
              </div>
              <div className="activity-tools">
                <input
                  aria-label="Search live ledger"
                  placeholder="Search loaded transactions…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              {filtered.map((p) => (
                <button
                  className="posting-item"
                  key={p.transaction_id}
                  onClick={() => setSelected(p)}
                >
                  <span className="posting-item-top">
                    <strong>
                      #{p.transaction_id} · {p.reference || p.type}
                    </strong>
                    <span className="posted">{p.status}</span>
                  </span>
                  <small>
                    {p.type} · {p.entries.length} entries ·{" "}
                    {new Date(p.created_at).toLocaleString()}
                  </small>
                </button>
              ))}
              {!filtered.length && (
                <div className="empty">No matching transactions.</div>
              )}
              {next && (
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={more}
                >
                  Load more
                </button>
              )}
              {selected && (
                <section
                  className="live-detail"
                  aria-label="Live transaction details"
                >
                  <h2>Transaction #{selected.transaction_id}</h2>
                  <div className="entry-flow">
                    {selected.entries.map((e, i) => (
                      <div className="entry-node" key={i}>
                        <div>
                          <strong>{e.label}</strong>
                          <small>
                            Account #{e.account_id} · {e.currency}
                          </small>
                        </div>
                        <div className="entry-value">
                          <b>
                            {e.amount > 0 ? "+" : ""}
                            {money(e.amount, e.currency)}
                          </b>
                          <small>{e.amount} minor units</small>
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="balance-proof">
                    {Object.entries(selected.totals).map(
                      ([currency, total]) => (
                        <p key={currency}>
                          {currency} sum = {total} ·{" "}
                          {total === 0 ? "Balanced" : "Unbalanced"}
                        </p>
                      ),
                    )}
                  </div>
                </section>
              )}
            </section>
            <section className="panel transfer-panel">
              <div className="panel-heading">
                <div>
                  <h2>Post a transfer</h2>
                  <p>This action writes to the connected database.</p>
                </div>
              </div>
              <form onSubmit={send}>
                <label htmlFor="live-source">From wallet</label>
                <select
                  id="live-source"
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                  required
                >
                  <option value="">Choose a wallet</option>
                  {wallets.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.currency} · #{w.id}
                    </option>
                  ))}
                </select>
                <label htmlFor="live-destination">Destination wallet ID</label>
                <input
                  id="live-destination"
                  inputMode="numeric"
                  pattern="[0-9]+"
                  value={destination}
                  onChange={(e) => setDestination(e.target.value)}
                  required
                />
                <label htmlFor="live-amount">Amount in source currency</label>
                <input
                  id="live-amount"
                  inputMode="decimal"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  required
                />
                <label htmlFor="live-reference">Reference</label>
                <input
                  id="live-reference"
                  maxLength={120}
                  value={reference}
                  onChange={(e) => setReference(e.target.value)}
                />
                <label htmlFor="live-key">Idempotency key</label>
                <input
                  id="live-key"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  required
                  maxLength={128}
                />
                <button
                  className="text-button"
                  type="button"
                  onClick={() => setKey(crypto.randomUUID())}
                >
                  Generate new key
                </button>
                <p className="field-hint">
                  Reuse the same key only to retry the same request. Fees and
                  KYC limits are enforced by the API.
                </p>
                <button
                  className="button send-button"
                  disabled={busy || !wallets.length}
                >
                  {busy ? "Working…" : "Post live transfer"}
                </button>
              </form>
            </section>
          </div>
        </>
      )}
    </main>
  );
}
