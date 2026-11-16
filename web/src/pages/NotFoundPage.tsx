import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";

export function NotFoundPage() {
  return (
    <div className="page">
      <EmptyState
        title="Page not found"
        action={
          <Link to="/" className="btn btn-primary">
            Go to the library
          </Link>
        }
      >
        The address doesn't match any page.
      </EmptyState>
    </div>
  );
}
