import { useCallback, useEffect, useState } from "react";
import { api, type HealthResponse } from "../../lib/api";

type State = "loading" | "available" | "unavailable";

export function SystemStatus() {
  const [apiState, setApiState] = useState<State>("loading");
  const [graphState, setGraphState] = useState<State>("loading");
  const [message, setMessage] = useState("Checking local services…");
  const refresh = useCallback(async () => {
    setApiState("loading");
    setGraphState("loading");
    setMessage("Checking local services…");
    try {
      const live = await api.live();
      setApiState(live.status === "ok" ? "available" : "unavailable");
      const ready: HealthResponse = await api.ready();
      const graphAvailable = ready.status === "ok";
      setGraphState(graphAvailable ? "available" : "unavailable");
      setMessage(
        graphAvailable
          ? "All local services are ready."
          : "API is running, but FalkorDB is unavailable.",
      );
    } catch {
      setApiState("unavailable");
      setGraphState("unavailable");
      setMessage("The API cannot be reached. Start the local stack and try again.");
    }
  }, []);
  useEffect(() => void refresh(), [refresh]);
  return (
    <section aria-live="polite">
      <p>{message}</p>
      <div className="cards">
        <StatusCard name="Backend API" state={apiState} />
        <StatusCard name="FalkorDB" state={graphState} />
      </div>
      <button onClick={() => void refresh()}>Refresh status</button>
    </section>
  );
}

function StatusCard({ name, state }: { name: string; state: State }) {
  const label =
    state === "loading" ? "Checking" : state === "available" ? "Available" : "Unavailable";
  return (
    <article className={state}>
      <h2>{name}</h2>
      <strong>{label}</strong>
    </article>
  );
}
