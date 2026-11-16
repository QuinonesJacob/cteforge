import type { ReactNode } from "react";
import type { Tone } from "../lib/trust";

interface Props {
  tone: Tone;
  children: ReactNode;
  title?: string;
  className?: string;
}

export function Badge({ tone, children, title, className = "" }: Props) {
  return (
    <span className={`badge tone-${tone} ${className}`.trim()} title={title}>
      {children}
    </span>
  );
}
