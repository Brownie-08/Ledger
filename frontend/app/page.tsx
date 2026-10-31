"use client";

import { useState } from "react";
import {
  balance,
  convert,
  createDemo,
  currencyTotals,
  DemoError,
  feeFor,
  money,
  parseAmount,
  postTransfer,
  type Currency,
  type Posting,
} from "../lib/demo";

type View = "overview" | "explorer" | "architecture";
const symbols: Record<Currency, string> = { NGN: "₦", USD: "$", GBP: "£" };
function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, string> = {
    grid: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
    ledger: "M5 3h14v18H5zM9 7h6M9 12h6M9 17h4",
    arrow: "M5 12h14m-5-5 5 5-5 5",
    transfer: "M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4",
    check: "m5 12 4 4L19 6",
    code: "m8 5-6 7 6 7m8-14 6 7-6 7m-3-16-2 18",
    search: "M16 16l5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
    reset: "M3 10a9 9 0 1 1 1 8M3 3v7h7",
    play: "m8 4 12 8-12 8z",
    lock: "M5 10h14v11H5zM8 10V6a4 4 0 0 1 8 0v4",
    down: "m7 10 5 5 5-5",
    close: "m6 6 12 12M6 18 18 6",
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name] || paths.ledger} />
    </svg>
  );
}
const date = (value: string) =>
  new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Africa/Lagos",
  }).format(new Date(value));

