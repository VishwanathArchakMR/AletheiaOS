import { useState, type FormEvent } from "react";
import { SystemStatus } from "./features/system-status/SystemStatus";
import {
  API_BASE_URL,
  knowledgeApi,
  type AnswerResponse,
  type EvidenceMatch,
  type GraphToolName,
  type IngestionReceipt,
  type SourceInput,
} from "./lib/api";
import "./app/styles.css";

type View = "evidence" | "ask" | "tools" | "ingest";
type SourceKind = SourceInput["kind"];
type SourceFormat = SourceInput["file_format"];

const GRAPH_TOOLS: Array<{ name: GraphToolName; label: string; fields: string[] }> = [
  { name: "search_evidence", label: "Search evidence", fields: ["query"] },
  { name: "resolve_entities", label: "Resolve entities", fields: ["query"] },
  { name: "who_owns", label: "Who owns", fields: ["entity_id"] },
  { name: "who_knows", label: "Who knows", fields: ["skill_id"] },
  { name: "why_decision", label: "Why decision", fields: ["decision_id"] },
  { name: "decision_lineage", label: "Decision lineage", fields: ["decision_id"] },
  { name: "impact", label: "Dependency impact", fields: ["entity_id"] },
  { name: "find_conflicts", label: "Find conflicts", fields: [] },
  { name: "find_stale_claims", label: "Find stale claims", fields: [] },
  { name: "how_related", label: "How related", fields: ["from_id", "to_id"] },
];

