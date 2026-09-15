import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { PageHeader } from '../../components/layout/PageHeader'
import { Button } from '../../components/ui/Button'
import { EmptyState } from '../../components/ui/EmptyState'
import { Input } from '../../components/ui/Input'
import {
  useChatSession,
  useChatSessions,
  useCreateChatSession,
  useDeleteChatSession,
  useSendChatMessage,
} from '../../lib/api/hooks'
import type { ChatMessage, ChatSession } from '../../lib/api/types'

// portfolio_assistant chat — see plans/agentic_asset_mapping_phase7_8.md
// Phase 8f. Two-pane layout: session history (left) + the active
// conversation (right). Session memory and history both live server-side
// (chat_sessions/chat_messages, ai/agents/portfolio_assistant/agent.py) —
// this page is a thin client over that; nothing here holds conversation
// state beyond the current draft and an optimistic "sending…" bubble.

export function AssistantPage() {
  const { sessionId: sessionIdParam } = useParams()
  const activeSessionId = sessionIdParam ? Number(sessionIdParam) : undefined

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Assistant" />
      <div className="flex min-h-0 flex-1 gap-4">
        <ChatHistorySidebar activeSessionId={activeSessionId} />
        <ChatPane sessionId={activeSessionId} />
      </div>
    </div>
  )
}

function ChatHistorySidebar({ activeSessionId }: { activeSessionId?: number }) {
  const sessions = useChatSessions()
  const createSession = useCreateChatSession()
  const deleteSession = useDeleteChatSession()
  const navigate = useNavigate()

  return (
    <div className="flex w-64 shrink-0 flex-col rounded-lg border border-border bg-surface">
      <div className="border-b border-border p-3">
        <Button
          variant="primary"
          className="w-full"
          disabled={createSession.isPending}
          onClick={() =>
            createSession.mutate(undefined, {
              onSuccess: (session) => navigate(`/assistant/${session.id}`),
            })
          }
        >
          + New chat
        </Button>
      </div>
      <div className="flex-1 space-y-0.5 overflow-y-auto p-2">
        {!sessions.data || sessions.data.length === 0 ? (
          <p className="px-2 py-1.5 text-xs text-muted">No conversations yet.</p>
        ) : (
          sessions.data.map((session) => (
            <SessionRow
              key={session.id}
              session={session}
              active={session.id === activeSessionId}
              onSelect={() => navigate(`/assistant/${session.id}`)}
              onDelete={() => {
                if (!window.confirm(`Delete "${session.title || 'this conversation'}"?`)) return
                deleteSession.mutate(session.id, {
                  onSuccess: () => {
                    if (session.id === activeSessionId) navigate('/assistant')
                  },
                })
              }}
            />
          ))
        )}
      </div>
    </div>
  )
}

function SessionRow({
  session,
  active,
  onSelect,
  onDelete,
}: {
  session: ChatSession
  active: boolean
  onSelect: () => void
  onDelete: () => void
}) {
  return (
    <div
      className={`group flex items-center rounded-md text-sm ${
        active ? 'bg-surface-raised font-medium text-text' : 'text-muted hover:bg-surface-raised hover:text-text'
      }`}
    >
      <button className="min-w-0 flex-1 truncate px-2.5 py-2 text-left" onClick={onSelect}>
        {session.title || 'New chat'}
      </button>
      <button
        className="mr-1.5 hidden shrink-0 px-1 text-subtle hover:text-negative group-hover:block"
        title="Delete conversation"
        onClick={(e) => {
          e.stopPropagation()
          onDelete()
        }}
      >
        ✕
      </button>
    </div>
  )
}

function ChatPane({ sessionId }: { sessionId?: number }) {
  const sessionDetail = useChatSession(sessionId)
  const sendMessage = useSendChatMessage()
  const createSession = useCreateChatSession()
  const navigate = useNavigate()
  const [draft, setDraft] = useState('')
  const [pendingUserMessage, setPendingUserMessage] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const busy = sendMessage.isPending || createSession.isPending

  const messages = sessionDetail.data?.messages ?? []

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages.length, pendingUserMessage])

  const handleSend = () => {
    const text = draft.trim()
    if (!text || busy) return
    setDraft('')
    setPendingUserMessage(text)

    if (sessionId === undefined) {
      createSession.mutate(undefined, {
        onSuccess: (session) => {
          navigate(`/assistant/${session.id}`)
          sendMessage.mutate({ sessionId: session.id, message: text }, { onSettled: () => setPendingUserMessage(null) })
        },
        onError: () => setPendingUserMessage(null),
      })
      return
    }
    sendMessage.mutate({ sessionId, message: text }, { onSettled: () => setPendingUserMessage(null) })
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-border bg-surface">
      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && !pendingUserMessage ? (
          <EmptyState
            title="Ask about your portfolio"
            description="e.g. “what's my total value?”, “how concentrated am I?”, “what's AAPL priced at?”"
          />
        ) : (
          <>
            {messages.map((message) => (
              <ChatBubble key={message.id} message={message} />
            ))}
            {pendingUserMessage && (
              <ChatBubble message={{ id: -1, session_id: sessionId ?? -1, role: 'user', content: pendingUserMessage, tool_calls: null, created_at: '' }} />
            )}
          </>
        )}
        {busy && <ThinkingIndicator />}
        {sendMessage.isError && <p className="text-sm text-negative">{(sendMessage.error as Error).message}</p>}
        {createSession.isError && <p className="text-sm text-negative">{(createSession.error as Error).message}</p>}
      </div>
      <div className="flex gap-2 border-t border-border p-3">
        <Input
          className="flex-1"
          placeholder="Ask about your portfolio…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSend()
            }
          }}
          disabled={busy}
        />
        <Button variant="primary" disabled={!draft.trim() || busy} onClick={handleSend}>
          Send
        </Button>
      </div>
    </div>
  )
}

function ChatBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user'
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[75%] rounded-lg px-3 py-2 text-sm ${
          isUser ? 'bg-accent text-accent-fg' : 'bg-surface-raised text-text'
        }`}
      >
        <p className="whitespace-pre-wrap">{message.content}</p>
        {message.tool_calls && message.tool_calls.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {message.tool_calls.map((call, i) => (
              <span key={i} className="rounded-full bg-black/10 px-2 py-0.5 text-[10px] opacity-70" title="Tool used to answer this">
                🔧 {call.name}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function ThinkingIndicator() {
  return (
    <div className="flex justify-start">
      <div className="flex items-center gap-1 rounded-lg bg-surface-raised px-3 py-2">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-muted [animation-delay:-0.3s]" />
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-muted [animation-delay:-0.15s]" />
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-muted" />
      </div>
    </div>
  )
}
