// Issue #13: let each catalog component read the streamed narrative through
// context. Mirrors components/catalog/catalogSize.tsx exactly — and for the
// same reason its comment gives: the renderer's renderItem contract spreads
// only validated props and is pinned by tests, so a copy prop threaded
// through A2UIRenderer would change it.

import { createContext, type ReactNode, useContext } from "react";

export interface SlotCopyValue {
  slotCopy: Record<string, string>;
  slotsPending: boolean;
}

const SlotCopyContext = createContext<SlotCopyValue>({
  slotCopy: {},
  slotsPending: false,
});

export function SlotCopyProvider({
  value,
  children,
}: {
  value: SlotCopyValue;
  children: ReactNode;
}) {
  return (
    <SlotCopyContext.Provider value={value}>
      {children}
    </SlotCopyContext.Provider>
  );
}

export function useSlotCopy(): SlotCopyValue {
  return useContext(SlotCopyContext);
}
