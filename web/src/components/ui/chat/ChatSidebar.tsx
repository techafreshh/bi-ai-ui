// Issue #13: the left chat sidebar — CopilotSidebar docked left with the v2
// slot composition. CopilotSidebar omits the chatView slot (it builds its
// own sidebar view internally), so the bubbles land via the messageView
// slot it forwards through CopilotChat. NOTE: v2 slots resolve component
// TYPES, not elements (renderSlotElement drops a valid element back to the
// default) — so both levels are wrapper components, with `as` casts for the
// namespace statics (MarkdownRenderer, Container, …) that slot resolution
// never reads at runtime. Prompt input, welcome screen and suggestions stay
// stock; only the message bubbles take beUI styling.

import {
  CopilotChatAssistantMessage,
  CopilotChatMessageView,
  CopilotChatUserMessage,
  CopilotSidebar,
  type CopilotChatMessageViewProps,
} from "@copilotkit/react-core/v2";
import { AGENT_ID } from "../../../lib/agent";
import { BeUIAssistantMessage, BeUIUserMessage } from "./bubbles";

function BeUIMessageView(props: CopilotChatMessageViewProps) {
  return (
    <CopilotChatMessageView
      {...props}
      assistantMessage={
        BeUIAssistantMessage as typeof CopilotChatAssistantMessage
      }
      userMessage={BeUIUserMessage as typeof CopilotChatUserMessage}
    />
  );
}

export function ChatSidebar() {
  return (
    <CopilotSidebar
      agentId={AGENT_ID}
      defaultOpen
      position="left"
      labels={{ chatInputPlaceholder: "Type a message…" }}
      messageView={BeUIMessageView as typeof CopilotChatMessageView}
    />
  );
}
