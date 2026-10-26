// Issue #12: let each catalog component know whether it renders full-size on
// the canvas or as an inline mini-card in chat later (issue #36 wraps the
// renderer in a "mini" provider). Context instead of a prop threaded through
// A2UIRenderer: the renderer's renderItem contract spreads only validated
// props and is pinned by tests — a size prop would change it.

import { createContext, type ReactNode, useContext } from "react";

export type CatalogSize = "full" | "mini";

const CatalogSizeContext = createContext<CatalogSize>("full");

export function CatalogSizeProvider({
  value,
  children,
}: {
  value: CatalogSize;
  children: ReactNode;
}) {
  return (
    <CatalogSizeContext.Provider value={value}>
      {children}
    </CatalogSizeContext.Provider>
  );
}

export function useCatalogSize(): CatalogSize {
  return useContext(CatalogSizeContext);
}
