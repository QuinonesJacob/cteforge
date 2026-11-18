import { CopyButton } from "./CopyButton";

interface Props {
  /** Whose token it is. */
  name: string;
  token: string;
  /** The token's own name ("Claude Code"), when known. */
  tokenName?: string;
  onDismiss: () => void;
}

/** A new token is shown exactly once. It is never stored or logged by the GUI. */
export function TokenReveal({ name, token, tokenName, onDismiss }: Props) {
  return (
    <div className="callout callout-warn token-reveal" role="alert">
      <p className="callout-title">
        Token {tokenName ? `"${tokenName}" ` : ""}for {name}: copy it now
      </p>
      <p className="small">
        This is the only time it is shown. The server stores only a hash; if it is lost, generate a new
        token and revoke this one.
      </p>
      <div className="token-row">
        <code className="token-value" aria-label="Token">
          {token}
        </code>
        <CopyButton text={token} label="Copy token" />
      </div>
      <button type="button" className="btn btn-sm mt" onClick={onDismiss}>
        I have saved it
      </button>
    </div>
  );
}
