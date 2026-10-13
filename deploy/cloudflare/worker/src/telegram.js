// Telegram Bot API over fetch: HTML messages, Blocked on 403, RateLimited on 429.
// Error messages are built here from the method name and Telegram's description only: never the URL (it holds the token).
export class Blocked extends Error {}
export class RateLimited extends Error {
  constructor(method, retryAfter) { super(`${method}: rate limited for ${retryAfter}s`); this.retryAfter = retryAfter; }
}

export class Telegram {
  // the arrow wrapper matters: calling the global fetch as this.fetch(...) throws "Illegal invocation" on Workers.
  // maxWait: how long one call may sleep on a 429 before giving up. The webhook waits a little; the scheduled
  // broadcast passes 0, so a 429 ends the run and the rest goes on the next tick (every retry costs a subrequest).
  constructor(token, fetchFn = (...a) => fetch(...a), api = "https://api.telegram.org", { maxWait = 3, tries = 2, timeoutMs = 10000 } = {}) {
    this.base = `${api}/bot${token}`; this.fetch = fetchFn; this.maxWait = maxWait; this.tries = tries; this.timeoutMs = timeoutMs;
  }
  async call(method, params) {
    for (let attempt = 1; ; attempt++) {
      let res;
      try {
        res = await this.fetch(`${this.base}/${method}`, {
          method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(params),
          signal: AbortSignal.timeout(this.timeoutMs), // a hung call must not eat the run's deadline
        });
      } catch {
        if (attempt < this.tries) continue;
        throw new Error(`${method}: network error`); // the original error may quote the URL
      }
      const body = await res.json().catch(() => ({}));
      if (res.ok) return body.result;
      if (res.status === 429) {
        const wait = Number(body?.parameters?.retry_after ?? 1);
        if (attempt < this.tries && wait <= this.maxWait) { await new Promise((r) => setTimeout(r, 1000 * wait)); continue; }
        throw new RateLimited(method, wait);
      }
      if (res.status === 403) throw new Blocked(body.description || "forbidden");
      if (res.status === 400 && /not modified/.test(body.description || "")) return null;
      if (res.status >= 500 && attempt < this.tries) continue;
      throw new Error(`${method}: ${res.status} ${String(body.description || "").slice(0, 120)}`);
    }
  }
  send(chat, text, kb) {
    return this.call("sendMessage", { chat_id: chat, text, parse_mode: "HTML", reply_markup: kb, link_preview_options: { is_disabled: true } });
  }
  edit(chat, mid, text, kb) {
    return this.call("editMessageText", { chat_id: chat, message_id: mid, text, parse_mode: "HTML", reply_markup: kb, link_preview_options: { is_disabled: true } });
  }
  answer(id, text) { return this.call("answerCallbackQuery", { callback_query_id: id, text: text || undefined }); }
  async react(chat, mid) {
    try { await this.call("setMessageReaction", { chat_id: chat, message_id: mid, reaction: [{ type: "emoji", emoji: "👀" }] }); } catch { /* cosmetic */ }
  }
}
