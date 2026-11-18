import { listProfiles } from "../api/endpoints";
import { useAsync } from "../state/useAsync";
import { UserMenu } from "./UserMenu";

/** The top bar (ADR 0029): the team's default connection (ADR 0021) and the user menu. */
export function AppHeader() {
  const { data } = useAsync((signal) => listProfiles({ signal }), []);
  const def = data?.find((p) => p.default);
  return (
    <header className="app-header">
      <span className="conn-indicator">
        {data === undefined ? null : def ? (
          <>
            <span className="conn-dot" aria-hidden="true" />
            <code>{def.name}</code> default connection
          </>
        ) : (
          <>
            <span className="conn-dot conn-dot-off" aria-hidden="true" />
            No default connection
          </>
        )}
      </span>
      <UserMenu />
    </header>
  );
}
