import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "../src/App";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);
beforeEach(() => mockFetch.mockReset());
afterEach(cleanup);

test("renders AletheiaOS and service status", async () => {
  mockFetch
    .mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ status: "ok", service: "api" }),
    })
    .mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ status: "ok", service: "falkordb" }),
    });
  render(<App />);
  expect(screen.getByRole("heading", { name: "AletheiaOS" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getAllByText("Available")).toHaveLength(2));
});

test("shows unavailable state and refreshes", async () => {
  mockFetch.mockRejectedValueOnce(new Error("offline"));
  render(<App />);
  await screen.findByText(/API cannot be reached/);
  mockFetch
    .mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ status: "ok", service: "api" }),
    })
    .mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: async () => ({ status: "unavailable", service: "falkordb" }),
    });
  fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
  await screen.findByText(/FalkorDB is unavailable/);
});

test("searches active evidence through the backend", async () => {
  mockFetch.mockImplementation(async (input: unknown) => {
    const url = String(input);
    if (url.endsWith("/live")) {
      return { ok: true, status: 200, json: async () => ({ status: "ok", service: "api" }) };
    }
    if (url.endsWith("/ready")) {
      return { ok: true, status: 200, json: async () => ({ status: "ok", service: "falkordb" }) };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        matches: [
          {
            source_id: "docs:handbook",
            kind: "document",
            canonical_url: "https://example.com/handbook",
            chunk_id: "chunk-1",
            revision_id: "revision-1234567890",
            citation_locator: "ownership, lines 4-6",
            content: "The release owner is Jordan.",
          },
        ],
      }),
    };
  });

  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: "Search terms" }), {
    target: { value: "release owner" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Search evidence" }));

  expect(await screen.findByText("The release owner is Jordan.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /example.com/ })).toHaveAttribute(
    "href",
    "https://example.com/handbook",
  );
  expect(mockFetch).toHaveBeenCalledWith(
    expect.stringContaining("/api/v1/evidence/search"),
    expect.objectContaining({ body: JSON.stringify({ query: "release owner", limit: 10 }) }),
  );
});

test("ingests a source revision through the backend", async () => {
  mockFetch.mockImplementation(async (input: unknown) => {
    const url = String(input);
    if (url.endsWith("/live")) {
      return { ok: true, status: 200, json: async () => ({ status: "ok", service: "api" }) };
    }
    if (url.endsWith("/ready")) {
      return { ok: true, status: 200, json: async () => ({ status: "ok", service: "falkordb" }) };
    }
    return {
      ok: true,
      status: 201,
      json: async () => ({
        source_id: "docs:handbook",
        revision_id: "revision-1234567890",
        chunks_processed: 2,
        status: "active",
      }),
    };
  });

  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Add a source" }));
  fireEvent.change(screen.getByLabelText("Source ID"), { target: { value: "docs:handbook" } });
  fireEvent.change(screen.getByLabelText("Source content"), {
    target: { value: "# Handbook\nCurrent source text." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Validate and activate revision" }));

  expect(await screen.findByText(/2 passages indexed/)).toBeInTheDocument();
  expect(mockFetch).toHaveBeenCalledWith(
    expect.stringContaining("/api/v1/sources"),
    expect.objectContaining({ method: "POST" }),
  );
});

test("shows a supported answer with its source citation", async () => {
  mockFetch.mockImplementation(async (input: unknown) => {
    const url = String(input);
    if (url.endsWith("/live")) {
      return { ok: true, status: 200, json: async () => ({ status: "ok", service: "api" }) };
    }
    if (url.endsWith("/ready")) {
      return { ok: true, status: 200, json: async () => ({ status: "ok", service: "falkordb" }) };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        status: "supported",
        answer: "Jordan is supported as the current owner.",
        claims: [
          {
            fact_id: "fact:owner",
            source_id: "docs:handbook",
            canonical_url: "https://example.com/handbook",
            chunk_id: "chunk-1",
            revision_id: "revision-1234567890",
            citation_locator: "ownership, lines 4-6",
          },
        ],
      }),
    };
  });

  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Ask AletheiaOS" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Question" }), {
    target: { value: "Who owns Project Launch?" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  expect(await screen.findByText("Jordan is supported as the current owner.")).toBeInTheDocument();
  expect(screen.getByText("ownership, lines 4-6")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "docs:handbook ↗" })).toHaveAttribute(
    "href",
    "https://example.com/handbook",
  );
});

test("loads the curated synthetic company dataset through the backend", async () => {
  mockFetch.mockImplementation(async (input: unknown) => {
    const url = String(input);
    if (url.endsWith("/live")) {
      return { ok: true, status: 200, json: async () => ({ status: "ok", service: "api" }) };
    }
    if (url.endsWith("/ready")) {
      return { ok: true, status: 200, json: async () => ({ status: "ok", service: "falkordb" }) };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({ sources_loaded: 7, sources: [] }),
    };
  });

  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Ask AletheiaOS" }));
  fireEvent.click(screen.getByRole("button", { name: "Load synthetic company demo" }));

  expect(await screen.findByText(/Loaded 7 demo sources/)).toBeInTheDocument();
  expect(mockFetch).toHaveBeenCalledWith(
    expect.stringContaining("/api/v1/demo/load"),
    expect.objectContaining({ method: "POST" }),
  );
});

test("runs a reviewed graph tool and renders its citation", async () => {
  mockFetch.mockImplementation(async (input: unknown) => {
    const url = String(input);
    if (url.endsWith("/live")) {
      return { ok: true, status: 200, json: async () => ({ status: "ok", service: "api" }) };
    }
    if (url.endsWith("/ready")) {
      return { ok: true, status: 200, json: async () => ({ status: "ok", service: "falkordb" }) };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        tool: "who_owns",
        results: [
          {
            fact_id: "fact:owner",
            source_id: "docs:handbook",
            chunk_id: "chunk-1",
            revision_id: "revision-1234567890",
            citation_locator: "ownership, lines 4-6",
          },
        ],
      }),
    };
  });

  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Graph tools" }));
  fireEvent.change(screen.getByLabelText("entity id"), { target: { value: "project:launch" } });
  fireEvent.click(screen.getByRole("button", { name: "Run operation" }));

  expect(await screen.findByText("fact:owner")).toBeInTheDocument();
  expect(screen.getByText("ownership, lines 4-6")).toBeInTheDocument();
  expect(mockFetch).toHaveBeenCalledWith(
    expect.stringContaining("/api/v1/tools/who_owns"),
    expect.objectContaining({
      body: JSON.stringify({ entity_id: "project:launch" }),
    }),
  );
});

test("renders the explicit graph-unavailable answer state", async () => {
  mockFetch.mockImplementation(async (input: unknown) => {
    const url = String(input);
    if (url.endsWith("/live")) {
      return { ok: true, status: 200, json: async () => ({ status: "ok", service: "api" }) };
    }
    if (url.endsWith("/ready")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ status: "unavailable", service: "falkordb" }),
      };
    }
    return {
      ok: false,
      status: 503,
      json: async () => ({
        status: "temporarily_unavailable",
        answer: "FalkorDB is temporarily unavailable.",
        claims: [],
      }),
    };
  });

  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Ask AletheiaOS" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Question" }), {
    target: { value: "Who owns Project Launch?" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  expect(await screen.findByText("temporarily unavailable")).toBeInTheDocument();
  expect(screen.getByText("FalkorDB is temporarily unavailable.")).toBeInTheDocument();
});
