const COUNT_PATTERNS = [
  { count: 3, pattern: /(?:三|3)\s*(?:套|身|组|个(?:方案|搭配))/i },
  { count: 2, pattern: /(?:两|二|2)\s*(?:套|身|组|个(?:方案|搭配))/i },
  { count: 1, pattern: /(?:一|1)\s*(?:套|身|组|个(?:方案|搭配))/i },
]

const sleep = (duration) => new Promise((resolve) => setTimeout(resolve, duration))

export function requestedResultCount(request) {
  const text = String(request || '').trim()
  for (const { count, pattern } of COUNT_PATTERNS) {
    if (pattern.test(text)) return count
  }
  if (/(?:几|多)\s*(?:套|身|组|个(?:方案|搭配))/.test(text)) return 3
  return 1
}

export function isRequestTimeout(error) {
  return ['ECONNABORTED', 'ETIMEDOUT'].includes(error?.code)
    || /timeout|超时/i.test(String(error?.message || ''))
}

export async function waitForPersistedAssistant({
  getSession,
  userId,
  sessionId,
  previousAssistantCount,
  isCurrent = () => true,
  intervalMs = 2000,
  timeoutMs = 180000,
  sleepFn = sleep,
}) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline && isCurrent()) {
    try {
      const response = await getSession(userId, sessionId)
      const assistants = (response.data.messages || [])
        .filter((message) => message.role === 'assistant')
      if (assistants.length > previousAssistantCount) {
        return assistants[assistants.length - 1]
      }
    } catch {
      // Keep polling across a transient session-read failure.
    }
    await sleepFn(intervalMs)
  }
  return null
}
