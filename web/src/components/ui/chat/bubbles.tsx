// Issue #13: beUI-styled chat bubbles for the CopilotKit v2 slot
// composition. Thin wrappers — CopilotChatAssistantMessage already renders
// markdown incrementally (streaming is CopilotKit-internal), so the only
// additions are the beUI token classes and the LatestMiniCard appended to
// the last assistant turn. Exercised by types + the runtime gate, not DOM
// tests: mounting the real message components requires the full thread
// runtime the fake-agent seam deliberately excludes.

import {
  CopilotChatAssistantMessage,
  CopilotChatUserMessage,
  type CopilotChatAssistantMessageProps,
  type CopilotChatUserMessageProps,
} from "@copilotkit/react-core/v2";
import { AGENT_ID } from "../../../lib/agent";
import { useA2UI } from "../../../hooks/useA2UI";
import { LatestMiniCard } from "./MiniCardPanel";

export function BeUIAssistantMessage(props: CopilotChatAssistantMessageProps) {
  const { message, isLatest } = props;
  const { a2ui, phase } = useA2UI(AGENT_ID);
  return (
    <div className="beui-assistant-message text-sm leading-relaxed text-foreground">
      <CopilotChatAssistantMessage {...props} message={message} />
      <LatestMiniCard isLatest={isLatest} a2ui={a2ui} phase={phase} />
    </div>
  );
}

export function BeUIUserMessage(props: CopilotChatUserMessageProps) {
  // No background of its own: the stock message already renders its bubble —
  // the wrapper only carries the beUI hook class and type size.
  return (
    <div className="beui-user-message text-sm leading-relaxed">
      <CopilotChatUserMessage {...props} />
    </div>
  );
}