export default function App() {
  const [view, setView] = useState<View>("evidence");
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<EvidenceMatch[]>([]);
  const [searchError, setSearchError] = useState("");
  const [searching, setSearching] = useState(false);
  const [sourceId, setSourceId] = useState("");
  const [sourceKind, setSourceKind] = useState<SourceKind>("document");
  const [sourceFormat, setSourceFormat] = useState<SourceFormat>("md");
  const [canonicalUrl, setCanonicalUrl] = useState("");
  const [content, setContent] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState("");
  const [receipt, setReceipt] = useState<IngestionReceipt | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AnswerResponse | null>(null);
  const [answerError, setAnswerError] = useState("");
  const [asking, setAsking] = useState(false);
  const [toolName, setToolName] = useState<GraphToolName>("who_owns");
  const [toolValues, setToolValues] = useState<Record<string, string>>({});
  const [toolResults, setToolResults] = useState<Array<Record<string, unknown>> | null>(null);
  const [toolError, setToolError] = useState("");
  const [runningTool, setRunningTool] = useState(false);
  const [loadingDemo, setLoadingDemo] = useState(false);
  const [demoMessage, setDemoMessage] = useState("");
  const [demoError, setDemoError] = useState("");

  async function loadDemo() {
    setLoadingDemo(true);
    setDemoMessage("");
    setDemoError("");
    try {
      const result = await knowledgeApi.loadDemo();
      setDemoMessage(`Loaded ${result.sources_loaded} demo sources. You can now ask questions.`);
    } catch (error) {
      setDemoError(messageFor(error));
    } finally {
      setLoadingDemo(false);
    }
  }

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSearching(true);
    setSearchError("");
    try {
      const result = await knowledgeApi.searchEvidence(query.trim());
      setMatches(result.matches);
    } catch (error) {
      setMatches([]);
      setSearchError(messageFor(error));
    } finally {
      setSearching(false);
    }
  }

  async function ingest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIngesting(true);
    setIngestError("");
    setReceipt(null);
    try {
      const result = await knowledgeApi.ingestSource({
        source_id: sourceId.trim(),
        kind: sourceKind,
        file_format: sourceFormat,
        canonical_url: canonicalUrl.trim() || undefined,
        content,
      });
      setReceipt(result);
    } catch (error) {
      setIngestError(messageFor(error));
    } finally {
      setIngesting(false);
    }
  }

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAsking(true);
    setAnswerError("");
    setAnswer(null);
    try {
      setAnswer(await knowledgeApi.answerQuestion(question.trim()));
    } catch (error) {
      setAnswerError(messageFor(error));
    } finally {
      setAsking(false);
    }
  }

  async function runTool(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRunningTool(true);
    setToolError("");
    setToolResults(null);
    try {
      const params = Object.fromEntries(
        Object.entries(toolValues).filter(([, value]) => value.trim()),
      );
      const result = await knowledgeApi.runTool(toolName, params);
      setToolResults(result.results);
    } catch (error) {
      setToolError(messageFor(error));
    } finally {
      setRunningTool(false);
    }
  }

  async function readFile(file: File | undefined) {
    if (!file) return;
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (!["md", "txt", "json", "csv"].includes(extension ?? "")) {
      setIngestError("Choose a Markdown, TXT, JSON, or CSV file.");
      return;
    }
    setSourceFormat(extension as SourceFormat);
    setContent(await file.text());
    setSourceId((current) => current || `document:${file.name.replace(/\.[^.]+$/, "")}`);
    setIngestError("");
  }

  return (
    <div className="app-shell">
      <header className="masthead">
        <a className="brand" href="#home" aria-label="AletheiaOS home">
          <span className="brand-mark" aria-hidden="true">
            A
          </span>
          <span>AletheiaOS</span>
        </a>
        <p className="product-subtitle">The Truth Layer for Organizational Intelligence</p>
        <a className="docs-link" href={`${API_BASE_URL}/docs`} target="_blank" rel="noreferrer">
          API docs <span aria-hidden="true">↗</span>
        </a>
      </header>

      <main id="home">
        <section className="intro-band">
          <div>
            <p className="eyebrow">PROOF-CARRYING COMPANY GRAPH</p>
            <h1>AletheiaOS</h1>
            <p className="lead-line">Knowledge, with its receipts.</p>
          </div>
          <SystemStatus />
        </section>

        <nav className="view-tabs" aria-label="Knowledge workspace">
          <button
            aria-current={view === "evidence" ? "page" : undefined}
            className={view === "evidence" ? "tab active" : "tab"}
            onClick={() => setView("evidence")}
            type="button"
          >
            Evidence search
          </button>
          <button
            aria-current={view === "ask" ? "page" : undefined}
            className={view === "ask" ? "tab active" : "tab"}
            onClick={() => setView("ask")}
            type="button"
          >
            Ask AletheiaOS
          </button>
          <button
            aria-current={view === "tools" ? "page" : undefined}
            className={view === "tools" ? "tab active" : "tab"}
            onClick={() => setView("tools")}
            type="button"
          >
            Graph tools
          </button>
          <button
            aria-current={view === "ingest" ? "page" : undefined}
            className={view === "ingest" ? "tab active" : "tab"}
            onClick={() => setView("ingest")}
            type="button"
          >
            Add a source
          </button>
        </nav>

        {view === "evidence" ? (
          <section className="workspace" aria-labelledby="evidence-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">RETRIEVAL</p>
                <h2 id="evidence-heading">Search active evidence</h2>
              </div>
              <span className="scope-note">Current source revisions only</span>
            </div>
            <form className="search-form" onSubmit={(event) => void search(event)}>
              <label className="visually-hidden" htmlFor="evidence-query">
                Search terms
              </label>
              <input
                id="evidence-query"
                maxLength={200}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search a phrase, person, project, or decision"
                required
                value={query}
              />
              <button className="primary-button" disabled={searching} type="submit">
                {searching ? "Searching..." : "Search evidence"}
              </button>
            </form>
            {searchError && (
              <p className="form-message error" role="alert">
                {searchError}
              </p>
            )}
            {matches.length > 0 ? (
              <div className="results" aria-live="polite">
                <p className="result-count">{matches.length} evidence matches</p>
                {matches.map((match) => (
                  <EvidenceItem key={match.chunk_id} match={match} />
                ))}
              </div>
            ) : !searchError && !searching && query ? (
              <p className="empty-state" role="status">
                No active evidence matched that search.
              </p>
            ) : (
              <p className="empty-state">Search indexed passages from active source revisions.</p>
            )}
          </section>
        ) : view === "ask" ? (
          <section className="workspace" aria-labelledby="ask-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">GROUNDED QUESTIONS</p>
                <h2 id="ask-heading">Ask against the graph</h2>
              </div>
              <span className="scope-note">Unresolved claims stay unanswered</span>
            </div>
            <div className="demo-loader">
              <button
                className="secondary-button"
                disabled={loadingDemo}
                onClick={() => void loadDemo()}
                type="button"
              >
                {loadingDemo ? "Loading demo graph..." : "Load synthetic company demo"}
              </button>
              {demoMessage && (
                <p className="form-message success" role="status">
                  {demoMessage}
                </p>
              )}
              {demoError && (
                <p className="form-message error" role="alert">
                  {demoError}
                </p>
              )}
            </div>
            <form className="question-form" onSubmit={(event) => void ask(event)}>
              <label htmlFor="question">Question</label>
              <textarea
                id="question"
                maxLength={1000}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Who owns Project Launch?"
                required
                rows={3}
                value={question}
              />
              <button className="primary-button" disabled={asking} type="submit">
                {asking ? "Checking evidence..." : "Ask"}
              </button>
            </form>
            {answerError && (
              <p className="form-message error" role="alert">
                {answerError}
              </p>
            )}
            {answer && (
              <section className="answer-result" aria-live="polite">
                <p className={`answer-status ${answer.status}`}>
                  {answer.status.replaceAll("_", " ")}
                </p>
                <p className="answer-text">{answer.answer}</p>
                {answer.entities?.map((entity) => (
                  <p className="entity-candidate" key={String(entity.entity_id)}>
                    {String(entity.entity_type)} · {String(entity.name)} ·{" "}
                    {String(entity.entity_id)}
                  </p>
                ))}
                <CitationList claims={answer.claims} />
              </section>
            )}
          </section>
        ) : view === "tools" ? (
          <section className="workspace" aria-labelledby="tools-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">REVIEWED OPERATIONS</p>
                <h2 id="tools-heading">Explore the graph</h2>
              </div>
            </div>
            <form className="tool-form" onSubmit={(event) => void runTool(event)}>
              <label>
                Graph operation
                <select
                  value={toolName}
                  onChange={(event) => {
                    setToolName(event.target.value as GraphToolName);
                    setToolValues({});
                    setToolResults(null);
                  }}
                >
                  {GRAPH_TOOLS.map((tool) => (
                    <option key={tool.name} value={tool.name}>
                      {tool.label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="tool-parameters">
                {GRAPH_TOOLS.find((tool) => tool.name === toolName)?.fields.map((field) => (
                  <label key={field}>
                    {field.replaceAll("_", " ")}
                    <input
                      onChange={(event) =>
                        setToolValues((values) => ({ ...values, [field]: event.target.value }))
                      }
                      required
                      value={toolValues[field] ?? ""}
                    />
                  </label>
                ))}
              </div>
              {toolError && (
                <p className="form-message error" role="alert">
                  {toolError}
                </p>
              )}
              <button className="primary-button" disabled={runningTool} type="submit">
                {runningTool ? "Querying..." : "Run operation"}
              </button>
            </form>
            {toolResults && <CitationList claims={toolResults} />}
          </section>
        ) : (
          <section className="workspace" aria-labelledby="ingest-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">SOURCE LEDGER</p>
                <h2 id="ingest-heading">Add a source revision</h2>
              </div>
            </div>
            <form className="ingest-form" onSubmit={(event) => void ingest(event)}>
              <label className="file-picker">
                <span>Choose a local file</span>
                <input
                  accept=".md,.markdown,.txt,.json,.csv"
                  onChange={(event) => void readFile(event.target.files?.[0])}
                  type="file"
                />
              </label>
              <div className="form-grid">
                <label>
                  Source ID
                  <input
                    maxLength={512}
                    onChange={(event) => setSourceId(event.target.value)}
                    placeholder="docs:handbook"
                    required
                    value={sourceId}
                  />
                </label>
                <label>
                  Source type
                  <select
                    value={sourceKind}
                    onChange={(event) => setSourceKind(event.target.value as SourceKind)}
                  >
                    <option value="document">Document</option>
                    <option value="ticket">Ticket</option>
                    <option value="chat">Chat message</option>
                    <option value="pull_request">Pull request</option>
                    <option value="decision">Decision</option>
                  </select>
                </label>
                <label>
                  Format
                  <select
                    value={sourceFormat}
                    onChange={(event) => setSourceFormat(event.target.value as SourceFormat)}
                  >
                    <option value="md">Markdown</option>
                    <option value="txt">TXT</option>
                    <option value="json">JSON</option>
                    <option value="csv">CSV</option>
                  </select>
                </label>
                <label>
                  Canonical URL <span className="optional">optional</span>
                  <input
                    onChange={(event) => setCanonicalUrl(event.target.value)}
                    placeholder="https://..."
                    type="url"
                    value={canonicalUrl}
                  />
                </label>
              </div>
              <label className="content-field">
                Source content
                <textarea
                  maxLength={1_000_000}
                  onChange={(event) => setContent(event.target.value)}
                  placeholder="Paste source text or load a local file"
                  required
                  rows={10}
                  value={content}
                />
              </label>
              {ingestError && (
                <p className="form-message error" role="alert">
                  {ingestError}
                </p>
              )}
              {receipt && (
                <p className="form-message success" role="status">
                  Revision {receipt.revision_id.slice(0, 12)} is active; {receipt.chunks_processed}{" "}
                  passages indexed.
                </p>
              )}
              <button className="primary-button" disabled={ingesting} type="submit">
                {ingesting ? "Preparing revision..." : "Validate and activate revision"}
              </button>
            </form>
          </section>
        )}
      </main>
      <footer className="page-footer">
        <span>ALETHEIAOS</span>
        <span>Evidence remains attached to its source revision.</span>
      </footer>
    </div>
  );
}

function CitationList({ claims }: { claims: Array<Record<string, unknown>> }) {
  if (claims.length === 0) return null;
  const citations = claims.flatMap((claim) => {
    const prefixes = ["", "first_", "second_"];
    const nestedEvidence = Array.isArray(claim.evidence) ? claim.evidence.filter(isRecord) : [];
    return [claim, ...nestedEvidence].flatMap((item) =>
      prefixes.flatMap((prefix) => {
        const sourceId = item[`${prefix}source_id`];
        const chunkId = item[`${prefix}chunk_id`];
        if (typeof sourceId !== "string" || typeof chunkId !== "string") return [];
        return [
          {
            sourceId,
            chunkId,
            factId: item[`${prefix}fact_id`],
            revisionId: item[`${prefix}revision_id`],
            locator: item[`${prefix}citation_locator`],
            quote: item[`${prefix}quote`],
            url: item[`${prefix}canonical_url`],
          },
        ];
      }),
    );
  });
  const paths = claims.flatMap((claim) => {
    if (!Array.isArray(claim.path_nodes) || !Array.isArray(claim.relationships)) return [];
    const nodes = claim.path_nodes.filter(isRecord);
    const names = nodes.map((node) =>
      typeof node.name === "string" ? node.name : String(node.id ?? "entity"),
    );
    const relationships = claim.relationships.filter(
      (relationship): relationship is string => typeof relationship === "string",
    );
    return names.length
      ? [
          `${names[0]} ${relationships
            .map((type, index) => `—${type.replaceAll("_", " ")}→ ${names[index + 1] ?? "?"}`)
            .join(" ")}`,
        ]
      : [];
  });
  if (citations.length === 0)
    return <p className="empty-state">No citation records were returned.</p>;
  return (
    <div className="citation-list">
      {paths.map((path) => (
        <p className="graph-path" key={path}>
          {path}
        </p>
      ))}
      {citations.map((citation, index) => (
        <article
          className="citation-item"
          key={`${citation.sourceId}:${citation.chunkId}:${index}`}
        >
          <strong>{typeof citation.factId === "string" ? citation.factId : "Evidence"}</strong>
          <span>{typeof citation.locator === "string" ? citation.locator : citation.chunkId}</span>
          <span>
            Revision{" "}
            {typeof citation.revisionId === "string" ? citation.revisionId.slice(0, 12) : "unknown"}
          </span>
          {typeof citation.url === "string" ? (
            <a href={citation.url} target="_blank" rel="noreferrer">
              {citation.sourceId} ↗
            </a>
          ) : (
            <span>{citation.sourceId}</span>
          )}
          {typeof citation.quote === "string" && <blockquote>{citation.quote}</blockquote>}
        </article>
      ))}
    </div>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function EvidenceItem({ match }: { match: EvidenceMatch }) {
  const sourceName = match.canonical_url ? new URL(match.canonical_url).hostname : match.source_id;
  return (
    <article className="evidence-item">
      <div className="evidence-meta">
        <span className="kind-label">{match.kind.replaceAll("_", " ")}</span>
        <span>{match.citation_locator}</span>
        <span>Revision {match.revision_id.slice(0, 10)}</span>
      </div>
      <p>{match.content}</p>
      <a
        href={match.canonical_url ?? `#${encodeURIComponent(match.source_id)}`}
        target={match.canonical_url ? "_blank" : undefined}
        rel="noreferrer"
      >
        {sourceName}
        {match.canonical_url && <span aria-hidden="true"> ↗</span>}
      </a>
    </article>
  );
}

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : "The request could not be completed.";
}
