# Optimization Report - Nakhla Helpdesk Agent

**Assessment Date:** 2026-10-27  
**Project:** Nakhla IT Helpdesk Agent  
**Scope:** `app/`, `services/`, `ui/` - Focus on functionality, token consumption, and runtime/response time

## Executive Summary

The application is well-architected with good separation of concerns. There are several optimization opportunities across token usage, runtime performance, and functionality. Some optimizations can be implemented without changing core behavior, while others require careful consideration.

**Overall Optimization Potential: HIGH**

## 1. Token Consumption Optimizations

### Current State
- Uses OpenRouter with `gpt-4.1-mini` (default)
- Has fallback to deterministic templates when LLM unavailable
- Usage tracking implemented
- Context includes session history (limited to 24 messages)

### Recommendations

| Optimization | Impact | Effort | Priority | Details |
|--------------|--------|--------|----------|---------|
| **Reduce context window** | HIGH (30-50% token reduction) | LOW | P0 | Currently stores up to 24 messages. Consider reducing to 12-16 messages or summarizing older turns. |
| **Smarter message truncation** | HIGH | LOW | P0 | Truncate very long messages in history, keep only recent + relevant context |
| **Prompt compression** | HIGH | MEDIUM | P0 | The intake/resolver prompts could be optimized to be more concise while maintaining quality |
| **Avoid redundant retrieval context** | MEDIUM-HIGH | MEDIUM | P0 | Some prompts include full retrieval results; could summarize or deduplicate chunks |
| **Use smaller model for simple tasks** | HIGH | MEDIUM | P1 | Use cheaper/faster model for intake extraction vs complex reasoning |
| **Cache LLM responses** | HIGH | MEDIUM | P1 | Cache responses for identical queries (with same context) - be careful about statefulness |
| **Token-aware trimming** | MEDIUM-HIGH | MEDIUM | P1 | Implement dynamic context trimming based on token count |
| **Few-shot example optimization** | MEDIUM | LOW | P2 | Remove unnecessary examples or use shorter ones |

**Estimated Savings:** 40-60% reduction in average token consumption

### Implementation Notes
- Add token counting (e.g., `tiktoken`) to estimate before sending
- Implement sliding window with importance scoring
- Consider using prompt caching if available on provider

## 2. Runtime & Response Time Optimizations

### Current State
- FastAPI async throughout
- Background warmup for index and MCP
- Caching in memory (sessions) and SQLite
- Average turn time ~3.2s according to evals

### Recommendations

| Optimization | Impact | Effort | Priority | Details |
|--------------|--------|--------|----------|---------|
| **Parallelize independent calls** | HIGH | LOW | P0 | In the agent graph, some MCP calls could be parallelized (e.g., status + other checks) |
| **Optimize retrieval** | HIGH | MEDIUM | P0 | BM25 is in-memory which is good; could optimize scoring, add early termination |
| **Cache service status longer** | MEDIUM | LOW | P0 | Status cached 120s per session - could increase or use global cache |
| **Reduce DB round trips** | MEDIUM-HIGH | LOW | P0 | Batch session saves, use connection pooling better |
| **Lazy loading** | MEDIUM | LOW | P1 | Load heavy dependencies only when needed |
| **Optimize JSON serialization** | LOW-MEDIUM | LOW | P1 | Use faster JSON libraries or reduce serialization overhead |
| **MCP connection pooling** | MEDIUM | MEDIUM | P1 | Already single connection but could add health checks/reconnect logic |
| **Vector search optimization** | MEDIUM | MEDIUM | P1 | If using embeddings, consider approximate nearest neighbor (FAISS) vs current approach |
| **Precompute common queries** | MEDIUM | MEDIUM | P2 | Cache results for frequent error codes/patterns |

**Estimated Improvement:** 20-40% reduction in response time

### Specific Code Improvements
- In `app/graph/chat.py` (`service_status`): status is cached per session; a shared cache would
  suppress the `get_service_status` calls that incident evals assert, so correctness wins
- In `retriever.py`: Could parallelize embedding generation and BM25
- In `sessions.py`: Consider in-memory cache with TTL instead of DB reads on every access

## 3. Functionality Optimizations

