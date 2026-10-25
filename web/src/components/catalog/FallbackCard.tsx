/**
 * FallbackCard: what an item renders when its type is not a catalog member
 * (PRD "the agent never invents markup" — unknown type never errors html).
 */
import type { CatalogComponentProps } from "./shared";
import { Card } from "./shared";

export default function FallbackCard({ props }: CatalogComponentProps) {
  return (
    <Card className="border-amber-200 bg-amber-50">
      <p className="text-sm font-medium text-amber-800">
        This component is outside the catalog and was not rendered.
      </p>
      {typeof props?.type === "string" ? (
        <p className="mt-1 font-mono text-xs text-amber-700">
          unknown type: {props.type}
        </p>
      ) : null}
    </Card>
  );
}