export default function Page() {
  const [state, setState] = useState(createDemo);
  const [view, setView] = useState<View>("overview");
  const [currency, setCurrency] = useState<Currency>("NGN");
  const [search, setSearch] = useState("");
  const [type, setType] = useState("all");
  const [selected, setSelected] = useState("TX-1044");
  const [source, setSource] = useState(1);
  const [destination, setDestination] = useState(4);
  const [amount, setAmount] = useState("10000.00");
  const [reference, setReference] = useState("Coffee & collaboration");
  const [key, setKey] = useState("demo-transfer-001");
  const [notice, setNotice] = useState<{
    text: string;
    error?: boolean;
  } | null>(null);
  const [guide, setGuide] = useState(0);
  const [guidePosting, setGuidePosting] = useState<string | null>(null);
  const [resetConfirm, setResetConfirm] = useState(false);
  const ownWallets = state.accounts.filter((a) => a.owned);
  const from = state.accounts.find((a) => a.id === source)!;
  const to = state.accounts.find((a) => a.id === destination)!;
  const chosenWallet = ownWallets.find((a) => a.currency === currency)!;
  const posting =
    state.postings.find((p) => p.id === selected) ?? state.postings[0];
  let minor = 0;
  try {
    minor = parseAmount(amount);
  } catch {
    /* Explained on submit. */
  }
  const fee = feeFor(minor);
  const results = [...state.postings]
    .reverse()
    .filter(
      (p) =>
        (view !== "overview" ||
          p.entries.some((e) => e.account === chosenWallet.id)) &&
        (type === "all" || p.type === type) &&
        `${p.id} ${p.reference} ${p.key} ${p.type} ${p.entries.map((e) => e.currency).join(" ")}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    );
  function navigate(next: View) {
    setView(next);
    setSearch("");
    setType("all");
  }
  function inspect(p: Posting) {
    setSelected(p.id);
    navigate("explorer");
  }
  function submit() {
    try {
      const result = postTransfer(
        state,
        { source, destination, amount: parseAmount(amount), reference },
        key,
      );
      setState(result.state);
      setSelected(result.posting.id);
      setNotice({
        text: result.replayed
          ? `${result.posting.id} replayed. Same result, no new entries or balance changes.`
          : `${result.posting.id} posted. ${money(parseAmount(amount), from.currency)} sent; ${money(fee, from.currency)} fee. Inspect it in Ledger Explorer.`,
      });
      return result.posting;
    } catch (error) {
      setNotice({
        text:
          error instanceof DemoError
            ? `${error.code}: ${error.message}`
            : "The demo could not complete this request.",
        error: true,
      });
    }
  }
  function replay(p: Posting) {
    if (!p.request) return;
    const result = postTransfer(state, p.request, p.key);
    setState(result.state);
    setNotice({
      text: `${p.id} replayed. Same result, no new entries or balance changes.`,
    });
  }
  function startGuide() {
    setSource(1);
    setDestination(4);
    setAmount("10000.00");
    setReference("Guided demo transfer");
    setKey(`guide-${state.postings.length}`);
    setGuidePosting(null);
    setGuide(1);
    setNotice(null);
    navigate("overview");
  }
  function advanceGuide() {
    if (guide === 1) {
      const p = submit();
      if (p) {
        setGuidePosting(p.id);
        setGuide(2);
      }
    } else if (guide === 2) {
      if (guidePosting) setSelected(guidePosting);
      navigate("explorer");
      setGuide(3);
    } else if (guide === 3) {
      const p = state.postings.find((p) => p.id === guidePosting);
      if (p) replay(p);
      setGuide(4);
    } else setGuide(0);
  }
  function reset() {
    setState(createDemo());
    setSelected("TX-1044");
    setSource(1);
    setDestination(4);
    setAmount("10000.00");
    setReference("Coffee & collaboration");
    setKey("demo-transfer-001");
    setGuide(0);
    setGuidePosting(null);
    setResetConfirm(false);
    setNotice({
      text: "Demo reset. Original sample balances and postings restored.",
    });
    navigate("overview");
    setCurrency("NGN");
  }
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Ledger home">
          <span className="brand-mark">
            <i />
            <i />
            <i />
          </span>
          ledger<span className="brand-dot">.</span>
        </a>
        <div className="workspace">
          <span className="workspace-icon">L</span>
          <div>
            <strong>Personal workspace</strong>
            <small>Portfolio sandbox</small>
          </div>
          <span className="status-dot" />
        </div>
        <div className="nav-caption">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {(
            [
              ["overview", "grid", "Overview"],
              ["explorer", "ledger", "Ledger Explorer"],
              ["architecture", "code", "Under the hood"],
            ] as const
          ).map(([next, icon, title]) => (
            <button
              key={next}
              aria-current={view === next ? "page" : undefined}
              onClick={() => navigate(next)}
            >
              <Icon name={icon} />
              {title}
              {next === "explorer" && (
                <span className="nav-count">{state.postings.length}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="route-links sidebar-routes">
          <a href="/live">Live API workspace ↗</a>
          <a href="/lab">Failure lab ↗</a>
        </div>
        <div className="sidebar-bottom">
          <div className="demo-help">
            <span className="eyebrow">BUILT TO BE EXPLORED</span>
            <strong>Follow the money.</strong>
            <p>Make a transfer. See every entry. Replay the request.</p>
            <button onClick={startGuide}>
              Start guided demo <Icon name="arrow" size={16} />
            </button>
          </div>
          <div className="profile">
            <span className="avatar">AM</span>
            <div>
              <strong>Alex Morgan</strong>
              <small>Demo account</small>
            </div>
            <span className="tier">T1</span>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div>
            <span className="muted">Workspace</span>
            <span className="breadcrumb">/</span>
            {view === "overview"
              ? "Overview"
              : view === "explorer"
                ? "Ledger Explorer"
                : "Under the hood"}
          </div>
          <div className="topbar-right">
            <span className="demo-badge">
              <span className="status-dot" /> DEMO MODE
            </span>
            <span className="avatar small">AM</span>
          </div>
        </header>
        <main id="main">
          <div className="page-heading">
            <div>
              <div className="eyebrow">THE WALLET PLAYGROUND</div>
              <h1>
                {view === "overview"
                  ? "Every move, accounted for."
                  : view === "explorer"
                    ? "Follow every entry."
                    : "Designed for correctness."}
              </h1>
              <p>
                {view === "overview"
                  ? "A clear view of your wallets and the ledger behind them."
                  : view === "explorer"
                    ? "From a single request to a balanced set of immutable entries."
                    : "Explore the engineering decisions behind the Django ledger."}
              </p>
            </div>
            <button
              className="button secondary"
              onClick={() => setResetConfirm(!resetConfirm)}
            >
              <Icon name="reset" size={16} />
              Reset demo
            </button>
          </div>
          <div className="simulation-note">
            <span className="status-dot" />
            <span>
              Interactive portfolio demo · All balances and requests are
              simulated in this tab. Refresh to start over.
            </span>
            <span className="no-money">No real money</span>
          </div>
          {resetConfirm && (
            <div
              className="confirm-bar"
              role="group"
              aria-label="Reset confirmation"
            >
              <span>
                Restore the sample data and discard this tab’s demo transfers?
              </span>
              <button className="button" onClick={reset}>
                Restore sample data
              </button>
              <button
                className="text-button"
                onClick={() => setResetConfirm(false)}
              >
                Cancel
              </button>
            </div>
          )}
          {notice && (
            <div
              className={`notice ${notice.error ? "error" : ""}`}
              role={notice.error ? "alert" : "status"}
            >
              <Icon name={notice.error ? "close" : "check"} size={18} />
              <span>{notice.text}</span>
              <button
                aria-label="Dismiss notification"
                onClick={() => setNotice(null)}
              >
                <Icon name="close" size={16} />
              </button>
            </div>
          )}
          {guide > 0 && (
            <section className="guide" aria-label="Guided demo">
              <div className="guide-step">
                0{Math.min(guide, 3)}
                <span>/ 03</span>
              </div>
              <div>
                <strong>
                  {
                    [
                      "",
                      "Send your first demo transfer",
                      "See what moved behind the scenes",
                      "Try the same request twice",
                      "One transfer. A complete audit trail.",
                    ][guide]
                  }
                </strong>
                <p>
                  {
                    [
                      "",
                      "Send ₦10,000 to Ada. A 0.5% fee becomes a third ledger entry.",
                      "Open the new transaction to inspect the sender, receiver, and fee account.",
                      "Replay its idempotency key. The original result returns and balances stay the same.",
                      "You’ve explored posting, zero-sum entries, and idempotent replay.",
                    ][guide]
                  }
                </p>
              </div>
              <button className="button" onClick={advanceGuide}>
                {
                  [
                    "",
                    "Post demo transfer",
                    "Inspect entries",
                    "Replay request",
                    "Finish walkthrough",
                  ][guide]
                }
                <Icon name="arrow" size={16} />
              </button>
              <button
                className="icon-button"
                aria-label="Close walkthrough"
                onClick={() => setGuide(0)}
              >
                <Icon name="close" size={16} />
              </button>
            </section>
          )}

          {view === "overview" && (
            <>
              <section className="hero">
                <div className="hero-copy">
                  <span className="eyebrow">
                    MONEY MOVES. THE LEDGER REMEMBERS.
                  </span>
                  <h2>
                    Simple on the surface.
                    <br />
                    Balanced underneath.
                  </h2>
                  <p>
                    Send a little. Explore a lot. See how a double-entry
                    <br className="desktop-br" /> ledger keeps every currency in
                    balance.
                  </p>
                  <button className="button hero-button" onClick={startGuide}>
                    <Icon name="play" size={15} />
                    Take the 60-second tour <Icon name="arrow" size={17} />
                  </button>
                </div>
                <div className="ledger-art" aria-hidden="true">
                  <div className="art-label">ONE TRANSFER · THREE ENTRIES</div>
                  {[
                    ["↗", "Sender’s wallet", "Debit", "−10,050.00"],
                    ["↙", "Ada’s wallet", "Credit", "+10,000.00"],
                    ["≋", "Fee account", "Credit", "+50.00"],
                  ].map(([icon, name, kind, value], i) => (
                    <div className={`art-node art-${i}`} key={name}>
                      <span className="art-icon">{icon}</span>
                      <div>
                        {name}
                        <small>{kind} · NGN</small>
                      </div>
                      <b>{value}</b>
                    </div>
                  ))}
                  <div className="art-proof">
                    <span>Σ entries = 0</span>
                    <span>
                      <Icon name="check" size={13} /> Balanced
                    </span>
                  </div>
                </div>
              </section>
              <div className="section-heading">
                <h2>
                  Your wallets <span className="count">03</span>
                </h2>
                <span className="muted caption">
                  Three currencies. One clear picture.
                </span>
              </div>
              <section className="wallet-grid" aria-label="Your wallets">
                {ownWallets.map((wallet) => (
                  <button
                    key={wallet.id}
                    className={`wallet-card ${currency === wallet.currency ? "selected" : ""}`}
                    aria-pressed={currency === wallet.currency}
                    onClick={() => {
                      setCurrency(wallet.currency);
                      setSearch("");
                    }}
                  >
                    <div className="wallet-top">
                      <span
                        className={`currency-icon ${wallet.currency.toLowerCase()}`}
                      >
                        {symbols[wallet.currency]}
                      </span>
                      <span>{wallet.currency} wallet</span>
                      <span className="wallet-check">
                        <Icon
                          name={
                            currency === wallet.currency ? "check" : "arrow"
                          }
                          size={15}
                        />
                      </span>
                    </div>
                    <span className="balance-label">Available balance</span>
                    <strong className="wallet-amount">
                      {money(
                        balance(state, wallet.id) - wallet.held,
                        wallet.currency,
                      )}
                    </strong>
                    <div className="wallet-bottom">
                      <span>
                        Balance{" "}
                        <b>
                          {money(balance(state, wallet.id), wallet.currency)}
                        </b>
                      </span>
                      <span>
                        Held <b>{money(wallet.held, wallet.currency)}</b>
                      </span>
                    </div>
                  </button>
                ))}
              </section>
              <div className="dashboard-grid">
                <section className="panel activity">
                  <div className="panel-heading">
                    <div>
                      <h2>Recent activity</h2>
                      <p>
                        {currency} wallet · Select a row to explore its entries
                      </p>
                    </div>
                    <button
                      className="text-button"
                      onClick={() => navigate("explorer")}
                    >
                      View ledger <Icon name="arrow" size={15} />
                    </button>
                  </div>
                  <div className="activity-tools">
                    <div className="search">
                      <Icon name="search" size={17} />
                      <input
                        aria-label="Search wallet activity"
                        placeholder="Search transactions…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                    </div>
                    <select
                      aria-label="Filter activity type"
                      value={type}
                      onChange={(e) => setType(e.target.value)}
                    >
                      <option value="all">All types</option>
                      <option value="transfer">Transfer</option>
                      <option value="deposit">Deposit</option>
                      <option value="fx">FX</option>
                    </select>
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Transaction</th>
                          <th>Amount</th>
                          <th>Status</th>
                          <th>
                            <span className="sr-only">Details</span>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {results.map((p) => {
                          const delta = p.entries
                            .filter((e) => e.account === chosenWallet.id)
                            .reduce((n, e) => n + e.amount, 0);
                          return (
                            <tr key={p.id}>
                              <td>
                                <button
                                  className="transaction-button"
                                  onClick={() => inspect(p)}
                                >
                                  <span
                                    className={`transaction-icon ${delta > 0 ? "incoming" : ""}`}
                                  >
                                    <Icon
                                      name={delta > 0 ? "down" : "transfer"}
                                      size={17}
                                    />
                                  </span>
                                  <span>
                                    <strong>{p.reference}</strong>
                                    <small>
                                      {p.id} · {date(p.created)} WAT
                                    </small>
                                  </span>
                                </button>
                              </td>
                              <td
                                className={`numeric ${delta > 0 ? "credit" : ""}`}
                              >
                                {delta > 0 ? "+" : ""}
                                {money(delta, currency)}
                              </td>
                              <td>
                                <span className="posted">Posted</span>
                              </td>
                              <td>
                                <button
                                  className="icon-button"
                                  aria-label={`Inspect ${p.id}`}
                                  onClick={() => inspect(p)}
                                >
                                  <Icon name="arrow" size={16} />
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    {!results.length && (
                      <div className="empty">
                        <Icon name="search" />
                        <strong>No matching transactions</strong>
                        <p>Try another search or type.</p>
                        <button
                          className="text-button"
                          onClick={() => {
                            setSearch("");
                            setType("all");
                          }}
                        >
                          Clear filters
                        </button>
                      </div>
                    )}
                  </div>
                  <div className="activity-footer">
                    <Icon name="lock" size={14} />
                    <span>
                      Posted entries are append-only. Corrections use new
                      entries.
                    </span>
                  </div>
                  <div className="held-note">
                    <strong>{money(chosenWallet.held, currency)} held</strong>
                    <span>
                      {chosenWallet.held
                        ? "Sample card authorisation. A hold reduces available funds without posting entries."
                        : "No active holds on this wallet."}
                    </span>
                  </div>
                </section>
                <section
                  className="panel transfer-panel"
                  aria-label="Send a demo transfer"
                >
                  <div className="panel-heading">
                    <div>
                      <h2>Make a move</h2>
                      <p>Try a transfer with sample funds.</p>
                    </div>
                    <span className="subtle-icon">
                      <Icon name="transfer" />
                    </span>
                  </div>
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      submit();
                    }}
                  >
                    <label htmlFor="source">From wallet</label>
                    <select
                      id="source"
                      value={source}
                      onChange={(e) => {
                        const id = Number(e.target.value);
                        setSource(id);
                        if (id === destination)
                          setDestination(id === 1 ? 4 : id === 2 ? 5 : 6);
                      }}
                    >
                      {ownWallets.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.currency} · Your wallet
                        </option>
                      ))}
                    </select>
                    <span className="field-hint">
                      Available:{" "}
                      {money(balance(state, source) - from.held, from.currency)}
                    </span>
                    <label htmlFor="destination">To wallet</label>
                    <select
                      id="destination"
                      value={destination}
                      onChange={(e) => setDestination(Number(e.target.value))}
                    >
                      {state.accounts
                        .filter((a) => a.kind === "wallet" && a.id !== source)
                        .map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name} · {a.currency}
                          </option>
                        ))}
                    </select>
                    <label htmlFor="amount">
                      Amount <span>{from.currency}</span>
                    </label>
                    <div className="amount-input">
                      <span>{symbols[from.currency]}</span>
                      <input
                        id="amount"
                        inputMode="decimal"
                        value={amount}
                        onChange={(e) => setAmount(e.target.value)}
                        required
                        maxLength={13}
                        autoComplete="off"
                      />
                    </div>
                    <label htmlFor="reference">Reference</label>
                    <input
                      id="reference"
                      value={reference}
                      onChange={(e) => setReference(e.target.value)}
                      maxLength={120}
                    />
                    <div className="fee-summary">
                      <div>
                        <span>
                          Transfer fee <small>(0.5%)</small>
                        </span>
                        <b>{money(fee, from.currency)}</b>
                      </div>
                      {from.currency !== to.currency && (
                        <>
                          <div>
                            <span>Recipient gets</span>
                            <b>
                              {money(
                                convert(minor, from.currency, to.currency),
                                to.currency,
                              )}
                            </b>
                          </div>
                          <small>
                            Fixed demo FX quote · rounded down to minor units
                          </small>
                        </>
                      )}
                      <div className="total">
                        <span>Total debit</span>
                        <b>{money(minor + fee, from.currency)}</b>
                      </div>
                    </div>
                    <details className="request-options">
                      <summary>Idempotency key</summary>
                      <label htmlFor="request-key" className="sr-only">
                        Idempotency key
                      </label>
                      <input
                        id="request-key"
                        value={key}
                        maxLength={100}
                        onChange={(e) => setKey(e.target.value)}
                      />
                      <button
                        type="button"
                        className="text-button"
                        onClick={() => setKey(`demo-${crypto.randomUUID()}`)}
                      >
                        Generate new key
                      </button>
                      <p>
                        Same key + same request replays the result. A changed
                        request returns a conflict.
                      </p>
                    </details>
                    <button className="button send-button" type="submit">
                      Send demo transfer <Icon name="arrow" size={17} />
                    </button>
                    <p className="form-footnote">
                      <Icon name="lock" size={12} />
                      Simulated funds. Real accounting principles.
                    </p>
                  </form>
                </section>
              </div>
            </>
          )}

          {view === "explorer" && (
            <>
              <div className="explorer-intro">
                <span className="subtle-icon">
                  <Icon name="ledger" />
                </span>
                <p>
                  <strong>The money trail, made visible.</strong> Each posting
                  balances independently in every currency. These are simulated
                  entries; PostgreSQL enforcement lives in the backend.
                </p>
                <span className="posted">{state.postings.length} postings</span>
              </div>
              <div className="explorer-grid">
                <section className="panel posting-list">
                  <div className="panel-heading">
                    <h2>Transactions</h2>
                    <span className="count">{results.length}</span>
                  </div>
                  <div className="explorer-filters">
                    <div className="search">
                      <Icon name="search" size={16} />
                      <input
                        aria-label="Search ledger"
                        placeholder="ID, reference, currency, key…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                    </div>
                    <select
                      aria-label="Filter ledger type"
                      value={type}
                      onChange={(e) => setType(e.target.value)}
                    >
                      <option value="all">All transaction types</option>
                      <option value="transfer">Transfer</option>
                      <option value="deposit">Deposit</option>
                      <option value="fx">FX</option>
                    </select>
                  </div>
                  <div className="posting-items">
                    {results.map((p) => (
                      <button
                        key={p.id}
                        className={`posting-item ${posting.id === p.id ? "active" : ""}`}
                        onClick={() => setSelected(p.id)}
                        aria-pressed={posting.id === p.id}
                      >
                        <span className="posting-item-top">
                          <code>{p.id}</code>
                          <span className="type-tag">
                            {p.type.toUpperCase()}
                          </span>
                        </span>
                        <strong>{p.reference}</strong>
                        <small>
                          {p.entries.length} entries · {date(p.created)} WAT
                        </small>
                      </button>
                    ))}
                    {!results.length && (
                      <div className="empty">
                        <strong>No matching postings</strong>
                        <p>Try another reference or currency.</p>
                        <button
                          className="text-button"
                          onClick={() => {
                            setSearch("");
                            setType("all");
                          }}
                        >
                          Clear filters
                        </button>
                      </div>
                    )}
                  </div>
                </section>
                <section
                  className="panel posting-detail"
                  aria-label="Selected transaction"
                >
                  <div className="panel-heading">
                    <div>
                      <span className="eyebrow">TRANSACTION DETAILS</span>
                      <h2 className="transaction-title">
                        {posting.id} <span className="posted">Posted</span>
                      </h2>
                      <p>
                        {posting.reference} · {date(posting.created)} WAT
                      </p>
                    </div>
                    <Icon name="lock" size={19} />
                  </div>
                  <div className="flow-heading">
                    <h3>The double-entry flow</h3>
                    <span>Signed amounts · minor units under the hood</span>
                  </div>
                  <div className="entry-flow">
                    {posting.entries.map((entry, i) => {
                      const account = state.accounts.find(
                        (a) => a.id === entry.account,
                      )!;
                      return (
                        <div
                          className={`entry-node ${entry.amount > 0 ? "positive" : "negative"}`}
                          key={`${entry.account}-${i}`}
                        >
                          <span className="entry-number">0{i + 1}</span>
                          <div>
                            <strong>{account.name}</strong>
                            <small>
                              {account.kind === "wallet"
                                ? "Wallet"
                                : account.kind === "cash"
                                  ? "System cash"
                                  : account.kind === "fees"
                                    ? "Fee account"
                                    : "FX position"}{" "}
                              · Account #{account.id} · {entry.currency}
                            </small>
                          </div>
                          <div className="entry-value">
                            <b>
                              {entry.amount > 0 ? "+" : ""}
                              {money(entry.amount, entry.currency)}
                            </b>
                            <small>
                              {entry.amount > 0 ? "Credit" : "Debit"} ·{" "}
                              {entry.amount.toLocaleString("en-GB")} minor
                            </small>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  <div className="balance-proof">
                    {Object.entries(currencyTotals(posting.entries)).map(
                      ([code, total]) => (
                        <div key={code}>
                          <span className="proof-check">
                            <Icon name="check" size={17} />
                          </span>
                          <div>
                            <strong>
                              {code} entries sum to {total}
                            </strong>
                            <small>Every debit has an equal credit.</small>
                          </div>
                          <span className="balanced-label">BALANCED</span>
                        </div>
                      ),
                    )}
                  </div>
                  <div className="posting-explanation">
                    <Icon name="code" size={18} />
                    <p>
                      {posting.type === "fx"
                        ? "FX uses separate system accounts in each currency. Each currency’s entries sum to zero; unlike currencies are never added together."
                        : posting.type === "deposit"
                          ? "A deposit credits the customer’s wallet and debits provider cash by the same amount."
                          : "The sender is debited for the transfer plus fee. The recipient and fee account receive matching credits."}
                    </p>
                  </div>
                  <details className="request-details">
                    <summary>
                      Inspect simulated request & entries{" "}
                      <Icon name="code" size={16} />
                    </summary>
                    <pre>
                      {JSON.stringify(
                        {
                          simulation: true,
                          idempotency_key: posting.key,
                          ...(posting.request
                            ? {
                                method: "POST",
                                path: "/api/transfers/",
                                body: {
                                  source_wallet: posting.request.source,
                                  destination_wallet:
                                    posting.request.destination,
                                  amount: posting.request.amount,
                                  reference: posting.request.reference,
                                },
                              }
                            : {
                                source:
                                  "Seeded opening balance; no API request sent",
                              }),
                          entries: posting.entries,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                  <div className="replay-panel">
                    <div>
                      <strong>One request. One result.</strong>
                      <p>
                        {posting.request
                          ? "Replay this key to return the existing posting without moving funds again."
                          : "Opening balances are fixtures. Select a transfer to try idempotent replay."}
                      </p>
                      <code>{posting.key}</code>
                    </div>
                    <button
                      className="button secondary"
                      disabled={!posting.request}
                      onClick={() => replay(posting)}
                    >
                      <Icon name="reset" size={15} />
                      Replay request
                    </button>
                  </div>
                </section>
              </div>
            </>
          )}

          {view === "architecture" && (
            <section className="architecture">
              <div className="architecture-banner">
                <span className="eyebrow">
                  A PORTFOLIO DEMO WITH A REAL BACKEND
                </span>
                <h2>
                  A wallet is a view.
                  <br />
                  The ledger is the source of truth.
                </h2>
                <p>
                  The frontend is an in-memory playground. The existing Django
                  API implements persistent posting, row locking, and database
                  guards in PostgreSQL.
                </p>
              </div>
              <div className="architecture-flow">
                {[
                  ["Request", "JWT ownership", "Idempotency key"],
                  [
                    "Ledger service",
                    "Integer minor units",
                    "Lock accounts in ID order",
                  ],
                  [
                    "PostgreSQL",
                    "Atomic entries + balances",
                    "Deferred zero-sum trigger",
                  ],
                  [
                    "Outbox",
                    "Event in the same commit",
                    "Celery delivery later",
                  ],
                ].map(([title, a, b], i) => (
                  <div key={title}>
                    <span>0{i + 1}</span>
                    <strong>{title}</strong>
                    <p>
                      {a}
                      <br />
                      {b}
                    </p>
                  </div>
                ))}
              </div>
              <div className="architecture-cards">
                {[
                  [
                    "Integer money",
                    "Kobo and cents stay integers. Decimal strings become minor units; fees round down, following the backend’s default fee rule.",
                    "ledger/services.py · compute_fee",
                  ],
                  [
                    "Balanced by currency",
                    "Every posting must sum to zero for each currency independently. FX moves through system accounts instead of adding unlike currencies.",
                    "ledger/services.py · post_transaction",
                  ],
                  [
                    "Append-only history",
                    "The backend rejects entry edits and deletes through model checks and PostgreSQL triggers. Corrections create reversing entries.",
                    "ledger/migrations/0002_ledger_db_guards.py",
                  ],
                  [
                    "Explicit demo boundaries",
                    "The playground keeps sample people, balances and quotes in this tab. The separate Live API workspace signs in to Django and reads actual PostgreSQL postings.",
                    "frontend/lib/demo.ts · in-memory adapter",
                  ],
                ].map(([title, text, file], i) => (
                  <article className="panel" key={title}>
                    <span className="eyebrow">0{i + 1} / DESIGN DECISION</span>
                    <h3>{title}</h3>
                    <p>{text}</p>
                    <code>{file}</code>
                  </article>
                ))}
              </div>
              <div className="next-phase">
                <Icon name="code" />
                <div>
                  <strong>Explore the connected workspace</strong>
                  <p>
                    The Live API workspace uses HTTP-only session cookies and
                    participant-scoped transaction views. The failure lab runs
                    repeatable browser experiments; PostgreSQL and worker
                    verification remain separate evidence.
                  </p>
                </div>
                <button className="button secondary" onClick={startGuide}>
                  Explore the demo <Icon name="arrow" size={16} />
                </button>
              </div>
            </section>
          )}
          <footer className="page-footer">
            <span>
              <span className="mini-brand">≋</span> Built on double-entry
              principles.
            </span>
            <span>
              Next.js frontend · Django + PostgreSQL backend · Portfolio demo
            </span>
          </footer>
        </main>
      </div>
    </div>
  );
}