### Current State
- Comprehensive feature set
- Good LangGraph graph design
- Deterministic policy engine
- Works without LLM key

### Recommendations

| Optimization | Impact | Effort | Priority | Details |
|--------------|--------|--------|----------|---------|
| **Improve clarification flow** | MEDIUM | LOW | P0 | Better detection of when clarification is needed, fewer unnecessary clarifications |
| **Enhanced error messages** | MEDIUM | LOW | P0 | More actionable error messages for users |
| **Better handling of multi-part questions** | MEDIUM | MEDIUM | P0 | Currently processes as single turn; could decompose complex queries |
| **Smarter ticket deduplication** | MEDIUM | LOW | P1 | Better detection of duplicate drafts beyond subject matching |
| **Improved memory recall** | MEDIUM | MEDIUM | P1 | More intelligent use of long-term memory, better relevance scoring |
| **Offline mode improvements** | MEDIUM | MEDIUM | P1 | Already has fallbacks, but could be more robust |
| **Better incident matching** | LOW-MEDIUM | LOW | P1 | Improve `_match_incident` logic for edge cases |
| **Batch operations** | LOW | LOW | P2 | Allow batch approvals or other operations |
| **WebSocket support** | LOW | HIGH | P2 | For real-time updates (overkill for this use case) |

## 4. Code Quality & Maintainability Optimizations

| Optimization | Impact | Effort | Priority | Details |
|--------------|--------|--------|----------|---------|
| **Remove dead code** | LOW | LOW | P1 | Scan for unused imports/functions |
| **Consolidate regex patterns** | LOW | LOW | P1 | Many regex patterns defined multiple places |
| **Type hints** | LOW | LOW | P1 | Some functions missing complete type hints |
| **Configuration validation** | LOW | LOW | P1 | Validate env vars on startup |
| **Logging improvements** | LOW | LOW | P2 | Structured logging, log levels |
| **Constants extraction** | LOW | LOW | P2 | Magic numbers could be constants |

## 5. Specific Implementation Suggestions

### Quick Wins (High Impact, Low Effort)

1. **Reduce session history in prompts** (tokens) - In agents, limit history passed to LLM
2. **Keep status cache session-scoped** (speed + correctness) - It is already cached for 120s per session
3. **Parallel tool calls** (speed) - Use `asyncio.gather` where independent
4. **Optimize DB writes** (speed) - Batch message inserts

### Medium-term Improvements

1. **Implement token counting** before LLM calls
2. **Add response caching** for deterministic queries
3. **Improve retrieval ranking** with learning-to-rank
4. **Add metrics/observability**

### Long-term Improvements

1. **Migrate to connection pooling** for SQLite (or use aiosqlite)
2. **Implement streaming responses**
3. **Add Redis for distributed caching**

## Estimated Overall Impact

| Category | Current | Optimized (Realistic) | Improvement |
|----------|---------|----------------------|-------------|
| **Avg Token Usage** | ~1200-1500 tokens/turn | ~700-900 tokens/turn | **40-50% reduction** |
| **Avg Response Time** | ~3.2s | ~2.0-2.5s | **25-35% reduction** |
| **Cost per 1000 turns** | ~$0.63 (as per eval) | ~$0.35-0.45 | **30-45% reduction** |
| **Memory Usage** | Moderate | Similar/Better | **Minimal change** |

## Implementation Priority Matrix

**Do First (P0):**
- Reduce context window size
- Parallelize independent async calls
- Session status cache
- Prompt compression

**Do Second (P1):**
- Token-aware trimming
- DB optimization
- Better deduplication
- Model selection by task

**Do Later (P2):**
- Advanced caching
- Streaming
- Observability

## Notes & Caveats

- **Don't sacrifice correctness** - The deterministic policy engine must remain unchanged
- **Test thoroughly** - Run evals after changes to ensure no regressions (currently 30/30 passing)
- **Maintain fallbacks** - Optimizations shouldn't break no-LLM mode
- **Preserve security** - Don't cache sensitive data inappropriately

**Conclusion:** The codebase is already well-optimized for its use case. The biggest wins are in token consumption (through better context management) and minor runtime improvements. Most optimizations are low-risk and can be implemented incrementally.
